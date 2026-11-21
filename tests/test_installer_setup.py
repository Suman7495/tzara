import io
from pathlib import Path

from helpers import FakeClock
from tzara.config import InstallerSettings, TzaraConfig
from tzara.installer.errors import AppNotPublished, SetupError
from tzara.installer.reporter import Reporter
from tzara.installer.setup import Installer, SetupOptions
from tzara.server.daemon import DaemonError, DaemonStatus

APP = Path("/Users/x/Applications/Tzara.app")


class FakeOllama:
    def __init__(self, installed=True, running=True, has_model=True):
        self.installed, self.running, self.model_present = installed, running, has_model
        self.log: list[str] = []
        self.fail_with: SetupError | None = None

    def is_installed(self):
        return self.installed

    def install(self):
        self.log.append("install")
        self.installed = True
        self._maybe_fail()

    def ensure_running(self):
        self.log.append("ensure_running")
        self._maybe_fail()

    def has_model(self, name):
        self.log.append(f"has_model {name}")
        return self.model_present

    def pull(self, name):
        self.log.append(f"pull {name}")
        self._maybe_fail()

    def _maybe_fail(self):
        if self.fail_with:
            raise self.fail_with


class FakeJob:
    def __init__(self, name, log, existed=True, fail=None):
        self.name, self.log, self.existed, self.fail = name, log, existed, fail

    def install(self):
        self.log.append(f"{self.name}.install")
        if self.fail:
            raise self.fail

    def uninstall(self):
        self.log.append(f"{self.name}.uninstall")
        return self.existed


class FakeService:
    def __init__(self, log, **options):
        self.server, self.app = (
            FakeJob("server_job", log, **options),
            FakeJob("app_job", log, **options),
        )

    def server_job(self):
        return self.server

    def app_job(self, path):
        assert path == APP
        return self.app


class FakeApps:
    app_path = APP

    def __init__(self, log, uninstall_result=True, fail=None):
        self.log, self.uninstall_result, self.fail = log, uninstall_result, fail

    def install(self):
        self.log.append("app.install")
        if self.fail:
            raise self.fail
        return APP

    def open(self):
        self.log.append("app.open")

    def uninstall(self):
        self.log.append("app.uninstall")
        return self.uninstall_result


class FakeDaemon:
    """Reports `running` according to a script, one answer per status() call."""

    def __init__(self, log, answers=(False,), stop_result=True, stop_error=None):
        self.log, self.answers, self.stop_result, self.stop_error = (
            log,
            list(answers),
            stop_result,
            stop_error,
        )

    def status(self):
        running = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return DaemonStatus(running=running)

    def stop(self):
        self.log.append("daemon.stop")
        if self.stop_error:
            raise self.stop_error
        return self.stop_result


class Setup:
    def __init__(
        self,
        platform="darwin",
        model="ollama:qwen3:8b",
        ollama=None,
        daemon_answers=(False, True),
        **parts,
    ):
        self.log: list[str] = []
        self.out = io.StringIO()
        self.clock = FakeClock()
        self.ollama = ollama or FakeOllama()
        self.ollama.log = self.log
        self.service = FakeService(self.log, **parts.pop("service", {}))
        self.apps = FakeApps(self.log, **parts.pop("apps", {}))
        self.daemon = FakeDaemon(self.log, daemon_answers, **parts.pop("daemon", {}))
        config = TzaraConfig(model=model, installer=InstallerSettings(wait_seconds=5))
        self.installer = Installer(
            config,
            Reporter(self.out, interactive=False),
            self.ollama,
            self.service,
            self.apps,
            self.daemon,
            platform=platform,
            sleep=self.clock.sleep,
            clock=self.clock,
        )

    @property
    def text(self):
        return self.out.getvalue()


