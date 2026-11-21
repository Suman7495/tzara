"""Running the server in the background: start, stop and status."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from tzara.config import TzaraConfig
from tzara.server.client import ServerClient
from tzara.server.files import ServerInfo, ServerInfoFile, ServerPaths

logger = logging.getLogger(__name__)


class DaemonError(RuntimeError):
    """Raised when the background server cannot be started or stopped."""


class Process(Protocol):
    """What the daemon manager needs from a child process."""

    pid: int

    def poll(self) -> int | None:
        """Return the exit code, or ``None`` while it is still running."""


class ProcessOperations(Protocol):
    """The operating-system process operations the daemon manager uses."""

    def spawn(self) -> Process:
        """Start the server detached from this terminal."""

    def is_alive(self, pid: int) -> bool:
        """Say whether process ``pid`` exists."""

    def terminate(self, pid: int) -> None:
        """Ask process ``pid`` to exit cleanly."""

    def kill(self, pid: int) -> None:
        """Force process ``pid`` to exit."""


class ProcessTools:
    """The real process operations, gathered so tests can replace them."""

    START_COMMAND = (sys.executable, "-m", "tzara", "serve")

    def spawn(self, command: Sequence[str] = START_COMMAND) -> Process:
        """Start ``command`` detached from this terminal.

        The child gets its own session, so closing the terminal does not stop it,
        and no standard streams: it logs to the log file.
        """
        return subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

    @staticmethod
    def is_alive(pid: int) -> bool:
        """Say whether process ``pid`` exists."""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists, but belongs to someone else
        return True

    @staticmethod
    def terminate(pid: int) -> None:
        """Ask process ``pid`` to exit cleanly."""
        os.kill(pid, signal.SIGTERM)

    @staticmethod
    def kill(pid: int) -> None:
        """Force process ``pid`` to exit."""
        os.kill(pid, signal.SIGKILL)


@dataclass(frozen=True)
class DaemonStatus:
    """What is known about the background server.

    Attributes:
        running: Whether a server is up and answering.
        info: Where it is, if a record of it exists.
        state: Its model state (``loading``, ``ready``, ``failed``), if readable.
        model: The model it is using, if readable.
        error: Why its model failed to load, if it did.
    """

    running: bool
    info: ServerInfo | None = None
    state: str | None = None
    model: str | None = None
    error: str | None = None


class DaemonManager:
    """Starts, stops and inspects the server running in the background."""

    POLL_SECONDS = 0.2
    KILL_GRACE_SECONDS = 2.0

    def __init__(
        self,
        config: TzaraConfig,
        tools: ProcessOperations | None = None,
        sleep: Callable[[float], Any] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create a manager.

        Args:
            config: Configuration.
            tools: Process operations. Replaced in tests.
            sleep: Waits for a number of seconds. Replaced in tests.
            clock: Returns monotonic time in seconds. Replaced in tests.
        """
        self._settings = config.server
        self._paths = ServerPaths.from_settings(config.server)
        self._info_file = ServerInfoFile(self._paths.info_file)
        self._tools = tools or ProcessTools()
        self._sleep = sleep
        self._clock = clock

    def status(self) -> DaemonStatus:
        """Report whether the server is up, and in what state."""
        info = self._info_file.read()
        if info is None or not self._tools.is_alive(info.pid):
            return DaemonStatus(running=False)

        client = ServerClient(info, self._read_token())
        if not client.is_healthy():
            return DaemonStatus(running=False, info=info)

        details = client.status() or {}
        return DaemonStatus(
            running=True,
            info=info,
            state=details.get("state"),
            model=details.get("model"),
            error=details.get("error"),
        )

    def start(self) -> DaemonStatus:
        """Start the server in the background, unless it is already running.

        Returns once the server answers. Its model keeps loading in the
        background, so :attr:`DaemonStatus.state` may still be ``loading``.

        Returns:
            The server's status.

        Raises:
            DaemonError: If a server process exists but does not answer, if the
                new one exits at once, or if it does not answer in time.
        """
        current = self.status()
        if current.running:
            return current
        if current.info is not None:
            raise DaemonError(
                f"a server (pid {current.info.pid}) exists but is not answering; "
                "run 'tzara stop' first"
            )

        process = self._tools.spawn()
        logger.info("started server process %d", process.pid)
        deadline = self._clock() + self._settings.startup_timeout
        while self._clock() < deadline:
            exit_code = process.poll()
            if exit_code is not None:
                raise DaemonError(
                    f"the server exited straight away (code {exit_code}); "
                    "the log file has the reason"
                )
            info = self._info_file.read()
            if info is not None and info.pid == process.pid and ServerClient(info).is_healthy():
                return self.status()
            self._sleep(self.POLL_SECONDS)

        self._tools.terminate(process.pid)
        raise DaemonError(
            f"the server did not answer within {self._settings.startup_timeout:g} seconds"
        )

    def stop(self) -> bool:
        """Stop the background server.

        Asks it to exit, and forces it to if it does not in time.

        Returns:
            True if a server was stopped, False if none was running.

        Raises:
            DaemonError: If the recorded process does not answer as a Tzara server.
                It is left alone: the record may be stale and the process id
                reused by an unrelated program.
        """
        info = self._info_file.read()
        if info is None:
            return False
        if not self._tools.is_alive(info.pid):
            self._info_file.remove_if_owned_by(info.pid)
            return False
        if not ServerClient(info).is_healthy():
            raise DaemonError(
                f"process {info.pid} does not answer as a Tzara server, so it was not "
                f"stopped. If the record is stale, delete {self._paths.info_file}"
            )

        self._tools.terminate(info.pid)
        if not self._wait_for_exit(info.pid, self._settings.stop_timeout):
            logger.warning("server %d ignored the stop request; forcing it", info.pid)
            self._tools.kill(info.pid)
            self._wait_for_exit(info.pid, self.KILL_GRACE_SECONDS)
        self._info_file.remove_if_owned_by(info.pid)
        return True

    def _wait_for_exit(self, pid: int, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for process ``pid`` to exit."""
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            if not self._tools.is_alive(pid):
                return True
            self._sleep(self.POLL_SECONDS)
        return not self._tools.is_alive(pid)

    def _read_token(self) -> str | None:
        """Return the stored API token, or ``None`` if it cannot be read."""
        try:
            return self._paths.token_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
