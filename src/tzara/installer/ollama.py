"""Getting Ollama, and the model, ready."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from tzara.config import InstallerSettings
from tzara.installer.commands import CommandRunner
from tzara.installer.download import Downloader
from tzara.installer.errors import SetupError
from tzara.installer.reporter import Reporter


class OllamaSetup:
    """Checks for Ollama, installs and starts it if needed, and downloads a model."""

    POLL_SECONDS = 1.0
    INSTALL_ELSEWHERE = (
        "Install Ollama from https://ollama.com/download, then run 'tzara setup' again."
    )

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        settings: InstallerSettings,
        runner: CommandRunner,
        client: httpx.Client,
        reporter: Reporter,
        platform: str = sys.platform,
        sleep: Callable[[float], object] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the helper.

        Args:
            settings: Addresses and timeouts.
            runner: Runs other programs.
            client: HTTP client for Ollama's API and the download.
            reporter: Where to report progress.
            platform: A ``sys.platform`` value. Only macOS can install Ollama here.
            sleep: Waits for a number of seconds. Replaced in tests.
            clock: Returns monotonic time in seconds. Replaced in tests.
        """
        self._settings = settings
        self._runner = runner
        self._client = client
        self._reporter = reporter
        self._platform = platform
        self._sleep = sleep
        self._clock = clock
        self._downloader = Downloader(client, reporter)

    def is_installed(self) -> bool:
        """Say whether Ollama is on this machine, as the app or the command."""
        return shutil.which("ollama") is not None or self._settings.ollama_app.exists()

    def is_running(self) -> bool:
        """Say whether Ollama's server is answering."""
        try:
            response = self._client.get(f"{self._settings.ollama_url}/api/version")
        except httpx.HTTPError:
            return False
        return response.status_code == httpx.codes.OK

    def install(self) -> None:
        """Install Ollama: with Homebrew if there is one, otherwise from the official download.

        Raises:
            SetupError: If it cannot be installed here, or the install fails.
        """
        if self._platform != "darwin":
            raise SetupError(self.INSTALL_ELSEWHERE)
        if (
            shutil.which("brew")
            and self._runner.run(["brew", "install", "--cask", "ollama"]).succeeded
        ):
            return
        self._reporter.note("Downloading Ollama from ollama.com")
        destination = self._settings.ollama_app.parent
        with tempfile.TemporaryDirectory() as scratch:
            archive = Path(scratch) / "Ollama-darwin.zip"
            self._downloader.save(self._settings.ollama_download_url, archive, "Ollama")
            result = self._runner.run(["ditto", "-xk", str(archive), str(destination)])
        if not result.succeeded:
            raise SetupError(f"Could not unpack Ollama into {destination}: {result.stderr.strip()}")

    def ensure_running(self) -> None:
        """Start Ollama if it is not running, and wait until it answers.

        Raises:
            SetupError: If it cannot be started here, or does not come up in time.
        """
        if self.is_running():
            return
        if self._platform != "darwin":
            raise SetupError(
                "Ollama is not running. Start it with 'ollama serve', then run 'tzara setup' again."
            )
        self._runner.run(["open", "-a", "Ollama"])
        deadline = self._clock() + self._settings.wait_seconds
        while self._clock() < deadline:
            if self.is_running():
                return
            self._sleep(self.POLL_SECONDS)
        raise SetupError(
            "Ollama did not start. Open the Ollama app once, then run 'tzara setup' again."
        )

    def has_model(self, name: str) -> bool:
        """Say whether ``name`` has already been downloaded.

        Raises:
            SetupError: If Ollama cannot be reached.
        """
        try:
            response = self._client.get(f"{self._settings.ollama_url}/api/tags")
            models = response.json().get("models", [])
        except (httpx.HTTPError, ValueError) as error:
            raise SetupError(f"Could not ask Ollama which models it has: {error}") from error
        known = {model.get("name") for model in models}
        return name in known or f"{name}:latest" in known

    def pull(self, name: str) -> None:
        """Download ``name`` through Ollama, showing progress.

        Raises:
            SetupError: If the download fails or Ollama reports an error.
        """
        label = f"downloading {name}"
        try:
            with self._client.stream(
                "POST",
                f"{self._settings.ollama_url}/api/pull",
                json={"model": name, "stream": True},
                timeout=httpx.Timeout(10.0, read=None),
            ) as response:
                if response.status_code != httpx.codes.OK:
                    raise SetupError(
                        f"Ollama refused to download {name} (HTTP {response.status_code})."
                    )
                for line in response.iter_lines():
                    self._handle_pull_line(line, name, label)
        except httpx.HTTPError as error:
            raise SetupError(f"The download of {name} was interrupted: {error}") from error

    def _handle_pull_line(self, line: str, name: str, label: str) -> None:
        """Act on one line of Ollama's streamed download status."""
        if not line.strip():
            return
        status = json.loads(line)
        if "error" in status:
            raise SetupError(f"Ollama could not download {name}: {status['error']}")
        total, completed = status.get("total"), status.get("completed", 0)
        if total:
            self._reporter.progress(label, completed / total)
