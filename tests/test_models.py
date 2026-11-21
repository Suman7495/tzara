from unittest.mock import patch

import pytest
from langchain_ollama import ChatOllama

from tzara.agent.models import ModelCatalog, ModelFactory
from tzara.config import OllamaSettings


class TestModelCatalog:
    @pytest.mark.parametrize(
        ("spec", "provider"),
        [
            ("ollama:qwen3:8b", "ollama"),
            ("anthropic:claude-haiku-4-5", "anthropic"),
            ("openai:gpt-4o", "openai"),
            ("qwen3", ""),
            ("", ""),
        ],
    )
    def test_provider_of(self, spec, provider):
        assert ModelCatalog.provider_of(spec) == provider

    def test_every_listed_model_belongs_to_its_provider(self):
        for provider, models in ModelCatalog.MODELS.items():
            assert models
            assert all(ModelCatalog.provider_of(model) == provider for model in models)

    def test_default_model_is_listed(self):
        from tzara.config import TzaraConfig

        assert TzaraConfig().model in ModelCatalog.MODELS["ollama"]


class TestModelFactory:
    def test_ollama_models_get_the_configured_settings(self):
        settings = OllamaSettings(
            num_ctx=8192, keep_alive="5m", reasoning=True, reserved_tokens=2048
        )
        with patch("tzara.agent.models.init_chat_model") as init:
            ModelFactory(settings).create("ollama:llama3.1:8b")
        init.assert_called_once_with(
            "ollama:llama3.1:8b",
            num_ctx=8192,
            keep_alive="5m",
            reasoning=True,
            profile={"max_input_tokens": 6144},
        )

    def test_other_providers_get_no_ollama_settings(self):
        with patch("tzara.agent.models.init_chat_model") as init:
            ModelFactory(OllamaSettings()).create("anthropic:claude-haiku-4-5")
        init.assert_called_once_with("anthropic:claude-haiku-4-5")

    def test_builds_a_real_ollama_model_without_network_access(self):
        model = ModelFactory(OllamaSettings(num_ctx=8192)).create("ollama:qwen3:8b")
        assert isinstance(model, ChatOllama)
        assert model.model == "qwen3:8b"
        assert model.num_ctx == 8192
        assert model.keep_alive == "30m"

    def test_each_call_returns_a_new_model(self):
        factory = ModelFactory(OllamaSettings())
        assert factory.create("ollama:qwen3:8b") is not factory.create("ollama:qwen3:8b")


class TestContextBudget:
    """Ollama reports no context size, so Tzara supplies the budget itself."""

    def test_ollama_models_know_how_much_history_fits(self):
        model = ModelFactory(OllamaSettings(num_ctx=16384, reserved_tokens=4096)).create(
            "ollama:qwen3:8b"
        )
        assert model.profile == {"max_input_tokens": 12288}

    def test_deepagents_therefore_summarizes_relative_to_the_window(self):
        """Without a profile DeepAgents waits until 170,000 tokens: far past num_ctx."""
        from deepagents.middleware.summarization import compute_summarization_defaults

        ollama = ModelFactory(OllamaSettings()).create("ollama:qwen3:8b")
        defaults = compute_summarization_defaults(ollama)
        assert defaults["trigger"] == ("fraction", 0.85)

        bare = ChatOllama(model="qwen3:8b")
        assert compute_summarization_defaults(bare)["trigger"] == ("tokens", 170000)


class TestCloseConnection:
    def test_closes_the_ollama_http_client(self):
        model = ChatOllama(model="qwen3:8b")
        assert not model._client._client.is_closed
        ModelFactory.close_connection(model)
        assert model._client._client.is_closed

    def test_ollama_private_attributes_still_exist(self):
        """Canary: close_connection depends on langchain_ollama internals."""
        model = ChatOllama(model="qwen3:8b")
        assert hasattr(model._client, "_client")
        assert callable(model._client._client.close)

    def test_models_without_a_client_are_left_alone(self):
        ModelFactory.close_connection(object())  # type: ignore[arg-type]
