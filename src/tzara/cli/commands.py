"""Slash commands for the Tzara CLI."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from rich.console import Console
from rich.table import Table

from tzara.agent.models import ModelCatalog
from tzara.config import CLISettings


class ExitRequested(Exception):
    """Raised by a command to end the chat session."""


class SessionController(Protocol):
    """What the built-in commands need from the CLI."""

    @property
    def current_model(self) -> str:
        """The model currently in use, as ``provider:model-name``."""

    def switch_model(self, model: str) -> None:
        """Switch to ``model`` and report the outcome to the user."""

    def new_conversation(self) -> None:
        """Forget the conversation so far and start a new one."""

    def show_memory(self) -> None:
        """Print what Tzara remembers about the user."""

    def show_apps(self) -> None:
        """Print the apps connected through MCP."""

    def erase_memory(self) -> None:
        """Forget every fact and every stored conversation."""


@dataclass(frozen=True)
class Command:
    """A registered slash command.

    Attributes:
        name: Name including the leading slash, e.g. ``/help``.
        description: One-line summary shown by ``/help``.
        handler: Called with the arguments that followed the command name.
    """

    name: str
    description: str
    handler: Callable[[Sequence[str]], None]


class CommandRegistry:
    """Registers slash commands and runs them from input lines."""

    def __init__(self, console: Console) -> None:
        """Create an empty registry.

        Args:
            console: Console used to report unknown commands.
        """
        self._console = console
        self._commands: dict[str, Command] = {}

    def register(
        self, name: str, description: str, handler: Callable[[Sequence[str]], None]
    ) -> None:
        """Add a command, replacing any command with the same name.

        Args:
            name: Command name including the leading slash.
            description: One-line summary shown by ``/help``.
            handler: Called with the arguments that followed the command name.
        """
        self._commands[name] = Command(name, description, handler)

    def execute(self, line: str) -> bool:
        """Run ``line`` if it is a slash command.

        Args:
            line: A line of user input.

        Returns:
            True if the line was a command (known or not), False if it is an
            ordinary message.
        """
        if not line.startswith("/"):
            return False

        name, *arguments = line.split()
        command = self._commands.get(name)
        if command is None:
            self._console.print(f"[red]Unknown command:[/red] {name}")
        else:
            command.handler(arguments)
        return True

    def help_text(self) -> str:
        """Return the list of commands, sorted by name."""
        lines = ["Commands:"]
        lines.extend(
            f"  {command.name:<20} {command.description}"
            for command in sorted(self._commands.values(), key=lambda c: c.name)
        )
        return "\n".join(lines)


class BuiltinCommands:
    """Tzara's built-in slash commands."""

    def __init__(
        self,
        registry: CommandRegistry,
        console: Console,
        controller: SessionController,
        config: CLISettings | None = None,
    ) -> None:
        """Register the built-in commands.

        Args:
            registry: Registry to add the commands to.
            console: Console the commands print to.
            controller: Provides the current model and changes the session.
            config: Text the commands print. Defaults to :class:`CLISettings`.
        """
        self._registry = registry
        self._console = console
        self._controller = controller
        self._config = config or CLISettings()

        registry.register("/help", "Show available commands.", self.help)
        registry.register("/list-models", "List available models.", self.list_models)
        registry.register("/current-model", "Show the current model.", self.current_model)
        registry.register("/set-model", "Switch model: /set-model <model>", self.set_model)
        registry.register("/new", "Forget the conversation and start a new one.", self.new)
        registry.register("/memory", "Show what Tzara remembers about you.", self.memory)
        registry.register("/apps", "Show the connected apps and what Tzara can use.", self.apps)
        registry.register(
            "/erase-memory", "Forget everything: /erase-memory yes", self.erase_memory
        )
        registry.register("/exit", "Exit Tzara.", self.exit)

    def help(self, _arguments: Sequence[str]) -> None:
        """Print the available commands."""
        self._console.print("\n" + self._registry.help_text() + "\n", markup=False)

    def list_models(self, _arguments: Sequence[str]) -> None:
        """Print the known models, one table per provider."""
        self._console.print("\nAvailable Models:\n")
        for provider, models in ModelCatalog.MODELS.items():
            table = Table(title=provider.upper(), show_header=False)
            for model_id, description in models.items():
                table.add_row(f"[bold]{model_id}[/bold]", description)
            self._console.print(table)
            self._console.print()

    def current_model(self, _arguments: Sequence[str]) -> None:
        """Print the model currently in use."""
        self._console.print(f"\nCurrent model: {self._controller.current_model}\n", markup=False)

    def set_model(self, arguments: Sequence[str]) -> None:
        """Switch to the model named in ``arguments``."""
        model = " ".join(arguments)
        if not model:
            self._console.print("Usage: /set-model <model>", markup=False)
            return
        if not ModelCatalog.provider_of(model):
            self._console.print(
                "Model must be written as provider:model-name, e.g. ollama:qwen3:8b",
                markup=False,
            )
            return
        self._controller.switch_model(model)

    def new(self, _arguments: Sequence[str]) -> None:
        """Start a new conversation."""
        self._controller.new_conversation()

    def apps(self, _arguments: Sequence[str]) -> None:
        """Handle ``/apps``."""
        self._controller.show_apps()

    def memory(self, _arguments: Sequence[str]) -> None:
        """Show what is remembered about the user."""
        self._controller.show_memory()

    def erase_memory(self, arguments: Sequence[str]) -> None:
        """Forget everything, but only when asked in so many words."""
        if [argument.lower() for argument in arguments] != ["yes"]:
            self._console.print(self._config.erase_usage, markup=False)
            return
        self._controller.erase_memory()

    def exit(self, _arguments: Sequence[str]) -> None:
        """End the chat session."""
        raise ExitRequested
