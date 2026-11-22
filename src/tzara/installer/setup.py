"""The whole of ``tzara setup`` and ``tzara uninstall``."""

from __future__ import annotations

import importlib.metadata
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from tzara.agent.models import ModelCatalog
from tzara.config import TzaraConfig
from tzara.installer.commands import SubprocessRunner
from tzara.installer.download import Downloader
from tzara.installer.errors import AppNotPublished, SetupError
from tzara.installer.macapp import AppInstaller
from tzara.installer.ollama import OllamaSetup
from tzara.installer.reporter import Reporter
from tzara.installer.service import ServiceInstaller
from tzara.server.daemon import DaemonError, DaemonManager, DaemonStatus


class OllamaPort(Protocol):
    """What setup needs from the Ollama helper."""

    def is_installed(self) -> bool:
        """Say whether Ollama is on this machine."""

    def install(self) -> None:
        """Install Ollama."""

    def ensure_running(self) -> None:
        """Start Ollama if needed and wait for it."""

    def has_model(self, name: str) -> bool:
        """Say whether a model is already downloaded."""

    def pull(self, name: str) -> None:
        """Download a model."""


class JobPort(Protocol):
    """One login job."""

    def install(self) -> None:
        """Install the job and start it."""

    def uninstall(self) -> bool:
        """Remove the job; say whether there was one."""


class ServicePort(Protocol):
    """What setup needs from the login-job helper."""

    def server_job(self) -> JobPort:
        """The job that runs the server."""

    def app_job(self, app_path: Path) -> JobPort:
        """The job that opens the app."""


class AppPort(Protocol):
    """What setup needs from the app installer."""

    @property
    def app_path(self) -> Path:
        """Where the app is, or will be."""

    def install(self) -> Path:
        """Install the app."""

    def open(self) -> None:
        """Open the app."""

    def uninstall(self) -> bool:
        """Remove the app; say whether there was one."""


class DaemonPort(Protocol):
    """What setup needs from the server manager."""

    def status(self) -> DaemonStatus:
        """Say whether the server is running."""

    def stop(self) -> bool:
        """Stop the server; say whether one was running."""


@dataclass(frozen=True)
class SetupOptions:
    """Which parts of setup to do. Everything is on by default.

    Attributes:
        model: Download the model if it is missing.
        service: Run Tzara's server at login, and now.
        app: Install and open the macOS app.
        open_at_login: Also open the app at login.
    """

    model: bool = True
    service: bool = True
    app: bool = True
    open_at_login: bool = False


