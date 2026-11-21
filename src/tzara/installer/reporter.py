"""What ``tzara setup`` tells the person running it."""

from __future__ import annotations

import sys
from typing import ClassVar, TextIO


class Reporter:
    """Prints setup progress in a form that reads well in a terminal and in a log.

    In a terminal a download shows as one line that updates in place. When output is
    redirected it prints a line every ten percent instead, so logs are not flooded.
    """

    STEP: ClassVar[str] = "==>"
    PROGRESS_BUCKET: ClassVar[float] = 0.10

    def __init__(self, out: TextIO | None = None, interactive: bool | None = None) -> None:
        """Create a reporter.

        Args:
            out: Where to write. Defaults to standard output.
            interactive: Whether to update progress in place. Defaults to whether
                ``out`` is a terminal.
        """
        self._out = out or sys.stdout
        self._interactive = self._out.isatty() if interactive is None else interactive
        self._last_bucket = -1
        self._line_open = False

    def step(self, text: str) -> None:
        """Announce the start of a step."""
        self._close_line()
        self._write(f"\n{self.STEP} {text}\n")

    def ok(self, text: str) -> None:
        """Report something that is done."""
        self._close_line()
        self._write(f"  ✓ {text}\n")

    def note(self, text: str) -> None:
        """Report something that is happening, or worth knowing."""
        self._close_line()
        self._write(f"    {text}\n")

    def warn(self, text: str) -> None:
        """Report something that went wrong but does not stop setup."""
        self._close_line()
        self._write(f"  ! {text}\n")

    def fail(self, text: str) -> None:
        """Report something that stopped setup."""
        self._close_line()
        self._write(f"  ✗ {text}\n")

    def progress(self, label: str, fraction: float) -> None:
        """Show how far along something is.

        Args:
            label: What is progressing, e.g. ``downloading qwen3:8b``.
            fraction: How far, from 0 to 1.
        """
        fraction = max(0.0, min(1.0, fraction))
        percent = int(fraction * 100)
        if self._interactive:
            self._write(f"\r    {label}  {percent:3d}%")
            self._line_open = True
            return
        bucket = int(fraction / self.PROGRESS_BUCKET)
        if bucket != self._last_bucket:
            self._last_bucket = bucket
            self._write(f"    {label}  {percent}%\n")

    def _close_line(self) -> None:
        """End an in-place progress line so the next message starts cleanly."""
        if self._line_open:
            self._write("\n")
            self._line_open = False
        self._last_bucket = -1

    def _write(self, text: str) -> None:
        self._out.write(text)
        self._out.flush()
