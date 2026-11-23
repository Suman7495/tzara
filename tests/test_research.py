from typing import ClassVar

import pytest

from tzara.config import ResearchSettings
from tzara.prompts import Prompts
from tzara.tools.research import PassagePicker, Research, Researcher, SubjectFinder
from tzara.tools.web import PageContent, SearchHit, WebError


class FakeBackend:
    def __init__(self, results):
        self.results, self.queries = results, []

    def hits(self, query):
        self.queries.append(query)
        found = self.results.get(query, self.results.get("*", []))
        if isinstance(found, Exception):
            raise found
        return found


class FakeReader:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url):
        page = self.pages.get(url)
        if page is None:
            raise WebError("cannot open")
        return page


def researcher(results, pages, **kwargs):
    backend = FakeBackend(results)
    return Researcher(ResearchSettings(), backend, FakeReader(pages), Prompts(), **kwargs), backend


HITS = [
    SearchHit("Docs", "https://docs.example.org/cors", "How to enable CORS"),
    SearchHit("Blog", "https://blog.example.net/post", "A post"),
    SearchHit("Video", "https://www.youtube.com/watch?v=1", "video"),
]
PAGES = {
    "https://docs.example.org/cors": PageContent(
        "Docs", "Use CORSMiddleware to enable cors", ("app.add_middleware(CORSMiddleware)",)
    ),
}


class TestResearch:
    def test_returns_pages_links_and_marks_unopened_ones(self):
        r, _ = researcher({"*": HITS}, PAGES)
        text = r.research("how to enable cors in fastapi")
        assert "Use CORSMiddleware" in text
        assert "(page read)" in text and "snippet only" in text
        assert "[docs.example.org](https://docs.example.org/cors)" in text

    def test_login_and_video_sites_are_left_out(self):
        r, _ = researcher({"*": HITS}, PAGES)
        assert "youtube.com" not in r.research("how to enable cors")

    def test_code_from_the_pages_is_offered(self):
        r, _ = researcher({"*": HITS}, PAGES)
        assert "app.add_middleware(CORSMiddleware)" in r.research("how to enable cors middleware")

    def test_no_model_request_is_needed_and_two_searches_run(self):
        r, backend = researcher({"*": HITS}, PAGES)
        r.research("How do I enable CORS in FastAPI?")
        assert len(backend.queries) == 2

    def test_an_official_site_ranks_above_a_blog(self):
        hits = [
            SearchHit("Blog", "https://blog.example.net/p", ""),
            SearchHit("Gov", "https://x.gov/p", ""),
        ]
        r, _ = researcher({"*": hits}, {})
        text = r.research("some question here")
        assert text.index("x.gov") < text.index("blog.example.net")

    def test_an_address_in_the_question_is_read_first(self):
        pages = {"https://acme.com": PageContent("Acme", "Acme makes robots")}
        r, _ = researcher({"*": HITS}, pages)
        assert "Acme makes robots" in r.research("what does acme.com do")

    def test_nothing_found_raises(self):
        r, _ = researcher({"*": WebError("none")}, {})
        with pytest.raises(WebError):
            r.research("anything")

    def test_cancelling_stops_it(self):
        r, _ = researcher({"*": HITS}, PAGES, cancelled=lambda: True)
        with pytest.raises(WebError, match="cancelled"):
            r.research("anything")

    def test_progress_is_reported(self):
        said: list[str] = []
        r, _ = researcher({"*": HITS}, PAGES, report=said.append)
        r.research("how to enable cors")
        assert any(text.startswith("Searching") for text in said)
        assert any("pages" in text for text in said)


class TestTool:
    def test_failure_is_text_that_forbids_searching_again(self):
        r, _ = researcher({"*": WebError("none")}, {})
        out = Research(r, "d").invoke({"question": "x"})
        assert "could not" in out and "do not answer from memory" in out.lower()


class TestHelpers:
    def test_addresses(self):
        assert SubjectFinder.addresses("compare acme.com and https://b.io/x.") == [
            "https://acme.com",
            "https://b.io/x",
        ]

    def test_picker_keeps_the_matching_lines(self):
        text = "menu\n" + "\n".join(f"filler {i}" for i in range(50)) + "\nthe answer about cors\n"
        picked = PassagePicker.pick(text, PassagePicker.keywords("cors"), 80)
        assert "the answer about cors" in picked and len(picked) <= 120


