"""Tools that act on the user's Mac: open apps, sites and files, control media.

These only launch things and press play; none of them runs a command the model
wrote. Opening is reversible and harmless, which is why they need no
confirmation. Anything that sends, deletes or shuts down belongs in a later,
confirmed tool.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import ClassVar, Literal, Protocol
from urllib.parse import urlsplit

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr

from tzara.config import SystemSettings, TzaraConfig

logger = logging.getLogger(__name__)


class SystemActionError(Exception):
    """Raised when an action on the machine cannot be done; the message is for the model."""


class CommandRunner(Protocol):
    """Runs a program and returns what it printed."""

    def run(self, arguments: list[str]) -> str:
        """Run ``arguments`` (program first) and return standard output.

        Raises:
            SystemActionError: If the program fails or takes too long.
        """


class SubprocessRunner:
    """Runs programs without a shell, with a time limit."""

    def __init__(self, timeout: float) -> None:
        """Create a runner.

        Args:
            timeout: Seconds a program may run.
        """
        self._timeout = timeout

    def run(self, arguments: list[str]) -> str:
        """Run ``arguments`` and return standard output.

        Raises:
            SystemActionError: If the program is missing, fails or times out.
        """
        try:
            done = subprocess.run(
                arguments,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except FileNotFoundError:
            raise SystemActionError(f"{arguments[0]} is not available on this machine.") from None
        except subprocess.TimeoutExpired:
            raise SystemActionError("That took too long and was stopped.") from None
        if done.returncode != 0:
            detail = (done.stderr or done.stdout).strip().splitlines()
            raise SystemActionError(detail[-1] if detail else f"{arguments[0]} failed.")
        return done.stdout.strip()


class AppOpener:
    """Opens applications by name."""

    def __init__(self, settings: SystemSettings, runner: CommandRunner) -> None:
        """Create the opener.

        Args:
            settings: Limits for names.
            runner: Runs the ``open`` program.
        """
        self._settings = settings
        self._runner = runner

    def open(self, name: str) -> str:
        """Open the application called ``name``.

        Raises:
            SystemActionError: If the name is not a plain application name, or the app
                does not exist.
        """
        name = name.strip()
        forbidden = ("/", "\n", "\r", "\0")
        if (
            not name
            or len(name) > self._settings.max_name_chars
            or name.startswith("-")
            or any(char in name for char in forbidden)
        ):
            raise SystemActionError("Give just the name of an application, such as 'Safari'.")
        self._runner.run([self._settings.open_program, "-a", name])
        logger.info("opened an application")
        return f"Opened {name}."


class WebsiteOpener:
    """Opens web addresses in the user's browser."""

    SCHEMES: ClassVar[tuple[str, ...]] = ("http", "https")

    def __init__(self, settings: SystemSettings, runner: CommandRunner) -> None:
        """Create the opener.

        Args:
            settings: Names the ``open`` program.
            runner: Runs it.
        """
        self._settings = settings
        self._runner = runner

    def open(self, url: str) -> str:
        """Open ``url`` in the default browser.

        Raises:
            SystemActionError: If ``url`` is not an http(s) address with a host.
        """
        url = url.strip()
        parts = urlsplit(url)
        if parts.scheme not in self.SCHEMES or not parts.hostname:
            raise SystemActionError("Only full http or https addresses can be opened.")
        self._runner.run([self._settings.open_program, url])
        logger.info("opened a website")
        return f"Opened {parts.hostname} in the browser."


class PathOpener:
    """Opens files and folders with their default application."""

    def __init__(self, settings: SystemSettings, runner: CommandRunner) -> None:
        """Create the opener.

        Args:
            settings: Names the ``open`` program and the kinds of file never opened.
            runner: Runs it.
        """
        self._settings = settings
        self._runner = runner

    def open(self, path: str) -> str:
        """Open a file or folder.

        Raises:
            SystemActionError: If it does not exist, or opening it would run a program
                rather than show a document.
        """
        target = Path(path.strip()).expanduser()
        if not target.is_absolute():
            raise SystemActionError("Give the full path, starting with / or ~.")
        target = target.resolve()
        if not target.exists():
            raise SystemActionError(f"{target} does not exist.")
        if target.suffix.lower() in self._settings.blocked_suffixes:
            raise SystemActionError(
                f"{target.suffix} items run programs, so they are not opened from here."
            )
        self._runner.run([self._settings.open_program, str(target)])
        logger.info("opened a %s", "folder" if target.is_dir() else "file")
        return f"Opened {target}."


class MediaController:
    """Controls the music player and the volume through AppleScript."""

    TRANSPORT: ClassVar[dict[str, str]] = {
        "play": "play",
        "pause": "pause",
        "next": "next track",
        "previous": "previous track",
    }

    def __init__(self, settings: SystemSettings, runner: CommandRunner) -> None:
        """Create the controller.

        Args:
            settings: Names the players and the AppleScript program.
            runner: Runs it.
        """
        self._settings = settings
        self._runner = runner

    def control(self, action: str, level: int | None = None) -> str:
        """Do one media action.

        Args:
            action: ``play``, ``pause``, ``next``, ``previous``, ``volume`` or ``mute``.
            level: Volume from 0 to 100, for ``volume``.

        Raises:
            SystemActionError: If the action is unknown, ``level`` is missing or out of
                range, or no player is running.
        """
        if action in self.TRANSPORT:
            player = self._running_player()
            self._script(f'tell application "{player}" to {self.TRANSPORT[action]}')
            return f"Sent {action} to {player}."
        if action == "volume":
            if level is None or not 0 <= level <= 100:
                raise SystemActionError("Give a volume from 0 to 100.")
            self._script(f"set volume output volume {int(level)}")
            return f"Volume set to {int(level)}."
        if action == "mute":
            self._script("set volume output muted true")
            return "Muted."
        raise SystemActionError(f"Unknown media action: {action}.")

    def _running_player(self) -> str:
        """Return the first configured player that is already running.

        Asking whether an application is running does not start it.
        """
        for player in self._settings.media_players:
            if self._script(f'application "{player}" is running') == "true":
                return player
        names = " or ".join(self._settings.media_players)
        raise SystemActionError(f"Nothing is playing: open {names} first.")

    def _script(self, source: str) -> str:
        """Run one line of AppleScript and return its output."""
        return self._runner.run([self._settings.script_program, "-e", source])


