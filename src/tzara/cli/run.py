"""Interactive command-line chat with Tzara."""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from tzara.agent import Tzara
from tzara.agent.memory import ConversationMemory
from tzara.cli.commands import BuiltinCommands, CommandRegistry, ExitRequested
from tzara.config import TzaraConfig
from tzara.memory import PersonalMemory

logger = logging.getLogger(__name__)


class TzaraCLI:
    """Interactive chat loop.

    Owns the agent, the console, the conversation memory and the slash
    commands. Ctrl+C during a reply cancels just that reply; at the prompt it
    ends the session.
    """

    def __init__(
        self,
        config: TzaraConfig | None = None,
        console: Console | None = None,
        agent_factory: Callable[..., Tzara] = Tzara,
        memory: ConversationMemory | None = None,
        *,
        log_path: Path | None = None,
        personal: PersonalMemory | None = None,
    ) -> None:
        """Create the CLI.

        Args:
            config: Configuration. Defaults to :meth:`TzaraConfig.from_env`.
            console: Console to read from and print to. Defaults to a new one.
            agent_factory: Builds an agent from ``model``, ``config`` and
                ``memory`` keyword arguments. Replaced in tests.
            memory: Conversation history shared by every agent this CLI creates,
                so it survives ``/set-model``. Defaults to a new, empty one.
            log_path: Log file, mentioned in error messages. ``None`` if
                logging to a file is unavailable.
            personal: What is remembered about the user, handed to every agent. ``None``
                means nothing is kept.
        """
        self._config = config or TzaraConfig.from_env()
        self._console = console or Console()
        self._agent_factory = agent_factory
        self._memory = memory or ConversationMemory()
        self._log_path = log_path
        self._personal = personal
        self._agent: Tzara | None = None
        self._registry = CommandRegistry(self._console)
        BuiltinCommands(self._registry, self._console, self, self._config.cli)

    @property
    def current_model(self) -> str:
        """The model currently in use, as ``provider:model-name``."""
        return self._agent.model if self._agent else self._config.model

    def run(self) -> None:
        """Load the model, then chat until the user exits."""
        if not self._start():
            return
        self._console.print(self._config.cli.banner + "\n", style="bold green", markup=False)
        self._chat_loop()

    def switch_model(self, model: str) -> None:
        """Switch to another model, keeping the current one if that fails.

        The conversation carries over to the new model.

        Args:
            model: New model as ``provider:model-name``.
        """
        cli = self._config.cli
        try:
            candidate = self._agent_factory(
                model=model, config=self._config, memory=self._memory, personal=self._personal
            )
            self._warm_up(candidate, cli.switching_status)
        except KeyboardInterrupt:
            logger.info("switch to %s cancelled", model)
            self._console.print("\nCancelled.", markup=False)
        except Exception as error:
            self._report_error(f"Could not switch to {model}", error)
        else:
            self._agent = candidate
            logger.info("switched to model %s", model)
            self._console.print(f"Switched → {model}", markup=False)

    def new_conversation(self) -> None:
        """Forget the conversation so far and start a new one."""
        self._memory.reset()
        logger.info("new conversation started")
        self._console.print(self._config.cli.new_conversation, markup=False)

    def show_memory(self) -> None:
        """Print what is remembered about the user."""
        cli = self._config.cli
        if self._personal is None:
            self._console.print(cli.memory_off, markup=False)
            return
        facts = self._personal.facts.all()
        if not facts:
            self._console.print(cli.memory_empty, markup=False)
            return
        self._console.print(
            "\n" + cli.memory_header.format(count=self._personal.log.count()), markup=False
        )
        for fact in facts:
            self._console.print(f"  - {fact.text}", markup=False)
        self._console.print()

    def erase_memory(self) -> None:
        """Forget every fact and every stored conversation, and this conversation too."""
        self._memory.reset()
        if self._personal is not None:
            self._personal.erase()
        logger.info("memory erased")
        self._console.print(self._config.cli.memory_erased, markup=False)

    def _start(self) -> bool:
        """Create the agent and load its model before accepting input.

        A cold start (loading the model and evaluating the prompt) takes about
        ten seconds. Doing it here means the first message is fast instead of
        queueing behind it.

        Returns:
            False if the session should end instead of starting.
        """
        cli = self._config.cli
        try:
            self._agent = self._agent_factory(
                model=self._config.model,
                config=self._config,
                memory=self._memory,
                personal=self._personal,
            )
        except Exception as error:
            self._report_error("Error", error)
            return False

        try:
            self._warm_up(self._agent, cli.loading_status)
        except KeyboardInterrupt:
            self._console.print("\n" + cli.goodbye, markup=False)
            return False
        except Exception as error:
            self._report_error(cli.load_failed, error, style="yellow")
        return True

    def _warm_up(self, agent: Tzara, status_text: str) -> None:
        """Load ``agent``'s model behind a spinner; Ctrl+C cancels the load."""
        with (
            self._sigint_cancels(agent),
            self._console.status(status_text, spinner=self._config.cli.spinner),
        ):
            agent.warm_up()

    def _chat_loop(self) -> None:
        """Read messages and commands until the user exits."""
        cli = self._config.cli
        while True:
            try:
                line = self._console.input(cli.prompt).strip()
                if not line or self._registry.execute(line):
                    continue
                self._respond(line)
            except (KeyboardInterrupt, EOFError, ExitRequested):
                # EOFError is Ctrl+D or closed stdin. It must end the loop: left to
                # the generic handler below it would repeat forever.
                self._console.print("\n" + cli.goodbye, markup=False)
                return
            except Exception as error:
                self._report_error("Error", error)

    def _respond(self, text: str) -> None:
        """Stream the agent's reply to ``text``; Ctrl+C cancels the reply."""
        cli = self._config.cli
        agent = self._agent
        assert agent is not None, "_respond called before the agent was created"

        self._console.print("\n" + cli.reply_label, end="")
        spinner_stopped = False
        try:
            with (
                self._sigint_cancels(agent),
                self._console.status(cli.thinking_status, spinner=cli.spinner) as status,
            ):
                for token in agent.handle(text):
                    if not spinner_stopped:
                        status.stop()
                        spinner_stopped = True
                    # Model output is plain text: brackets in it are not markup.
                    self._console.print(token, end="", markup=False, highlight=False)
        except KeyboardInterrupt:
            agent.interrupt()
            self._console.print(f"\n[dim]{escape(cli.interrupted)}[/dim]\n")
            return
        self._console.print()

    def _report_error(self, label: str, error: Exception, style: str = "red") -> None:
        """Log ``error`` with its traceback and show a short message in the chat.

        Args:
            label: What failed, e.g. ``Error``.
            error: The exception.
            style: Rich colour for the label.
        """
        logger.error("%s", label, exc_info=error)
        self._console.print(f"\n[{style}]{escape(label)}:[/{style}] {escape(str(error))}")
        if self._log_path is not None:
            hint = self._config.cli.log_hint.format(path=self._log_path)
            self._console.print(f"[dim]{escape(hint)}[/dim]")
        self._console.print()

    @staticmethod
    @contextmanager
    def _sigint_cancels(agent: Tzara) -> Iterator[None]:
        """Make Ctrl+C cancel the agent's in-flight request, then raise.

        Raising alone is not enough: LangGraph waits for its worker thread while
        unwinding, which blocks until the model finishes generating. Cancelling
        in the signal handler closes the connection first, so the unwind is quick.

        Does nothing outside the main thread, where signal handlers cannot be set.
        """
        if threading.current_thread() is not threading.main_thread():
            yield
            return

        def handler(_signum: int, _frame: object) -> None:
            agent.cancel()
            raise KeyboardInterrupt

        previous = signal.signal(signal.SIGINT, handler)
        try:
            yield
        finally:
            signal.signal(signal.SIGINT, previous)
