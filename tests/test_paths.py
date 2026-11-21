from pathlib import Path

from tzara.paths import DataLocation

HOME = Path("/home/someone")


def test_macos():
    path = DataLocation.default_dir(platform="darwin", env={}, home=HOME)
    assert path == HOME / "Library" / "Application Support" / "tzara"


def test_linux_uses_xdg_data_home_when_set():
    path = DataLocation.default_dir(platform="linux", env={"XDG_DATA_HOME": "/var/data"}, home=HOME)
    assert path == Path("/var/data/tzara")


def test_linux_falls_back_to_local_share():
    assert DataLocation.default_dir(platform="linux", env={}, home=HOME) == (
        HOME / ".local" / "share" / "tzara"
    )


def test_windows_uses_appdata_when_set():
    path = DataLocation.default_dir(platform="win32", env={"APPDATA": "/roaming"}, home=HOME)
    assert path == Path("/roaming/tzara")


def test_windows_falls_back_to_the_home_directory():
    path = DataLocation.default_dir(platform="win32", env={}, home=HOME)
    assert path == HOME / "AppData" / "Roaming" / "tzara"


def test_defaults_to_the_running_platform():
    assert DataLocation.default_dir().name == "tzara"
