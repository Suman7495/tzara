"""Web tools: search the internet and read a page.

The model gets two read-only tools: ``research`` (see :mod:`tzara.tools.research`), which
searches DuckDuckGo and reads the best pages, and ``read_webpage``, which returns the text of
one page. The model then writes the answer.

Everything fetched is untrusted: it is clearly labelled as data for the model, and
the tools can only read. A page address is checked before and after every redirect so
the model, or text in a page, cannot make the agent fetch something on this computer
or its network.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from tzara.config import TzaraConfig, WebSettings
from tzara.prompts import Prompts

logger = logging.getLogger(__name__)


class WebError(Exception):
    """A web lookup failed. The message says why, in words the model can act on."""


@dataclass(frozen=True)
class UrlCleaner:
    """Shortens a web address by taking out the parts that only track the visitor.

    Search results often carry ``utm_source`` or ``srsltid`` parameters. They make an address
    several times longer, which costs a small model time to write out, and tell the site who
    sent the visitor. The page is the same without them.
    """

    PREFIXES: ClassVar[tuple[str, ...]] = ("utm_", "pk_", "mtm_")
    NAMES: ClassVar[frozenset[str]] = frozenset(
        {
            "srsltid",
            "gclid",
            "gbraid",
            "wbraid",
            "fbclid",
            "msclkid",
            "mc_cid",
            "mc_eid",
            "igshid",
            "yclid",
            "dclid",
            "_ga",
            "ref_src",
        }
    )

    @classmethod
    def clean(cls, url: str) -> str:
        """``url`` without tracking parameters or a fragment."""
        parts = urlsplit(url)
        kept = [
            (name, value)
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
            if name.lower() not in cls.NAMES and not name.lower().startswith(cls.PREFIXES)
        ]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


class TtlCache:
    """Remembers answers for a while, so asking again costs nothing.

    Thread-safe, because searches and page reads run at the same time. A time of zero turns it off.
    """

    LIMIT: ClassVar[int] = 128

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        """Create the cache.

        Args:
            seconds: How long an entry is kept.
            clock: Returns the time in seconds. Replaced in tests.
        """
        self._seconds = seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        """The remembered value for ``key``, or ``None`` if there is none or it is too old."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or self._clock() - entry[0] > self._seconds:
                self._entries.pop(key, None)
                return None
            return entry[1]

    def put(self, key: str, value: Any) -> None:
        """Remember ``value`` for ``key``, forgetting the oldest entry if the cache is full."""
        if self._seconds <= 0:
            return
        with self._lock:
            if len(self._entries) >= self.LIMIT:
                del self._entries[min(self._entries, key=lambda name: self._entries[name][0])]
            self._entries[key] = (self._clock(), value)


class Sanitizer:
    """Removes text a person cannot see from what the web returned.

    Zero-width characters, direction overrides and the "tag" block can hide an instruction inside
    a page or a snippet: the model reads them, the user never sees them.
    """

    HIDDEN: ClassVar[frozenset[str]] = frozenset({"Cf", "Co", "Cs", "Cn"})

    @classmethod
    def clean(cls, text: str) -> str:
        """``text`` without invisible or control characters (line breaks and tabs stay)."""
        return "".join(
            char
            for char in text
            if char in "\n\t" or unicodedata.category(char) not in cls.HIDDEN | {"Cc"}
        )


@dataclass(frozen=True)
class PageContent:
    """The readable part of a web page.

    Attributes:
        title: The page title, or empty.
        text: The page text, one paragraph per line.
        code: The code blocks on the page, with their line breaks and indentation as written.
    """

    title: str
    text: str
    code: tuple[str, ...] = ()


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
    """Something that answers a search query with results."""

    def hits(self, query: str) -> list[SearchHit]:
        """Return the results for ``query``.

        Raises:
            WebError: If the search could not be done.
        """


class PageSource(Protocol):
    """Something that returns the text of a web page."""

    def fetch(self, url: str) -> PageContent:
        """Return the title and full readable text of the page at ``url``.

        Raises:
            WebError: If the page cannot be read.
        """

    def read(self, url: str) -> str:
        """Return the text of the page at ``url``.

        Raises:
            WebError: If the page cannot be read.
        """


