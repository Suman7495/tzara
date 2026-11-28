"""Apps that can be added with one press: the program, the arguments and what to do first."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from tzara.mcp.config import McpServerConfig


@dataclass(frozen=True)
class McpPreset:
    """A ready-made app.

    Attributes:
        id: A short stable name, such as ``microsoft-365-outlook``.
        title: What the user sees on the button.
        description: One or two sentences: what it lets Tzara do.
        first_use: What to say to Tzara the first time, such as signing in.
        server: The configuration that is added.
        token_env: The environment variable that carries the access token the app needs, or
            empty if it signs in by itself.
        token_label: What to call that token for the user, such as ``Integration token``.
        token_help: Where the user gets the token, in a sentence.
        signin_args: Added to the app's arguments to make it log in, so Settings can offer a
            Sign in button that shows the user a link and a code. Empty if it needs none.
    """

    id: str
    title: str
    description: str
    first_use: str
    server: McpServerConfig
    token_env: str = ""
    token_label: str = ""
    token_help: str = ""
    signin_args: tuple[str, ...] = ()


class McpPresets:
    """The apps Tzara knows how to connect."""

    PACKAGE: ClassVar[str] = "@softeria/ms-365-mcp-server"
    NOTION_BASICS: ClassVar[tuple[str, ...]] = (
        "notion-search",
        "notion-fetch",
        "notion-create-pages",
        "notion-update-page",
    )
    SIGN_IN: ClassVar[str] = (
        "Press Sign in. You get a link and a code: open the link, enter the code and sign in "
        "with Microsoft. Tzara never sees your password."
    )

    @classmethod
    def all(cls) -> list[McpPreset]:
        """Every preset, in the order they are offered."""
        return [
            cls._microsoft(
                "microsoft-365-outlook",
                "microsoft-365",
                "Microsoft 365: Outlook and Calendar",
                "Read your mail, calendar and contacts: find a message, see what is on tomorrow. "
                "Read-only: it cannot send or change anything.",
                ("--preset", "outlook"),
            ),
            cls._microsoft(
                "microsoft-365-files",
                "microsoft-365-files",
                "Microsoft 365: OneDrive files",
                "Search and read your OneDrive files. Read-only.",
                ("--preset", "onedrive"),
            ),
            cls._microsoft(
                "microsoft-365-teams",
                "microsoft-365-teams",
                "Microsoft 365: Teams (work account)",
                "Read your Teams chats and channels. Needs a work or school account. Read-only.",
                ("--org-mode", "--preset", "teams"),
            ),
            cls._microsoft(
                "microsoft-365-excel",
                "microsoft-365-excel",
                "Microsoft 365: Excel workbooks",
                "Read the workbooks in your OneDrive: sheets, ranges, tables. Read-only.",
                ("--preset", "excel"),
            ),
            McpPreset(
                id="notion",
                title="Notion",
                description="Search Notion, read pages, and add or edit page content.",
                first_use=(
                    "Press Sign in: your browser opens Notion, you approve access, and Tzara "
                    "can search your workspace."
                ),
                # The basics: find a page, read it, make a page, change a page's content (text,
                # links and images are all content). The user ticks more in Settings; the rest of
                # its 77 tools (delete, convert, move...) stay off until they do.
                server=McpServerConfig(
                    name="notion", url="https://mcp.notion.com/mcp", tools=cls.NOTION_BASICS
                ),
            ),
            McpPreset(
                id="hubspot",
                title="HubSpot",
                description="Look up contacts, companies and deals in your HubSpot CRM (beta).",
                first_use="Give the private app only the read scopes you want Tzara to have.",
                server=McpServerConfig(
                    name="hubspot",
                    command="npx",
                    args=("-y", "@hubspot/mcp-server"),
                    allow_actions=True,
                ),
                token_env="PRIVATE_APP_ACCESS_TOKEN",
                token_label="Private app access token",
                token_help=(
                    "In HubSpot: Settings, Integrations, Private Apps: create one with read "
                    "scopes only, and copy its token. What the token allows is what Tzara can do."
                ),
            ),
        ]

    @classmethod
    def find(cls, preset_id: str) -> McpPreset | None:
        """The preset called ``preset_id``, if there is one."""
        return next((preset for preset in cls.all() if preset.id == preset_id), None)

    @classmethod
    def _microsoft(
        cls, preset_id: str, name: str, title: str, description: str, selection: tuple[str, ...]
    ) -> McpPreset:
        """A Microsoft 365 preset: the open-source ms-365 MCP server, in its read-only mode.

        The server is told to expose only read tools (``--read-only``) and only the app asked
        for (``--preset``), so the model is given a short list and nothing that changes anything.
        Its tools are not all marked read-only in the protocol, so the app is added with
        ``allow_actions``; the server's own read-only mode is what keeps actions out.
        """
        return McpPreset(
            id=preset_id,
            title=title,
            description=description,
            first_use=cls.SIGN_IN,
            server=McpServerConfig(
                name=name,
                command="npx",
                args=("-y", cls.PACKAGE, *selection, "--read-only"),
                allow_actions=True,
            ),
            signin_args=("--login",),
        )
