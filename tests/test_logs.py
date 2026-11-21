import logging
import warnings
from pathlib import Path

import pytest

from tzara.config import LogSettings
from tzara.logs import LogConfigurator, LogLocation

HOME = Path("/home/someone")


class TestLogLocation:
    def test_macos(self):
        path = LogLocation.default_file(platform="darwin", env={}, home=HOME)
        assert path == HOME / "Library" / "Logs" / "tzara" / "tzara.log"

    def test_linux_uses_xdg_state_home_when_set(self):
        path = LogLocation.default_file(
            platform="linux", env={"XDG_STATE_HOME": "/var/state"}, home=HOME
        )
        assert path == Path("/var/state/tzara/tzara.log")

    def test_linux_falls_back_to_local_state(self):
        path = LogLocation.default_file(platform="linux", env={}, home=HOME)
        assert path == HOME / ".local" / "state" / "tzara" / "tzara.log"

    def test_windows_uses_local_app_data_when_set(self):
        path = LogLocation.default_file(
            platform="win32", env={"LOCALAPPDATA": "/appdata"}, home=HOME
        )
        assert path == Path("/appdata/tzara/Logs/tzara.log")

    def test_windows_falls_back_to_the_home_directory(self):
        path = LogLocation.default_file(platform="win32", env={}, home=HOME)
        assert path == HOME / "AppData" / "Local" / "tzara" / "Logs" / "tzara.log"

    def test_defaults_to_the_running_platform(self):
        assert LogLocation.default_file().name == "tzara.log"


@pytest.fixture(name="installed")
def installed_fixture(tmp_path):
    """Install logging into a temp file; restore global logging state afterwards."""
    root = logging.getLogger()
    tzara_logger = logging.getLogger("tzara")
    before = (list(root.handlers), root.level, tzara_logger.level)
    configurators: list[LogConfigurator] = []

    def install(**settings):
        settings.setdefault("file", tmp_path / "logs" / "tzara.log")
        configurator = LogConfigurator(LogSettings(**settings))
        configurators.append(configurator)
        return configurator, configurator.install()

    yield install

    for configurator in configurators:
        configurator.uninstall()
    root.handlers[:] = before[0]
    root.setLevel(before[1])
    tzara_logger.setLevel(before[2])


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestLogConfigurator:
    def test_creates_the_log_directory_and_returns_the_file(self, installed, tmp_path):
        _, path = installed()
        assert path == tmp_path / "logs" / "tzara.log"
        assert path.parent.is_dir()

    def test_writes_tzara_messages_with_a_timestamp_level_and_logger_name(self, installed):
        _, path = installed(level="INFO")
        logging.getLogger("tzara.something").info("hello log")
        line = read(path).strip()
        assert "INFO" in line
        assert "tzara.something: hello log" in line
        assert line[:4].isdigit()  # starts with the year

    def test_debug_messages_are_hidden_at_info_level(self, installed):
        _, path = installed(level="INFO")
        logging.getLogger("tzara.x").debug("too chatty")
        assert "too chatty" not in read(path)

    def test_debug_messages_are_written_at_debug_level(self, installed):
        _, path = installed(level="DEBUG")
        logging.getLogger("tzara.x").debug("detail")
        assert "detail" in read(path)

    def test_third_party_info_is_hidden_but_warnings_are_kept(self, installed):
        _, path = installed(level="DEBUG")
        logging.getLogger("httpx").info("HTTP Request: POST ...")
        logging.getLogger("httpx").warning("connection reset")
        text = read(path)
        assert "HTTP Request" not in text
        assert "connection reset" in text

    def test_python_warnings_go_to_the_log_instead_of_the_terminal(self, installed):
        _, path = installed()
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.warn("deprecated thing", DeprecationWarning, stacklevel=1)
        assert "deprecated thing" in read(path)

    def test_installing_twice_does_not_duplicate_lines(self, installed):
        configurator, path = installed()
        configurator.install()
        logging.getLogger("tzara.x").info("only once")
        assert read(path).count("only once") == 1

    def test_uninstall_stops_logging_and_is_safe_to_repeat(self, installed):
        configurator, path = installed()
        configurator.uninstall()
        configurator.uninstall()
        logging.getLogger("tzara.x").error("after uninstall")
        assert "after uninstall" not in read(path)

    def test_rotates_when_the_file_gets_large(self, installed):
        _, path = installed(max_bytes=300, backup_count=1)
        for number in range(20):
            logging.getLogger("tzara.x").info("line %d %s", number, "x" * 40)
        assert path.with_name("tzara.log.1").exists()

    def test_an_unwritable_location_is_reported_not_fatal(self, installed, tmp_path, capsys):
        blocker = tmp_path / "blocker"
        blocker.write_text("a file, so it cannot be a directory")
        _, path = installed(file=blocker / "tzara.log")

        assert path is None
        assert "cannot write log file" in capsys.readouterr().err
        logging.getLogger("tzara.x").error("goes nowhere, but must not raise")
