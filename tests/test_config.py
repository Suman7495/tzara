import dataclasses
from pathlib import Path

import pytest

from tzara.config import (
    ConfigError,
    EnvReader,
    InstallerSettings,
    LogSettings,
    OllamaSettings,
    ServerSettings,
    TzaraConfig,
)


def test_defaults_match_documented_values():
    config = TzaraConfig()
    assert config.model == "ollama:qwen3:8b"
    assert config.ollama.num_ctx == 16384
    assert config.ollama.keep_alive == "30m"
    assert config.ollama.reasoning is False


def test_config_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        TzaraConfig().model = "other"  # type: ignore[misc]


def test_from_env_without_variables_returns_defaults():
    assert TzaraConfig.from_env({}) == TzaraConfig()


def test_from_env_overrides_every_supported_variable():
    config = TzaraConfig.from_env(
        {
            "TZARA_MODEL": "anthropic:claude-haiku-4-5",
            "TZARA_OLLAMA_NUM_CTX": "8192",
            "TZARA_OLLAMA_KEEP_ALIVE": "2h",
            "TZARA_OLLAMA_REASONING": "true",
        }
    )
    assert config.model == "anthropic:claude-haiku-4-5"
    assert config.ollama.num_ctx == 8192
    assert config.ollama.keep_alive == "2h"
    assert config.ollama.reasoning is True


def test_from_env_ignores_blank_values():
    config = TzaraConfig.from_env({"TZARA_MODEL": "   ", "TZARA_OLLAMA_NUM_CTX": ""})
    assert config == TzaraConfig()


def test_from_env_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("TZARA_MODEL", "openai:gpt-4o")
    assert TzaraConfig.from_env().model == "openai:gpt-4o"


@pytest.mark.parametrize("value", ["abc", "1.5", "0", "-4"])
def test_invalid_integer_raises_config_error(value):
    with pytest.raises(ConfigError, match="TZARA_OLLAMA_NUM_CTX"):
        TzaraConfig.from_env({"TZARA_OLLAMA_NUM_CTX": value})


def test_invalid_boolean_raises_config_error():
    with pytest.raises(ConfigError, match="TZARA_OLLAMA_REASONING"):
        TzaraConfig.from_env({"TZARA_OLLAMA_REASONING": "maybe"})


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_boolean_true_spellings(value):
    assert EnvReader({"TZARA_FLAG": value}).boolean("FLAG", default=False) is True


@pytest.mark.parametrize("value", ["0", "false", "No", "off"])
def test_boolean_false_spellings(value):
    assert EnvReader({"TZARA_FLAG": value}).boolean("FLAG", default=True) is False


class TestHistoryBudget:
    def test_history_gets_what_the_reservation_leaves(self):
        settings = OllamaSettings(num_ctx=16384, reserved_tokens=4096)
        assert settings.history_tokens == 12288

    def test_a_reservation_that_fills_the_window_is_rejected(self):
        with pytest.raises(ConfigError, match="reserved_tokens"):
            OllamaSettings(num_ctx=4096, reserved_tokens=4096)

    def test_the_reservation_can_be_set_from_the_environment(self):
        config = TzaraConfig.from_env({"TZARA_OLLAMA_RESERVED_TOKENS": "2000"})
        assert config.ollama.reserved_tokens == 2000

    def test_an_environment_reservation_too_big_for_the_window_is_rejected(self):
        with pytest.raises(ConfigError, match="reserved_tokens"):
            TzaraConfig.from_env({"TZARA_OLLAMA_NUM_CTX": "2048"})  # default reservation is 4096


class TestLogSettings:
    def test_defaults_keep_message_text_out_of_the_log(self):
        assert LogSettings().level == "INFO"
        assert LogSettings().file is None

    def test_level_is_read_case_insensitively(self):
        assert TzaraConfig.from_env({"TZARA_LOG_LEVEL": "debug"}).log.level == "DEBUG"

    def test_invalid_level_raises_config_error(self):
        with pytest.raises(ConfigError, match="TZARA_LOG_LEVEL"):
            TzaraConfig.from_env({"TZARA_LOG_LEVEL": "loud"})

    def test_file_has_the_home_directory_expanded(self):
        config = TzaraConfig.from_env({"TZARA_LOG_FILE": "~/tzara.log"})
        assert config.log.file == Path.home() / "tzara.log"

    def test_blank_file_means_the_default_location(self):
        assert TzaraConfig.from_env({"TZARA_LOG_FILE": "  "}).log.file is None


class TestServerSettings:
    def test_defaults(self):
        settings = ServerSettings()
        assert settings.port == 8927
        assert settings.data_dir is None

    def test_a_name_may_be_forty_characters_long(self):
        assert ServerSettings().max_name_chars == 40

    def test_the_server_only_listens_on_loopback(self):
        assert ServerSettings.HOST == "127.0.0.1"

    def test_port_and_data_dir_come_from_the_environment(self, tmp_path):
        config = TzaraConfig.from_env(
            {"TZARA_SERVER_PORT": "9100", "TZARA_DATA_DIR": str(tmp_path)}
        )
        assert config.server.port == 9100
        assert config.server.data_dir == tmp_path

    @pytest.mark.parametrize("port", [0, 65536, -1])
    def test_a_port_outside_the_valid_range_is_rejected(self, port):
        with pytest.raises(ConfigError, match="port"):
            ServerSettings(port=port)

    @pytest.mark.parametrize("seconds", [0, -1.5])
    def test_a_non_positive_heartbeat_is_rejected(self, seconds):
        with pytest.raises(ConfigError, match="heartbeat_seconds"):
            ServerSettings(heartbeat_seconds=seconds)

    def test_an_environment_port_outside_the_range_is_rejected(self):
        with pytest.raises(ConfigError, match="port"):
            TzaraConfig.from_env({"TZARA_SERVER_PORT": "70000"})


class TestInstallerSettings:
    def test_the_release_address_names_a_version_and_a_file(self):
        url = InstallerSettings().release_url.format(version="1.2.3", asset="Tzara.app.zip")
        assert url == "https://github.com/Suman7495/tzara/releases/download/v1.2.3/Tzara.app.zip"

    @pytest.mark.parametrize(
        "url", ["https://x.test/{version}", "https://x.test/{asset}", "https://x.test/plain"]
    )
    def test_a_release_address_that_cannot_name_both_is_rejected(self, url):
        with pytest.raises(ConfigError, match="release_url"):
            InstallerSettings(release_url=url)

    def test_the_release_address_and_app_folder_come_from_the_environment(self, tmp_path):
        config = TzaraConfig.from_env(
            {
                "TZARA_RELEASE_URL": "https://fork.test/{version}/{asset}",
                "TZARA_APP_DIR": str(tmp_path),
            }
        )
        assert config.installer.release_url == "https://fork.test/{version}/{asset}"
        assert config.installer.app_dir == tmp_path

    def test_an_environment_release_address_without_placeholders_is_rejected(self):
        with pytest.raises(ConfigError, match="release_url"):
            TzaraConfig.from_env({"TZARA_RELEASE_URL": "https://fork.test/nothing"})

    def test_the_defaults_for_the_places_things_go(self):
        settings = InstallerSettings()
        assert settings.app_dir is None and settings.launch_agents_dir is None
        assert settings.service_label == "ai.telekinesis.tzara.server"
