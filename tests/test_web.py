"""Tests for the web tools. Nothing here touches the network."""

from dataclasses import replace
from typing import cast

import httpx
import pytest

from tzara.config import ConfigError, TzaraConfig, WebSettings
from tzara.prompts import Prompts
from tzara.tools.web import (
    DuckDuckGoBackend,
    GeminiBackend,
    PageReader,
    PageText,
    ReadWebpage,
    SearchHit,
    UrlGuard,
    WebError,
    WebSearch,
    WebTools,
)

PUBLIC = "93.184.216.34"


def public_guard(*_args) -> UrlGuard:
    return UrlGuard(resolver=lambda _host: [PUBLIC])


class TestDuckDuckGoBackend:
    def hits(self, count: int = 2):
        return lambda _query, _limit: [
            SearchHit(f"Title {n}", f"https://example.com/{n}", f"Snippet {n}")
            for n in range(1, count + 1)
        ]

    def test_numbers_each_result_with_title_link_and_snippet(self):
        text = DuckDuckGoBackend(WebSettings(), self.hits()).search("cats")
        assert text == (
            "1. Title 1\n   https://example.com/1\n   Snippet 1\n\n"
            "2. Title 2\n   https://example.com/2\n   Snippet 2"
        )

    def test_asks_for_the_configured_number_of_results(self):
        seen = []

        def search(query, limit):
            seen.append((query, limit))
            return self.hits()(query, limit)

        DuckDuckGoBackend(WebSettings(max_results=3), search).search("cats")
        assert seen == [("cats", 3)]

    def test_no_results_is_an_error_with_advice(self):
        with pytest.raises(WebError, match=r"No results found\. Try again"):
            DuckDuckGoBackend(WebSettings(), lambda _q, _n: []).search("zzz")

    def test_a_failing_search_is_reported_not_raised_raw(self):
        def broken(_query, _limit):
            raise RuntimeError("blocked")

        with pytest.raises(WebError, match="The search failed: blocked"):
            DuckDuckGoBackend(WebSettings(), broken).search("cats")

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("No results found.", "No results found"),
            ("rate limited", "The search failed: rate limited"),
        ],
    )
    def test_ddgs_nothing_found_is_an_answer_but_other_errors_are_failures(
        self, monkeypatch, message, expected
    ):
        import sys
        import types

        class FakeDDGS:
            def __init__(self, timeout):
                pass

            def text(self, query, max_results):
                raise RuntimeError(message)

        monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FakeDDGS))
        with pytest.raises(WebError, match=expected):
            DuckDuckGoBackend(WebSettings()).search("cats")

    def test_the_default_search_reads_ddgs_results(self, monkeypatch):
        import sys
        import types

        class FakeDDGS:
            def __init__(self, timeout):
                self.timeout = timeout

            def text(self, query, max_results):
                assert (query, max_results, self.timeout) == ("cats", 2, 7)
                return [{"title": "T", "href": "https://x.test", "body": "B"}, {}]

        monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FakeDDGS))
        backend = DuckDuckGoBackend(WebSettings(max_results=2, timeout_seconds=7))
        assert backend.search("cats") == "1. T\n   https://x.test\n   B\n\n2. \n   \n   "


def gemini(handler, key="secret", settings=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    environment = {"GOOGLE_API_KEY": key} if key else {}
    return GeminiBackend(settings or WebSettings(), Prompts(), client, environment)


def answer(text="Canberra.", chunks=None):
    candidate = {"content": {"parts": [{"text": text}]}}
    if chunks is not None:
        candidate["groundingMetadata"] = {"groundingChunks": chunks}
    return httpx.Response(200, json={"candidates": [candidate]})


class TestGeminiBackend:
    def test_returns_the_answer_and_its_sources(self):
        chunks = [{"web": {"title": "bbc.com", "uri": "https://g.test/1"}}, {"other": 1}]
        text = gemini(lambda _r: answer("Canberra.", chunks)).search("capital of Australia")
        assert text == "Canberra.\n\nSources:\n- bbc.com https://g.test/1"

    def test_an_answer_without_sources_is_just_the_answer(self):
        assert gemini(lambda _r: answer()).search("q") == "Canberra."

    def test_sends_the_key_in_a_header_and_never_in_the_url(self):
        seen = []

        def handler(request):
            seen.append(request)
            return answer()

        gemini(handler, key="sekrit").search("cats")
        request = seen[0]
        assert request.headers["x-goog-api-key"] == "sekrit"
        assert "sekrit" not in str(request.url)
        assert str(request.url).endswith("/models/gemini-3.5-flash:generateContent")

    def test_asks_gemini_to_use_google_search_for_the_query(self):
        import json

        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return answer()

        gemini(handler).search("cats")
        body = seen[0]
        assert body["tools"] == [{"google_search": {}}]
        assert "cats" in body["contents"][0]["parts"][0]["text"]

    def test_no_key_says_which_variable_to_set(self):
        with pytest.raises(WebError, match="GOOGLE_API_KEY"):
            gemini(lambda _r: answer(), key="").search("q")

    def test_a_refusal_is_reported_with_its_status(self):
        with pytest.raises(WebError, match="status 403"):
            gemini(lambda _r: httpx.Response(403)).search("q")

    def test_a_network_failure_is_reported(self):
        def offline(_request):
            raise httpx.ConnectError("down")

        with pytest.raises(WebError, match="Could not reach Google: ConnectError"):
            gemini(offline).search("q")

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(200, text="not json"),
            httpx.Response(200, json={}),
            httpx.Response(200, json={"candidates": []}),
            httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": " "}]}}]}),
            httpx.Response(200, json={"candidates": [{"content": {"parts": ["x"]}}]}),
        ],
    )
    def test_an_unusable_answer_is_reported(self, response):
        with pytest.raises(WebError, match="gave no answer"):
            gemini(lambda _r: response).search("q")

    def test_the_default_client_and_environment_are_used_when_none_are_given(self):
        backend = GeminiBackend(WebSettings(), Prompts())
        with pytest.raises(WebError):  # no key in the test environment, so no request is sent
            backend._environment = {}
            backend.search("q")


