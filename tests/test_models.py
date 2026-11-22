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
        model = ModelFactory(settings).create("ollama:llama3.1:8b")
        assert isinstance(model, ChatOllama)
        assert (model.model, model.num_ctx, model.keep_alive, model.reasoning) == (
            "llama3.1:8b",
            8192,
            "5m",
            True,
        )
        assert model.profile == {"max_input_tokens": 6144}

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


class TestNumberedToolCalls:
    """Ollama sends tool calls without an index, which glued parallel calls into one."""

    @staticmethod
    def stream_of(*calls):
        """The chunks Ollama would send: one tool call per chunk, no index."""
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        return [
            ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": name,
                            "args": args,
                            "id": f"id-{n}",
                            "index": None,
                            "type": "tool_call_chunk",
                        }
                    ],
                )
            )
            for n, (name, args) in enumerate(calls)
        ]

    def run(self, monkeypatch, chunks):
        model = ModelFactory(OllamaSettings()).create("ollama:qwen3:8b")
        monkeypatch.setattr(ChatOllama, "_stream", lambda self, *a, **k: iter(chunks))
        return list(model._stream([]))

    def test_each_streamed_call_gets_its_own_index(self, monkeypatch):
        chunks = self.stream_of(("remember", '{"fact": "a"}'), ("remember", '{"fact": "b"}'))
        out = self.run(monkeypatch, chunks)
        assert [c.message.tool_call_chunks[0]["index"] for c in out] == [0, 1]

    def test_merged_they_are_separate_valid_calls(self, monkeypatch):
        chunks = self.stream_of(
            ("remember", '{"fact": "a"}'),
            ("remember", '{"fact": "b"}'),
            ("remember", '{"fact": "c"}'),
        )
        out = self.run(monkeypatch, chunks)
        merged = out[0].message
        for chunk in out[1:]:
            merged = merged + chunk.message
        assert [call["args"] for call in merged.tool_calls] == [
            {"fact": "a"},
            {"fact": "b"},
            {"fact": "c"},
        ]
        assert merged.invalid_tool_calls == []

    def test_chunks_without_tool_calls_pass_through(self, monkeypatch):
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        text = ChatGenerationChunk(message=AIMessageChunk(content="Hello"))
        out = self.run(monkeypatch, [text])
        assert out[0].message.content == "Hello" and out[0].message.tool_call_chunks == []


class TestTemperature:
    def test_an_ollama_model_can_be_made_to_answer_the_same_way_each_time(self):
        model = ModelFactory(OllamaSettings()).create("ollama:qwen3:8b", temperature=0)
        assert model.temperature == 0  # type: ignore[attr-defined]

    def test_by_default_the_providers_own_temperature_is_left_alone(self):
        model = ModelFactory(OllamaSettings()).create("ollama:qwen3:8b")
        assert model.temperature is None  # type: ignore[attr-defined]

    def test_other_providers_are_given_the_temperature_too(self):
        with patch("tzara.agent.models.init_chat_model") as init:
            ModelFactory(OllamaSettings()).create("anthropic:claude-haiku-4-5", temperature=0)
            ModelFactory(OllamaSettings()).create("anthropic:claude-haiku-4-5")
        assert init.call_args_list[0].kwargs == {"temperature": 0}
        assert init.call_args_list[1].kwargs == {}
