"""Runtime configuration for Tzara.

Every tunable value lives here as a frozen dataclass with a default. Defaults
can be overridden from the environment (see :meth:`TzaraConfig.from_env`), and
tests can build a config directly.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import ClassVar

from tzara.prompts import Prompts


class ConfigError(ValueError):
    """Raised when a configuration value from the environment is invalid."""


@dataclass(frozen=True)
class OllamaSettings:
    """Settings applied only to Ollama models.

    Attributes:
        num_ctx: Context window in tokens. Ollama's default of 4096 silently
            truncates the start of the prompt once the tools and a long prompt are on.
        keep_alive: How long Ollama keeps the model loaded while idle. A model
            that has been unloaded costs several seconds to load again.
        reasoning: Whether thinking-capable models (e.g. Qwen3) think before
            answering. Off by default because it adds seconds to every reply.
        reserved_tokens: Part of ``num_ctx`` kept free for the system prompt,
            tool schemas and the model's reply. The rest is the budget for
            conversation history: once history nears it, old turns are summarized
            older turns instead of letting Ollama silently truncate the prompt.
    """

    num_ctx: int = 16384
    keep_alive: str = "30m"
    reasoning: bool = False
    reserved_tokens: int = 4096

    def __post_init__(self) -> None:
        """Check that some of the context window is left for history.

        Raises:
            ConfigError: If ``reserved_tokens`` leaves no room for history.
        """
        if self.reserved_tokens >= self.num_ctx:
            raise ConfigError(
                f"reserved_tokens ({self.reserved_tokens}) must be smaller than "
                f"num_ctx ({self.num_ctx})"
            )

    @property
    def history_tokens(self) -> int:
        """Tokens available for conversation history."""
        return self.num_ctx - self.reserved_tokens


@dataclass(frozen=True)
class LogSettings:
    """Where and how much Tzara logs.

    Logs go to a rotating file, never to the chat. Message text is only logged
    at ``DEBUG``, so the default level keeps conversations out of the log.

    Attributes:
        level: Minimum level for Tzara's own messages.
        file: Log file. ``None`` means the platform's standard log directory.
        max_bytes: Size at which the file is rotated.
        backup_count: Number of rotated files kept.
    """

    LEVELS: ClassVar[tuple[str, ...]] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

    level: str = "INFO"
    file: Path | None = None
    max_bytes: int = 1_000_000
    backup_count: int = 3


@dataclass(frozen=True)
class ServerSettings:
    """Settings for the local HTTP server that clients such as the overlay use.

    The server only ever listens on the loopback address; that is not
    configurable, because anything else would expose the agent to the network.

    Attributes:
        port: TCP port on ``127.0.0.1``.
        max_message_chars: Longest chat message the server accepts.
        max_name_chars: Longest name the assistant may be given.
        startup_timeout: Seconds ``tzara start`` waits for the server to answer.
        stop_timeout: Seconds ``tzara stop`` waits for the server to exit.
        wake_after_seconds: How long the server must have been idle before a client opening its
            chat box makes it get the model ready again. Ollama unloads an idle model, and loading
            it takes seconds the user would otherwise wait through after typing.
        heartbeat_seconds: How long a reply stream may stay silent before the server
            sends a keep-alive comment, so clients with a read timeout don't drop a
            healthy connection while the model is thinking.
        data_dir: Where the token and server files live. ``None`` means the
            platform's standard directory.
    """

    HOST: ClassVar[str] = "127.0.0.1"

    port: int = 8927
    max_message_chars: int = 20_000
    max_name_chars: int = 40
    startup_timeout: float = 30.0
    stop_timeout: float = 15.0
    heartbeat_seconds: float = 10.0
    wake_after_seconds: float = 120.0
    data_dir: Path | None = None

    def __post_init__(self) -> None:
        """Check that the port and heartbeat interval are valid.

        Raises:
            ConfigError: If ``port`` is outside 1-65535, or ``heartbeat_seconds``
                is not positive.
        """
        if not 0 < self.port < 65536:
            raise ConfigError(f"port must be between 1 and 65535, got {self.port}")
        if self.heartbeat_seconds <= 0:
            raise ConfigError(f"heartbeat_seconds must be positive, got {self.heartbeat_seconds}")
        if self.wake_after_seconds < 0:
            raise ConfigError(
                f"wake_after_seconds must not be negative, got {self.wake_after_seconds}"
            )


@dataclass(frozen=True)
class InstallerSettings:
    """Where ``tzara setup`` gets things from and puts them.

    Attributes:
        ollama_url: Address of the local Ollama server.
        ollama_download_url: Official macOS download of the Ollama app.
        ollama_app: Where the Ollama app lives.
        release_url: Where release files are published. ``{version}`` and ``{asset}``
            are filled in; the app is fetched by Tzara's own version so the two always
            speak the same protocol.
        app_asset: Name of the zipped macOS app in a release.
        app_name: Name of the app bundle.
        app_dir: Where to put the app. ``None`` means ``~/Applications``.
        launch_agents_dir: Where login jobs go. ``None`` means ``~/Library/LaunchAgents``.
        service_label: launchd label of the server's login job.
        login_label: launchd label of the job that opens the app at login.
        wait_seconds: How long to wait for Ollama or the server to come up.
    """

    ollama_url: str = "http://127.0.0.1:11434"
    ollama_download_url: str = "https://ollama.com/download/Ollama-darwin.zip"
    ollama_app: Path = Path("/Applications/Ollama.app")
    release_url: str = "https://github.com/Suman7495/tzara/releases/download/v{version}/{asset}"
    app_asset: str = "Tzara.app.zip"
    app_name: str = "Tzara.app"
    app_dir: Path | None = None
    launch_agents_dir: Path | None = None
    service_label: str = "ai.telekinesis.tzara.server"
    login_label: str = "ai.telekinesis.tzara.app"
    wait_seconds: float = 90.0

    def __post_init__(self) -> None:
        """Check that the release address can name a version and a file.

        Raises:
            ConfigError: If ``release_url`` lacks ``{version}`` or ``{asset}``.
        """
        for placeholder in ("{version}", "{asset}"):
            if placeholder not in self.release_url:
                raise ConfigError(f"release_url must contain {placeholder}")


@dataclass(frozen=True)
class WebSettings:
    """Settings for searching the web and reading pages.

    This is the one feature that sends something off the machine: the search
    query goes to DuckDuckGo, and pages are fetched from their sites.
    The model and the conversation stay local.

    Attributes:
        enabled: Whether the agent gets the web tools at all.
        max_results: Search results returned per search.
        max_page_chars: Most text of one page given to the model (about four
            characters per token, so the context window is not filled by one page).
        max_download_bytes: Most bytes read from one page.
        max_redirects: Redirects followed for one page, each checked again.
        timeout_seconds: Network timeout for a page.
        search_timeout_seconds: How long one search may take. The search engine either answers in
            a second or two or not at all, so waiting longer only makes the answer slower.
        search_attempts: Times a search is tried when the search engine does not answer.
        user_agent: Sent when fetching pages.
        region: The search engine's region, which decides the language of the results.
        engines: The engines ``ddgs`` asks first, as a comma-separated list, before it falls back to
            all of them. Its own "auto" spends seconds on engines that hang on many networks
            (DuckDuckGo's page): measured here, Yahoo answered in about 1 s while Brave, Startpage
            and Mojeek refused programs. Bing and Google are disabled by ``ddgs`` itself.
        wikipedia_language: Which Wikipedia is asked (``en``, ``de``...).
        temperature_unit: ``celsius`` or ``fahrenheit``, for weather answers.
        cache_seconds: How long a search result or a page is remembered, so a follow-up question
            on the same subject does not fetch it again.
        max_html_chars: Most HTML read from one page, so a huge or hostile page cannot stall it.
    """

    enabled: bool = True
    max_results: int = 8
    max_page_chars: int = 6000
    max_download_bytes: int = 2_000_000
    max_redirects: int = 3
    timeout_seconds: float = 6.0
    search_timeout_seconds: float = 4.0
    search_attempts: int = 2
    user_agent: str = "Mozilla/5.0 (compatible; Tzara; +https://github.com/Suman7495/tzara)"
    region: str = "us-en"
    engines: str = "yahoo,brave,startpage,mojeek"
    wikipedia_language: str = "en"
    temperature_unit: str = "celsius"
    cache_seconds: float = 900.0
    max_html_chars: int = 1_000_000

    def __post_init__(self) -> None:
        """Check the limits.

        Raises:
            ConfigError: If a limit is not positive.
        """
        limits = (
            self.max_results,
            self.max_page_chars,
            self.max_download_bytes,
            self.timeout_seconds,
            self.max_html_chars,
        )
        if any(limit <= 0 for limit in limits) or self.max_redirects < 0 or self.cache_seconds < 0:
            raise ConfigError("web search limits must be positive")


@dataclass(frozen=True)
class SystemSettings:
    """Settings for the tools that act on the Mac (open things, control media).

    Attributes:
        enabled: Whether the agent gets these tools. They only exist on macOS.
        command_timeout: Seconds a launched program may run before it is stopped.
        max_name_chars: Longest application name accepted.
        open_program: Program that opens apps, addresses, files and folders.
        script_program: Program that runs AppleScript, used for media and volume.
        media_players: Music apps the media tool may control, in order of preference.
        blocked_suffixes: Kinds of item never opened, because opening them runs a program.
    """

    enabled: bool = True
    command_timeout: float = 10.0
    max_name_chars: int = 100
    open_program: str = "open"
    script_program: str = "osascript"
    media_players: tuple[str, ...] = ("Music", "Spotify")
    blocked_suffixes: tuple[str, ...] = (
        ".app",
        ".command",
        ".sh",
        ".zsh",
        ".bash",
        ".tool",
        ".terminal",
        ".scpt",
        ".scptd",
        ".workflow",
        ".action",
        ".pkg",
        ".mpkg",
        ".dmg",
        ".jar",
        ".webloc",
        ".inetloc",
    )

    def __post_init__(self) -> None:
        """Check the limits.

        Raises:
            ConfigError: If a limit is not positive.
        """
        if self.command_timeout <= 0 or self.max_name_chars <= 0:
            raise ConfigError("system tool limits must be positive")


@dataclass(frozen=True)
class ResearchSettings:
    """Settings for the ``research`` tool, which reads pages instead of only search snippets.

    Attributes:
        max_query_chars: Longest search query kept.
        keyword_words: Most words in the second search, made of the question's key words.
        pages: How many pages to read.
        listed: How many results are listed in the evidence, the rest of them with their search
            snippets only.
        max_per_host: Most pages from one site, so one site cannot fill the answer.
        page_chars: Most text kept from one page: the passages that match the question.
        total_chars: Most text returned to the model in all; the context window is small.
        links: How many ready-made source links are listed at the end of the evidence.
        code_blocks: How many code blocks from the pages are given to the model, so that it
            copies working code instead of writing its own.
        code_chars: Longest code block kept.
        address_bonus: Added to a page's score when the user wrote its address.
        authority_bonus: Added to a page's rank when its site is official, a reference or
            documentation (see :class:`SourceAuthority`).
        official_suffixes: Address endings of governments, universities and international bodies.
        reference_hosts: Reference works, health bodies and publishers of papers.
        docs_prefixes: First parts of an address that mean documentation (``docs.python.org``).
        docs_hosts: Sites that host the documentation of many projects.
        authority_sites: Where an extra search looks for questions about health, law or money.
        authority_words: Words in a question that make it one of those.
        grace_seconds: How long the slower searches and pages may take once one has answered.
        skip_hosts: Sites that need a login or are mostly video, so never worth reading.
        question_starts: First words that make a message a question for the web ("what", "how").
        question_words: Words anywhere in a message that ask for facts, advice or comparisons.
        chat_patterns: Regular expressions for messages that need no web even if they look like
            questions: small talk, questions about the user, arithmetic, commands for the Mac.
    """

    max_query_chars: int = 100
    keyword_words: int = 8
    pages: int = 4
    listed: int = 6
    max_per_host: int = 2
    page_chars: int = 1500
    total_chars: int = 4400
    links: int = 4
    code_blocks: int = 2
    code_chars: int = 600
    address_bonus: int = 20
    authority_bonus: int = 5
    official_suffixes: tuple[str, ...] = (
        ".gov",
        ".gov.uk",
        ".edu",
        ".ac.uk",
        ".int",
        ".mil",
        "europa.eu",
    )
    reference_hosts: tuple[str, ...] = (
        "wikipedia.org",
        "britannica.com",
        "nih.gov",
        "nhs.uk",
        "mayoclinic.org",
        "cochrane.org",
        "arxiv.org",
        "doi.org",
        "nature.com",
        "sciencedirect.com",
        "springer.com",
        "who.int",
    )
    docs_prefixes: tuple[str, ...] = ("docs", "developer", "developers", "learn", "devdocs")
    docs_hosts: tuple[str, ...] = ("readthedocs.io", "developer.mozilla.org", "github.io")
    authority_sites: tuple[str, ...] = ("nih.gov", "nhs.uk", "mayoclinic.org", "wikipedia.org")
    authority_words: tuple[str, ...] = (
        " safe ",
        " dose ",
        " dosage ",
        " symptom",
        " side effect",
        " interaction",
        " medicine",
        " medication",
        " pregnan",
        " disease",
        " treatment",
        " illness",
        " vitamin",
        " legal",
        " illegal",
        " law ",
        " tax ",
    )
    grace_seconds: float = 1.5
    question_starts: tuple[str, ...] = (
        "what",
        "who",
        "when",
        "where",
        "why",
        "how",
        "which",
        "is",
        "are",
        "was",
        "were",
        "does",
        "do",
        "did",
        "should",
        "can",
        "will",
    )
    question_words: tuple[str, ...] = (
        "recommend",
        "best",
        "compare",
        "versus",
        " vs ",
        "price",
        "cost of",
        "buy",
        "review",
        "latest",
        "news",
        "difference between",
        "tell me about",
        "look up",
        "search for",
        "find me",
        "alternatives",
        "competitors",
        "weather",
        "forecast",
        "temperature",
        "exchange rate",
        "stock price",
        "score",
        "how much",
        "how many",
        "opening hours",
        "near me",
        "tonight",
        "tomorrow",
        "right now",
        "this week",
        "release date",
        "population",
    )
    chat_patterns: tuple[str, ...] = (
        r"\b(my|mine|i told you|remember that|forget that|you remember)\b",
        r"^(thanks|thank you|good (morning|evening|night))\b",
        r"\b(how are you|what'?s up|who are you|what is your name|what are you)\b",
        r"\byou (just )?(said|explained|explain|told|mentioned|wrote)\b",
        r"\d\s*(\+|-|\*|/|x|times|plus|minus|divided by)\s*\d",
        r"^(open|launch|play|pause|skip|mute|unmute|set the volume)\b",
        r"^(do|are) you\b",
        r"^(can|could|would|will) you (open|play|pause|skip|write|help|remember|forget)\b",
    )
    skip_hosts: tuple[str, ...] = (
        "youtube.com",
        "youtu.be",
        "facebook.com",
        "instagram.com",
        "tiktok.com",
        "x.com",
        "twitter.com",
        "pinterest.com",
        "linkedin.com",
        "reddit.com",
    )

    def __post_init__(self) -> None:
        """Check the limits.

        Raises:
            ConfigError: If a limit is not positive.
        """
        limits = (
            self.max_query_chars,
            self.keyword_words,
            self.pages,
            self.listed,
            self.max_per_host,
            self.page_chars,
            self.total_chars,
            self.links,
            self.code_blocks,
            self.code_chars,
            self.grace_seconds,
        )
        if min(limits) <= 0:
            raise ConfigError("research limits must be positive")


@dataclass(frozen=True)
class MemorySettings:
    """Settings for what Tzara remembers between conversations.

    Memory is kept in one file in the data directory, readable only by you. Turn it off and
    Tzara forgets everything when it stops, as before.

    Attributes:
        enabled: Whether anything is kept on disk.
        max_fact_chars: Longest fact accepted. A fact is one short sentence.
        max_facts: Most facts kept; past that, one has to be forgotten first.
        inject_facts: How many facts, the most recently confirmed, are told to the model. They
            do not depend on what is being said: a prompt that changes from message to message
            makes Ollama redo its work on the whole prompt, which costs seconds. Older facts are
            found with the ``recall`` tool.
        inject_chars: Most text of facts put in the prompt, since the context window is small.
        recall_results: Most results of a search through memory.
        snippet_chars: Longest excerpt of a past message shown in search results.
        restore_chars: Most text of the last conversation put back when Tzara starts.
        inject_activity: How many of the latest things Tzara did for the user (a page changed, a
            message written) are told to the model, so "did that work?" and "expand it" know what
            "it" is, also after a restart. Only the last ``activity_window_seconds`` count.
        activity_window_seconds: How far back those go.
        max_activity: Most activity entries kept; the oldest go first.
        max_samples: Most writing samples kept for each kind of place (chat, email, social).
        min_sample_chars: Shortest text kept as a sample of how the user writes.
        max_sample_chars: Longest sample kept (the rest is cut).
        capture_max_chars: Longest message from which facts are picked up by themselves.
            Longer text is a paste, not the user speaking about themselves.
    """

    enabled: bool = True
    inject_activity: int = 5
    activity_window_seconds: int = 86_400
    max_activity: int = 500
    max_samples: int = 30
    min_sample_chars: int = 30
    max_sample_chars: int = 600
    capture_max_chars: int = 400
    max_fact_chars: int = 300
    max_facts: int = 200
    inject_facts: int = 10
    inject_chars: int = 900
    recall_results: int = 5
    snippet_chars: int = 240
    restore_chars: int = 24_000

    def __post_init__(self) -> None:
        """Check the limits.

        Raises:
            ConfigError: If a limit is not positive.
        """
        limits = (
            self.max_fact_chars,
            self.max_facts,
            self.inject_facts,
            self.inject_chars,
            self.recall_results,
            self.snippet_chars,
            self.restore_chars,
            self.inject_activity,
            self.activity_window_seconds,
            self.max_activity,
            self.max_samples,
            self.min_sample_chars,
            self.max_sample_chars,
            self.capture_max_chars,
        )
        if min(limits) <= 0:
            raise ConfigError("memory limits must be positive")


@dataclass(frozen=True)
class ScreenSettings:
    """Settings for what the user was looking at, which the Mac app may send with a message.

    Attributes:
        enabled: Whether the server accepts and uses it.
        max_selection_chars: Most selected text given to the model (the rest is cut).
        max_field_chars: Longest app name, window title or address given to the model.
        max_page_chars: Most text of the page or document behind the context given to the model.
        max_typing_chars: Most text given from the field the user is typing in (around the cursor).
        max_visible_chars: Most text given from what is visible in the window.
        web_words: Words in a message that ask for the web outright ("search", "news", "weather").
            Only these send a question to the web when something relevant could be on the screen.
        screen_words: Words in a message that mean it is about what is on screen or being typed
            ("reply to this email", "continue", "fix this"). Only then are the typed text and the
            window text given to the model: they cost seconds of reading and change every message.
        page_words: Words in a message that mean it is about the page or document the user has
            open ("summarize this page"). Only then is the page read, and only if nothing is
            selected: a selection is what the user means.
        document_suffixes: File types read from disk when the context is a local document. Types
            that hold secrets (keys, ``.env``) are not in the list, and nor are hidden files.
        max_file_bytes: Largest file read.
        app_page_words: Words in a message about the page the user has open in a connected app
            ("add a paragraph", "expand it"). Then that page is read through the app, first.
        noise_pattern: Lines of window or page text that are only a notice from the page or
            browser ("JavaScript must be enabled"), never what the user is working on. They are
            left out: the model once answered with one as if it were the page.
    """

    enabled: bool = True
    app_page_words: tuple[str, ...] = (
        "page", "paragraph", "para", "section", "heading", "text", "note", "doc", "add", "write",
        "expand", "edit", "rewrite", "shorten", "summar", "translate", "explain", "continue",
        "it", "this", "that", "here", "fix", "change", "update", "append",
    )  # fmt: skip
    noise_pattern: str = (
        r"(?i)javascript (?:must be|is|needs to be) (?:enabled|turned on)|enable javascript"
        r"|your browser (?:does not|doesn't) support|please (?:enable|turn on) cookies"
    )
    max_selection_chars: int = 6000
    max_field_chars: int = 300
    max_page_chars: int = 5000
    max_typing_chars: int = 1500
    max_visible_chars: int = 3000
    web_words: tuple[str, ...] = (
        "search",
        "google",
        "look up",
        "lookup",
        "online",
        "on the web",
        "internet",
        "news",
        "latest",
        "weather",
        "forecast",
        "stock price",
        "exchange rate",
        "score",
        "who won",
        "price of",
        "how much does",
    )
    screen_words: tuple[str, ...] = (
        "this email",
        "this message",
        "this thread",
        "this chat",
        "this conversation",
        "this code",
        "this error",
        "this screen",
        "this window",
        "this text",
        "this draft",
        "my screen",
        "the screen",
        "the window",
        "on screen",
        "reply",
        "respond",
        "continue",
        "finish",
        "fix this",
        "improve",
        "proofread",
        "draft",
        "summarize",
        "summarise",
        "summary",
        "tl;dr",
        "translate",
        "explain",
        "what does this",
        "what is this",
        "what's this",
        "what am i",
        # Leaning to the screen: a bare "this", "that" or "it" points at something in front of the
        # user. If the guess is wrong, the user is asked whether to search online.
        " this ",
        " that ",
        " it ",
        "email",
        "document",
        # Someone on screen, who is not named in the message.
        " she ",
        " he ",
        " her ",
        " his ",
        " they ",
        " their ",
        " them ",
        "the sender",
        "the author",
    )
    page_words: tuple[str, ...] = (
        "this page",
        "this article",
        "this site",
        "this website",
        "this link",
        "this document",
        "this file",
        "this pdf",
        "this paper",
        "this post",
        "the page",
        "the article",
        "the document",
        "summarize",
        "summarise",
        "summary",
        "tl;dr",
        "key points",
        "what is this",
        "what does this",
        "what's this",
        "translate",
        "explain this",
        "proofread",
    )
    document_suffixes: tuple[str, ...] = (
        ".txt",
        ".md",
        ".markdown",
        ".rst",
        ".csv",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".html",
        ".htm",
        ".log",
        ".py",
        ".js",
        ".ts",
        ".swift",
        ".pdf",
    )
    max_file_bytes: int = 5_000_000

    def __post_init__(self) -> None:
        """Check the limits.

        Raises:
            ConfigError: If a limit is not positive.
        """
        limits = (
            self.max_selection_chars,
            self.max_field_chars,
            self.max_page_chars,
            self.max_typing_chars,
            self.max_visible_chars,
            self.max_file_bytes,
        )
        if min(limits) <= 0:
            raise ConfigError("screen limits must be positive")


@dataclass(frozen=True)
class McpSettings:
    """Settings for connecting to apps through the Model Context Protocol (MCP).

    The apps to connect are listed in ``mcp.json`` in Tzara's data folder. Every tool of a
    connected app costs prompt text on every message, so the number and size are limited.

    Attributes:
        enabled: Whether Tzara connects to the apps in ``mcp.json`` at all.
        file_name: The file, inside the data folder, that lists the apps.
        startup_seconds: How long a server may take to start and list its tools.
        call_seconds: How long one tool call may take.
        max_result_chars: Most text of one result given to the model.
        max_prompt_chars: Most text, over all apps' tools (names, descriptions and argument
            lists), put straight into the prompt of every message. Microsoft 365's server has
            hundreds of tools with long argument lists, and a local model reads the prompt at
            about 250 tokens a second: unbounded, a plain "hi" took over a minute. What does not fit
            is left out, and Settings says so; the user ticks the tools they want.
        claim_pattern: A regular expression for a reply that says something was changed in an
            app ("I've added", "I created"). If one matches and no app tool ran, the reply
            carries a note saying nothing was changed: a small model otherwise reports success
            it never had.
        max_description_chars: Longest tool description kept.
        promise_pattern: A regular expression for a reply that only promises a step ("Let me
            fetch the page… one moment"). When the model ends its turn with such words and no
            tool call, it is told once to do the step now instead of announcing it.
        tool_hints: Short instructions that replace an app tool's own description (by the tool's
            name in the app). Some descriptions are thousands of characters and are cut to
            ``max_description_chars``, which loses the one rule that matters (Notion's update tool
            needs ``command`` set to ``insert_content`` to add text).
        callback_port: The port on 127.0.0.1 where the browser returns after signing in to a
            remote app. Fixed, because the app remembers it as part of Tzara's registration.
        sign_in_seconds: How long the user has to finish signing in to a remote app.
        extra_paths: Folders added to the search path when an app is started. The server runs
            under launchd, whose path has only the system folders, so Homebrew's ``npx`` and
            ``node``, or ``uvx``, would not be found.
    """

    enabled: bool = True
    file_name: str = "mcp.json"
    startup_seconds: float = 60.0
    call_seconds: float = 30.0
    max_result_chars: int = 6000
    max_prompt_chars: int = 14000
    claim_pattern: str = (
        r"\bi(?:'ve| have)?\s+(?:now\s+|just\s+|successfully\s+)?"
        r"(?:added|created|updated|written|wrote|sent|deleted|saved|posted|edited|appended|inserted)\b"
    )
    max_description_chars: int = 200
    promise_pattern: str = (
        r"\b(?:let me|i(?:'ll| will)|one moment|give me a moment|i need to|first,? i)\b"
        r".{0,60}\b(?:fetch|check|look|search|read|update|add|get|find|try|open|retrieve|insert)"
    )
    tool_hints: Mapping[str, str] = field(
        default_factory=lambda: {
            "notion-update-page": (
                "Change a Notion page. To add text: command=insert_content, page_id=the page's "
                "address, content=the text in Markdown (it is added at the end). To fix a phrase: "
                "command=update_content. Never use replace_content unless asked."
            ),
            "notion-create-pages": (
                "Create Notion pages. Give pages=[{properties:{title:...}, content:Markdown}] and "
                "parent={page_id:...} (omit parent to create a private page)."
            ),
            "notion-fetch": "Read a Notion page or database. id=the page's address or id.",
        }
    )
    callback_port: int = 8929
    sign_in_seconds: float = 300.0
    extra_paths: tuple[str, ...] = (
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "~/.local/bin",
        "~/.cargo/bin",
        "~/.volta/bin",
    )

    def __post_init__(self) -> None:
        """Check the limits.

        Raises:
            ConfigError: If a limit is not positive.
        """
        limits = (
            self.startup_seconds,
            self.call_seconds,
            self.max_result_chars,
            self.max_prompt_chars,
            self.max_description_chars,
        )
        if min(limits) <= 0:
            raise ConfigError("MCP limits must be positive")


@dataclass(frozen=True)
class CLISettings:
    """Text shown by the command-line interface.

    Strings that are printed through ``rich`` may contain console markup.
    """

    prompt: str = "[bold cyan]You:[/bold cyan] "
    reply_label: str = "[bold cyan]Tzara:[/bold cyan] "
    banner: str = "Tzara is online. Type /help for commands or start chatting."
    goodbye: str = "Tzara: Goodbye."
    spinner: str = "dots"
    thinking_status: str = "thinking..."
    loading_status: str = "loading model..."
    switching_status: str = "switching model..."
    interrupted: str = "[interrupted]"
    load_failed: str = "Could not load the model"
    new_conversation: str = "Started a new conversation."
    log_hint: str = "Details are in the log: {path}"
    memory_off: str = "Memory is turned off (TZARA_MEMORY=false)."
    mcp_off: str = "Connections to other apps are turned off (TZARA_MCP=false)."
    mcp_empty: str = (
        'No apps are connected. List them in {file} (see the README, "Connecting apps").'
    )
    mcp_header: str = "Connected apps:"
    memory_empty: str = "Tzara has not remembered anything about you yet."
    memory_header: str = "What Tzara remembers about you ({count} conversations stored):"
    memory_erased: str = "Forgot everything: all facts and all stored conversations."
    erase_usage: str = (
        "This forgets every fact and every stored conversation. To go ahead: /erase-memory yes"
    )


@dataclass(frozen=True)
class WriteSettings:
    """Settings for writing into the user's own text fields in other apps (LinkedIn, Slack...).

    Attributes:
        enabled: Whether the server answers write requests.
        places: For each kind of place, words that identify it: found in the app's name or
            identifier, the page's host, or the window title. The first kind that matches
            decides the style of what is written.
        instruction_starts: Words a field's text may start with that make it an instruction
            ("reply that I agree") rather than a draft to polish.
        sample_count: How many of the user's own earlier texts for that place are shown to the
            model as examples of how they write.
        max_visible_chars: Most conversation text from the screen given to the model (its end
            is kept: the latest messages matter most).
        max_field_chars: Longest field text or selection given to the model.
        max_output_chars: Longest text written.
    """

    enabled: bool = True
    places: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "chat": (
                "slack", "discord", "teams", "whatsapp", "telegram", "signal", "messenger",
                "mobilesms", "messages", "mattermost", "zulip", "google chat", "hangouts",
            ),
            "social": (
                "linkedin", "twitter", "x.com", "facebook", "instagram", "reddit", "threads",
                "mastodon", "bsky", "bluesky", "youtube", "medium", "substack",
            ),
            "email": (
                "mail.google", "outlook", "mail", "superhuman", "thunderbird", "spark", "proton",
            ),
            "code": (
                "xcode", "vscode", "visual studio", "cursor", "intellij", "pycharm", "github",
                "gitlab", "terminal", "iterm", "sublime", "zed",
            ),
        }
    )  # fmt: skip
    instruction_starts: tuple[str, ...] = (
        "write", "reply", "respond", "answer", "say", "tell", "ask", "draft", "compose", "make",
        "translate", "suggest", "give", "decline", "accept", "thank", "congratulate", "invite",
        "politely", "/g",
    )  # fmt: skip
    sample_count: int = 3
    max_visible_chars: int = 4000
    max_field_chars: int = 4000
    max_output_chars: int = 3000

    def __post_init__(self) -> None:
        """Check the limits.

        Raises:
            ConfigError: If a limit is not positive.
        """
        if min(self.sample_count, self.max_visible_chars, self.max_field_chars) <= 0:
            raise ConfigError("write limits must be positive")
        if self.max_output_chars <= 0:
            raise ConfigError("write limits must be positive")


@dataclass(frozen=True)
class TzaraConfig:
    """Top-level configuration.

    Attributes:
        model: Default model as ``provider:model-name``, e.g. ``ollama:qwen3:8b``.
        ollama: Ollama-specific settings.
        prompts: Prompts and tool descriptions.
        cli: Text shown by the CLI.
        log: Logging settings.
        server: Settings for the local HTTP server.
        installer: Where ``tzara setup`` fetches and installs things.
        web: Web search and page reading.
        system: Tools that open things and control media on the Mac.
        research: The ``research`` tool, which reads pages to answer bigger questions.
        memory: What Tzara remembers between conversations.
        screen: What the user was looking at, sent by the Mac app.
        mcp: Connections to other apps through the Model Context Protocol.
        write: Writing into the user's text fields in other apps.
        waiting_seconds: How long the reply may be silent before the user is told what is going
            on, then how much longer before the next note. Reading a long prompt takes many
            seconds on a local model, and a spinner alone looks like a hang.
        warmup_message: Throwaway message used to load the model and prime
            Ollama's prompt cache before the user's first message.
    """

    model: str = "ollama:qwen3:8b"
    ollama: OllamaSettings = field(default_factory=OllamaSettings)
    prompts: Prompts = field(default_factory=Prompts)
    cli: CLISettings = field(default_factory=CLISettings)
    log: LogSettings = field(default_factory=LogSettings)
    server: ServerSettings = field(default_factory=ServerSettings)
    installer: InstallerSettings = field(default_factory=InstallerSettings)
    web: WebSettings = field(default_factory=WebSettings)
    system: SystemSettings = field(default_factory=SystemSettings)
    research: ResearchSettings = field(default_factory=ResearchSettings)
    memory: MemorySettings = field(default_factory=MemorySettings)
    screen: ScreenSettings = field(default_factory=ScreenSettings)
    mcp: McpSettings = field(default_factory=McpSettings)
    write: WriteSettings = field(default_factory=WriteSettings)
    waiting_seconds: tuple[float, ...] = (2.0, 10.0, 30.0)
    warmup_message: str = "hi"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TzaraConfig:
        """Build a config, overriding defaults from ``TZARA_*`` variables.

        Recognised variables: ``TZARA_MODEL``, ``TZARA_OLLAMA_NUM_CTX``,
        ``TZARA_OLLAMA_KEEP_ALIVE``, ``TZARA_OLLAMA_REASONING``,
        ``TZARA_OLLAMA_RESERVED_TOKENS``, ``TZARA_LOG_LEVEL``, ``TZARA_LOG_FILE``,
        ``TZARA_SERVER_PORT``, ``TZARA_DATA_DIR``, ``TZARA_RELEASE_URL``,
        ``TZARA_APP_DIR``, ``TZARA_WEB_SEARCH``, ``TZARA_SYSTEM_TOOLS``, ``TZARA_MEMORY``,
        ``TZARA_SCREEN`` and ``TZARA_MCP``.

        Args:
            env: Variables to read. Defaults to ``os.environ``.

        Returns:
            The resulting configuration.

        Raises:
            ConfigError: If a variable is set to an invalid value.
        """
        reader = EnvReader(os.environ if env is None else env)
        defaults = cls()
        return replace(
            defaults,
            model=reader.text("MODEL", defaults.model),
            ollama=OllamaSettings(
                num_ctx=reader.integer("OLLAMA_NUM_CTX", defaults.ollama.num_ctx),
                keep_alive=reader.text("OLLAMA_KEEP_ALIVE", defaults.ollama.keep_alive),
                reasoning=reader.boolean("OLLAMA_REASONING", defaults.ollama.reasoning),
                reserved_tokens=reader.integer(
                    "OLLAMA_RESERVED_TOKENS", defaults.ollama.reserved_tokens
                ),
            ),
            log=replace(
                defaults.log,
                level=reader.choice("LOG_LEVEL", defaults.log.level, LogSettings.LEVELS),
                file=reader.path("LOG_FILE", defaults.log.file),
            ),
            server=replace(
                defaults.server,
                port=reader.integer("SERVER_PORT", defaults.server.port),
                data_dir=reader.path("DATA_DIR", defaults.server.data_dir),
            ),
            installer=replace(
                defaults.installer,
                release_url=reader.text("RELEASE_URL", defaults.installer.release_url),
                app_dir=reader.path("APP_DIR", defaults.installer.app_dir),
            ),
            web=replace(
                defaults.web,
                enabled=reader.boolean("WEB_SEARCH", defaults.web.enabled),
            ),
            system=replace(
                defaults.system,
                enabled=reader.boolean("SYSTEM_TOOLS", defaults.system.enabled),
            ),
            memory=replace(
                defaults.memory,
                enabled=reader.boolean("MEMORY", defaults.memory.enabled),
            ),
            screen=replace(
                defaults.screen,
                enabled=reader.boolean("SCREEN", defaults.screen.enabled),
            ),
            mcp=replace(
                defaults.mcp,
                enabled=reader.boolean("MCP", defaults.mcp.enabled),
            ),
        )


class EnvReader:
    """Reads typed ``TZARA_*`` values from an environment mapping."""

    PREFIX: ClassVar[str] = "TZARA_"
    TRUE_VALUES: ClassVar[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
    FALSE_VALUES: ClassVar[frozenset[str]] = frozenset({"0", "false", "no", "off"})

    def __init__(self, env: Mapping[str, str]) -> None:
        """Create a reader.

        Args:
            env: Mapping of variable names to values.
        """
        self._env = env

    def text(self, name: str, default: str) -> str:
        """Return a string value, or ``default`` when unset or blank."""
        value = self._env.get(self.PREFIX + name, "").strip()
        return value or default

    def integer(self, name: str, default: int) -> int:
        """Return a positive integer value, or ``default`` when unset.

        Raises:
            ConfigError: If the value is not a positive integer.
        """
        raw = self._env.get(self.PREFIX + name, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            raise ConfigError(f"{self.PREFIX}{name} must be an integer, got {raw!r}") from None
        if value <= 0:
            raise ConfigError(f"{self.PREFIX}{name} must be positive, got {value}")
        return value

    def boolean(self, name: str, default: bool) -> bool:
        """Return a boolean value, or ``default`` when unset.

        Raises:
            ConfigError: If the value is not a recognised boolean spelling.
        """
        raw = self._env.get(self.PREFIX + name, "").strip().lower()
        if not raw:
            return default
        if raw in self.TRUE_VALUES:
            return True
        if raw in self.FALSE_VALUES:
            return False
        raise ConfigError(f"{self.PREFIX}{name} must be true or false, got {raw!r}")

    def choice(self, name: str, default: str, choices: Collection[str]) -> str:
        """Return a value from ``choices`` (case-insensitive), or ``default`` when unset.

        Raises:
            ConfigError: If the value is not one of ``choices``.
        """
        raw = self._env.get(self.PREFIX + name, "").strip().upper()
        if not raw:
            return default
        if raw not in choices:
            raise ConfigError(
                f"{self.PREFIX}{name} must be one of {', '.join(choices)}, got {raw!r}"
            )
        return raw

    def path(self, name: str, default: Path | None) -> Path | None:
        """Return a path with ``~`` expanded, or ``default`` when unset or blank."""
        raw = self._env.get(self.PREFIX + name, "").strip()
        return Path(raw).expanduser() if raw else default
