"""Chat model catalogue and construction."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import ChatGenerationChunk
from langchain_ollama import ChatOllama

from tzara.config import OllamaSettings


class ModelCatalog:
    """Models Tzara knows about, grouped by provider.

    The catalogue only feeds ``/list-models``. Any ``provider:model-name`` that
    LangChain can load is accepted, listed here or not.
    """

    OLLAMA: ClassVar[str] = "ollama"

    MODELS: ClassVar[dict[str, dict[str, str]]] = {
        "ollama": {
            "ollama:qwen3:8b": "Qwen3 8B (default, fast, reliable tools, local)",
            "ollama:llama3.1:8b": "Llama 3.1 8B (local, weak tool calling)",
            "ollama:llama3.1:70b": "Llama 3.1 70B (powerful, local)",
            "ollama:mistral": "Mistral (efficient)",
        },
        "anthropic": {
            "anthropic:claude-opus-4-8": "Claude Opus 4.8 (most capable)",
            "anthropic:claude-opus-4-7": "Claude Opus 4.7",
            "anthropic:claude-sonnet-4-6": "Claude Sonnet 4.6 (balanced)",
            "anthropic:claude-haiku-4-5": "Claude Haiku 4.5 (fast, cheap)",
        },
        "openai": {
            "openai:gpt-4o": "GPT-4o (advanced reasoning)",
            "openai:gpt-4-turbo": "GPT-4 Turbo",
            "openai:gpt-3.5-turbo": "GPT-3.5 Turbo (fast)",
        },
        "google_genai": {
            "google_genai:gemini-3.5-flash": "Gemini 3.5 Flash (fast)",
            "google_genai:gemini-3.1-pro": "Gemini 3.1 Pro (powerful)",
        },
    }

    @staticmethod
    def provider_of(spec: str) -> str:
        """Return the provider part of a ``provider:model-name`` spec.

        Args:
            spec: Model spec, e.g. ``ollama:llama3.1:8b``.

        Returns:
            The text before the first colon, or an empty string if there is none.
        """
        provider, separator, _ = spec.partition(":")
        return provider if separator else ""


class NumberedToolCallsChatOllama(ChatOllama):
    """``ChatOllama`` that numbers the tool calls it streams.

    Ollama sends each tool call whole, in a chunk of its own, with no ``index``. LangChain's
    streaming protocol merges chunks that share an index, and a missing index counts as the
    same one every time. So when a model asked for several tools at once, the calls were
    glued into one with unreadable arguments (``{"a": 1}{"a": 2}``), nothing ran, and the reply
    came back empty. Numbering them keeps them apart.
    """

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        """Stream the reply, giving each tool call in it its own index."""
        index = 0
        for chunk in super()._stream(*args, **kwargs):
            for call in getattr(chunk.message, "tool_call_chunks", None) or []:
                call["index"] = index
                index += 1
            yield chunk


class ModelFactory:
    """Builds chat model instances from ``provider:model-name`` specs.

    The agent is given a model *instance*, which carries the context size (``profile``) that
    decides when old turns are summarized.
    """

    def __init__(self, ollama: OllamaSettings) -> None:
        """Create a factory.

        Args:
            ollama: Settings applied to Ollama models.
        """
        self._ollama = ollama

    def create(self, spec: str, temperature: float | None = None) -> BaseChatModel:
        """Build a chat model.

        Args:
            spec: Model spec as ``provider:model-name``.
            temperature: How much the model varies its answers; 0 gives the same answer each
                time. ``None`` leaves the provider's own default.

        Returns:
            A new chat model with its own HTTP client.
        """
        if ModelCatalog.provider_of(spec) == ModelCatalog.OLLAMA:
            return NumberedToolCallsChatOllama(
                model=spec.split(":", 1)[1],
                num_ctx=self._ollama.num_ctx,
                keep_alive=self._ollama.keep_alive,
                # Ignored by models without thinking support.
                reasoning=self._ollama.reasoning,
                # Ollama models report no context size, so nothing would summarize old turns
                # before num_ctx, where Ollama silently truncates the prompt. A profile makes
                # the summarizer act when history nears the budget instead.
                profile={"max_input_tokens": self._ollama.history_tokens},
                temperature=temperature,
            )
        return (
            init_chat_model(spec)
            if temperature is None
            else init_chat_model(spec, temperature=temperature)
        )

    @staticmethod
    def close_connection(model: BaseChatModel) -> None:
        """Abort any in-flight request by closing the model's HTTP client.

        Ollama stops generating when the connection drops, and the thread blocked
        reading the response gets an error instead of waiting for the model to
        finish. Only Ollama models are supported; for other models this does
        nothing. The model cannot be used afterwards.

        This relies on private attributes of ``langchain_ollama.ChatOllama``;
        a unit test fails if an upgrade renames them.

        Args:
            model: The chat model whose request should be cancelled.
        """
        http_client = getattr(getattr(model, "_client", None), "_client", None)
        if http_client is not None:
            http_client.close()
