"""Model readiness, keys and downloads."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import httpx
import pytest

from tzara.server.setup import KeyStore, ModelSetup


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in KeyStore.VARIABLES.values():
        monkeypatch.delenv(variable, raising=False)


def handler_with(models: list[str]) -> httpx.Client:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": name} for name in models]})

    return httpx.Client(transport=httpx.MockTransport(answer))


def need_of(setup: ModelSetup, model_id: str) -> str:
    for provider in setup.overview():
        for model in provider["models"]:  # type: ignore[attr-defined]
            if model["id"] == model_id:
                return str(model["need"])
    raise AssertionError(model_id)


def test_key_is_saved_private_and_used(tmp_path: Path) -> None:
    keys = KeyStore(tmp_path / "keys.json")
    keys.save("anthropic", " sk-test ")
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test"
    assert stat.S_IMODE((tmp_path / "keys.json").stat().st_mode) == 0o600
    del os.environ["ANTHROPIC_API_KEY"]
    KeyStore(tmp_path / "keys.json").apply()
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test"


@pytest.mark.parametrize("bad", ["", "two words", "x" * 501])
def test_bad_key_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        KeyStore(tmp_path / "keys.json").save("openai", bad)


def test_models_report_what_they_need(tmp_path: Path) -> None:
    keys = KeyStore(tmp_path / "keys.json")
    setup = ModelSetup(keys, "http://ollama.test", handler_with(["qwen3:8b"]))
    assert need_of(setup, "ollama:qwen3:8b") == "ready"
    assert need_of(setup, "ollama:mistral") == "download"
    assert need_of(setup, "anthropic:claude-opus-4-8") == "key"
    keys.save("anthropic", "sk-test")
    assert need_of(setup, "anthropic:claude-opus-4-8") == "ready"


def test_ollama_not_running(tmp_path: Path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no", request=request)

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    setup = ModelSetup(KeyStore(tmp_path / "k"), "http://ollama.test", client)
    assert need_of(setup, "ollama:qwen3:8b") == "ollama"


def test_only_local_models_download(tmp_path: Path) -> None:
    setup = ModelSetup(KeyStore(tmp_path / "k"), "http://ollama.test", handler_with([]))
    with pytest.raises(ValueError):
        setup.start_download("openai:gpt-4o")
