"""Prompt text sent to the model.

Kept apart from the code that uses it so wording can be tuned, tested, and
overridden without touching agent logic.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass(frozen=True)
class Prompts:
    """Every prompt and tool description Tzara sends to the model.

    DeepAgents ships long, tutorial-style defaults for its built-in tools. A
    local model evaluates the prompt at only a few hundred tokens per second,
    so the defaults are replaced with short equivalents. All built-in tools stay
    available.

    Attributes:
        system: Tzara's persona and behaviour rules. ``{name}`` stands for the assistant's name.
        assistant_name: What the assistant calls itself. The macOS app lets the user change it.
        base: Replaces DeepAgents' coding-agent base prompt, which conflicts
            with Tzara's conversational persona. Placed after ``system``.
        todo_system: System prompt section describing the todo tool.
        todo_tool: Description of the ``write_todos`` tool.
        tool_descriptions: Replacement descriptions keyed by built-in tool name.
    """

    DEFAULT_TOOL_DESCRIPTIONS: ClassVar[Mapping[str, str]] = {
        "ls": "List the files in a directory. Takes an absolute path.",
        "read_file": (
            "Read a file. Takes an absolute path and optional offset and limit "
            "(line numbers) for large files. Returns lines prefixed with line numbers."
        ),
        "write_file": (
            "Create a new file with the given content. Takes an absolute path and the content."
        ),
        "edit_file": (
            "Replace exact text in an existing file. Takes an absolute path, the "
            "old_string to find, and the new_string to put in its place. Read the "
            "file first."
        ),
        "glob": "Find files matching a glob pattern such as '**/*.py'.",
        "grep": "Search file contents for a text pattern. Optionally restrict by path or glob.",
        "task": (
            "Delegate a self-contained subtask to a subagent that works in its own "
            "context and returns one final report. Takes a description of the "
            "task and the subagent_type. Use it for complex or independent work, "
            "not for simple questions."
        ),
    }

    NAME_PLACEHOLDER: ClassVar[str] = "{name}"

    system: str = (
        "You are {name}, a natural conversational AI assistant.\n\n"
        "Behavior rules:\n"
        "- Speak naturally and concisely.\n"
        "- Never mention tools, functions, APIs, or internal reasoning.\n"
        "- Never explain why you did or did not take an action.\n"
        "- Never apologize for internal behavior or mistakes.\n"
        "- Respond directly to the user's message.\n\n"
        "Tool usage rules:\n"
        "- Use tools silently when and only when the user explicitly asks for "
        "information that requires them.\n"
        "- If no tool is required, answer directly in plain natural language.\n\n"
        "If the user asks about your name, identity, or general topics, answer "
        "directly without meta commentary."
    )
    assistant_name: str = "Tzara"
    base: str = "You are a helpful assistant."
    todo_system: str = (
        "## `write_todos`\n\n"
        "For complex multi-step tasks, track your plan with `write_todos` and keep "
        "statuses current. Skip it for simple requests and conversation. Give the "
        "user your answer as text after your last `write_todos` call."
    )
    todo_tool: str = (
        "Create or update a todo list for a complex multi-step task. Takes a list "
        "of todos, each with content and a status (pending, in_progress or "
        "completed). Don't use it for simple requests."
    )
    tool_descriptions: Mapping[str, str] = field(
        default_factory=lambda: dict(Prompts.DEFAULT_TOOL_DESCRIPTIONS)
    )

    def rendered_system(self) -> str:
        """Return the system prompt with the assistant's name filled in."""
        return self.system.replace(self.NAME_PLACEHOLDER, self.assistant_name)
