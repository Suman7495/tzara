import logging
import runpy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tzara.agent.memory import ConversationMemory
from tzara.cli import launcher as launcher_module
from tzara.cli.launcher import (
    EXIT_BAD_CONFIG,
    EXIT_FAILED,
    EXIT_NOT_RUNNING,
    EXIT_OK,
    Launcher,
)
from tzara.config import MemorySettings, ServerSettings, TzaraConfig
from tzara.logs import LogConfigurator
from tzara.memory import PersonalMemory
from tzara.server.daemon import DaemonError, DaemonStatus
from tzara.server.files import ServerInfo

RUNNING = DaemonStatus(
    running=True,
    info=ServerInfo("127.0.0.1", 8927, 4242),
    state="loading",
    model="ollama:qwen3:8b",
)


@pytest.fixture(autouse=True)
def no_real_logging(monkeypatch):
    """Keep main() from installing log handlers into the test process."""
    monkeypatch.setattr(LogConfigurator, "install", lambda self: None)


class FakeManager:
    """Replaces DaemonManager; each test sets what its methods do."""

    status_result = DaemonStatus(running=False)
    start_result = RUNNING
    stop_result = True
    error: DaemonError | None = None

    def __init__(self, config):
        self.config = config

    def start(self):
        if self.error:
            raise self.error
        return self.start_result

    def stop(self):
        if self.error:
            raise self.error
        return self.stop_result

    def status(self):
        return self.status_result


@pytest.fixture(name="manager")
def manager_fixture(monkeypatch):
    manager_class = type("Manager", (FakeManager,), {})
    monkeypatch.setattr(launcher_module, "DaemonManager", manager_class)
    return manager_class


