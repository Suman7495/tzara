"""Keeping the summarizer's output out of the chat."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler


class SummaryRunFilter(BaseCallbackHandler):
    """Recognises the model runs that write conversation summaries.

    When history nears the context budget, DeepAgents summarizes older turns by
    calling the same chat model from inside the turn. That call is streamed like
    any other, so without a filter the summary would be printed as part of the
    reply.

    LangChain marks the summarizer's calls with ``lc_source="summarization"``
    run metadata, and names each message after its run (``lc_run--<run id>``).
    This handler, passed in the run's callbacks, records the runs; the caller
    asks :meth:`is_summary` about each streamed message.
    """

    SOURCE = "summarization"
    MESSAGE_ID_PREFIX = "lc_run--"

    def __init__(self) -> None:
        """Create a filter that has seen no runs."""
        super().__init__()
        self._summary_run_ids: set[str] = set()

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Remember runs that are summarizations."""
        if metadata and metadata.get("lc_source") == self.SOURCE:
            self._summary_run_ids.add(str(run_id))

    def is_summary(self, message_id: str | None) -> bool:
        """Say whether a streamed message was written by the summarizer.

        Args:
            message_id: The streamed message's id.

        Returns:
            True if the message came from a summarization run seen so far.
        """
        if not message_id:
            return False
        return message_id.removeprefix(self.MESSAGE_ID_PREFIX) in self._summary_run_ids