class TestUrlGuard:
    @pytest.mark.parametrize(
        "url",
        [
            "ftp://example.com/x",
            "file:///etc/passwd",
            "example.com",
            "http://",
            "javascript:alert(1)",
        ],
    )
    def test_only_full_web_addresses_pass(self, url):
        with pytest.raises(WebError, match="http:// or https://"):
            public_guard().check(url)

    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "10.0.0.5",
            "192.168.1.1",
            "169.254.169.254",
            "::1",
            "fe80::1%en0",
            "100.64.0.1",
            "0.0.0.0",
        ],
    )
    def test_private_and_local_addresses_are_refused(self, address):
        guard = UrlGuard(resolver=lambda _host: [address])
        with pytest.raises(WebError, match="not on the public internet"):
            guard.check("http://anything.test/")

    def test_one_private_address_among_public_ones_refuses_the_host(self):
        guard = UrlGuard(resolver=lambda _host: [PUBLIC, "127.0.0.1"])
        with pytest.raises(WebError):
            guard.check("https://sneaky.test/")

    def test_a_host_that_resolves_to_nothing_is_refused(self):
        with pytest.raises(WebError):
            UrlGuard(resolver=lambda _host: []).check("https://x.test/")

    def test_an_unparseable_address_is_refused(self):
        with pytest.raises(WebError):
            UrlGuard(resolver=lambda _host: ["not-an-ip"]).check("https://x.test/")

    def test_an_unknown_site_says_so(self):
        def missing(_host):
            raise OSError("nope")

        with pytest.raises(WebError, match=r"Could not find the site nowhere\.test"):
            UrlGuard(resolver=missing).check("https://nowhere.test/")

    def test_a_public_address_passes(self):
        public_guard().check("https://example.com/page")

    def test_the_real_resolver_handles_a_literal_address(self):
        UrlGuard().check("http://93.184.216.34/")
        with pytest.raises(WebError):
            UrlGuard().check("http://127.0.0.1:8927/v1/status")


class TestPageText:
    def extract(self, html: str) -> PageText:
        extractor = PageText()
        extractor.feed(html)
        return extractor

    def test_keeps_visible_text_and_drops_scripts_and_styles(self):
        page = self.extract(
            "<html><head><title>Hi</title><style>p{}</style></head>"
            "<body><script>evil()</script><p>Hello <b>world</b></p>"
            "<noscript>x</noscript></body></html>"
        )
        assert page.text() == "Hello world"
        assert page.title == "Hi"

    def test_block_elements_start_new_lines(self):
        page = self.extract("<h1>Title</h1><p>One</p><p>Two</p><ul><li>a</li><li>b</li></ul>")
        assert page.text().splitlines() == ["Title", "One", "Two", "a", "b"]

    def test_navigation_and_footers_are_dropped(self):
        page = self.extract("<nav>Menu</nav><article>Story</article><footer>Legal</footer>")
        assert page.text() == "Story"

    def test_whitespace_is_collapsed_and_entities_decoded(self):
        assert self.extract("<p>  a   &amp;\n b  </p>").text() == "a & b"

    def test_the_main_content_is_preferred_over_the_page_around_it(self):
        story = "A long enough sentence about the story itself. " * 6
        page = self.extract(
            f"<div>Sidebar words here</div><main><p>{story}</p></main><div>Other</div>"
        )
        assert page.text() == story.strip()

    def test_a_small_main_is_not_trusted_and_the_whole_page_is_used(self):
        page = self.extract("<div>Intro text for the page</div><main><p>tiny</p></main>")
        assert "Intro text for the page" in page.text()

    def test_nested_main_elements_are_balanced(self):
        story = "Words that make up the story text. " * 8
        page = self.extract(f"<main><article>{story}</article><p>{story}</p></main><p>outside</p>")
        assert "outside" not in page.text()

    def test_long_runs_of_short_lines_are_dropped_as_menus(self):
        menu = "".join(f"<li>Lang{n}</li>" for n in range(10))
        page = self.extract(
            "<p>This sentence is real prose, not a menu.</p>"
            f"<ul>{menu}</ul><p>More real prose follows here.</p>"
        )
        assert (
            page.text() == "This sentence is real prose, not a menu.\nMore real prose follows here."
        )

    def test_short_runs_of_short_lines_are_kept(self):
        page = self.extract(
            "<ul><li>Population 484</li><li>Area 814</li></ul><p>Some longer prose line here.</p>"
        )
        assert page.text().splitlines() == [
            "Population 484",
            "Area 814",
            "Some longer prose line here.",
        ]

    def test_an_unbalanced_end_tag_is_harmless(self):
        assert self.extract("</script><p>ok</p>").text() == "ok"


