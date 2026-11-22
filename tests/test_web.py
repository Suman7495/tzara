"""Tests for the web tools. Nothing here touches the network."""

from dataclasses import replace
from typing import cast

import httpx
import pytest

from tzara.config import ConfigError, TzaraConfig, WebSettings
from tzara.prompts import Prompts
from tzara.tools.web import (
    DuckDuckGoBackend,
    PageExtractor,
    PageReader,
    ReadWebpage,
    Sanitizer,
    SearchHit,
    StackExchange,
    TtlCache,
    UrlGuard,
    WebError,
    WebTools,
)

PUBLIC = "93.184.216.34"
PROSE = (
    "Browsers block requests between different origins unless the server explicitly allows them, "
    "and the middleware lets an application list the origins, methods and headers it accepts. "
    "Credentials such as cookies are only sent when the server says so, and a wildcard origin "
    "cannot be combined with credentials, so each trusted origin has to be listed by name. "
)


def no_hits(*_args):
    return []


def public_guard(*_args) -> UrlGuard:
    return UrlGuard(resolver=lambda _host: [PUBLIC])


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


def page(
    body: str = "<p>Hello</p>", content_type: str = "text/html; charset=utf-8", status: int = 200
):
    if "html" in content_type and "<html" not in body:
        body = f"<html><body><main>{body}</main></body></html>"
    return httpx.Response(status, content=body.encode(), headers={"content-type": content_type})


def reader(handler, settings=None, guard=None):
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return PageReader(settings or WebSettings(), Prompts(), guard or public_guard(), client)


class TestPageReader:
    def test_returns_the_notice_title_and_text(self):
        text = reader(lambda _r: page("<h1>News</h1><p>" + PROSE + "</p>")).read(
            "https://example.com/"
        )
        assert text.startswith(Prompts().untrusted_page_notice)
        assert "News" in text and "wildcard origin" in text

    def test_plain_text_pages_are_read_as_they_are(self):
        text = reader(lambda _r: page("just text", "text/plain")).read("https://example.com/a.txt")
        assert text.endswith("\njust text")

    def test_long_pages_are_shortened_with_a_marker(self):
        settings = WebSettings(max_page_chars=20)
        text = reader(lambda _r: page("<p>" + PROSE + "</p>"), settings).read(
            "https://example.com/"
        )
        assert text.endswith("\n[shortened]")
        assert len(text) < len(Prompts().untrusted_page_notice) + 60

    def test_at_most_the_download_limit_is_read(self):
        settings = WebSettings(max_download_bytes=10)
        body = "<p>" + PROSE + " 0123456789ABCDEF</p>"
        with pytest.raises(WebError, match="no readable text"):
            reader(lambda _r: page(body), settings).read("https://example.com/")

    def test_follows_redirects_and_checks_each_address(self):
        checked = []

        def guard_resolver(host):
            checked.append(host)
            return [PUBLIC]

        def handler(request):
            if request.url.path == "/start":
                return httpx.Response(302, headers={"location": "https://other.test/end"})
            return page("<p>arrived " + PROSE + "</p>")

        text = reader(handler, guard=UrlGuard(resolver=guard_resolver)).read(
            "https://example.com/start"
        )
        assert "arrived" in text
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
            return page("<p>" + PROSE + " b</p>")

        assert reader(handler).read("https://example.com/a").endswith("b")

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


class TestPageFetch:
    def test_returns_the_title_and_the_whole_text(self):
        settings = WebSettings(max_page_chars=20)
        body = "<h1>News</h1><p>" + PROSE + "</p>"
        page_content = reader(lambda _r: page(body), settings).fetch("https://example.com/")
        assert page_content.title == "News"
        assert len(page_content.text) > 300  # not shortened, and without the notice
        assert "[shortened]" not in page_content.text

    def test_a_page_without_text_is_an_error(self):
        with pytest.raises(WebError, match="no readable text"):
            reader(lambda _r: page("<script>x</script>")).fetch("https://example.com/")


