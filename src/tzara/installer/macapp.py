"""Installing the macOS app from a release."""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

from tzara.config import InstallerSettings
from tzara.installer.commands import CommandRunner
from tzara.installer.download import Downloader, DownloadError
from tzara.installer.errors import AppNotPublished, SetupError
from tzara.installer.reporter import Reporter


class AppInstaller:
    """Downloads ``Tzara.app`` from the release matching this version, checks it, installs it.

    The version is Tzara's own, so the app and the server it talks to always agree on the
    protocol. Apps fetched this way carry no quarantine flag, so macOS shows no
    "unidentified developer" warning.
    """

    PROCESS_NAME = "Tzara"

    def __init__(
        self,
        settings: InstallerSettings,
        version: str,
        runner: CommandRunner,
        downloader: Downloader,
        reporter: Reporter,
        platform: str = sys.platform,
    ) -> None:
        """Create the installer.

        Args:
            settings: Where releases are published and where the app goes.
            version: The release to fetch, e.g. ``0.1.0`` for the tag ``v0.1.0``.
            runner: Runs other programs.
            downloader: Fetches files.
            reporter: Where to report progress.
            platform: A ``sys.platform`` value. The app only exists for macOS.
        """
        self._settings = settings
        self._version = version
        self._runner = runner
        self._downloader = downloader
        self._reporter = reporter
        self._platform = platform

    @property
    def app_path(self) -> Path:
        """Where the app is, or will be, installed."""
        directory = self._settings.app_dir or Path.home() / "Applications"
        return directory / self._settings.app_name

    def install(self) -> Path:
        """Fetch, verify and install the app, replacing any earlier copy.

        Returns:
            Where the app now is.

        Raises:
            AppNotPublished: If there is no release for this version.
            SetupError: If this is not macOS, the download fails or is corrupt, or it
                cannot be unpacked.
        """
        if self._platform != "darwin":
            raise SetupError("The Tzara app is for macOS.")

        asset_url = self._settings.release_url.format(
            version=self._version, asset=self._settings.app_asset
        )
        with tempfile.TemporaryDirectory() as scratch:
            archive = Path(scratch) / self._settings.app_asset
            self._fetch(asset_url, archive)
            self._verify(asset_url, archive)
            self._replace_with(archive)
        self._runner.run(["xattr", "-dr", "com.apple.quarantine", str(self.app_path)])
        return self.app_path

    def open(self) -> None:
        """Launch the installed app."""
        self._runner.run(["open", str(self.app_path)])

    def uninstall(self) -> bool:
        """Quit the app and delete it.

        Returns:
            True if there was an app to remove.
        """
        self._runner.run(["pkill", "-x", self.PROCESS_NAME])
        if not self.app_path.exists():
            return False
        shutil.rmtree(self.app_path)
        return True

    def _fetch(self, url: str, archive: Path) -> None:
        """Download the app archive, explaining a missing release."""
        self._reporter.note(f"Downloading {self._settings.app_asset} for version {self._version}")
        try:
            self._downloader.save(url, archive, "Tzara.app")
        except DownloadError as error:
            if error.status == 404:
                raise AppNotPublished(
                    f"There is no app build for version {self._version} yet ({url}). "
                    "To get the app, build it yourself with 'make app'."
                ) from error
            raise

    def _verify(self, asset_url: str, archive: Path) -> None:
        """Check the archive against the checksum published beside it."""
        published = self._downloader.text(f"{asset_url}.sha256").split()
        expected = published[0].lower() if published else ""
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != expected:
            raise SetupError(
                "The downloaded app does not match its published checksum, so it was not installed."
            )

    def _replace_with(self, archive: Path) -> None:
        """Quit any running copy, delete it, and unpack the new one."""
        self._runner.run(["pkill", "-x", self.PROCESS_NAME])
        shutil.rmtree(self.app_path, ignore_errors=True)
        self.app_path.parent.mkdir(parents=True, exist_ok=True)
        # ditto keeps the executable bits and the code signature that zipfile would lose.
        result = self._runner.run(["ditto", "-xk", str(archive), str(self.app_path.parent)])
        if not result.succeeded:
            raise SetupError(f"Could not unpack the app: {result.stderr.strip()}")