def page(
    body: str = "<p>Hello</p>", content_type: str = "text/html; charset=utf-8", status: int = 200
):
    return httpx.Response(status, content=body.encode(), headers={"content-type": content_type})


def reader(handler, settings=None, guard=None):
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return PageReader(settings or WebSettings(), Prompts(), guard or public_guard(), client)


class TestPageReader:
    def test_returns_the_notice_title_and_text(self):
        text = reader(lambda _r: page("<title>News</title><p>Big story</p>")).read(
            "https://example.com/"
        )
        assert text == f"{Prompts().untrusted_page_notice}\nNews\nBig story"

    def test_plain_text_pages_are_read_as_they_are(self):
        text = reader(lambda _r: page("just text", "text/plain")).read("https://example.com/a.txt")
        assert text.endswith("\njust text")

    def test_long_pages_are_shortened_with_a_marker(self):
        settings = WebSettings(max_page_chars=20)
        text = reader(lambda _r: page("<p>" + "word " * 100 + "</p>"), settings).read(
            "https://example.com/"
        )
        assert text.endswith("\n[shortened]")
        assert len(text) < len(Prompts().untrusted_page_notice) + 60

    def test_at_most_the_download_limit_is_read(self):
        settings = WebSettings(max_download_bytes=10)
        text = reader(lambda _r: page("<p>0123456789ABCDEF</p>"), settings).read(
            "https://example.com/"
        )
        assert "ABCDEF" not in text

    def test_follows_redirects_and_checks_each_address(self):
        checked = []

        def guard_resolver(host):
            checked.append(host)
            return [PUBLIC]

        def handler(request):
            if request.url.path == "/start":
                return httpx.Response(302, headers={"location": "https://other.test/end"})
            return page("<p>arrived</p>")

        text = reader(handler, guard=UrlGuard(resolver=guard_resolver)).read(
            "https://example.com/start"
        )
        assert text.endswith("arrived")
        assert checked == ["example.com", "other.test"]

    def test_a_redirect_to_a_private_address_is_refused(self):
        def resolver(host):
            return ["127.0.0.1"] if host == "localhost.test" else [PUBLIC]

        def handler(_request):
            return httpx.Response(302, headers={"location": "http://localhost.test:8927/v1/status"})

        with pytest.raises(WebError, match="not on the public internet"):
            reader(handler, guard=UrlGuard(resolver=resolver)).read("https://example.com/")

    def test_a_relative_redirect_stays_on_the_site(self):
        def handler(request):
            if request.url.path == "/a":
                return httpx.Response(301, headers={"location": "/b"})
            return page("<p>b</p>")

        assert reader(handler).read("https://example.com/a").endswith("\nb")

    def test_too_many_redirects_are_refused(self):
        def handler(_request):
            return httpx.Response(302, headers={"location": "https://example.com/again"})

        with pytest.raises(WebError, match="too many times"):
            reader(handler, WebSettings(max_redirects=2)).read("https://example.com/")

    def test_a_redirect_without_a_destination_is_just_an_error_status(self):
        with pytest.raises(WebError, match="status 302"):
            reader(lambda _r: httpx.Response(302)).read("https://example.com/")

    def test_error_statuses_are_reported(self):
        with pytest.raises(WebError, match="status 404"):
            reader(lambda _r: page(status=404)).read("https://example.com/")

    @pytest.mark.parametrize("content_type", ["application/pdf", "image/png", ""])
    def test_binary_pages_are_refused(self, content_type):
        with pytest.raises(WebError, match="not a text page"):
            reader(lambda _r: page("x", content_type)).read("https://example.com/f")

    def test_an_empty_page_is_reported(self):
        with pytest.raises(WebError, match="no readable text"):
            reader(lambda _r: page("<script>x()</script>")).read("https://example.com/")

    def test_network_failures_are_reported_by_kind(self):
        def offline(_request):
            raise httpx.ConnectTimeout("slow")

        with pytest.raises(WebError, match="Could not load the page: ConnectTimeout"):
            reader(offline).read("https://example.com/")

    def test_a_forbidden_address_never_reaches_the_network(self):
        def handler(_request):
            raise AssertionError("must not be fetched")

        with pytest.raises(WebError):
            reader(handler, guard=UrlGuard()).read("http://127.0.0.1:8927/v1/status")

    def test_the_default_client_does_not_follow_redirects_by_itself(self):
        built = PageReader(WebSettings(), Prompts(), public_guard())
        assert built._client.follow_redirects is False
        assert built._client.headers["user-agent"] == WebSettings().user_agent