class TestWebSettings:
    def test_defaults_are_on(self):
        assert WebSettings().enabled is True

    def test_a_limit_must_be_positive(self):
        with pytest.raises(ConfigError):
            WebSettings(max_results=0)

    def test_environment_variable_turns_it_off(self):
        assert TzaraConfig.from_env({"TZARA_WEB_SEARCH": "false"}).web.enabled is False


class TestSearch:
    def test_retries_then_returns_hits(self):
        calls = []

        def search(query, limit):
            calls.append(query)
            if len(calls) < 2:
                raise RuntimeError("timeout")
            return [SearchHit("T", "https://a.example/x", "s")]

        backend = DuckDuckGoBackend(WebSettings(search_attempts=3), search)
        assert backend.hits("q") == [SearchHit("T", "https://a.example/x", "s")]
        assert len(calls) == 2

    def test_gives_up_after_the_attempts(self):
        def search(*_args):
            raise RuntimeError("timeout")

        with pytest.raises(WebError):
            DuckDuckGoBackend(WebSettings(search_attempts=2), search).hits("q")

    def test_adverts_are_left_out(self):
        ad = SearchHit("ad", "https://www.bing.com/aclick?x=1", "")
        backend = DuckDuckGoBackend(WebSettings(), lambda *_a: [ad])
        with pytest.raises(WebError):
            backend.hits("q")


class TestWebTools:
    def test_off_gives_no_tools(self):
        config = replace(TzaraConfig(), web=WebSettings(enabled=False))
        assert WebTools.create(config) == []

    def test_research_and_read_webpage(self):
        tools = WebTools.create(TzaraConfig(), backend=DuckDuckGoBackend(WebSettings(), no_hits))
        assert {tool.name for tool in tools} == {"research", "read_webpage"}

    def test_read_webpage_returns_errors_as_text(self):
        class Failing:
            def read(self, url):
                raise WebError("nope")

        tool = ReadWebpage(cast(PageReader, Failing()), "d")
        assert tool.invoke({"url": "https://a.example"}) == "Error: nope"


ARTICLE = (
    "<html><head><title>CORS guide</title></head><body><nav>Home About Contact</nav>"
    "<main><h1>CORS</h1><p>Browsers block requests between origins unless the server allows "
    "them, and the CORSMiddleware lets you list the allowed origins for an application.</p>"
    "<pre><code>app = FastAPI()\n"
    "app.add_middleware(CORSMiddleware, allow_origins=['*'])</code></pre>"
    "<p>Set allow_credentials only for origins you trust, never together with a wildcard.</p>"
    "</main><footer>Copyright</footer></body></html>"
)


class TestPageExtractor:
    def test_keeps_the_article_and_drops_menus(self):
        content = PageExtractor.extract(ARTICLE)
        assert "CORSMiddleware lets you list" in content.text
        assert "Home About Contact" not in content.text and "Copyright" not in content.text
        assert content.title == "CORS"

    def test_code_is_kept_apart_with_its_text(self):
        content = PageExtractor.extract(ARTICLE)
        assert content.code == (
            "app = FastAPI()\napp.add_middleware(CORSMiddleware, allow_origins=['*'])",
        )
        assert "add_middleware" not in content.text

    def test_a_page_without_text_is_an_error(self):
        with pytest.raises(WebError):
            PageExtractor.extract("<html><body><script>x()</script></body></html>")