class DuckDuckGoBackend:
    """Searches DuckDuckGo, which needs no account or key."""

    def __init__(
        self,
        settings: WebSettings,
        search: Callable[[str, int], list[SearchHit]] | None = None,
    ) -> None:
        """Create the backend.

        Args:
            settings: Web settings.
            search: Returns hits for a query and a limit. Replaced in tests; the
                default uses the ``ddgs`` package.
        """
        self._settings = settings
        self._search = search or self._ddgs_search
        self._cache = TtlCache(settings.cache_seconds)

    def hits(self, query: str) -> list[SearchHit]:
        """Return the results for ``query``, leaving out adverts.

        Raises:
            WebError: If the search failed or found nothing.
        """
        remembered = self._cache.get(query)
        if remembered is not None:
            return list(remembered)
        attempt = 0
        while True:
            attempt += 1
            try:
                found = [
                    SearchHit(Sanitizer.clean(hit.title), hit.url, Sanitizer.clean(hit.snippet))
                    for hit in self._search(query, self._settings.max_results)
                    if not self.is_advert(hit.url)
                ]
                break
            except Exception as error:
                logger.warning("web search failed (attempt %d): %s", attempt, error)
                if attempt >= self._settings.search_attempts:
                    raise WebError(f"The search failed: {error}") from error
        if not found:
            raise WebError("No results found. Try again with fewer, simpler words.")
        self._cache.put(query, tuple(found))
        return found

    @staticmethod
    def is_advert(url: str) -> bool:
        """Whether a result is a paid advert's tracking redirect, not a page a person would want."""
        parts = urlparse(url)
        host = (parts.hostname or "").lower()
        return (host.endswith("bing.com") and parts.path.startswith("/aclick")) or (
            host.endswith("duckduckgo.com") and parts.path.startswith("/y.js")
        )

    def _ddgs_search(self, query: str, limit: int) -> list[SearchHit]:
        """Ask DuckDuckGo through the ``ddgs`` package, imported on first use."""
        from ddgs import DDGS

        try:
            found = DDGS(timeout=int(self._settings.search_timeout_seconds)).text(
                query,
                region=self._settings.region,
                max_results=limit,
                backend=self._settings.engines,
            )
        except Exception as error:
            # ddgs reports "nothing found" as an exception; that is an answer, not a failure.
            if "no results" in str(error).lower():
                return []
            raise
        return [
            SearchHit(item.get("title", ""), item.get("href", ""), item.get("body", ""))
            for item in found
        ]


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


class PageExtractor:
    """Turns the HTML of a page into its main text, without menus, adverts and comments.

    Done by ``trafilatura``, which is built for this: it finds the article or documentation body,
    keeps headings, lists and tables, and gives code as fenced blocks. Writing that by hand
    kept menus and lost pages built from many small elements.
    """

    ESCAPED: ClassVar[re.Pattern[str]] = re.compile(r"\\([\\`*_{}\[\]()#+.!|<>~-])")
    FENCE: ClassVar[re.Pattern[str]] = re.compile(
        r"^```[^\n]*\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE
    )

    @classmethod
    def extract(cls, html: str, max_chars: int = 1_000_000) -> PageContent:
        """The title, the text (prose only) and the code blocks of ``html``.

        Args:
            html: The page.
            max_chars: Only this much of it is read.

        Raises:
            WebError: If the page has no readable text.
        """
        import trafilatura

        html = html[:max_chars]

        markdown = trafilatura.extract(
            html,
            output_format="markdown",
            include_formatting=True,
            include_tables=True,
            include_comments=False,
            favor_precision=True,
        )
        if not markdown:
            markdown = trafilatura.extract(html, output_format="markdown", favor_recall=True)
        if not markdown or not markdown.strip():
            raise WebError("The page has no readable text.")
        code = [block.strip("\n") for block in cls.FENCE.findall(markdown) if block.strip()]
        markdown = Sanitizer.clean(markdown)
        prose = cls.ESCAPED.sub(r"\1", cls.FENCE.sub("", markdown))
        text = "\n".join(line for line in prose.splitlines() if line.strip())
        metadata = trafilatura.extract_metadata(html)
        title = Sanitizer.clean((metadata.title or "") if metadata else "")
        return PageContent(title.strip(), text, tuple(code))