class TestTools:
    def test_search_returns_the_backend_text(self):
        class Backend:
            def search(self, query):
                return f"results for {query}"

        tool = WebSearch(Backend(), "Search the web.")
        assert tool.invoke({"query": "cats"}) == "results for cats"
        assert tool.name == "web_search" and tool.description == "Search the web."

    def test_search_errors_come_back_as_text_for_the_model(self):
        class Backend:
            def search(self, query):
                raise WebError("No results found.")

        assert WebSearch(Backend(), "d").invoke({"query": "x"}) == "Error: No results found."

    def test_reading_returns_the_page_text(self):
        class Reader:
            def read(self, url):
                return f"text of {url}"

        tool = ReadWebpage(Reader(), "Read a page.")
        assert tool.invoke({"url": "https://example.com"}) == "text of https://example.com"
        assert tool.name == "read_webpage"

    def test_reading_errors_come_back_as_text_for_the_model(self):
        class Reader:
            def read(self, url):
                raise WebError("not a text page")

        assert (
            ReadWebpage(Reader(), "d").invoke({"url": "https://x.test"}) == "Error: not a text page"
        )


class TestWebTools:
    def names(self, **config):
        return [tool.name for tool in WebTools.create(TzaraConfig(**config))]

    def test_both_tools_are_on_by_default(self):
        assert self.names() == ["web_search", "read_webpage"]

    def test_none_when_turned_off(self):
        assert self.names(web=WebSettings(enabled=False)) == []

    def test_descriptions_come_from_the_prompts(self):
        search, read = WebTools.create(TzaraConfig())
        assert search.description == Prompts().web_search_tool
        assert read.description == Prompts().read_webpage_tool

    def test_the_provider_chooses_the_backend(self):
        duck = WebTools.create(TzaraConfig())[0]
        google = WebTools.create(TzaraConfig(web=WebSettings(provider="gemini")))[0]
        assert isinstance(cast(WebSearch, duck).backend, DuckDuckGoBackend)
        assert isinstance(cast(WebSearch, google).backend, GeminiBackend)

    def test_a_backend_and_reader_can_be_supplied(self):
        marker_backend, marker_reader = object(), object()
        search, read = WebTools.create(TzaraConfig(), backend=marker_backend, reader=marker_reader)
        assert cast(WebSearch, search).backend is marker_backend
        assert cast(ReadWebpage, read).reader is marker_reader


class TestWebSettings:
    def test_defaults_are_on_with_duckduckgo(self):
        settings = WebSettings()
        assert settings.enabled and settings.provider == "duckduckgo"

    def test_an_unknown_provider_is_rejected(self):
        with pytest.raises(ConfigError, match="provider must be one of"):
            WebSettings(provider="bing")

    @pytest.mark.parametrize(
        "field", ["max_results", "max_page_chars", "max_download_bytes", "timeout_seconds"]
    )
    def test_limits_must_be_positive(self, field):
        with pytest.raises(ConfigError, match="limits"):
            replace(WebSettings(), **{field: 0})  # type: ignore[arg-type]

    def test_a_negative_redirect_limit_is_rejected_but_zero_is_fine(self):
        with pytest.raises(ConfigError):
            WebSettings(max_redirects=-1)
        WebSettings(max_redirects=0)

    def test_environment_variables_override(self):
        config = TzaraConfig.from_env(
            {"TZARA_WEB_SEARCH": "false", "TZARA_WEB_SEARCH_PROVIDER": "Gemini"}
        )
        assert config.web.enabled is False and config.web.provider == "gemini"

    def test_the_environment_leaves_defaults_alone_when_unset(self):
        assert TzaraConfig.from_env({}).web == WebSettings()