class TestFullSetup:
    def test_does_everything_in_order_on_a_fresh_mac(self):
        setup = Setup(ollama=FakeOllama(installed=False, has_model=False))
        assert setup.installer.setup(SetupOptions()) == 0
        assert setup.log == [
            "install",
            "ensure_running",
            "has_model qwen3:8b",
            "pull qwen3:8b",
            "server_job.install",
            "app.install",
            "app.open",
        ]

    def test_does_only_what_is_missing(self):
        setup = Setup(ollama=FakeOllama(installed=True, has_model=True))
        setup.installer.setup(SetupOptions())
        assert "install" not in setup.log and "pull qwen3:8b" not in setup.log
        assert "qwen3:8b is already downloaded" in setup.text

    def test_finishes_by_saying_how_to_use_it(self):
        setup = Setup()
        setup.installer.setup(SetupOptions())
        assert (
            "Control+Option" in setup.text
            and "Input Monitoring" in setup.text
            and "tzara" in setup.text
        )

    def test_the_hotkey_hint_is_left_out_when_the_app_was_not_installed(self):
        setup = Setup()
        setup.installer.setup(SetupOptions(app=False))
        assert "Control+Option" not in setup.text
        assert "chat in the terminal" in setup.text


class TestChoosingParts:
    def test_no_model(self):
        setup = Setup()
        setup.installer.setup(SetupOptions(model=False))
        assert not any(entry.startswith(("has_model", "pull")) for entry in setup.log)

    def test_no_service(self):
        setup = Setup()
        setup.installer.setup(SetupOptions(service=False))
        assert "server_job.install" not in setup.log

    def test_no_app(self):
        setup = Setup()
        setup.installer.setup(SetupOptions(app=False))
        assert "app.install" not in setup.log

    def test_open_at_login_adds_the_apps_login_job(self):
        setup = Setup()
        setup.installer.setup(SetupOptions(open_at_login=True))
        assert setup.log.index("app_job.install") > setup.log.index("app.install")
        assert "open at login" in setup.text

    def test_the_login_job_is_not_added_by_default(self):
        setup = Setup()
        setup.installer.setup(SetupOptions())
        assert "app_job.install" not in setup.log

    def test_a_cloud_model_has_nothing_to_download(self):
        setup = Setup(model="anthropic:claude-haiku-4-5")
        setup.installer.setup(SetupOptions())
        assert not any(entry.startswith(("has_model", "pull")) for entry in setup.log)
        assert "not a local model" in setup.text

    def test_the_model_to_download_comes_from_the_configuration(self):
        setup = Setup(model="ollama:mistral", ollama=FakeOllama(has_model=False))
        setup.installer.setup(SetupOptions())
        assert "pull mistral" in setup.log


class TestTheServer:
    def test_waits_until_the_server_answers(self):
        setup = Setup(daemon_answers=(False, False, False, True))
        assert setup.installer.setup(SetupOptions(app=False)) == 0
        assert setup.clock.now >= 2

    def test_replaces_a_server_that_was_already_running(self):
        setup = Setup(daemon_answers=(True, True))
        setup.installer.setup(SetupOptions(app=False))
        assert setup.log.index("daemon.stop") < setup.log.index("server_job.install")
        assert "Replacing the server" in setup.text

    def test_a_server_that_never_starts_fails_setup_with_where_to_look(self):
        setup = Setup(daemon_answers=(False,))
        assert setup.installer.setup(SetupOptions(app=False)) == 1
        assert "did not start" in setup.text and "launchd.log" in setup.text

    def test_a_server_that_cannot_be_stopped_fails_setup_with_the_reason(self):
        setup = Setup(
            daemon_answers=(True,),
            daemon={"stop_error": DaemonError("pid 5 does not answer as a Tzara server")},
        )
        assert setup.installer.setup(SetupOptions(app=False)) == 1
        assert "does not answer as a Tzara server" in setup.text
        assert "server_job.install" not in setup.log

    def test_launchd_refusing_the_job_fails_setup(self):
        setup = Setup(service={"fail": SetupError("launchd would not load it")})
        assert setup.installer.setup(SetupOptions(app=False)) == 1
        assert "launchd would not load it" in setup.text