class TestRanking:
    def test_the_same_page_in_another_language_takes_no_second_place(self):
        hits = [
            SearchHit("En", "https://docs.example.org/tutorial/cors/", "x"),
            SearchHit("De", "https://docs.example.org/de/tutorial/cors/", "x"),
            SearchHit("Other", "https://other.example.net/cors", "x"),
        ]
        r, _ = researcher({"*": hits}, {})
        text = r.research("how to enable cors")
        assert "/de/" not in text and "other.example.net" in text

    def test_a_health_question_also_searches_trusted_sites_in_the_same_round(self):
        blog = [SearchHit("Blog", "https://blog.example.net/p", "x")]
        trusted = [SearchHit("NHS", "https://www.nhs.uk/medicines/x", "x")]
        r, backend = researcher({"*": blog}, {})
        original = backend.hits

        def hits(query):
            return trusted if "site:nhs.uk" in query else original(query)

        backend.hits = hits
        text = r.research("is ibuprofen safe with coffee")
        assert "nhs.uk" in text and text.index("nhs.uk") < text.index("blog.example.net")

    def test_other_questions_do_not_search_those_sites(self):
        r, backend = researcher({"*": [SearchHit("Docs", "https://docs.example.org/x", "x")]}, {})
        r.research("how to do the thing")
        assert not any("site:" in query for query in backend.queries)


class TestHedging:
    def test_a_slow_search_does_not_hold_up_the_answer(self):
        import threading
        import time

        release = threading.Event()

        class Slow(FakeBackend):
            def hits(self, query):
                if query != "how to enable cors":
                    release.wait(5)
                return super().hits(query)

        backend = Slow({"*": HITS})
        settings = ResearchSettings(grace_seconds=0.05)
        r = Researcher(settings, backend, FakeReader(PAGES), Prompts())
        started = time.monotonic()
        assert "docs.example.org" in r.research("how to enable cors")
        release.set()
        assert time.monotonic() - started < 2

    def test_a_slow_page_is_left_out_but_the_rest_is_used(self):
        import threading

        release = threading.Event()

        class SlowReader(FakeReader):
            def fetch(self, url):
                if "blog" in url:
                    release.wait(5)
                return super().fetch(url)

        pages = {**PAGES, "https://blog.example.net/post": PageContent("B", "blog text")}
        r = Researcher(
            ResearchSettings(grace_seconds=0.05),
            FakeBackend({"*": HITS}),
            SlowReader(pages),
            Prompts(),
        )
        text = r.research("how to enable cors")
        release.set()
        assert "Use CORSMiddleware" in text and "blog text" not in text


class FakeExtra:
    def __init__(self, hits, applies=True):
        self._hits, self._applies, self.queries = hits, applies, []

    def applies(self, question):
        return self._applies

    def hits(self, query):
        self.queries.append(query)
        return self._hits


class TestExtraSources:
    WIKI: ClassVar[list[SearchHit]] = [SearchHit("Wiki", "https://en.wikipedia.org/wiki/Cors", "x")]

    def make(self, results, extra):
        backend = FakeBackend(results)
        return Researcher(
            ResearchSettings(), backend, FakeReader({}), Prompts(), extras=[extra]
        ), backend

    def test_extra_sources_fill_in_when_the_search_finds_nothing(self):
        r, _ = self.make({"*": WebError("none")}, FakeExtra(self.WIKI))
        assert "en.wikipedia.org/wiki/Cors" in r.research("what is cors")

    def test_extra_hits_never_outrank_the_searchs_best_pages(self):
        hits = [SearchHit("Docs", "https://docs.example.org/a", "x")]
        r, _ = self.make({"*": hits}, FakeExtra(self.WIKI))
        text = r.research("what is cors")
        assert text.index("docs.example.org") < text.index("wikipedia.org")

    def test_a_source_that_does_not_apply_is_not_asked(self):
        extra = FakeExtra(self.WIKI, applies=False)
        r, _ = self.make({"*": HITS}, extra)
        r.research("who won on sunday")
        assert extra.queries == []

    def test_a_failing_extra_source_is_ignored(self):
        class Failing(FakeExtra):
            def hits(self, query):
                raise WebError("down")

        r, _ = self.make({"*": HITS}, Failing([]))
        assert "docs.example.org" in r.research("how to enable cors")


class TestGraceStartsWithTheGeneralSearch:
    def test_a_fast_extra_source_does_not_cut_off_a_slower_general_search(self):
        import time

        class Slowish(FakeBackend):
            def hits(self, query):
                time.sleep(0.3)
                return super().hits(query)

        backend = Slowish({"*": HITS})
        wiki = FakeExtra([SearchHit("Wiki", "https://en.wikipedia.org/wiki/X", "x")])
        r = Researcher(
            ResearchSettings(grace_seconds=0.05),
            backend,
            FakeReader(PAGES),
            Prompts(),
            extras=[wiki],
        )
        text = r.research("how to enable cors")
        assert "docs.example.org" in text and "wikipedia.org" in text
