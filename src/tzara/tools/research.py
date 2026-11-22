"""The ``research`` tool: search the web, read the best pages, hand the model the evidence.

Search snippets alone answer "who won on Sunday" but not "how do I enable CORS in FastAPI".
The steps are fixed here so that no extra model request is needed: each one would make Ollama
read the whole conversation again, which costs seconds. The model only reads the evidence and
writes the answer.

1. Search the question, and its key words, at the same time.
2. Rank the pages by how high and how often the searches returned them, and by how much their
   site can be trusted. Leave out login walls and video sites; keep one site from taking over.
3. Read the best pages together and keep the passages (and code) that match the question.
4. Return that, with ready-made source links.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol
from urllib.parse import urlparse

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from tzara.config import ResearchSettings
from tzara.prompts import Prompts
from tzara.stopwords import StopWords
from tzara.tools.sources import SourceAuthority
from tzara.tools.web import PageContent, SearchBackend, SearchHit, UrlCleaner, WebError

logger = logging.getLogger(__name__)


class PageFetcher(Protocol):
    """Something that returns the text of a web page."""

    def fetch(self, url: str) -> PageContent:
        """Return the page at ``url``.

        Raises:
            WebError: If it cannot be read.
        """


class SubjectFinder:
    """Finds web addresses written in a question, such as ``telekinesis.ai``."""

    ADDRESS: ClassVar[re.Pattern[str]] = re.compile(
        r"(?<![\w@.-])((?:https?://)?(?:[a-z0-9][a-z0-9-]*\.)+[a-z]{2,}(?:/[^\s,;)\"']*)?)",
        re.IGNORECASE,
    )

    @classmethod
    def addresses(cls, question: str, limit: int = 2) -> list[str]:
        """Return up to ``limit`` distinct addresses in ``question``, each as a full URL."""
        found: list[str] = []
        for match in cls.ADDRESS.findall(question):
            address = match.rstrip(".")
            url = address if address.lower().startswith("http") else f"https://{address}"
            if url not in found:
                found.append(url)
        return found[:limit]


class PassagePicker:
    """Keeps the lines of a page that matter for a question.

    A page may be long, with the answer in the middle. Cutting it at the start would keep the
    menu and drop the answer, so lines are scored by how many of the question's words they
    contain, and the best are kept, in their original order.
    """

    STOPWORDS: ClassVar[frozenset[str]] = StopWords.COMMON
    WORD: ClassVar[re.Pattern[str]] = re.compile(r"[a-z0-9][a-z0-9+#.-]{2,}")
    MAX_LINE_CHARS: ClassVar[int] = 400

    @classmethod
    def keywords(cls, *texts: str) -> frozenset[str]:
        """Return the words worth matching in ``texts``."""
        words = {word.strip(".-") for text in texts for word in cls.WORD.findall(text.lower())}
        return frozenset(word for word in words if word and word not in cls.STOPWORDS)

    @classmethod
    def pick(cls, text: str, keywords: frozenset[str], limit: int) -> str:
        """Return at most about ``limit`` characters of ``text``, the best-matching lines."""
        if len(text) <= limit:
            return text
        lines = [line[: cls.MAX_LINE_CHARS] for line in text.splitlines() if line.strip()]
        ranked = sorted(
            range(len(lines)),
            key=lambda index: (-sum(word in lines[index].lower() for word in keywords), index),
        )
        chosen = {0}
        used = len(lines[0])
        for index in ranked:
            if index in chosen:
                continue
            if used + len(lines[index]) + 1 > limit:
                continue
            chosen.add(index)
            used += len(lines[index]) + 1
        kept = [lines[index] for index in sorted(chosen)]
        # A heading with nothing under it only takes room.
        return "\n".join(
            line
            for number, line in enumerate(kept)
            if not line.startswith("#")
            or (number + 1 < len(kept) and not kept[number + 1].startswith("#"))
        )


@dataclass(frozen=True)
class _Candidate:
    """A page that a search found, and how well it did."""

    title: str
    url: str
    host: str
    snippet: str
    score: float


@dataclass(frozen=True)
class _Reading:
    """What reading one page gave: the passages that matter, and its code."""

    text: str
    code: tuple[str, ...] = ()


class Researcher:
    """Runs the research steps for one question and returns the evidence as text."""

    def __init__(
        self,
        settings: ResearchSettings,
        backend: SearchBackend,
        reader: PageFetcher,
        prompts: Prompts,
        report: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        extras: list[Any] | None = None,
    ) -> None:
        """Create the researcher.

        Args:
            settings: Limits.
            backend: Runs the searches.
            reader: Fetches pages.
            prompts: Where the evidence's notice and the progress texts come from.
            report: Told what is happening, so the user is not left looking at a spinner.
            cancelled: Returns True once the user has stopped the reply.
            extras: More sources searched in the same round, such as Wikipedia: each has
                ``applies(question)`` and ``hits(query)``. Their results rank below the general
                search's, but fill in when that finds little.
        """
        self._settings = settings
        self._extras = extras or []
        self._backend = backend
        self._reader = reader
        self._prompts = prompts
        self._report = report or (lambda _text: None)
        self._cancelled = cancelled or (lambda: False)
        self._authority = SourceAuthority(settings)

    def research(self, question: str) -> str:
        """Search for ``question``, read the best pages and return the evidence.

        Raises:
            WebError: If nothing could be found or read, or the reply was cancelled.
        """
        started = time.monotonic()
        question = question.strip()
        keywords = PassagePicker.keywords(question)
        queries = self._queries(question, keywords)
        main, extra = self._search(queries, question)
        candidates = self._rank(main, SubjectFinder.addresses(question), extra)
        if not candidates:
            raise WebError("The searches found no pages to read. Try different words.")

        searched = time.monotonic()
        self._check()
        reading = candidates[: self._settings.pages]
        self._report(self._prompts.research_reading_pages.format(count=len(reading)))
        read = self._read(reading, keywords)
        evidence = self._compose(question, candidates, read)
        logger.info(
            "research: searched %.1fs, read %d of %d pages %.1fs, %d characters",
            searched - started,
            len(read),
            len(reading),
            time.monotonic() - searched,
            len(evidence),
        )
        return evidence

    def failed(self, error: WebError) -> str:
        """What the model reads when research failed: the reason, and what to tell the user."""
        return self._prompts.research_failed.format(error=error)

    def _queries(self, question: str, keywords: frozenset[str]) -> list[str]:
        """The question, and a second search of its key words, which finds different pages."""
        queries = [question[: self._settings.max_query_chars]]
        words = [
            word for word in re.findall(r"\S+", question.lower()) if word.strip(".,?!") in keywords
        ]
        short = " ".join(word.strip(".,?!") for word in words[: self._settings.keyword_words])
        if short and short.lower() != queries[0].lower():
            queries.append(short)
        lowered = f" {question.lower()} "
        if any(word in lowered for word in self._settings.authority_words):
            # Popular pages on such questions are often shops and blogs: also look where the
            # answer is trusted, in the same round so it costs no time.
            sites = " OR ".join(f"site:{site}" for site in self._settings.authority_sites)
            queries.append(f"{queries[0]} ({sites})")
        return queries

    def _search(
        self, queries: list[str], question: str
    ) -> tuple[list[list[SearchHit]], list[list[SearchHit]]]:
        """Run every query, and the extra sources, at the same time; a failure gives no results.

        Returns:
            The results of the general search's queries, and those of the extra sources.
        """
        self._check()
        self._report(self._prompts.research_searching.format(queries=" · ".join(queries[:2])))
        # The key words suit an API's own search better than a whole sentence does.
        short_query = next((q for q in queries[1:] if "site:" not in q), queries[0])
        sources = [(self._backend, query) for query in queries] + [
            (extra, short_query) for extra in self._extras if extra.applies(question)
        ]

        def one(source: tuple[Any, str]) -> list[SearchHit]:
            backend, query = source
            try:
                return list(backend.hits(query))
            except WebError as error:
                logger.info("search %r found nothing: %s", query, error)
                return []

        results = [found or [] for found in self._gather(one, sources, good=bool)]
        return results[: len(queries)], results[len(queries) :]

    def _gather(
        self, work: Callable[[Any], Any], items: list[Any], good: Callable[[Any], bool]
    ) -> list[Any]:
        """Run ``work`` on every item at once; stop waiting shortly after the first good result.

        The slowest search or page decides how long everything takes, and the slow one is often
        a site that will time out anyway. Once something good is in, the rest get
        ``grace_seconds`` more. Work not finished by then is left out (``None``).
        """
        pool = ThreadPoolExecutor(max_workers=max(1, len(items)))
        futures = [pool.submit(work, item) for item in items]
        pending = set(futures)
        deadline: float | None = None
        while pending:
            timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
            done, pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            if not done:
                break
            if deadline is None and any(good(future.result()) for future in done):
                deadline = time.monotonic() + self._settings.grace_seconds
        pool.shutdown(wait=False, cancel_futures=True)
        return [future.result() if future.done() else None for future in futures]

    def _rank(
        self,
        results: list[list[SearchHit]],
        addresses: list[str],
        extra: list[list[SearchHit]] | None = None,
    ) -> list[_Candidate]:
        """Order pages by how many searches found them and how high, best first.

        A page several searches agree on is likelier to be right. Official, reference and
        documentation sites get a bonus, an address the user wrote goes first, and sites that
        need a login are left out. One site cannot take every place. Hits of the extra sources
        (Wikipedia, GitHub) are scored by position only, so they fill in when the general search
        finds little but never push its best pages out.
        """
        scores: dict[str, float] = defaultdict(float)
        first: dict[str, SearchHit] = {}
        for url in addresses:
            key = self._key(url)
            if key:
                scores[key] += self._settings.address_bonus
                first.setdefault(key, SearchHit("", url, ""))
        for hits, trusted in [(hits, True) for hits in results] + [
            (hits, False) for hits in extra or []
        ]:
            for position, hit in enumerate(hits):
                key = self._key(hit.url)
                if not key or self._skipped(self._host(hit.url)):
                    continue
                scores[key] += len(hits) - position
                if key not in first or not first[key].title:
                    if trusted:
                        scores[key] += self._settings.authority_bonus * self._authority.level(
                            hit.url
                        )
                    first[key] = hit

        chosen: list[_Candidate] = []
        per_host: dict[str, int] = defaultdict(int)
        for key in sorted(scores, key=lambda item: -scores[item]):
            hit = first[key]
            host = self._host(hit.url)
            if per_host[host] >= self._settings.max_per_host:
                continue
            per_host[host] += 1
            chosen.append(_Candidate(hit.title, hit.url, host, hit.snippet, scores[key]))
            if len(chosen) == self._settings.listed:
                break
        return chosen

    def _read(self, candidates: list[_Candidate], keywords: frozenset[str]) -> dict[str, _Reading]:
        """Read the pages together; a page that cannot be opened is simply left out."""
        limit = min(
            self._settings.page_chars, self._settings.total_chars // max(len(candidates), 1)
        )

        def one(candidate: _Candidate) -> tuple[str, _Reading] | None:
            try:
                page = self._reader.fetch(candidate.url)
            except WebError as error:
                logger.info("could not read %s: %s", candidate.host, error)
                return None
            return candidate.url, _Reading(
                PassagePicker.pick(page.text, keywords, limit), page.code
            )

        results = self._gather(one, candidates, good=lambda result: result is not None)
        return dict(result for result in results if result is not None)

    def _compose(
        self,
        question: str,
        candidates: list[_Candidate],
        read: dict[str, _Reading],
    ) -> str:
        """Lay the evidence out for the model, saying plainly which pages were opened."""
        parts = [
            self._prompts.untrusted_page_notice,
            f"Question: {question}",
        ]
        code = self._code_section(question, candidates, read)
        if code:
            parts.append(code)
        parts.append(self._prompts.research_results_heading)
        for number, candidate in enumerate(candidates, start=1):
            parts.append(self._entry(number, candidate, read.get(candidate.url)))
        opened = [candidate for candidate in candidates if candidate.url in read] or candidates
        links = " · ".join(
            f"[{candidate.host}]({UrlCleaner.clean(candidate.url)})"
            for candidate in opened[: self._settings.links]
        )
        parts.append(f"Links you may use (each was returned by the search): {links}")
        return "\n\n".join(parts)

    def _code_section(
        self, question: str, candidates: list[_Candidate], read: dict[str, _Reading]
    ) -> str:
        """The code on the pages that is about the question, to be copied as it is.

        Written from memory, code got an import and a combination of settings wrong; the
        documentation has working code, so that is what the model is given.
        """
        keywords = PassagePicker.keywords(question)
        chosen: list[tuple[int, int, _Candidate, str]] = []
        for rank, candidate in enumerate(candidates):
            for block in read[candidate.url].code if candidate.url in read else ():
                lowered = block.lower()
                score = sum(word in lowered for word in keywords)
                if score and len(block) <= self._settings.code_chars:
                    chosen.append((-score, rank, candidate, block))
        chosen.sort(key=lambda item: item[:2])
        lines: list[str] = []
        seen: set[str] = set()
        for _, _, candidate, block in chosen:
            if block in seen or len(seen) >= self._settings.code_blocks:
                continue
            seen.add(block)
            address = UrlCleaner.clean(candidate.url)
            lines.append(f"From [{candidate.host}]({address}):\n```\n{block}\n```")
        return "\n\n".join([self._prompts.research_code_heading, *lines]) if lines else ""

    def _entry(self, number: int, candidate: _Candidate, reading: _Reading | None) -> str:
        """One result: its address, whether it was opened, and what was found there."""
        title = candidate.title or candidate.host
        if reading is None:
            status = self._prompts.research_not_opened
            detail = f"Snippet: {candidate.snippet}" if candidate.snippet else ""
        else:
            status = self._prompts.research_opened
            detail = reading.text or f"Snippet: {candidate.snippet}"
        address = UrlCleaner.clean(candidate.url)
        return f"[{number}] {title} <{address}> ({status})\n{detail}".rstrip()

    def _check(self) -> None:
        """Stop if the user has cancelled the reply."""
        if self._cancelled():
            raise WebError("The research was cancelled.")

    def _skipped(self, host: str) -> bool:
        """Whether pages of ``host`` should not be read."""
        return any(
            host == skipped or host.endswith("." + skipped) for skipped in self._settings.skip_hosts
        )

    @staticmethod
    def _host(url: str) -> str:
        """The site of ``url`` without ``www.``, in lower case."""
        return (urlparse(url).hostname or "").lower().removeprefix("www.")

    LANGUAGE_PREFIX: ClassVar[re.Pattern[str]] = re.compile(r"^/[a-z]{2}(?:-[a-z]{2})?(?=/)")

    @classmethod
    def _key(cls, url: str) -> str:
        """What makes two addresses the same page: site and path, not query or fragment."""
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return ""
        # /de/tutorial/x and /tutorial/x are one page in two languages: keep the first found.
        path = cls.LANGUAGE_PREFIX.sub("", parts.path).rstrip("/")
        return cls._host(url) + path


class ResearchInput(BaseModel):
    """Arguments accepted by the research tool."""

    question: str = Field(
        description="The question to research, in full, e.g. 'How do I enable CORS in FastAPI?'"
    )


class Research(BaseTool):
    """Tool that searches the web and reads the best pages."""

    name: str = "research"
    description: str = "Search the web and read the best pages."
    args_schema: type[BaseModel] = ResearchInput

    def __init__(self, researcher: Researcher, description: str) -> None:
        """Create the tool.

        Args:
            researcher: Does the work.
            description: What the model is told the tool does.
        """
        super().__init__(description=description)
        self._researcher = researcher

    @property
    def researcher(self) -> Researcher:
        """What does the research."""
        return self._researcher

    def _run(self, question: str) -> str:
        """Research ``question``; return the evidence, or ``Error: ...`` for the model to read."""
        try:
            return self._researcher.research(question)
        except WebError as error:
            return self._researcher.failed(error)
