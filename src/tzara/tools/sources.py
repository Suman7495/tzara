"""How much to trust the site a page comes from."""

from __future__ import annotations

from urllib.parse import urlparse

from tzara.config import ResearchSettings


class SourceAuthority:
    """Says whether a site is one that deserves more belief than the average search result.

    A search engine ranks by popularity, and for a question about health or a regulation the
    popular page is often a shop or a blog. This favours official sources (governments,
    universities, international bodies), reference works and papers, and documentation. It only
    adds to a page's rank; it never drops a page, so a good page from an unknown site still counts.
    """

    def __init__(self, settings: ResearchSettings) -> None:
        """Create it.

        Args:
            settings: Which sites and address patterns count as which kind.
        """
        self._settings = settings

    def kind(self, url: str) -> str:
        """``official``, ``reference``, ``docs`` or ``other`` for the site of ``url``."""
        parts = urlparse(url)
        host = (parts.hostname or "").lower().removeprefix("www.")
        if any(
            host == s.lstrip(".") or host.endswith(s if s.startswith(".") else "." + s)
            for s in self._settings.official_suffixes
        ):
            return "official"
        if any(
            host == name or host.endswith("." + name) for name in self._settings.reference_hosts
        ):
            return "reference"
        first_label = host.split(".")[0]
        if first_label in self._settings.docs_prefixes or any(
            host == name or host.endswith("." + name) for name in self._settings.docs_hosts
        ):
            return "docs"
        if parts.path.lower().startswith(("/docs", "/documentation")):
            return "docs"
        return "other"

    def level(self, url: str) -> int:
        """1 for an official, reference or documentation site, else 0."""
        return int(self.kind(url) != "other")