class Installer:
    """Takes a fresh install of Tzara to a working one, and back.

    Every step first checks whether it is already done, so running setup again is safe and
    only does what is missing.
    """

    OLLAMA_PROVIDER = "ollama"

    def __init__(
        self,
        config: TzaraConfig,
        reporter: Reporter,
        ollama: OllamaPort,
        service: ServicePort,
        apps: AppPort,
        daemon: DaemonPort,
        platform: str = sys.platform,
        sleep: Callable[[float], object] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create an installer from its parts. Use :meth:`create` for the real ones.

        Args:
            config: Configuration.
            reporter: Where to report progress.
            ollama: Installs and starts Ollama, downloads the model.
            service: Sets up the login jobs.
            apps: Installs the macOS app.
            daemon: Starts, stops and inspects the server.
            platform: A ``sys.platform`` value. Login jobs and the app are macOS-only.
            sleep: Waits for a number of seconds. Replaced in tests.
            clock: Returns monotonic time in seconds. Replaced in tests.
        """
        self._config = config
        self._reporter = reporter
        self._ollama = ollama
        self._service = service
        self._apps = apps
        self._daemon = daemon
        self._platform = platform
        self._sleep = sleep
        self._clock = clock

    @classmethod
    def create(cls, config: TzaraConfig, reporter: Reporter | None = None) -> Installer:
        """Build an installer wired to the real system.

        Args:
            config: Configuration.
            reporter: Where to report progress. Defaults to the terminal.
        """
        reporter = reporter or Reporter()
        runner = SubprocessRunner()
        client = httpx.Client(timeout=httpx.Timeout(10.0, read=300.0))
        return cls(
            config,
            reporter,
            OllamaSetup(config.installer, runner, client, reporter),
            ServiceInstaller(config.installer, runner, os.environ),
            AppInstaller(
                config.installer,
                importlib.metadata.version("tzara"),
                runner,
                Downloader(client, reporter),
                reporter,
            ),
            DaemonManager(config),
        )

    def setup(self, options: SetupOptions) -> int:
        """Run setup.

        Returns:
            The process exit status: 0 if it worked, 1 if a step failed.
        """
        try:
            self._ensure_ollama()
            if options.model:
                self._ensure_model()
            if options.service:
                self._start_server()
            opened_app = self._install_app(options.open_at_login) if options.app else False
        except SetupError as error:
            self._reporter.fail(str(error))
            return 1
        self._finish(opened_app)
        return 0

    def uninstall(self) -> int:
        """Remove what setup added: the login jobs and the app.

        Ollama, the models, Tzara's logs and the command itself are left alone.

        Returns:
            The process exit status.
        """
        self._reporter.step("Removing Tzara's login jobs and app")
        removed = []
        if self._platform == "darwin":
            if self._service.server_job().uninstall():
                removed.append("the server's login job")
            if self._service.app_job(self._apps.app_path).uninstall():
                removed.append("the app's login job")
            if self._apps.uninstall():
                removed.append("the app")
        try:
            if self._daemon.stop():
                removed.append("the running server")
        except DaemonError as error:
            self._reporter.warn(str(error))
        self._reporter.ok(
            f"Removed {', '.join(removed)}" if removed else "There was nothing to remove"
        )
        self._reporter.note("Left in place: Ollama and its models, Tzara's logs and settings.")
        return 0

    # --- steps ----------------------------------------------------------------

    def _ensure_ollama(self) -> None:
        self._reporter.step("Checking Ollama")
        if self._ollama.is_installed():
            self._reporter.ok("Ollama is installed")
        else:
            self._reporter.note("Ollama is not installed; installing it")
            self._ollama.install()
            self._reporter.ok("Installed Ollama")
        self._ollama.ensure_running()
        self._reporter.ok("Ollama is running")

    def _ensure_model(self) -> None:
        model = self._config.model
        if ModelCatalog.provider_of(model) != self.OLLAMA_PROVIDER:
            self._reporter.step("Model")
            self._reporter.note(f"{model} is not a local model, so there is nothing to download.")
            return
        name = model.removeprefix(f"{self.OLLAMA_PROVIDER}:")
        self._reporter.step(f"Getting the model {name}")
        if self._ollama.has_model(name):
            self._reporter.ok(f"{name} is already downloaded")
            return
        self._reporter.note("This is a large download and can take several minutes.")
        self._ollama.pull(name)
        self._reporter.ok(f"Downloaded {name}")

    def _start_server(self) -> None:
        self._reporter.step("Starting Tzara's server")
        if self._platform != "darwin":
            self._reporter.note(
                "Start the server with 'tzara start'; login jobs are macOS-only for now."
            )
            return
        try:
            if self._daemon.status().running and self._daemon.stop():
                self._reporter.note("Replacing the server that was already running.")
        except DaemonError as error:
            raise SetupError(str(error)) from error
        self._service.server_job().install()
        self._wait_for_server()
        self._reporter.ok("The server runs at login, and is running now")

    def _wait_for_server(self) -> None:
        deadline = self._clock() + self._config.installer.wait_seconds
        while self._clock() < deadline:
            if self._daemon.status().running:
                return
            self._sleep(1.0)
        raise SetupError(
            "The server did not start. Its output is in ~/Library/Logs/tzara/launchd.log."
        )

    def _install_app(self, open_at_login: bool) -> bool:
        self._reporter.step("Installing the Tzara app")
        if self._platform != "darwin":
            self._reporter.note("The app is macOS-only, so it was skipped.")
            return False
        try:
            path = self._apps.install()
        except AppNotPublished as error:
            self._reporter.warn(str(error))
            return False
        self._reporter.ok(f"Installed {path}")
        if open_at_login:
            self._service.app_job(path).install()
            self._reporter.ok("It will open at login")
        self._apps.open()
        return True

    def _finish(self, opened_app: bool) -> None:
        self._reporter.step("Done")
        if opened_app:
            self._reporter.note("Tap Control+Option to open the chat box, anywhere.")
            self._reporter.note("The first time, macOS asks for Input Monitoring. Allow it:")
            self._reporter.note("that is how the app sees the hotkey.")
        self._reporter.note("Or chat in the terminal with: tzara")