class TestArguments:
    def test_help_lists_every_command(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            Launcher.main(["--help"])
        assert exit_info.value.code == 0
        output = capsys.readouterr().out
        for command in ("serve", "start", "stop", "status"):
            assert command in output

    def test_an_unknown_command_is_a_usage_error(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            Launcher.main(["frobnicate"])
        assert exit_info.value.code == 2
        assert "usage: tzara" in capsys.readouterr().err

    def test_the_old_plain_flag_is_gone(self):
        with pytest.raises(SystemExit):
            Launcher.main(["--plain"])


class TestMain:
    def test_invalid_environment_returns_status_2(self, monkeypatch, capsys):
        monkeypatch.setenv("TZARA_OLLAMA_NUM_CTX", "lots")
        assert Launcher.main(["status"]) == EXIT_BAD_CONFIG
        assert "TZARA_OLLAMA_NUM_CTX" in capsys.readouterr().err

    def test_with_no_command_it_opens_the_chat(self, monkeypatch):
        opened = []

        def chat(self):
            opened.append(self._config.model)
            return 0

        monkeypatch.setattr(Launcher, "chat", chat)
        monkeypatch.setenv("TZARA_MODEL", "ollama:mistral")
        assert Launcher.main([]) == EXIT_OK
        assert opened == ["ollama:mistral"]

    @pytest.mark.parametrize("command", ["serve", "start", "stop", "status", "setup", "uninstall"])
    def test_each_command_runs_its_method_and_returns_its_status(self, monkeypatch, command):
        monkeypatch.setattr(Launcher, command, lambda self: 42)
        assert Launcher.main([command]) == 42

    def test_logging_is_installed_with_the_configured_settings(self, monkeypatch, tmp_path):
        seen = {}

        def install(self):
            seen["settings"] = self._settings
            return tmp_path / "tzara.log"

        monkeypatch.setattr(LogConfigurator, "install", install)

        def start(self):
            seen["path"] = self._log_path
            return 0

        monkeypatch.setattr(Launcher, "start", start)
        monkeypatch.setenv("TZARA_LOG_LEVEL", "DEBUG")

        Launcher.main(["start"])
        assert seen["settings"].level == "DEBUG"
        assert seen["path"] == tmp_path / "tzara.log"

    def test_the_command_is_logged(self, monkeypatch, caplog):
        monkeypatch.setattr(Launcher, "status", lambda self: 0)
        with caplog.at_level(logging.INFO, logger="tzara"):
            Launcher.main(["status"])
        assert "tzara status: model=" in caplog.text


class TestChatAndServe:
    def test_chat_opens_the_interactive_cli(self, monkeypatch, tmp_path):
        calls: list[tuple[Any, ...]] = []

        class FakeCLI:
            def __init__(self, config, log_path, memory, personal):
                calls.append(("init", config.model, log_path, memory, personal))

            def run(self):
                calls.append(("run",))

        monkeypatch.setattr("tzara.cli.run.TzaraCLI", FakeCLI)
        config = replace(TzaraConfig(), server=ServerSettings(data_dir=tmp_path))
        launcher = Launcher(config, log_path=Path("/var/log/tzara.log"))
        assert launcher.chat() == EXIT_OK
        (_, model, log_path, memory, personal), run = calls
        assert (model, log_path, run) == (config.model, Path("/var/log/tzara.log"), ("run",))
        assert isinstance(personal, PersonalMemory) and (tmp_path / "memory.db").exists()
        assert isinstance(memory, ConversationMemory)

    def test_chat_keeps_nothing_when_memory_is_off(self, monkeypatch, tmp_path):
        calls: list[Any] = []

        class FakeCLI:
            def __init__(self, config, log_path, memory, personal):
                calls.append(personal)

            def run(self):
                pass

        monkeypatch.setattr("tzara.cli.run.TzaraCLI", FakeCLI)
        config = replace(
            TzaraConfig(),
            server=ServerSettings(data_dir=tmp_path),
            memory=MemorySettings(enabled=False),
        )
        Launcher(config).chat()
        assert calls == [None] and not (tmp_path / "memory.db").exists()

    def test_serve_runs_the_server_in_this_process(self, monkeypatch):
        served = []
        monkeypatch.setattr(
            "tzara.server.app.TzaraServer.serve",
            classmethod(lambda cls, config: served.append(config)),
        )
        config = TzaraConfig()
        assert Launcher(config).serve() == EXIT_OK
        assert served == [config]


class TestDaemonCommands:
    def run(self, command, capsys, log_path=None):
        status = getattr(Launcher(TzaraConfig(), log_path=log_path), command)()
        captured = capsys.readouterr()
        return status, captured.out, captured.err

    def test_start_reports_where_the_server_is(self, manager, capsys):
        status, out, _ = self.run("start", capsys)
        assert status == EXIT_OK
        assert "running on http://127.0.0.1:8927 (pid 4242)" in out
        assert "Model: ollama:qwen3:8b (loading)." in out

    def test_start_failure_is_reported_on_stderr(self, manager, capsys):
        manager.error = DaemonError("the server exited straight away (code 1)")
        status, out, err = self.run("start", capsys)
        assert status == EXIT_FAILED
        assert out == ""
        assert "tzara: the server exited straight away (code 1)" in err

    def test_stop_says_what_it_did(self, manager, capsys):
        assert self.run("stop", capsys)[1].strip() == "Stopped the Tzara server."
        manager.stop_result = False
        assert self.run("stop", capsys)[1].strip() == "The Tzara server is not running."

    def test_stop_failure_is_reported_on_stderr(self, manager, capsys):
        manager.error = DaemonError("process 5 does not answer as a Tzara server")
        status, _, err = self.run("stop", capsys)
        assert status == EXIT_FAILED
        assert "does not answer as a Tzara server" in err

    def test_status_of_a_running_server_exits_zero(self, manager, capsys):
        manager.status_result = RUNNING
        status, out, _ = self.run("status", capsys)
        assert status == EXIT_OK
        assert "running on http://127.0.0.1:8927" in out

    def test_status_of_a_stopped_server_exits_three(self, manager, capsys):
        status, out, _ = self.run("status", capsys)
        assert status == EXIT_NOT_RUNNING
        assert "not running" in out

    def test_a_failed_model_load_shows_the_error_and_where_the_log_is(self, manager, capsys):
        manager.status_result = DaemonStatus(
            running=True,
            info=ServerInfo("127.0.0.1", 8927, 1),
            state="failed",
            model="ollama:qwen3:8b",
            error="ollama is not running",
        )
        _, out, _ = self.run("status", capsys, log_path=Path("/var/log/tzara.log"))
        assert "Error: ollama is not running" in out
        assert "Details are in the log: /var/log/tzara.log" in out

    def test_a_running_server_without_readable_details_still_reports_its_address(
        self, manager, capsys
    ):
        manager.status_result = DaemonStatus(running=True, info=ServerInfo("127.0.0.1", 8927, 1))
        _, out, _ = self.run("status", capsys)
        assert out.strip() == "The Tzara server is running on http://127.0.0.1:8927 (pid 1)."


class FakeInstaller:
    """Replaces Installer.create(...): records what it was asked to do."""

    def __init__(self):
        self.options = None
        self.uninstalled = False

    def setup(self, options):
        self.options = options
        return 0

    def uninstall(self):
        self.uninstalled = True
        return 0


@pytest.fixture(name="installer")
def installer_fixture(monkeypatch):
    fake = FakeInstaller()
    monkeypatch.setattr(
        "tzara.installer.setup.Installer.create",
        classmethod(lambda cls, config, reporter=None: fake),
    )
    return fake


class TestSetupCommand:
    def test_everything_is_on_by_default(self, installer):
        assert Launcher.main(["setup"]) == EXIT_OK
        options = installer.options
        assert (options.model, options.service, options.app, options.open_at_login) == (
            True,
            True,
            True,
            False,
        )

    def test_each_part_can_be_turned_off(self, installer):
        Launcher.main(["setup", "--no-model", "--no-service", "--no-app"])
        options = installer.options
        assert (options.model, options.service, options.app) == (False, False, False)

    def test_the_app_can_be_opened_at_login(self, installer):
        Launcher.main(["setup", "--open-at-login"])
        assert installer.options.open_at_login is True

    def test_a_failed_setup_is_a_failed_command(self, monkeypatch):
        class Failing(FakeInstaller):
            def setup(self, options):
                return 1

        monkeypatch.setattr(
            "tzara.installer.setup.Installer.create",
            classmethod(lambda cls, config, reporter=None: Failing()),
        )
        assert Launcher.main(["setup"]) == 1

    def test_help_lists_the_options(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            Launcher.main(["setup", "--help"])
        assert exit_info.value.code == 0
        output = capsys.readouterr().out
        for option in ("--no-model", "--no-service", "--no-app", "--open-at-login"):
            assert option in output

    def test_uninstall_asks_the_installer_to_remove_things(self, installer):
        assert Launcher.main(["uninstall"]) == EXIT_OK
        assert installer.uninstalled is True

    def test_the_commands_are_listed_in_the_main_help(self, capsys):
        with pytest.raises(SystemExit):
            Launcher.main(["--help"])
        output = capsys.readouterr().out
        assert "setup" in output and "uninstall" in output


def test_python_dash_m_tzara_runs_the_launcher(monkeypatch):
    monkeypatch.setattr(Launcher, "main", classmethod(lambda cls, argv=None: 7))
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("tzara", run_name="__main__")
    assert exit_info.value.code == 7
