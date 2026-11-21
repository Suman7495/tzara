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
class TzaraConfig:
    """Top-level configuration.

    Attributes:
        model: Default model as ``provider:model-name``, e.g. ``ollama:qwen3:8b``.
        ollama: Ollama-specific settings.
        prompts: Prompts and tool descriptions.
        cli: Text shown by the CLI.
        log: Logging settings.
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
    harness_providers: tuple[str, ...] = ("ollama", "anthropic", "openai", "google_genai")
    warmup_message: str = "hi"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TzaraConfig:
        """Build a config, overriding defaults from ``TZARA_*`` variables.

        Recognised variables: ``TZARA_MODEL``, ``TZARA_OLLAMA_NUM_CTX``,
        ``TZARA_OLLAMA_KEEP_ALIVE``, ``TZARA_OLLAMA_REASONING``,
        ``TZARA_OLLAMA_RESERVED_TOKENS``, ``TZARA_LOG_LEVEL`` and ``TZARA_LOG_FILE``.

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
