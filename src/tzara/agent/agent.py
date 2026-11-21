"""Tzara, a deep agent built on LangChain's DeepAgents framework."""

from __future__ import annotations

import logging
import time
import warnings
from collections.abc import Generator
from typing import TYPE_CHECKING

from deepagents import create_deep_agent
from langchain_core._api import LangChainBetaWarning

from tzara.agent.harness import HarnessConfigurator
from tzara.agent.memory import ConversationMemory, HistoryMessage
from tzara.agent.models import ModelFactory
from tzara.agent.summaries import SummaryRunFilter
from tzara.config import TzaraConfig
from tzara.tools.calculator import Calculator

if TYPE_CHECKING:
    from langgraph.stream.run_stream import GraphRunStream

logger = logging.getLogger(__name__)


class Tzara:
    """Conversational agent with calculator, file, todo and subagent tools.

    Wraps a DeepAgents graph and exposes what the CLI needs: streaming a reply,
    loading the model ahead of the first message, and cancelling a response.
    The conversation is remembered in a :class:`ConversationMemory`.
    """

    def __init__(
        self,
        model: str | None = None,
        config: TzaraConfig | None = None,
        memory: ConversationMemory | None = None,
    ) -> None:
        """Create the agent.

        Args:
            model: Model as ``provider:model-name``. Defaults to ``config.model``.
            config: Configuration. Defaults to :class:`TzaraConfig` defaults.
            memory: Conversation to continue. Defaults to a new, empty one. Pass
                the previous agent's memory to keep the history across a model
                switch.
        """
        self._config = config or TzaraConfig()
        self._model = model or self._config.model
        self._memory = memory or ConversationMemory()
        self._tools = [Calculator()]
        self._harness = HarnessConfigurator(self._config.prompts, self._config.harness_providers)
        self._factory = ModelFactory(self._config.ollama)
        self._stream: GraphRunStream | None = None
        self._cancel_requested = False

        # The v3 streaming protocol is flagged as beta, and langchain_core
        # re-enables the warning on import, so it would be printed into the chat.
        warnings.filterwarnings("ignore", category=LangChainBetaWarning)

        self._harness.install_profiles()
        self._create_agent()

    @property
    def model(self) -> str:
        """The model this agent uses, as ``provider:model-name``."""
        return self._model

    @property
    def memory(self) -> ConversationMemory:
        """The conversation this agent is part of."""
        return self._memory

    def handle(self, text: str) -> Generator[str]:
        """Stream the reply to one user message.

        The message and reply are added to the conversation. The generator aborts
        the underlying run if it is closed early.

        Args:
            text: The user's message.

        Yields:
            Pieces of the assistant's reply as the model produces them.
        """
        self._cancel_requested = False
        logger.info("turn started: %d characters", len(text))
        logger.debug("user message: %s", text)
        started = time.monotonic()
        first_token_after: float | None = None
        reply_characters = 0
        outcome = "cancelled"

        summaries = SummaryRunFilter()
        stream = self._agent.stream_events(
            {"messages": [{"role": "user", "content": text}]},
            {**self._memory.run_config, "callbacks": [summaries]},
            version="v3",
        )
        self._stream = stream

        try:
            for message in stream.messages:
                if summaries.is_summary(message.message_id):
                    logger.info("conversation history summarized to stay within the context window")
                    continue
                for piece in message.text:
                    if first_token_after is None:
                        first_token_after = time.monotonic() - started
                    reply_characters += len(piece)
                    yield piece
            outcome = "completed"
        except Exception:
            # Cancelling closes the connection, which surfaces as an error here.
            outcome = "cancelled" if self._cancel_requested else "failed"
            raise
        finally:
            if self._stream is stream:
                self._stream = None
            stream.abort()
            logger.info(
                "turn %s: first token after %s, total %.1fs, %d reply characters",
                outcome,
                "n/a" if first_token_after is None else f"{first_token_after:.1f}s",
                time.monotonic() - started,
                reply_characters,
            )

    def history(self) -> list[HistoryMessage]:
        """Return the conversation so far, as the user and assistant said it.

        Tool calls, tool results and empty messages are left out.
        """
        state = self._agent.get_state(self._memory.run_config)
        messages = (state.values or {}).get("messages", [])
        roles = {"human": "user", "ai": "assistant"}
        return [
            HistoryMessage(roles[message.type], message.text)
            for message in messages
            if message.type in roles and message.text.strip()
        ]

    def warm_up(self) -> None:
        """Load the model and prime Ollama's prompt cache.

        Sends one throwaway message so the user's first real message does not
        pay for loading the model and evaluating the prompt. The message is not
        added to the conversation.

        Raises:
            Exception: Whatever the model client raises if it cannot be reached.
        """
        started = time.monotonic()
        with self._memory.scratch() as run_config:
            self._agent.invoke(
                {"messages": [{"role": "user", "content": self._config.warmup_message}]},
                run_config,
            )
        logger.info("model %s loaded in %.1fs", self._model, time.monotonic() - started)

    def cancel(self) -> None:
        """Cancel the in-flight model request.

        Model calls run in a worker thread blocked on the HTTP response. Closing
        the connection makes that read fail and stops generation. Without it,
        unwinding the stream or exiting the interpreter waits for the model to
        finish generating.

        Quick and non-blocking, so it is safe to call from a signal handler. The
        agent cannot be used again until :meth:`interrupt` rebuilds it.
        """
        self._cancel_requested = True
        self._factory.close_connection(self._chat_model)

    def interrupt(self) -> None:
        """Cancel the in-flight response and make the agent usable again.

        The cancelled client cannot be reused, so a fresh chat model and agent
        are built. The conversation is kept.
        """
        logger.info("response interrupted")
        self.cancel()

        stream, self._stream = self._stream, None
        if stream is not None:
            stream.abort()

        self._create_agent()

    def _create_agent(self) -> None:
        """Build the chat model and the DeepAgents graph around it."""
        self._cancel_requested = False
        self._chat_model = self._factory.create(self._model)
        self._agent = create_deep_agent(
            model=self._chat_model,
            tools=self._tools,
            system_prompt=self._config.prompts.rendered_system(),
            middleware=self._harness.middleware(),
            checkpointer=self._memory.checkpointer,
            interrupt_on={},
        )
        logger.info("agent created with model %s", self._model)
