"""The ``tzara`` command: chat, or run and manage the background server."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import ClassVar

from tzara.config import ConfigError, TzaraConfig
from tzara.logs import LogConfigurator
from tzara.server.daemon import DaemonError, DaemonManager, DaemonStatus

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BAD_CONFIG = 2
EXIT_NOT_RUNNING = 3


class Launcher:
    """Runs one ``tzara`` subcommand.

    ``tzara`` with no subcommand opens the chat. The others control the server
    that the overlay talks to: ``serve`` runs it in this terminal, and ``start``,
    ``stop`` and ``status`` manage a copy running in the background.
    """

    DESCRIPTION = "Tzara, your personal AI assistant."
    COMMANDS: ClassVar[dict[str, str]] = {
        "serve": "run the server in this terminal",
        "start": "start the server in the background",
        "stop": "stop the background server",
        "status": "show whether the background server is running",
        "setup": "set up Ollama, the model, the server and the macOS app",
        "uninstall": "remove the server's login job and the macOS app",
    }

    def __init__(
        self,
        config: TzaraConfig,
        log_path: Path | None = None,
        out: Callable[[str], None] = print,
        err: Callable[[str], None] | None = None,
        arguments: argparse.Namespace | None = None,
    ) -> None:
        """Create a launcher.

        Args:
            config: Configuration.
            log_path: The log file, shown when something fails. ``None`` if
                logging to a file is unavailable.
            out: Prints a line of normal output.
            err: Prints a line of error output. Defaults to stderr.
            arguments: The parsed command line, for the options of ``setup``.
        """
        self._config = config
        self._log_path = log_path
        self._out = out
        self._err = err or (lambda line: print(line, file=sys.stderr))
        self._arguments = arguments or argparse.Namespace()

    @classmethod
    def main(cls, argv: Sequence[str] | None = None) -> int:
        """Run the ``tzara`` command. This is the console-script entry point.

        Args:
            argv: Command-line arguments. Defaults to ``sys.argv[1:]``.

        Returns:
            The process exit status.
        """
        arguments = cls._parse_arguments(argv)
        command = arguments.command or "chat"
        try:
            config = TzaraConfig.from_env()
        except ConfigError as error:
            print(f"tzara: {error}", file=sys.stderr)
            return EXIT_BAD_CONFIG

        log_path = LogConfigurator(config.log).install()
        logger.info("tzara %s: model=%s, num_ctx=%d", command, config.model, config.ollama.num_ctx)
        return getattr(cls(config, log_path, arguments=arguments), command)()

    def chat(self) -> int:
        """Open the interactive chat."""
        # Imported here so that `tzara status` does not pay for loading the agent.
        from tzara.cli.run import TzaraCLI  # pylint: disable=import-outside-toplevel

        TzaraCLI(config=self._config, log_path=self._log_path).run()
        return EXIT_OK

    def serve(self) -> int:
        """Run the server in this terminal until it is stopped."""
        from tzara.server.app import TzaraServer  # pylint: disable=import-outside-toplevel

        TzaraServer.serve(self._config)
        return EXIT_OK

    def start(self) -> int:
        """Start the server in the background."""
        try:
            status = DaemonManager(self._config).start()
        except DaemonError as error:
            self._err(f"tzara: {error}")
            return EXIT_FAILED
        self._report(status)
        return EXIT_OK

    def stop(self) -> int:
        """Stop the background server."""
        try:
            stopped = DaemonManager(self._config).stop()
        except DaemonError as error:
            self._err(f"tzara: {error}")
            return EXIT_FAILED
        self._out("Stopped the Tzara server." if stopped else "The Tzara server is not running.")
        return EXIT_OK

    def setup(self) -> int:
        """Set up Ollama, the model, the server and the macOS app."""
        from tzara.installer.setup import (  # pylint: disable=import-outside-toplevel
            Installer,
            SetupOptions,
        )

        options = SetupOptions(
            model=not getattr(self._arguments, "no_model", False),
            service=not getattr(self._arguments, "no_service", False),
            app=not getattr(self._arguments, "no_app", False),
            open_at_login=getattr(self._arguments, "open_at_login", False),
        )
        return Installer.create(self._config).setup(options)

    def uninstall(self) -> int:
        """Remove the server's login job and the macOS app."""
        from tzara.installer.setup import Installer  # pylint: disable=import-outside-toplevel

        return Installer.create(self._config).uninstall()

    def status(self) -> int:
        """Show whether the background server is running."""
        status = DaemonManager(self._config).status()
        self._report(status)
        return EXIT_OK if status.running else EXIT_NOT_RUNNING

    def _report(self, status: DaemonStatus) -> None:
        """Print a description of the server's state."""
        if not status.running or status.info is None:
            self._out("The Tzara server is not running.")
            return
        self._out(f"The Tzara server is running on {status.info.url} (pid {status.info.pid}).")
        if status.model:
            self._out(f"Model: {status.model} ({status.state}).")
        if status.error:
            self._out(f"Error: {status.error}")
        if status.state == "failed" and self._log_path is not None:
            self._out(f"Details are in the log: {self._log_path}")

    @classmethod
    def _parse_arguments(cls, argv: Sequence[str] | None) -> argparse.Namespace:
        """Parse the command line, exiting with usage help on a mistake."""
        parser = argparse.ArgumentParser(prog="tzara", description=cls.DESCRIPTION)
        subcommands = parser.add_subparsers(dest="command", title="commands", metavar="COMMAND")
        for name, summary in cls.COMMANDS.items():
            subparser = subcommands.add_parser(name, help=summary, description=summary)
            if name == "setup":
                cls._add_setup_options(subparser)
        return parser.parse_args(argv)

    @staticmethod
    def _add_setup_options(parser: argparse.ArgumentParser) -> None:
        """Add the options that choose which parts of setup to do."""
        parser.add_argument("--no-model", action="store_true", help="do not download the model")
        parser.add_argument(
            "--no-service", action="store_true", help="do not run the server at login"
        )
        parser.add_argument("--no-app", action="store_true", help="do not install the macOS app")
        parser.add_argument(
            "--open-at-login", action="store_true", help="also open the app at login"
        )
