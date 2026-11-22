"""Shared test setup: tests never reach the network."""

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
