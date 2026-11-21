"""Running other programs, behind an interface so tests need not run them."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    """What a finished command printed and how it ended.

    Attributes:
        returncode: Exit status. 127 means the program was not found.
        stdout: Standard output.
        stderr: Standard error.
    """

    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def succeeded(self) -> bool:
        """Whether the command exited with status 0."""
        return self.returncode == 0


class CommandRunner(Protocol):
    """Runs a program and waits for it."""

    def run(self, args: Sequence[str]) -> CommandResult:
        """Run ``args`` and return how it ended."""


class SubprocessRunner:
    """The real :class:`CommandRunner`."""

    NOT_FOUND = 127

    def run(self, args: Sequence[str]) -> CommandResult:
        """Run ``args``, capturing its output.

        A program that does not exist is reported as status 127 rather than raised, so
        callers handle "missing" and "failed" the same way.
        """
        try:
            completed = subprocess.run(list(args), capture_output=True, text=True, check=False)
        except FileNotFoundError as error:
            return CommandResult(self.NOT_FOUND, "", str(error))
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)