class TestStackExchange:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://stackoverflow.com/questions/123/how-to", ("stackoverflow", "123")),
            ("https://unix.stackexchange.com/questions/9/x", ("unix", "9")),
            ("https://stackoverflow.com/users/1/someone", None),
            ("https://example.com/questions/1/x", None),
        ],
    )
    def test_recognises_questions(self, url, expected):
        assert StackExchange.site(url) == expected

    def test_reads_answers_with_the_accepted_one_first(self):
        seen = []

        def handler(request):
            seen.append(request.url.params["site"])
            items = [
                {"body": "<p>Second answer with enough words to be real text here.</p>"},
                {
                    "is_accepted": True,
                    "body": "<p>Accepted answer with enough words to be real text.</p>"
                    "<pre><code>x = 1\ny = 2</code></pre>",
                },
            ]
            return httpx.Response(200, json={"items": items})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        content = StackExchange.content(client, "stackoverflow", "1")
        assert content.text.index("Accepted") < content.text.index("Second")
        assert content.code == ("x = 1\ny = 2",) and seen == ["stackoverflow"]

    def test_an_api_failure_is_a_web_error(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        with pytest.raises(WebError):
            StackExchange.content(client, "stackoverflow", "1")


class TestCache:
    def test_remembers_then_forgets_after_the_time(self):
        now = [0.0]
        cache = TtlCache(10, clock=lambda: now[0])
        cache.put("a", 1)
        assert cache.get("a") == 1
        now[0] = 11
        assert cache.get("a") is None

    def test_zero_seconds_remembers_nothing(self):
        cache = TtlCache(0)
        cache.put("a", 1)
        assert cache.get("a") is None

    def test_the_oldest_entry_goes_when_full(self):
        now = [0.0]
        cache = TtlCache(100, clock=lambda: now[0])
        for number in range(TtlCache.LIMIT + 1):
            now[0] = number
            cache.put(str(number), number)
        assert cache.get("0") is None and cache.get(str(TtlCache.LIMIT)) == TtlCache.LIMIT

    def test_a_repeated_search_is_not_made_twice(self):
        calls = []

        def search(query, limit):
            calls.append(query)
            return [SearchHit("T", "https://a.example/x", "s")]

        backend = DuckDuckGoBackend(WebSettings(), search)
        backend.hits("q")
        backend.hits("q")
        assert calls == ["q"]

    def test_a_repeated_page_is_not_fetched_twice(self):
        fetched = []

        def handler(request):
            fetched.append(str(request.url))
            return page("<p>" + PROSE + "</p>")

        r = reader(handler)
        r.fetch("https://example.com/a")
        r.fetch("https://example.com/a")
        assert len(fetched) == 1


class TestSanitizer:
    def test_hidden_characters_are_removed_and_text_is_kept(self):
        hidden = "ig\u200bnore\u202e this\U000e0041\x07 line\nnext\tcell"
        assert Sanitizer.clean(hidden) == "ignore this line\nnext\tcell"

    def test_snippets_and_pages_are_cleaned(self):
        backend = DuckDuckGoBackend(
            WebSettings(), lambda *_a: [SearchHit("T\u200b", "https://a.example", "s\u202e")]
        )
        assert backend.hits("q") == [SearchHit("T", "https://a.example", "s")]
        content = PageExtractor.extract(ARTICLE.replace("CORS guide", "CORS\u200b guide"))
        assert "\u200b" not in content.title + content.text


class TestEngines:
    def test_the_configured_engines_are_asked(self):
        import sys
        import types

        seen = {}

        class FakeDDGS:
            def __init__(self, timeout):
                seen["timeout"] = timeout

            def text(self, query, **kwargs):
                seen.update(kwargs)
                return [{"title": "T", "href": "https://a.example", "body": "b"}]

        module = types.SimpleNamespace(DDGS=FakeDDGS)
        sys.modules["ddgs"] = module  # type: ignore[assignment]
        try:
            backend = DuckDuckGoBackend(WebSettings(engines="bing,google"))
            # conftest blocks the real search; it keeps the real one under another name
            backend._search = backend._real_ddgs_search  # type: ignore[attr-defined]
            hits = backend.hits("q")
        finally:
            del sys.modules["ddgs"]
        assert hits[0].title == "T" and seen["backend"] == "bing,google"
