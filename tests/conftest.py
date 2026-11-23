"""Shared test setup: tests never reach the network."""

import httpx
import pytest

from tzara.tools.web import DuckDuckGoBackend


@pytest.fixture(autouse=True)
def no_real_web_search(monkeypatch):
    """A question in a test is routed to research; the real search engine must not be asked."""

    def refuse(*_args, **_kwargs):
        raise RuntimeError("tests do not search the web")

    monkeypatch.setattr(
        DuckDuckGoBackend, "_real_ddgs_search", DuckDuckGoBackend._ddgs_search, raising=False
    )
    monkeypatch.setattr(DuckDuckGoBackend, "_ddgs_search", refuse)


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    """Nothing but this computer may be reached over HTTP: not Wikipedia, not a web page.

    A question in a test is researched, and its Wikipedia search and page reads used to go out
    for real, which made the tests slow and, with several pages parsed at once, crash.
    """
    original = httpx.HTTPTransport.handle_request

    def guarded(self, request):
        if request.url.host not in ("127.0.0.1", "localhost"):
            raise httpx.ConnectError("tests do not use the network")
        return original(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarded)