class TestFailures:
    def test_a_failing_step_stops_setup_and_exits_nonzero(self):
        ollama = FakeOllama(installed=False)
        ollama.fail_with = SetupError("Install Ollama from https://ollama.com/download")
        setup = Setup(ollama=ollama)
        assert setup.installer.setup(SetupOptions()) == 1
        assert "ollama.com/download" in setup.text
        assert "server_job.install" not in setup.log and "app.install" not in setup.log
        assert "Done" not in setup.text

    def test_a_failed_model_download_stops_before_the_server_is_touched(self):
        ollama = FakeOllama(has_model=False)
        ollama.fail_with = SetupError("The download of qwen3:8b was interrupted")
        setup = Setup(ollama=ollama)
        assert setup.installer.setup(SetupOptions()) == 1
        assert "server_job.install" not in setup.log

    def test_a_failed_app_download_stops_setup(self):
        setup = Setup(
            apps={"fail": SetupError("The downloaded app does not match its published checksum")}
        )
        assert setup.installer.setup(SetupOptions()) == 1
        assert "checksum" in setup.text


class TestAnAppThatIsNotPublishedYet:
    def test_is_a_warning_not_a_failure_because_the_rest_still_works(self):
        setup = Setup(
            apps={
                "fail": AppNotPublished(
                    "There is no app build for version 0.1.0 yet. Use 'make app'."
                )
            }
        )
        assert setup.installer.setup(SetupOptions()) == 0
        assert "! There is no app build" in setup.text
        assert "Done" in setup.text

    def test_nothing_is_opened_and_no_hotkey_hint_is_given(self):
        setup = Setup(apps={"fail": AppNotPublished("no build")})
        setup.installer.setup(SetupOptions(open_at_login=True))
        assert "app.open" not in setup.log and "app_job.install" not in setup.log
        assert "Control+Option" not in setup.text

    def test_the_server_was_still_set_up(self):
        setup = Setup(apps={"fail": AppNotPublished("no build")})
        setup.installer.setup(SetupOptions())
        assert "server_job.install" in setup.log


class TestOtherSystems:
    def test_without_login_jobs_it_says_how_to_start_the_server(self):
        setup = Setup(platform="linux")
        assert setup.installer.setup(SetupOptions()) == 0
        assert "tzara start" in setup.text
        assert "server_job.install" not in setup.log

    def test_the_app_is_skipped_rather_than_failed(self):
        setup = Setup(platform="linux")
        setup.installer.setup(SetupOptions())
        assert "app.install" not in setup.log
        assert "macOS-only" in setup.text


class TestUninstall:
    def test_removes_the_jobs_the_app_and_a_running_server(self):
        setup = Setup()
        assert setup.installer.uninstall() == 0
        assert setup.log == [
            "server_job.uninstall",
            "app_job.uninstall",
            "app.uninstall",
            "daemon.stop",
        ]
        for removed in ("server's login job", "app's login job", "the app", "running server"):
            assert removed in setup.text

    def test_leaves_ollama_and_the_models_alone_and_says_so(self):
        setup = Setup()
        setup.installer.uninstall()
        assert "Ollama and its models" in setup.text
        assert not any("ollama" in entry.lower() for entry in setup.log)

    def test_says_when_there_was_nothing_to_remove(self):
        setup = Setup(
            service={"existed": False},
            apps={"uninstall_result": False},
            daemon={"stop_result": False},
        )
        setup.installer.uninstall()
        assert "nothing to remove" in setup.text

    def test_a_server_that_cannot_be_stopped_is_a_warning_not_a_failure(self):
        setup = Setup(daemon={"stop_error": DaemonError("not a tzara server")})
        assert setup.installer.uninstall() == 0
        assert "not a tzara server" in setup.text

    def test_on_other_systems_only_the_server_is_stopped(self):
        setup = Setup(platform="linux")
        setup.installer.uninstall()
        assert setup.log == ["daemon.stop"]


def test_create_wires_up_the_real_parts_without_touching_the_network_or_the_system():
    installer = Installer.create(TzaraConfig(), Reporter(io.StringIO()))
    assert isinstance(installer, Installer)
