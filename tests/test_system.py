"""Tests for the tools that act on the Mac, with a fake runner instead of ``open``."""

import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from tzara.config import ConfigError, SystemSettings, TzaraConfig
from tzara.tools.system import (
    AppOpener,
    ControlMedia,
    MediaController,
    OpenApp,
    OpenPath,
    OpenWebsite,
    PathOpener,
    SubprocessRunner,
    SystemActionError,
    SystemTools,
    WebsiteOpener,
)


class FakeRunner:
    """Records the commands and answers as scripted."""

    def __init__(self, answers=None, error=None):
        self.calls: list[list[str]] = []
        self._answers = answers or {}
        self._error = error

    def run(self, arguments):
        self.calls.append(arguments)
        if self._error:
            raise SystemActionError(self._error)
        return self._answers.get(arguments[-1], "")


SETTINGS = SystemSettings()


class TestAppOpener:
    def test_opens_an_app_by_name(self):
        runner = FakeRunner()
        assert AppOpener(SETTINGS, runner).open(" Safari ") == "Opened Safari."
        assert runner.calls == [["open", "-a", "Safari"]]

    @pytest.mark.parametrize("name", ["", "  ", "-g", "/Applications/X.app", "a\nb", "x" * 101])
    def test_refuses_names_that_are_not_plain(self, name):
        runner = FakeRunner()
        with pytest.raises(SystemActionError, match="name of an application"):
            AppOpener(SETTINGS, runner).open(name)
        assert not runner.calls

    def test_passes_on_a_failure(self):
        with pytest.raises(SystemActionError, match="Unable to find"):
            AppOpener(SETTINGS, FakeRunner(error="Unable to find application")).open("Nope")


class TestWebsiteOpener:
    def test_opens_https(self):
        runner = FakeRunner()
        assert "github.com" in WebsiteOpener(SETTINGS, runner).open("https://github.com/x")
        assert runner.calls == [["open", "https://github.com/x"]]

    @pytest.mark.parametrize(
        "url", ["file:///etc/passwd", "javascript:alert(1)", "github.com", "https://", "-a Foo"]
    )
    def test_refuses_anything_but_http(self, url):
        runner = FakeRunner()
        with pytest.raises(SystemActionError, match="http"):
            WebsiteOpener(SETTINGS, runner).open(url)
        assert not runner.calls


class TestPathOpener:
    def test_opens_a_file_and_a_folder(self, tmp_path):
        note = tmp_path / "note.txt"
        note.write_text("hi")
        runner = FakeRunner()
        opener = PathOpener(SETTINGS, runner)
        opener.open(str(note))
        opener.open(str(tmp_path))
        assert runner.calls == [
            ["open", str(note.resolve())],
            ["open", str(tmp_path.resolve())],
        ]

    def test_expands_the_home_folder(self, tmp_path):
        runner = FakeRunner()
        with patch.dict("os.environ", {"HOME": str(tmp_path)}):
            PathOpener(SETTINGS, runner).open("~")
        assert runner.calls == [["open", str(tmp_path.resolve())]]

    def test_needs_a_full_path(self):
        with pytest.raises(SystemActionError, match="full path"):
            PathOpener(SETTINGS, FakeRunner()).open("notes.txt")

    def test_missing_path(self, tmp_path):
        with pytest.raises(SystemActionError, match="does not exist"):
            PathOpener(SETTINGS, FakeRunner()).open(str(tmp_path / "gone"))

    @pytest.mark.parametrize("name", ["run.command", "x.SH", "Thing.app", "a.pkg", "l.webloc"])
    def test_never_opens_items_that_run_programs(self, tmp_path, name):
        item = tmp_path / name
        item.write_text("x")
        runner = FakeRunner()
        with pytest.raises(SystemActionError, match="run programs"):
            PathOpener(SETTINGS, runner).open(str(item))
        assert not runner.calls

    def test_a_link_to_a_program_is_judged_by_where_it_points(self, tmp_path):
        target = tmp_path / "evil.command"
        target.write_text("x")
        link = tmp_path / "harmless.txt"
        link.symlink_to(target)
        with pytest.raises(SystemActionError, match="run programs"):
            PathOpener(SETTINGS, FakeRunner()).open(str(link))