class StackExchange:
    """Reads a Stack Overflow (or sister site) question through the sites' public API.

    Their pages answer a program with 403, but the API is open to programs, so the answers are
    asked for there, best first. That is the source most how-to questions are really answered by.
    """

    API: ClassVar[str] = "https://api.stackexchange.com/2.3/questions/{id}/answers"
    SITES: ClassVar[dict[str, str]] = {
        "stackoverflow.com": "stackoverflow",
        "superuser.com": "superuser",
        "serverfault.com": "serverfault",
        "askubuntu.com": "askubuntu",
    }
    QUESTION: ClassVar[re.Pattern[str]] = re.compile(r"^/(?:questions|q)/(\d+)")
    ANSWERS: ClassVar[int] = 3

    @classmethod
    def site(cls, url: str) -> tuple[str, str] | None:
        """The API site name and question number of ``url``, or ``None`` if it is not a question."""
        parts = urlparse(url)
        host = (parts.hostname or "").lower().removeprefix("www.")
        name = cls.SITES.get(host) or (
            host.removesuffix(".stackexchange.com") if host.endswith(".stackexchange.com") else None
        )
        found = cls.QUESTION.match(parts.path)
        return (name, found.group(1)) if name and found else None

    @classmethod
    def content(cls, client: httpx.Client, site: str, question: str) -> PageContent:
        """The best answers to a question, as text with their code.

        Raises:
            WebError: If the API does not answer or the question has no answers.
        """
        try:
            response = client.get(
                cls.API.format(id=question),
                params={
                    "site": site,
                    "order": "desc",
                    "sort": "votes",
                    "filter": "withbody",
                    "pagesize": cls.ANSWERS,
                },
            )
            response.raise_for_status()
            items = response.json().get("items", [])
        except (httpx.HTTPError, ValueError) as error:
            raise WebError(f"Could not load the answers: {error.__class__.__name__}") from error
        items.sort(key=lambda item: not item.get("is_accepted"))
        html = "".join(f"<article>{item.get('body', '')}</article>" for item in items)
        return PageExtractor.extract(html)


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
        self._cache = TtlCache(settings.cache_seconds)

    def read(self, url: str) -> str:
        """Return the text of the page at ``url``, shortened to the configured size.

        Raises:
            WebError: If the address is not allowed, the page cannot be fetched, or it is
                not a text page.
        """
        page = self.fetch(url)
        text = page.text
        limit = self._settings.max_page_chars
        if len(text) > limit:
            text = text[:limit].rstrip() + "\n[shortened]"
        heading = f"{page.title}\n" if page.title else ""
        return f"{self._prompts.untrusted_page_notice}\n{heading}{text}"

    def fetch(self, url: str) -> PageContent:
        """Return the title and the whole readable text of the page at ``url``.

        Raises:
            WebError: If the address is not allowed, the page cannot be fetched, or it is
                not a text page.
        """
        url = url.strip()
        remembered = self._cache.get(url)
        if remembered is not None:
            return remembered
        question = StackExchange.site(url)
        page = StackExchange.content(self._client, *question) if question else self._page(url)
        self._cache.put(url, page)
        return page

    def _page(self, url: str) -> PageContent:
        """Download and read the page at ``url``."""
        body, content_type, encoding = self._fetch(url)
        text = body.decode(encoding, errors="replace")
        if "html" in content_type:
            return PageExtractor.extract(text, self._settings.max_html_chars)
        text = Sanitizer.clean(text).strip()
        if not text:
            raise WebError("The page has no readable text.")
        return PageContent("", text)

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


class PageInput(BaseModel):
    """Arguments accepted by the page reading tool."""

    url: str = Field(description="Full address of the page, starting with http:// or https://.")


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

    def _run(self, url: str) -> str:
        """Read ``url``; return its text, or ``Error: ...`` for the model to read."""
        try:
            return self._reader.read(url)
        except WebError as error:
            return f"Error: {error}"


class WebTools:
    """Builds the web tools a configuration asks for."""

    @staticmethod
    def create(
        config: TzaraConfig,
        *,
        report: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        **overrides: Any,
    ) -> list[BaseTool]:
        """Return the web tools, or none when web access is off.

        Args:
            config: The configuration.
            report: Told what the ``research`` tool is doing, step by step.
            cancelled: Returns True once the user has stopped the reply.
            **overrides: ``backend`` and ``reader`` to use instead of the real ones (tests).
        """
        # research.py builds on this module's types, so it is imported here to avoid a cycle.
        from tzara.tools.research import Research, Researcher

        settings = config.web
        if not settings.enabled:
            return []
        backend = overrides.get("backend") or DuckDuckGoBackend(settings)
        reader = overrides.get("reader") or PageReader(settings, config.prompts)
        researcher = Researcher(config.research, backend, reader, config.prompts, report, cancelled)
        return [
            ReadWebpage(reader, config.prompts.read_webpage_tool),
            Research(researcher, config.prompts.research_tool),
        ]
