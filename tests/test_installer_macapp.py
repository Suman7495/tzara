import hashlib
import io
from pathlib import Path

import httpx
import pytest

from helpers import FakeRunner
from tzara.config import InstallerSettings
from tzara.installer.commands import CommandResult
from tzara.installer.download import Downloader
from tzara.installer.errors import AppNotPublished, SetupError
from tzara.installer.macapp import AppInstaller
from tzara.installer.reporter import Reporter

ARCHIVE = b"pretend this is a zipped app"
CHECKSUM = hashlib.sha256(ARCHIVE).hexdigest()


class Harness:
    """An app installer with a fake release server and a fake `ditto`."""

    def __init__(
        self, tmp_path, platform="darwin", release=None, ditto_fails=False, version="0.1.0"
    ):
        self.app_dir = tmp_path / "Applications"
        self.settings = InstallerSettings(
            app_dir=self.app_dir, release_url="https://example.test/v{version}/{asset}"
        )
        self.requests: list[str] = []
        self.release = (
            release
            if release is not None
            else {
                "Tzara.app.zip": ARCHIVE,
                "Tzara.app.zip.sha256": f"{CHECKSUM}  Tzara.app.zip\n".encode(),
            }
        )
        self.runner = FakeRunner(self._run)
        self.ditto_fails = ditto_fails
        client = httpx.Client(transport=httpx.MockTransport(self._serve))
        reporter = Reporter(io.StringIO(), interactive=False)
        self.installer = AppInstaller(
            self.settings,
            version,
            self.runner,
            Downloader(client, reporter),
            reporter,
            platform=platform,
        )

    def _serve(self, request):
        self.requests.append(str(request.url))
        content = self.release.get(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, content=content) if content is not None else httpx.Response(404)

    def _run(self, call):
        if call[0] == "ditto":
            if self.ditto_fails:
                return CommandResult(1, "", "cannot write")
            (Path(call[3]) / "Tzara.app" / "Contents" / "MacOS").mkdir(parents=True)
            (Path(call[3]) / "Tzara.app" / "Contents" / "MacOS" / "Tzara").write_text("binary")
        return None


class TestInstall:
    def test_downloads_the_release_for_this_version_and_unpacks_it(self, tmp_path):
        harness = Harness(tmp_path, version="1.2.3")
        path = harness.installer.install()

        assert path == harness.app_dir / "Tzara.app"
        assert (path / "Contents" / "MacOS" / "Tzara").exists()
        assert harness.requests[0] == "https://example.test/v1.2.3/Tzara.app.zip"
        assert harness.requests[1] == "https://example.test/v1.2.3/Tzara.app.zip.sha256"

    def test_quits_a_running_copy_before_replacing_it_and_clears_the_quarantine_flag(
        self, tmp_path
    ):
        harness = Harness(tmp_path)
        harness.installer.install()
        assert harness.runner.commands() == ["pkill", "ditto", "xattr"]
        assert harness.runner.calls[0] == ["pkill", "-x", "Tzara"]
        assert harness.runner.calls[2][:3] == ["xattr", "-dr", "com.apple.quarantine"]

    def test_unpacks_with_ditto_to_keep_the_signature_and_executable_bits(self, tmp_path):
        harness = Harness(tmp_path)
        harness.installer.install()
        ditto = next(call for call in harness.runner.calls if call[0] == "ditto")
        assert ditto[1] == "-xk" and ditto[3] == str(harness.app_dir)

    def test_replaces_an_older_copy(self, tmp_path):
        harness = Harness(tmp_path)
        old = harness.app_dir / "Tzara.app"
        (old / "Contents").mkdir(parents=True)
        (old / "Contents" / "stale-file").write_text("old")
        harness.installer.install()
        assert not (old / "Contents" / "stale-file").exists()

    def test_a_corrupt_download_is_refused_before_anything_is_replaced(self, tmp_path):
        harness = Harness(
            tmp_path, release={"Tzara.app.zip": ARCHIVE, "Tzara.app.zip.sha256": b"0" * 64}
        )
        with pytest.raises(SetupError, match="checksum"):
            harness.installer.install()
        assert "ditto" not in harness.runner.commands()
        assert "pkill" not in harness.runner.commands()

    def test_a_missing_checksum_file_is_a_failure_not_a_pass(self, tmp_path):
        harness = Harness(tmp_path, release={"Tzara.app.zip": ARCHIVE, "Tzara.app.zip.sha256": b""})
        with pytest.raises(SetupError, match="checksum"):
            harness.installer.install()

    def test_the_checksum_comparison_ignores_case(self, tmp_path):
        upper = CHECKSUM.upper().encode()
        Harness(
            tmp_path, release={"Tzara.app.zip": ARCHIVE, "Tzara.app.zip.sha256": upper}
        ).installer.install()

    def test_no_release_for_this_version_says_how_to_carry_on(self, tmp_path):
        harness = Harness(tmp_path, release={}, version="9.9.9")
        with pytest.raises(AppNotPublished, match=r"no app build for version 9\.9\.9") as error:
            harness.installer.install()
        assert "make app" in str(error.value)

    def test_other_download_failures_are_passed_on(self, tmp_path):
        harness = Harness(tmp_path)
        harness.release.clear()

        def broken(request):
            return httpx.Response(500)

        harness.installer._downloader._client = httpx.Client(transport=httpx.MockTransport(broken))
        with pytest.raises(SetupError, match="HTTP 500"):
            harness.installer.install()

    def test_a_failed_unpack_is_reported(self, tmp_path):
        with pytest.raises(SetupError, match="cannot write"):
            Harness(tmp_path, ditto_fails=True).installer.install()

    def test_the_app_is_macos_only(self, tmp_path):
        harness = Harness(tmp_path, platform="linux")
        with pytest.raises(SetupError, match="macOS"):
            harness.installer.install()
        assert harness.requests == []


class TestLocationAndLifecycle:
    def test_defaults_to_the_users_applications_folder(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        harness = Harness(tmp_path)
        harness.settings = InstallerSettings()
        installer = AppInstaller(
            harness.settings,
            "0.1.0",
            harness.runner,
            harness.installer._downloader,
            Reporter(io.StringIO()),
        )
        assert installer.app_path == tmp_path / "Applications" / "Tzara.app"

    def test_open_launches_the_installed_app(self, tmp_path):
        harness = Harness(tmp_path)
        harness.installer.open()
        assert harness.runner.calls == [["open", str(harness.app_dir / "Tzara.app")]]

    def test_uninstall_quits_and_removes_the_app(self, tmp_path):
        harness = Harness(tmp_path)
        harness.installer.install()
        harness.runner.calls.clear()
        assert harness.installer.uninstall() is True
        assert not (harness.app_dir / "Tzara.app").exists()
        assert harness.runner.calls[0] == ["pkill", "-x", "Tzara"]

    def test_uninstall_with_no_app_says_so(self, tmp_path):
        assert Harness(tmp_path).installer.uninstall() is False
