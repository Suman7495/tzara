"""Downloading files, with a progress bar."""

from __future__ import annotations

from pathlib import Path

import httpx

from tzara.installer.errors import SetupError
from tzara.installer.reporter import Reporter


class DownloadError(SetupError):
    """A download did not complete.

    Attributes:
        status: The HTTP status the server answered with, or ``None`` if it never did.
    """

    def __init__(self, url: str, status: int | None, detail: str = "") -> None:
        """Describe a failed download.

        Args:
            url: What was being fetched.
            status: The HTTP status, if there was one.
            detail: Extra explanation, such as a network error.
        """
        reason = f"HTTP {status}" if status is not None else detail or "no answer"
        super().__init__(f"Could not download {url} ({reason}).")
        self.status = status


class Downloader:
    """Fetches files over HTTP, following redirects (release downloads redirect)."""

    def __init__(self, client: httpx.Client, reporter: Reporter) -> None:
        """Create a downloader.

        Args:
            client: The HTTP client to use.
            reporter: Where to show progress.
        """
        self._client = client
        self._reporter = reporter

    def save(self, url: str, destination: Path, label: str) -> None:
        """Download ``url`` to ``destination``.

        Args:
            url: What to fetch.
            destination: File to write.
            label: What to call it in the progress line.

        Raises:
            DownloadError: If the server refuses or the connection fails.
        """
        try:
            with self._client.stream("GET", url, follow_redirects=True) as response:
                if response.status_code != httpx.codes.OK:
                    raise DownloadError(url, response.status_code)
                total = int(response.headers.get("content-length", 0))
                received = 0
                with destination.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
                        received += len(chunk)
                        if total:
                            self._reporter.progress(f"downloading {label}", received / total)
        except httpx.HTTPError as error:
            raise DownloadError(url, None, str(error)) from error

    def text(self, url: str) -> str:
        """Fetch a small text file.

        Raises:
            DownloadError: If the server refuses or the connection fails.
        """
        try:
            response = self._client.get(url, follow_redirects=True)
        except httpx.HTTPError as error:
            raise DownloadError(url, None, str(error)) from error
        if response.status_code != httpx.codes.OK:
            raise DownloadError(url, response.status_code)
        return response.text
