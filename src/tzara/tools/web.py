"""Web tools: search the internet and read a page.

The model gets two read-only tools. ``web_search`` returns result titles, links and
snippets (from DuckDuckGo, or from Google Search through the Gemini API), and
``read_webpage`` returns the text of one page. The model then writes the summary.

Everything fetched is untrusted: it is clearly labelled as data for the model, and
the tools can only read. A page address is checked before and after every redirect so
the model, or text in a page, cannot make the agent fetch something on this computer
or its network.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, ClassVar, Protocol
from urllib.parse import urljoin, urlparse

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from tzara.config import TzaraConfig, WebSettings
from tzara.prompts import Prompts

logger = logging.getLogger(__name__)


class WebError(Exception):
    """A web lookup failed. The message says why, in words the model can act on."""


@dataclass(frozen=True)
class SearchHit:
    """One search result.

    Attributes:
        title: The page title.
        url: The page address.
        snippet: A short excerpt.
    """

    title: str
    url: str
    snippet: str


class SearchBackend(Protocol):
    """Something that answers a search query with text for the model."""

    def search(self, query: str) -> str:
        """Return the results for ``query`` as text.

        Raises:
            WebError: If the search could not be done.
        """


class PageSource(Protocol):
    """Something that returns the text of a web page."""

    def read(self, url: str) -> str:
        """Return the text of the page at ``url``.

        Raises:
            WebError: If the page cannot be read.
        """


class DuckDuckGoBackend:
    """Searches DuckDuckGo, which needs no account or key."""

    def __init__(
        self, settings: WebSettings, search: Callable[[str, int], list[SearchHit]] | None = None
    ) -> None:
        """Create the backend.

        Args:
            settings: Web settings.
            search: Returns hits for a query and a limit. Replaced in tests; the default
                uses the ``ddgs`` package.
        """
        self._settings = settings
        self._search = search or self._ddgs_search

    def search(self, query: str) -> str:
        """Return numbered results (title, link, snippet) for ``query``.

        Raises:
            WebError: If the search failed or found nothing.
        """
        try:
            hits = self._search(query, self._settings.max_results)
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.warning("web search failed: %s", error)
            raise WebError(f"The search failed: {error}") from error
        if not hits:
            raise WebError("No results found. Try again with fewer, simpler words.")
        return "\n\n".join(
            f"{number}. {hit.title}\n   {hit.url}\n   {hit.snippet}"
            for number, hit in enumerate(hits, start=1)
        )

    def _ddgs_search(self, query: str, limit: int) -> list[SearchHit]:
        """Ask DuckDuckGo through the ``ddgs`` package, imported on first use."""
        from ddgs import DDGS  # pylint: disable=import-outside-toplevel

        try:
            found = DDGS(timeout=int(self._settings.timeout_seconds)).text(query, max_results=limit)
        except Exception as error:  # pylint: disable=broad-exception-caught
            # ddgs reports "nothing found" as an exception; that is an answer, not a failure.
            if "no results" in str(error).lower():
                return []
            raise
        return [
            SearchHit(item.get("title", ""), item.get("href", ""), item.get("body", ""))
            for item in found
        ]


class GeminiBackend:
    """Searches Google through the Gemini API's Google Search grounding.

    The API key is read from the environment each time, never stored, and is sent in a
    header so it does not end up in a URL or a log.
    """

    def __init__(
        self,
        settings: WebSettings,
        prompts: Prompts,
        client: httpx.Client | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        """Create the backend.

        Args:
            settings: Web settings.
            prompts: Where the instruction sent to Gemini comes from.
            client: HTTP client. Replaced in tests.
            environment: Where the key is looked up. Defaults to ``os.environ``.
        """
        self._settings = settings
        self._prompts = prompts
        self._client = client or httpx.Client(timeout=settings.timeout_seconds)
        self._environment = os.environ if environment is None else environment

    def search(self, query: str) -> str:
        """Return Gemini's short grounded answer to ``query``, with its sources.

        Raises:
            WebError: If there is no key, the API refused, or it gave no answer.
        """
        key = self._environment.get(self._settings.gemini_key_variable, "").strip()
        if not key:
            raise WebError(
                f"Google search needs the {self._settings.gemini_key_variable} environment "
                "variable to hold a Gemini API key."
            )
        url = self._settings.gemini_url.format(model=self._settings.gemini_model)
        body = {
            "contents": [
                {"parts": [{"text": self._prompts.gemini_search_prompt.format(query=query)}]}
            ],
            "tools": [{"google_search": {}}],
        }
        try:
            response = self._client.post(url, json=body, headers={"x-goog-api-key": key})
        except httpx.HTTPError as error:
            raise WebError(f"Could not reach Google: {error.__class__.__name__}") from error
        if response.status_code != 200:
            raise WebError(f"Google search failed (status {response.status_code}).")
        return self._format(response)

    @staticmethod
    def _format(response: httpx.Response) -> str:
        """Turn the API's answer into the answer text followed by its sources."""
        try:
            candidate = response.json()["candidates"][0]
            parts = candidate["content"]["parts"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise WebError("Google search gave no answer.") from None
        answer = "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
        if not answer:
            raise WebError("Google search gave no answer.")
        chunks = candidate.get("groundingMetadata", {}).get("groundingChunks", [])
        sources = [
            f"- {web.get('title', '')} {web.get('uri', '')}".strip()
            for chunk in chunks
            if isinstance(web := chunk.get("web"), dict)
        ]
        return answer + ("\n\nSources:\n" + "\n".join(sources) if sources else "")


class UrlGuard:
    """Decides whether a page address is safe to fetch.

    Only ``http`` and ``https`` addresses whose host resolves to public internet addresses
    pass. That keeps the agent away from this computer (including Tzara's own server),
    the local network and cloud metadata addresses. A host name could in principle change
    its answer between this check and the fetch; the tools can only read, which limits
    what that could expose.
    """

    SCHEMES: ClassVar[tuple[str, ...]] = ("http", "https")

    def __init__(self, resolver: Callable[[str], list[str]] | None = None) -> None:
        """Create the guard.

        Args:
            resolver: Returns the IP addresses of a host name. Replaced in tests.
        """
        self._resolver = resolver or self._resolve

    def check(self, url: str) -> None:
        """Accept ``url`` or explain why not.

        Raises:
            WebError: If the address is not a public web address.
        """
        parsed = urlparse(url)
        if parsed.scheme not in self.SCHEMES or not parsed.hostname:
            raise WebError("Only full http:// or https:// web addresses can be read.")
        try:
            addresses = self._resolver(parsed.hostname)
        except OSError:
            raise WebError(f"Could not find the site {parsed.hostname}.") from None
        if not addresses or not all(self._is_public(address) for address in addresses):
            raise WebError("That address is not on the public internet, so it is not read.")

    @staticmethod
    def _is_public(address: str) -> bool:
        """Whether ``address`` is a normal public internet address."""
        try:
            return ipaddress.ip_address(address.split("%")[0]).is_global
        except ValueError:
            return False

    @staticmethod
    def _resolve(host: str) -> list[str]:
        """Look up the addresses of ``host``."""
        return [str(item[4][0]) for item in socket.getaddrinfo(host, None)]


class PageText(HTMLParser):
    """Collects the readable text of an HTML page.

    Scripts, styles and similar elements are dropped, and block elements start new lines.
    """

    SKIPPED: ClassVar[frozenset[str]] = frozenset(
        {
            "script",
            "style",
            "noscript",
            "svg",
            "template",
            "iframe",
            "head",
            "nav",
            "footer",
            "aside",
            "form",
            "button",
            "select",
            "dialog",
        }
    )
    MAIN: ClassVar[frozenset[str]] = frozenset({"main", "article"})
    MIN_MAIN_CHARS: ClassVar[int] = 200
    # Menus, language lists and tag clouds are long runs of very short lines; prose is not.
    SHORT_LINE_WORDS: ClassVar[int] = 4
    LIST_RUN_LINES: ClassVar[int] = 8
    BLOCKS: ClassVar[frozenset[str]] = frozenset(
        {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}
    )

    def __init__(self) -> None:
        """Start with no text."""
        super().__init__(convert_charrefs=True)
        self._pieces: list[str] = []
        self._main_pieces: list[str] = []
        self._in_main = 0
        self._skipping = 0
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Skip the contents of dropped elements; break lines at block elements."""
        if tag == "title":
            self._in_title = True
        if tag in self.MAIN:
            self._in_main += 1
        if tag in self.SKIPPED:
            self._skipping += 1
        elif tag in self.BLOCKS:
            self._add("\n")

    def handle_endtag(self, tag: str) -> None:
        """Leave a dropped element."""
        if tag == "title":
            self._in_title = False
        if tag in self.MAIN and self._in_main:
            self._in_main -= 1
        if tag in self.SKIPPED and self._skipping:
            self._skipping -= 1

    def handle_data(self, data: str) -> None:
        """Keep text outside dropped elements."""
        if self._in_title:
            self.title += data
        elif not self._skipping:
            # Line breaks inside the source text are only wrapping, not paragraphs.
            self._add(re.sub(r"\s+", " ", data))

    def _add(self, piece: str) -> None:
        """Keep ``piece``, and note it separately when it is inside ``main`` or ``article``."""
        self._pieces.append(piece)
        if self._in_main:
            self._main_pieces.append(piece)

    def text(self) -> str:
        """Return the page text with whitespace tidied.

        The ``main``/``article`` part is preferred when it holds real content, which leaves out
        menus and sidebars that the page puts around it.
        """
        main = self._tidy(self._main_pieces)
        return main if len(main) >= self.MIN_MAIN_CHARS else self._tidy(self._pieces)

    @classmethod
    def _tidy(cls, pieces: list[str]) -> str:
        """Join ``pieces`` into lines, collapse whitespace and drop empty lines and link lists."""
        lines = [" ".join(line.split()) for line in "".join(pieces).splitlines()]
        lines = [line for line in lines if line]
        kept: list[str] = []
        run: list[str] = []
        for line in [*lines, ""]:
            if line and len(line.split()) < cls.SHORT_LINE_WORDS:
                run.append(line)
                continue
            if len(run) < cls.LIST_RUN_LINES:
                kept.extend(run)
            run = []
            if line:
                kept.append(line)
        return "\n".join(kept)


class PageReader:
    """Fetches a page and returns its text."""

    TEXT_TYPES: ClassVar[tuple[str, ...]] = ("text/html", "application/xhtml", "text/plain")
    REDIRECTS: ClassVar[frozenset[int]] = frozenset({301, 302, 303, 307, 308})

    def __init__(
        self,
        settings: WebSettings,
        prompts: Prompts,
        guard: UrlGuard | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        """Create the reader.

        Args:
            settings: Web settings.
            prompts: Where the untrusted-text notice comes from.
            guard: Decides which addresses may be fetched.
            client: HTTP client, which must not follow redirects itself. Replaced in tests.
        """
        self._settings = settings
        self._prompts = prompts
        self._guard = guard or UrlGuard()
        self._client = client or httpx.Client(
            timeout=settings.timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": settings.user_agent},
        )

    def read(self, url: str) -> str:
        """Return the text of the page at ``url``, shortened to the configured size.

        Raises:
            WebError: If the address is not allowed, the page cannot be fetched, or it is
                not a text page.
        """
        body, content_type, encoding = self._fetch(url.strip())
        if "html" in content_type:
            extractor = PageText()
            extractor.feed(body.decode(encoding, errors="replace"))
            title, text = extractor.title.strip(), extractor.text()
        else:
            title, text = "", body.decode(encoding, errors="replace").strip()
        if not text:
            raise WebError("The page has no readable text.")
        limit = self._settings.max_page_chars
        if len(text) > limit:
            text = text[:limit].rstrip() + "\n[shortened]"
        heading = f"{title}\n" if title else ""
        return f"{self._prompts.untrusted_page_notice}\n{heading}{text}"

    def _fetch(self, url: str) -> tuple[bytes, str, str]:
        """Download ``url``, checking every redirect, and return its bytes, type and encoding."""
        for _ in range(self._settings.max_redirects + 1):
            self._guard.check(url)
            try:
                with self._client.stream("GET", url) as response:
                    if response.status_code in self.REDIRECTS and "location" in response.headers:
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code != 200:
                        raise WebError(f"The page answered with status {response.status_code}.")
                    content_type = (
                        response.headers.get("content-type", "").split(";")[0].strip().lower()
                    )
                    if not content_type.startswith(self.TEXT_TYPES):
                        raise WebError(
                            f"That is not a text page ({content_type or 'unknown type'})."
                        )
                    return self._read_limited(response), content_type, response.encoding or "utf-8"
            except httpx.HTTPError as error:
                raise WebError(f"Could not load the page: {error.__class__.__name__}") from error
        raise WebError("The page redirected too many times.")

    def _read_limited(self, response: httpx.Response) -> bytes:
        """Read at most ``max_download_bytes`` of the response."""
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= self._settings.max_download_bytes:
                break
        return b"".join(chunks)[: self._settings.max_download_bytes]


class SearchInput(BaseModel):
    """Arguments accepted by the web search tool."""

    query: str = Field(description="What to search for, in a few words.")


class PageInput(BaseModel):
    """Arguments accepted by the page reading tool."""

    url: str = Field(description="Full address of the page, starting with http:// or https://.")


class WebSearch(BaseTool):
    """Tool that searches the web for the agent."""

    name: str = "web_search"
    description: str = "Search the web."
    args_schema: type[BaseModel] = SearchInput

    def __init__(self, backend: SearchBackend, description: str) -> None:
        """Create the tool.

        Args:
            backend: Does the searching.
            description: What the model is told the tool does.
        """
        super().__init__(description=description)
        self._backend = backend

    @property
    def backend(self) -> SearchBackend:
        """What does the searching."""
        return self._backend

    def _run(self, query: str) -> str:  # pylint: disable=arguments-differ
        """Search for ``query``; return results, or ``Error: ...`` for the model to read."""
        try:
            return self._backend.search(query)
        except WebError as error:
            return f"Error: {error}"


class ReadWebpage(BaseTool):
    """Tool that reads one web page for the agent."""

    name: str = "read_webpage"
    description: str = "Read a web page."
    args_schema: type[BaseModel] = PageInput

    def __init__(self, reader: PageSource, description: str) -> None:
        """Create the tool.

        Args:
            reader: Fetches the page.
            description: What the model is told the tool does.
        """
        super().__init__(description=description)
        self._reader = reader

    @property
    def reader(self) -> PageSource:
        """What fetches the page."""
        return self._reader

    def _run(self, url: str) -> str:  # pylint: disable=arguments-differ
        """Read ``url``; return its text, or ``Error: ...`` for the model to read."""
        try:
            return self._reader.read(url)
        except WebError as error:
            return f"Error: {error}"


class WebTools:
    """Builds the web tools a configuration asks for."""

    @staticmethod
    def create(config: TzaraConfig, **overrides: Any) -> list[BaseTool]:
        """Return the web tools, or none when web access is off.

        Args:
            config: The configuration.
            **overrides: ``backend`` and ``reader`` to use instead of the real ones (tests).
        """
        settings = config.web
        if not settings.enabled:
            return []
        backend = overrides.get("backend") or (
            GeminiBackend(settings, config.prompts)
            if settings.provider == "gemini"
            else DuckDuckGoBackend(settings)
        )
        reader = overrides.get("reader") or PageReader(settings, config.prompts)
        return [
            WebSearch(backend, config.prompts.web_search_tool),
            ReadWebpage(reader, config.prompts.read_webpage_tool),
        ]
