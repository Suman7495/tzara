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
            truncates the start of the prompt once all DeepAgents tools are on.
        keep_alive: How long Ollama keeps the model loaded while idle. A model
            that has been unloaded costs several seconds to load again.
        reasoning: Whether thinking-capable models (e.g. Qwen3) think before
            answering. Off by default because it adds seconds to every reply.
        reserved_tokens: Part of ``num_ctx`` kept free for the system prompt,
            tool schemas and the model's reply. The rest is the budget for
            conversation history: once history nears it, DeepAgents summarizes
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


@dataclass(frozen=True)
class InstallerSettings:  # pylint: disable=too-many-instance-attributes
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
class WebSettings:  # pylint: disable=too-many-instance-attributes
    """Settings for searching the web and reading pages.

    This is the one feature that sends something off the machine: the search
    query goes to the search provider, and pages are fetched from their sites.
    The model and the conversation stay local.

    Attributes:
        enabled: Whether the agent gets the web tools at all.
        provider: ``duckduckgo`` (no account or key) or ``gemini`` (Google Search
            through the Gemini API, needs the key named by ``gemini_key_variable``).
        max_results: Search results returned to the model.
        max_page_chars: Most text of one page given to the model (about four
            characters per token, so the context window is not filled by one page).
        max_download_bytes: Most bytes read from one page.
        max_redirects: Redirects followed for one page, each checked again.
        timeout_seconds: Network timeout for a search or a page.
        user_agent: Sent when fetching pages.
        gemini_model: Gemini model used to search when ``provider`` is ``gemini``.
        gemini_key_variable: Environment variable holding the Gemini API key.
        gemini_url: Gemini API endpoint; ``{model}`` is replaced by ``gemini_model``.
    """

    PROVIDERS: ClassVar[tuple[str, ...]] = ("duckduckgo", "gemini")

    enabled: bool = True
    provider: str = "duckduckgo"
    max_results: int = 5
    max_page_chars: int = 6000
    max_download_bytes: int = 2_000_000
    max_redirects: int = 3
    timeout_seconds: float = 15.0
    user_agent: str = "Mozilla/5.0 (compatible; Tzara; +https://github.com/Suman7495/tzara)"
    gemini_model: str = "gemini-3.5-flash"
    gemini_key_variable: str = "GOOGLE_API_KEY"
    gemini_url: str = (
        "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    )

    def __post_init__(self) -> None:
        """Check the provider and the limits.

        Raises:
            ConfigError: If ``provider`` is unknown or a limit is not positive.
        """
        if self.provider not in self.PROVIDERS:
            raise ConfigError(
                f"web search provider must be one of {', '.join(self.PROVIDERS)}, "
                f"got {self.provider!r}"
            )
        limits = (
            self.max_results,
            self.max_page_chars,
            self.max_download_bytes,
            self.timeout_seconds,
        )
        if any(limit <= 0 for limit in limits) or self.max_redirects < 0:
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
class CLISettings:  # pylint: disable=too-many-instance-attributes
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


@dataclass(frozen=True)
class TzaraConfig:  # pylint: disable=too-many-instance-attributes
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
        harness_providers: Providers the DeepAgents harness profile is
            registered for.
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
    harness_providers: tuple[str, ...] = ("ollama", "anthropic", "openai", "google_genai")
    warmup_message: str = "hi"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TzaraConfig:
        """Build a config, overriding defaults from ``TZARA_*`` variables.

        Recognised variables: ``TZARA_MODEL``, ``TZARA_OLLAMA_NUM_CTX``,
        ``TZARA_OLLAMA_KEEP_ALIVE``, ``TZARA_OLLAMA_REASONING``,
        ``TZARA_OLLAMA_RESERVED_TOKENS``, ``TZARA_LOG_LEVEL``, ``TZARA_LOG_FILE``,
        ``TZARA_SERVER_PORT``, ``TZARA_DATA_DIR``, ``TZARA_RELEASE_URL``,
        ``TZARA_APP_DIR``, ``TZARA_WEB_SEARCH``, ``TZARA_WEB_SEARCH_PROVIDER``
        and ``TZARA_SYSTEM_TOOLS``.

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
                provider=reader.text("WEB_SEARCH_PROVIDER", defaults.web.provider).lower(),
            ),
            system=replace(
                defaults.system,
                enabled=reader.boolean("SYSTEM_TOOLS", defaults.system.enabled),
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
