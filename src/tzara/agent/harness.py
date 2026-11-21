"""Configuration of the DeepAgents harness: prompts, tool text, middleware."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from deepagents import HarnessProfile, register_harness_profile
from langchain.agents.middleware import TodoListMiddleware

from tzara.prompts import Prompts


class ConciseTodoListMiddleware(TodoListMiddleware):
    """``TodoListMiddleware`` with short prompt text.

    This is a separate class so that its name differs from DeepAgents' own
    ``TodoListMiddleware``. DeepAgents' instance is excluded by name (see
    :class:`HarnessConfigurator`), which would otherwise remove this one too.
    """

    def __init__(self, system_prompt: str, tool_description: str) -> None:
        """Create the middleware.

        Args:
            system_prompt: System prompt section describing the todo tool.
            tool_description: Description of the ``write_todos`` tool.
        """
        super().__init__(system_prompt=system_prompt, tool_description=tool_description)


class HarnessConfigurator:
    """Applies Tzara's prompts to the DeepAgents harness.

    Every built-in DeepAgents tool stays enabled. Only the text sent to the
    model changes: a short base prompt, short tool descriptions, and a concise
    todo-list middleware.
    """

    EXCLUDED_MIDDLEWARE = frozenset({"TodoListMiddleware"})

    def __init__(self, prompts: Prompts, providers: Sequence[str]) -> None:
        """Create a configurator.

        Args:
            prompts: Prompts and tool descriptions to apply.
            providers: Providers to register the harness profile for.
        """
        self._prompts = prompts
        self._providers = tuple(providers)

    def install_profiles(self) -> None:
        """Register the harness profile for each provider.

        The DeepAgents registry is process-global and registrations are
        additive, so calling this more than once is harmless.
        """
        profile = HarnessProfile(
            base_system_prompt=self._prompts.base,
            tool_description_overrides=self._prompts.tool_descriptions,
            excluded_middleware=self.EXCLUDED_MIDDLEWARE,
        )
        for provider in self._providers:
            register_harness_profile(provider, profile)

    def middleware(self) -> list[Any]:
        """Return the extra middleware to pass to ``create_deep_agent``.

        Returns:
            Replacements for the middleware excluded by the profile.
        """
        return [
            ConciseTodoListMiddleware(
                system_prompt=self._prompts.todo_system,
                tool_description=self._prompts.todo_tool,
            )
        ]
