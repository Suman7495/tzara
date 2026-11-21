import plistlib
from pathlib import Path

import pytest

from helpers import FakeRunner
from tzara.config import InstallerSettings
from tzara.installer.commands import CommandResult
from tzara.installer.errors import SetupError
from tzara.installer.service import LaunchAgent, ServiceInstaller


def make_agent(tmp_path, runner=None, **options):
    options.setdefault("uid", lambda: 501)
    return LaunchAgent(
        "ai.test.job",
        ["/usr/bin/python3", "-m", "tzara", "serve"],
        tmp_path / "agents",
        runner or FakeRunner(),
        **options,
    )


class TestRender:
    def test_describes_a_job_that_runs_at_login(self, tmp_path):
        definition = make_agent(tmp_path).render()
        assert definition["Label"] == "ai.test.job"
        assert definition["ProgramArguments"] == ["/usr/bin/python3", "-m", "tzara", "serve"]
        assert definition["RunAtLoad"] is True
        assert definition["ThrottleInterval"] == LaunchAgent.THROTTLE_SECONDS

    def test_restarts_after_an_unclean_exit_but_not_a_clean_one_so_stopping_works(self, tmp_path):
        keep_alive = make_agent(tmp_path, restart_on_crash=True).render()["KeepAlive"]
        assert keep_alive == {"SuccessfulExit": False}
        assert "KeepAlive" not in make_agent(tmp_path).render()

    def test_logs_and_environment_are_included_only_when_given(self, tmp_path):
        plain = make_agent(tmp_path).render()
        assert "StandardOutPath" not in plain and "EnvironmentVariables" not in plain

        full = make_agent(
            tmp_path,
            log_path=tmp_path / "logs" / "job.log",
            environment={"TZARA_SERVER_PORT": "9000"},
        ).render()
        assert (
            full["StandardOutPath"]
            == full["StandardErrorPath"]
            == str(tmp_path / "logs" / "job.log")
        )
        assert full["EnvironmentVariables"] == {"TZARA_SERVER_PORT": "9000"}


class TestInstall:
    def test_writes_the_definition_then_replaces_whatever_was_loaded(self, tmp_path):
        runner = FakeRunner()
        agent = make_agent(tmp_path, runner, log_path=tmp_path / "logs" / "job.log")
        agent.install()

        with agent.plist_path.open("rb") as handle:
            assert plistlib.load(handle) == agent.render()
        assert runner.calls == [
            ["launchctl", "bootout", "gui/501/ai.test.job"],
            ["launchctl", "bootstrap", "gui/501", str(agent.plist_path)],
        ]
        assert (tmp_path / "logs").is_dir()

    def test_is_safe_to_run_again(self, tmp_path):
        agent = make_agent(tmp_path)
        agent.install()
        agent.install()
        assert agent.plist_path.exists()

    def test_launchd_refusing_the_job_is_a_setup_error_with_its_reason(self, tmp_path):
        runner = FakeRunner(
            lambda call: (
                CommandResult(5, "", "Bootstrap failed: 5") if call[1] == "bootstrap" else None
            )
        )
        with pytest.raises(SetupError, match="Bootstrap failed: 5"):
            make_agent(tmp_path, runner).install()

    def test_the_plist_is_under_the_given_directory_and_named_for_the_label(self, tmp_path):
        assert make_agent(tmp_path).plist_path == tmp_path / "agents" / "ai.test.job.plist"


class TestUninstall:
    def test_stops_the_job_and_deletes_its_definition(self, tmp_path):
        runner = FakeRunner()
        agent = make_agent(tmp_path, runner)
        agent.install()
        runner.calls.clear()

        assert agent.uninstall() is True
        assert runner.calls == [["launchctl", "bootout", "gui/501/ai.test.job"]]
        assert not agent.plist_path.exists()

    def test_nothing_to_remove_is_not_an_error(self, tmp_path):
        assert make_agent(tmp_path).uninstall() is False


class TestServiceInstaller:
    def make(self, tmp_path, environment=None, **options):
        settings = InstallerSettings(launch_agents_dir=tmp_path / "from-settings")
        return ServiceInstaller(
            settings, FakeRunner(), environment or {}, python="/venv/bin/python", **options
        )

    def test_the_server_job_runs_this_interpreter_so_it_does_not_depend_on_path(self, tmp_path):
        job = self.make(tmp_path).server_job()
        assert job.render()["ProgramArguments"] == ["/venv/bin/python", "-m", "tzara", "serve"]
        assert job.render()["Label"] == "ai.telekinesis.tzara.server"

    def test_the_server_job_is_restarted_when_it_dies_and_logs_to_launchd_log(self, tmp_path):
        definition = self.make(tmp_path).server_job().render()
        assert definition["KeepAlive"] == {"SuccessfulExit": False}
        assert definition["StandardOutPath"].endswith("launchd.log")

    def test_tzara_settings_made_at_setup_are_kept_and_nothing_else_is(self, tmp_path):
        environment = {
            "TZARA_SERVER_PORT": "9001",
            "TZARA_MODEL": "ollama:mistral",
            "SECRET_TOKEN": "x",
            "PATH": "/bin",
        }
        definition = self.make(tmp_path, environment).server_job().render()
        assert definition["EnvironmentVariables"] == {
            "TZARA_SERVER_PORT": "9001",
            "TZARA_MODEL": "ollama:mistral",
        }

    def test_no_environment_means_no_environment_block(self, tmp_path):
        assert (
            "EnvironmentVariables"
            not in self.make(tmp_path, {"PATH": "/bin"}).server_job().render()
        )

    def test_the_app_job_opens_the_app_at_login(self, tmp_path):
        definition = self.make(tmp_path).app_job(Path("/Users/x/Applications/Tzara.app")).render()
        assert definition["ProgramArguments"] == [
            "/usr/bin/open",
            "-a",
            "/Users/x/Applications/Tzara.app",
        ]
        assert definition["Label"] == "ai.telekinesis.tzara.app"
        assert "KeepAlive" not in definition

    def test_job_files_go_where_the_settings_say(self, tmp_path):
        assert self.make(tmp_path).server_job().plist_path.parent == tmp_path / "from-settings"

    def test_an_explicit_directory_wins(self, tmp_path):
        job = self.make(tmp_path, launch_agents_dir=tmp_path / "explicit").server_job()
        assert job.plist_path.parent == tmp_path / "explicit"

    def test_defaults_to_the_users_launch_agents_folder(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        installer = ServiceInstaller(InstallerSettings(), FakeRunner(), {}, python="/p")
        assert installer.server_job().plist_path.parent == tmp_path / "Library" / "LaunchAgents"