class OpenAppInput(BaseModel):
    """Arguments accepted by the open_app tool."""

    name: str = Field(description="Application name, e.g. 'Safari', 'Terminal' or 'Notes'.")


class OpenWebsiteInput(BaseModel):
    """Arguments accepted by the open_website tool."""

    url: str = Field(description="Full address starting with https://, e.g. 'https://github.com'.")


class OpenPathInput(BaseModel):
    """Arguments accepted by the open_path tool."""

    path: str = Field(description="Full path of a file or folder, e.g. '~/Documents'.")


class ControlMediaInput(BaseModel):
    """Arguments accepted by the control_media tool."""

    action: Literal["play", "pause", "next", "previous", "volume", "mute"] = Field(
        description="What to do."
    )
    level: int | None = Field(default=None, description="Volume 0-100, only for 'volume'.")


class OpenApp(BaseTool):
    """Tool that opens an application."""

    name: str = "open_app"
    description: str = "Open an application."
    args_schema: type[BaseModel] = OpenAppInput
    _opener: AppOpener = PrivateAttr()

    def __init__(self, opener: AppOpener, description: str) -> None:
        """Create the tool.

        Args:
            opener: Does the work.
            description: What the model is told the tool does.
        """
        super().__init__(description=description)
        self._opener = opener

    def _run(self, name: str) -> str:  # pylint: disable=arguments-differ
        """Open the application; errors come back as text."""
        try:
            return self._opener.open(name)
        except SystemActionError as error:
            return f"Error: {error}"


class OpenWebsite(BaseTool):
    """Tool that opens a web address in the browser."""

    name: str = "open_website"
    description: str = "Open a web address in the browser."
    args_schema: type[BaseModel] = OpenWebsiteInput
    _opener: WebsiteOpener = PrivateAttr()

    def __init__(self, opener: WebsiteOpener, description: str) -> None:
        """Create the tool.

        Args:
            opener: Does the work.
            description: What the model is told the tool does.
        """
        super().__init__(description=description)
        self._opener = opener

    def _run(self, url: str) -> str:  # pylint: disable=arguments-differ
        """Open the address; errors come back as text."""
        try:
            return self._opener.open(url)
        except SystemActionError as error:
            return f"Error: {error}"


class OpenPath(BaseTool):
    """Tool that opens a file or folder."""

    name: str = "open_path"
    description: str = "Open a file or folder."
    args_schema: type[BaseModel] = OpenPathInput
    _opener: PathOpener = PrivateAttr()

    def __init__(self, opener: PathOpener, description: str) -> None:
        """Create the tool.

        Args:
            opener: Does the work.
            description: What the model is told the tool does.
        """
        super().__init__(description=description)
        self._opener = opener

    def _run(self, path: str) -> str:  # pylint: disable=arguments-differ
        """Open the file or folder; errors come back as text."""
        try:
            return self._opener.open(path)
        except SystemActionError as error:
            return f"Error: {error}"


class ControlMedia(BaseTool):
    """Tool that controls music playback and the volume."""

    name: str = "control_media"
    description: str = "Control music and volume."
    args_schema: type[BaseModel] = ControlMediaInput
    _controller: MediaController = PrivateAttr()

    def __init__(self, controller: MediaController, description: str) -> None:
        """Create the tool.

        Args:
            controller: Does the work.
            description: What the model is told the tool does.
        """
        super().__init__(description=description)
        self._controller = controller

    def _run(self, action: str, level: int | None = None) -> str:  # pylint: disable=arguments-differ
        """Do the media action; errors come back as text."""
        try:
            return self._controller.control(action, level)
        except SystemActionError as error:
            return f"Error: {error}"


class SystemTools:
    """Builds the tools that act on the machine."""

    @staticmethod
    def create(
        config: TzaraConfig,
        runner: CommandRunner | None = None,
        platform: str | None = None,
    ) -> list[BaseTool]:
        """Return the tools, or none when they are off or the system is not a Mac.

        Args:
            config: Where the settings come from.
            runner: Runs programs. Defaults to a real one; replaced in tests.
            platform: Operating system name as in :data:`sys.platform`. Defaults to
                the current one.
        """
        settings = config.system
        if not settings.enabled or (platform or sys.platform) != "darwin":
            return []
        runner = runner or SubprocessRunner(settings.command_timeout)
        prompts = config.prompts
        return [
            OpenApp(AppOpener(settings, runner), prompts.open_app_tool),
            OpenWebsite(WebsiteOpener(settings, runner), prompts.open_website_tool),
            OpenPath(PathOpener(settings, runner), prompts.open_path_tool),
            ControlMedia(MediaController(settings, runner), prompts.control_media_tool),
        ]