class TestMediaController:
    @staticmethod
    def controller(running=("Music",), **kwargs):
        answers = {f'application "{p}" is running': "true" for p in running}
        runner = FakeRunner(answers, **kwargs)
        return MediaController(SETTINGS, runner), runner

    def test_transport_goes_to_the_running_player(self):
        controller, runner = self.controller(running=("Spotify",))
        assert controller.control("next") == "Sent next to Spotify."
        assert runner.calls[-1] == ["osascript", "-e", 'tell application "Spotify" to next track']

    def test_prefers_the_first_player(self):
        controller, runner = self.controller(running=("Music", "Spotify"))
        controller.control("pause")
        assert runner.calls[-1][-1] == 'tell application "Music" to pause'

    def test_does_not_start_a_player(self):
        controller, runner = self.controller(running=())
        with pytest.raises(SystemActionError, match="Music or Spotify"):
            controller.control("play")
        assert all("is running" in call[-1] for call in runner.calls)

    def test_volume_and_mute(self):
        controller, runner = self.controller()
        assert controller.control("volume", 30) == "Volume set to 30."
        assert controller.control("mute") == "Muted."
        assert [call[-1] for call in runner.calls] == [
            "set volume output volume 30",
            "set volume output muted true",
        ]

    @pytest.mark.parametrize("level", [None, -1, 101])
    def test_volume_needs_a_level_in_range(self, level):
        controller, runner = self.controller()
        with pytest.raises(SystemActionError, match="0 to 100"):
            controller.control("volume", level)
        assert not runner.calls

    def test_unknown_action(self):
        with pytest.raises(SystemActionError, match="Unknown"):
            self.controller()[0].control("explode")


class TestSubprocessRunner:
    def test_returns_output(self):
        assert SubprocessRunner(5).run(["echo", "hi"]) == "hi"

    def test_reports_the_last_line_of_an_error(self):
        with pytest.raises(SystemActionError, match="second"):
            SubprocessRunner(5).run(["sh", "-c", "echo first >&2; echo second >&2; exit 1"])

    def test_reports_a_silent_failure_by_name(self):
        with pytest.raises(SystemActionError, match="false failed"):
            SubprocessRunner(5).run(["false"])

    def test_missing_program(self):
        with pytest.raises(SystemActionError, match="not available"):
            SubprocessRunner(5).run(["definitely-not-a-program"])

    def test_timeout(self):
        with (
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)),
            pytest.raises(SystemActionError, match="too long"),
        ):
            SubprocessRunner(1).run(["sleep", "9"])


class TestTools:
    def test_each_tool_runs_and_returns_errors_as_text(self, tmp_path):
        runner = FakeRunner()
        tools = {t.name: t for t in SystemTools.create(TzaraConfig(), runner, "darwin")}
        assert tools["open_app"].invoke({"name": "Notes"}) == "Opened Notes."
        assert "Opened" in tools["open_website"].invoke({"url": "https://example.com"})
        assert "Opened" in tools["open_path"].invoke({"path": str(tmp_path)})
        assert "Muted" in tools["control_media"].invoke({"action": "mute"})
        assert tools["open_app"].invoke({"name": "-x"}).startswith("Error:")
        assert tools["open_website"].invoke({"url": "ftp://x"}).startswith("Error:")
        assert tools["open_path"].invoke({"path": "x"}).startswith("Error:")
        assert tools["control_media"].invoke({"action": "volume"}).startswith("Error:")

    def test_the_descriptions_come_from_the_prompts(self):
        prompts = replace(TzaraConfig().prompts, open_app_tool="custom")
        tools = SystemTools.create(replace(TzaraConfig(), prompts=prompts), FakeRunner(), "darwin")
        assert tools[0].description == "custom"

    def test_the_classes_are_the_expected_ones(self):
        tools = SystemTools.create(TzaraConfig(), FakeRunner(), "darwin")
        assert [type(t) for t in tools] == [OpenApp, OpenWebsite, OpenPath, ControlMedia]

    def test_none_when_off_or_not_a_mac(self):
        off = replace(TzaraConfig(), system=SystemSettings(enabled=False))
        assert SystemTools.create(off, FakeRunner(), "darwin") == []
        assert SystemTools.create(TzaraConfig(), FakeRunner(), "linux") == []

    def test_uses_a_real_runner_by_default(self):
        assert len(SystemTools.create(TzaraConfig(), platform="darwin")) == 4

    def test_platform_defaults_to_the_current_one(self):
        with patch("tzara.tools.system.sys.platform", "linux"):
            assert SystemTools.create(TzaraConfig()) == []


class TestSettings:
    def test_limits_must_be_positive(self):
        with pytest.raises(ConfigError):
            SystemSettings(command_timeout=0)
        with pytest.raises(ConfigError):
            SystemSettings(max_name_chars=0)

    def test_env_can_turn_them_off(self):
        assert TzaraConfig.from_env({"TZARA_SYSTEM_TOOLS": "false"}).system.enabled is False
        assert TzaraConfig.from_env({}).system.enabled is True

    def test_default_blocked_suffixes_are_lowercase_with_a_dot(self):
        assert all(s.startswith(".") and s == s.lower() for s in SystemSettings().blocked_suffixes)
        assert Path("x.command").suffix in SystemSettings().blocked_suffixes
