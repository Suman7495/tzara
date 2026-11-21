import io
import json
import shutil

import httpx
import pytest

from helpers import FakeClock, FakeRunner
from tzara.config import InstallerSettings
from tzara.installer.commands import CommandResult
from tzara.installer.errors import SetupError
from tzara.installer.ollama import OllamaSetup
from tzara.installer.reporter import Reporter

URL = "http://127.0.0.1:11434"


class Harness:
    def __init__(self, handler, tmp_path, platform="darwin", runner=None, **settings):
        self.settings = InstallerSettings(ollama_app=tmp_path / "Apps" / "Ollama.app", **settings)
        self.out = io.StringIO()
        self.runner = runner or FakeRunner()
        self.clock = FakeClock()
        self.ollama = OllamaSetup(
            self.settings,
            self.runner,
            httpx.Client(transport=httpx.MockTransport(handler)),
            Reporter(self.out, interactive=False),
            platform=platform,
            sleep=self.clock.sleep,
            clock=self.clock,
        )


def ok_handler(request):
    return httpx.Response(200, json={"version": "0.1"})


class TestDetection:
    def test_installed_when_the_command_is_on_the_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/ollama")
        assert Harness(ok_handler, tmp_path).ollama.is_installed()

    def test_installed_when_the_app_is_there(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        harness = Harness(ok_handler, tmp_path)
        harness.settings.ollama_app.mkdir(parents=True)
        assert harness.ollama.is_installed()

    def test_not_installed_when_neither_is_there(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        assert not Harness(ok_handler, tmp_path).ollama.is_installed()

    def test_running_when_the_api_answers(self, tmp_path):
        assert Harness(ok_handler, tmp_path).ollama.is_running()

    def test_not_running_on_an_error_status(self, tmp_path):
        assert not Harness(lambda request: httpx.Response(500), tmp_path).ollama.is_running()

    def test_not_running_when_nothing_answers(self, tmp_path):
        def handler(request):
            raise httpx.ConnectError("refused")

        assert not Harness(handler, tmp_path).ollama.is_running()


class TestInstall:
    def archive_handler(self, request):
        return httpx.Response(200, content=b"zip bytes")

    def test_only_macos_can_install_it(self, tmp_path):
        with pytest.raises(SetupError, match=r"ollama\.com/download"):
            Harness(ok_handler, tmp_path, platform="linux").ollama.install()

    def test_uses_homebrew_when_there_is_one(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: "/opt/homebrew/bin/brew")
        harness = Harness(ok_handler, tmp_path)
        harness.ollama.install()
        assert harness.runner.calls == [["brew", "install", "--cask", "ollama"]]

    def test_falls_back_to_the_official_download_when_homebrew_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: "/opt/homebrew/bin/brew")
        runner = FakeRunner(
            lambda call: CommandResult(1, "", "boom") if call[0] == "brew" else None
        )
        harness = Harness(self.archive_handler, tmp_path, runner=runner)
        harness.ollama.install()
        assert harness.runner.commands() == ["brew", "ditto"]

    def test_downloads_and_unpacks_into_the_apps_folder_without_homebrew(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        harness = Harness(self.archive_handler, tmp_path)
        harness.ollama.install()
        ditto = harness.runner.calls[0]
        assert ditto[:2] == ["ditto", "-xk"]
        assert ditto[3] == str(harness.settings.ollama_app.parent)
        assert "Downloading Ollama" in harness.out.getvalue()

    def test_a_failed_unpack_is_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        runner = FakeRunner(lambda call: CommandResult(1, "", "disk full"))
        harness = Harness(self.archive_handler, tmp_path, runner=runner)
        with pytest.raises(SetupError, match="disk full"):
            harness.ollama.install()


class TestEnsureRunning:
    def test_does_nothing_when_it_is_already_running(self, tmp_path):
        harness = Harness(ok_handler, tmp_path)
        harness.ollama.ensure_running()
        assert harness.runner.calls == []

    def test_cannot_start_it_on_other_systems(self, tmp_path):
        def handler(request):
            raise httpx.ConnectError("refused")

        with pytest.raises(SetupError, match="ollama serve"):
            Harness(handler, tmp_path, platform="linux").ollama.ensure_running()

    def test_opens_the_app_and_waits_until_it_answers(self, tmp_path):
        answers = iter([False, False, True])

        def handler(request):
            if next(answers):
                return httpx.Response(200, json={})
            raise httpx.ConnectError("not yet")

        # first call is the "already running?" check; the polls follow
        harness = Harness(handler, tmp_path)
        harness.ollama.ensure_running()
        assert harness.runner.calls == [["open", "-a", "Ollama"]]

    def test_gives_up_when_it_never_starts(self, tmp_path):
        def handler(request):
            raise httpx.ConnectError("refused")

        harness = Harness(handler, tmp_path, wait_seconds=5)
        with pytest.raises(SetupError, match="did not start"):
            harness.ollama.ensure_running()
        assert harness.clock.now >= 5


class TestModels:
    def tags(self, *names):
        return lambda request: httpx.Response(
            200, json={"models": [{"name": name} for name in names]}
        )

    def test_has_a_downloaded_model(self, tmp_path):
        assert Harness(self.tags("qwen3:8b", "mistral:latest"), tmp_path).ollama.has_model(
            "qwen3:8b"
        )

    def test_a_model_listed_with_latest_counts(self, tmp_path):
        assert Harness(self.tags("mistral:latest"), tmp_path).ollama.has_model("mistral")

    def test_does_not_have_one_that_is_missing(self, tmp_path):
        assert not Harness(self.tags("llama3.1:8b"), tmp_path).ollama.has_model("qwen3:8b")

    def test_an_empty_list_is_fine(self, tmp_path):
        assert not Harness(lambda request: httpx.Response(200, json={}), tmp_path).ollama.has_model(
            "x"
        )

    def test_garbled_answers_and_network_failures_are_setup_errors(self, tmp_path):
        with pytest.raises(SetupError, match="which models"):
            Harness(
                lambda request: httpx.Response(200, text="not json"), tmp_path
            ).ollama.has_model("x")

        def broken(request):
            raise httpx.ConnectError("down")

        with pytest.raises(SetupError, match="which models"):
            Harness(broken, tmp_path).ollama.has_model("x")


class TestPull:
    def stream(self, *events, status=200):
        body = "\n".join(
            json.dumps(event) if isinstance(event, dict) else event for event in events
        )
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(status, content=body.encode())

        return handler, seen

    def test_streams_progress_from_ollama(self, tmp_path):
        handler, seen = self.stream(
            {"status": "pulling manifest"},
            {"status": "downloading", "total": 100, "completed": 25},
            {"status": "downloading", "total": 100, "completed": 100},
            {"status": "success"},
        )
        harness = Harness(handler, tmp_path)
        harness.ollama.pull("qwen3:8b")
        assert seen["body"] == {"model": "qwen3:8b", "stream": True}
        assert "downloading qwen3:8b  100%" in harness.out.getvalue()

    def test_blank_lines_are_ignored(self, tmp_path):
        handler, _ = self.stream("", {"status": "success"}, "   ")
        Harness(handler, tmp_path).ollama.pull("x")

    def test_an_error_from_ollama_is_reported(self, tmp_path):
        handler, _ = self.stream({"error": "pull model manifest: file does not exist"})
        with pytest.raises(SetupError, match="file does not exist"):
            Harness(handler, tmp_path).ollama.pull("nope")

    def test_a_refusal_is_reported_with_its_status(self, tmp_path):
        handler, _ = self.stream(status=500)
        with pytest.raises(SetupError, match="HTTP 500"):
            Harness(handler, tmp_path).ollama.pull("x")

    def test_an_interrupted_download_is_reported(self, tmp_path):
        def handler(request):
            raise httpx.ReadError("connection reset")

        with pytest.raises(SetupError, match="interrupted"):
            Harness(handler, tmp_path).ollama.pull("x")
