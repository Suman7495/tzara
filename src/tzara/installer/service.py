"""Login jobs: starting Tzara's server (and, optionally, its app) when you log in."""

from __future__ import annotations

import os
import plistlib
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from tzara.config import InstallerSettings
from tzara.installer.commands import CommandRunner
from tzara.installer.errors import SetupError
from tzara.logs import LogLocation


class LaunchAgent:
    """One macOS launchd job that runs at login.

    launchd starts it at login and, if it dies unexpectedly (a crash, being killed, an error
    exit), starts it again. It does not restart a job that exits cleanly, so ``tzara stop``,
    which asks the server to shut down, still stops it.
    """

    THROTTLE_SECONDS = 10

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        label: str,
        arguments: Sequence[str],
        directory: Path,
        runner: CommandRunner,
        log_path: Path | None = None,
        environment: Mapping[str, str] | None = None,
        restart_on_crash: bool = False,
        uid: Callable[[], int] = os.getuid,
    ) -> None:
        """Describe a job. Nothing happens until :meth:`install`.

        Args:
            label: launchd's name for the job, e.g. ``ai.telekinesis.tzara.server``.
            arguments: The program and its arguments.
            directory: Where the job's ``.plist`` file goes.
            runner: Runs ``launchctl``.
            log_path: File for what the job prints. ``None`` discards it.
            environment: Environment variables to give the job.
            restart_on_crash: Whether launchd should restart it when it dies unexpectedly.
            uid: Returns the user id, which names launchd's domain. Replaced in tests.
        """
        self._label = label
        self._arguments = list(arguments)
        self._plist_path = directory / f"{label}.plist"
        self._runner = runner
        self._log_path = log_path
        self._environment = dict(environment or {})
        self._restart_on_crash = restart_on_crash
        self._uid = uid

    @property
    def plist_path(self) -> Path:
        """Where the job's definition is written."""
        return self._plist_path

    def render(self) -> dict[str, Any]:
        """Return the job's definition, as it is written to the ``.plist`` file."""
        definition: dict[str, Any] = {
            "Label": self._label,
            "ProgramArguments": self._arguments,
            "RunAtLoad": True,
            "ThrottleInterval": self.THROTTLE_SECONDS,
        }
        if self._restart_on_crash:
            # Not "Crashed": launchd counts only crash signals (SIGSEGV...) as crashes and treats
            # SIGKILL as deliberate. SuccessfulExit=false restarts on any unclean exit and leaves
            # a clean exit alone.
            definition["KeepAlive"] = {"SuccessfulExit": False}
        if self._environment:
            definition["EnvironmentVariables"] = self._environment
        if self._log_path is not None:
            definition["StandardOutPath"] = str(self._log_path)
            definition["StandardErrorPath"] = str(self._log_path)
        return definition

    def install(self) -> None:
        """Write the job's definition and start it now, replacing any earlier version.

        Raises:
            SetupError: If launchd refuses the job.
        """
        self._plist_path.parent.mkdir(parents=True, exist_ok=True)
        if self._log_path is not None:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._plist_path.open("wb") as handle:
            plistlib.dump(self.render(), handle)

        self._runner.run(
            ["launchctl", "bootout", f"{self._domain}/{self._label}"]
        )  # not loaded yet is fine
        result = self._runner.run(["launchctl", "bootstrap", self._domain, str(self._plist_path)])
        if not result.succeeded:
            raise SetupError(f"launchd would not load {self._label}: {result.stderr.strip()}")

    def uninstall(self) -> bool:
        """Stop the job and delete its definition.

        Returns:
            True if there was a definition to remove.
        """
        self._runner.run(["launchctl", "bootout", f"{self._domain}/{self._label}"])
        existed = self._plist_path.exists()
        self._plist_path.unlink(missing_ok=True)
        return existed

    @property
    def _domain(self) -> str:
        return f"gui/{self._uid()}"


class ServiceInstaller:
    """Sets up the login jobs Tzara uses."""

    ENVIRONMENT_PREFIX = "TZARA_"

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        settings: InstallerSettings,
        runner: CommandRunner,
        environment: Mapping[str, str],
        python: str = sys.executable,
        launch_agents_dir: Path | None = None,
    ) -> None:
        """Create the installer.

        Args:
            settings: Labels and locations.
            runner: Runs ``launchctl``.
            environment: The current environment. Any ``TZARA_*`` settings in it are kept for
                the server, so a port or model chosen at setup stays chosen.
            python: The interpreter that runs the server. Using this exact one means the job
                does not depend on ``PATH``.
            launch_agents_dir: Where job definitions go. Defaults to ``~/Library/LaunchAgents``.
        """
        self._settings = settings
        self._runner = runner
        self._python = python
        self._environment = {
            name: value
            for name, value in environment.items()
            if name.startswith(self.ENVIRONMENT_PREFIX)
        }
        self._directory = (
            launch_agents_dir
            or settings.launch_agents_dir
            or Path.home() / "Library" / "LaunchAgents"
        )

    def server_job(self) -> LaunchAgent:
        """The job that runs ``tzara serve`` at login."""
        return LaunchAgent(
            self._settings.service_label,
            [self._python, "-m", "tzara", "serve"],
            self._directory,
            self._runner,
            log_path=LogLocation.default_file().with_name("launchd.log"),
            environment=self._environment,
            restart_on_crash=True,
        )

    def app_job(self, app_path: Path) -> LaunchAgent:
        """The job that opens the app at login."""
        return LaunchAgent(
            self._settings.login_label,
            ["/usr/bin/open", "-a", str(app_path), "--args", "--background"],
            self._directory,
            self._runner,
        )
