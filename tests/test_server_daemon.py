import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from tzara.config import ServerSettings, TzaraConfig
from tzara.server import daemon as daemon_module
from tzara.server.daemon import DaemonError, DaemonManager, DaemonStatus, ProcessTools
from tzara.server.files import ServerInfo, ServerInfoFile, ServerPaths

STATUS_PAYLOAD = {"state": "ready", "model": "ollama:qwen3:8b", "busy": False, "error": None}


class FakeClock:
    """Time that only moves when something sleeps."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeProcess:
    def __init__(self, pid, exit_code=None):
        self.pid = pid
        self.exit_code = exit_code

    def poll(self):
        return self.exit_code


class FakeTools:
    """Stands in for the operating system's process operations."""

    def __init__(self):
        self.alive: set[int] = set()
        self.signals: list[tuple[str, int]] = []
        self.spawned = 0
        self.on_spawn = None
        self.exits_on_terminate = True
        self.next_pid = 500

    def spawn(self):
        self.spawned += 1
        process = FakeProcess(self.next_pid)
        self.alive.add(process.pid)
        if self.on_spawn:
            self.on_spawn(process)
        return process

    def is_alive(self, pid):
        return pid in self.alive

    def terminate(self, pid):
        self.signals.append(("terminate", pid))
        if self.exits_on_terminate:
            self.alive.discard(pid)

    def kill(self, pid):
        self.signals.append(("kill", pid))
        self.alive.discard(pid)


class FakeServerClient:
    """Answers by process id, so a test says who is healthy."""

    healthy: ClassVar[set[int]] = set()
    payload: ClassVar[dict] = STATUS_PAYLOAD

    def __init__(self, info, token=None, timeout=2.0):
        self.info = info
        self.token = token

    def is_healthy(self):
        return self.info.pid in type(self).healthy

    def status(self):
        return type(self).payload if self.token else None


@pytest.fixture(name="env")
def env_fixture(tmp_path, monkeypatch):
    client_class = type(
        "Client", (FakeServerClient,), {"healthy": set(), "payload": STATUS_PAYLOAD}
    )
    monkeypatch.setattr(daemon_module, "ServerClient", client_class)
    clock, tools = FakeClock(), FakeTools()
    config = TzaraConfig(
        server=ServerSettings(data_dir=tmp_path, startup_timeout=5, stop_timeout=3)
    )
    manager = DaemonManager(config, tools=tools, sleep=clock.sleep, clock=clock)
    paths = ServerPaths(tmp_path)

    return SimpleNamespace(
        manager=manager,
        tools=tools,
        clock=clock,
        client=client_class,
        info_file=ServerInfoFile(paths.info_file),
        paths=paths,
    )


def record_running_server(env, pid=100, healthy=True):
    env.info_file.write(ServerInfo("127.0.0.1", 8927, pid))
    env.tools.alive.add(pid)
    if healthy:
        env.client.healthy.add(pid)
    return pid


class TestStatus:
    def test_not_running_without_a_record(self, env):
        assert env.manager.status() == DaemonStatus(running=False)

    def test_a_record_of_a_dead_process_means_not_running(self, env):
        env.info_file.write(ServerInfo("127.0.0.1", 8927, 100))
        assert env.manager.status() == DaemonStatus(running=False)

    def test_a_live_process_that_does_not_answer_is_not_running(self, env):
        record_running_server(env, healthy=False)
        status = env.manager.status()
        assert status.running is False
        assert status.info is not None

    def test_a_healthy_server_reports_its_state_when_the_token_can_be_read(self, env):
        record_running_server(env)
        env.paths.token_file.write_text("secret", encoding="utf-8")
        status = env.manager.status()
        assert status.running is True
        assert (status.state, status.model) == ("ready", "ollama:qwen3:8b")

    def test_without_a_readable_token_it_is_still_running_just_without_details(self, env):
        record_running_server(env)
        status = env.manager.status()
        assert status.running is True
        assert status.state is None

    def test_a_failed_model_load_is_reported(self, env):
        record_running_server(env)
        env.paths.token_file.write_text("secret", encoding="utf-8")
        env.client.payload = {"state": "failed", "model": "m", "busy": False, "error": "no ollama"}
        status = env.manager.status()
        assert (status.state, status.error) == ("failed", "no ollama")


class TestStart:
    def test_does_nothing_when_a_server_is_already_running(self, env):
        record_running_server(env)
        assert env.manager.start().running is True
        assert env.tools.spawned == 0

    def test_starts_a_server_and_waits_until_it_answers(self, env):
        def server_comes_up(process):
            env.info_file.write(ServerInfo("127.0.0.1", 8927, process.pid))
            env.client.healthy.add(process.pid)

        env.tools.on_spawn = server_comes_up
        status = env.manager.start()
        assert status.running is True
        assert status.info.pid == 500
        assert env.tools.spawned == 1

    def test_waits_for_the_new_server_not_a_stale_record(self, env):
        env.info_file.write(ServerInfo("127.0.0.1", 8927, 77))  # left by a dead server
        env.client.healthy.add(77)  # even if something answers for the old pid
        sleeps = []

        def server_comes_up_late(process):
            original_sleep = env.manager._sleep

            def sleeping(seconds):
                sleeps.append(seconds)
                original_sleep(seconds)
                if len(sleeps) == 3:
                    env.info_file.write(ServerInfo("127.0.0.1", 8927, process.pid))
                    env.client.healthy.add(process.pid)

            env.manager._sleep = sleeping

        env.tools.on_spawn = server_comes_up_late
        status = env.manager.start()
        assert status.info.pid == 500
        assert len(sleeps) >= 3

    def test_a_server_that_exits_straight_away_is_reported(self, env):
        env.tools.on_spawn = lambda process: setattr(process, "exit_code", 3)
        with pytest.raises(DaemonError, match=r"exited straight away \(code 3\)"):
            env.manager.start()

    def test_a_server_that_never_answers_is_stopped_and_reported(self, env):
        with pytest.raises(DaemonError, match="did not answer within 5 seconds"):
            env.manager.start()
        assert env.tools.signals == [("terminate", 500)]

    def test_refuses_when_a_server_exists_but_is_not_answering(self, env):
        record_running_server(env, healthy=False)
        with pytest.raises(DaemonError, match="not answering; run 'tzara stop'"):
            env.manager.start()
        assert env.tools.spawned == 0


class TestStop:
    def test_false_when_there_is_no_record(self, env):
        assert env.manager.stop() is False
        assert env.tools.signals == []

    def test_a_stale_record_is_cleaned_up(self, env):
        env.info_file.write(ServerInfo("127.0.0.1", 8927, 100))
        assert env.manager.stop() is False
        assert env.info_file.read() is None

    def test_stops_a_healthy_server_and_removes_its_record(self, env):
        record_running_server(env, pid=100)
        assert env.manager.stop() is True
        assert env.tools.signals == [("terminate", 100)]
        assert env.info_file.read() is None

    def test_forces_a_server_that_ignores_the_request(self, env):
        record_running_server(env, pid=100)
        env.tools.exits_on_terminate = False
        assert env.manager.stop() is True
        assert env.tools.signals == [("terminate", 100), ("kill", 100)]
        assert env.clock.now >= 3  # waited out stop_timeout first

    def test_never_signals_a_process_that_does_not_answer_as_tzara(self, env):
        """The record may be stale and the pid reused by an unrelated program."""
        record_running_server(env, pid=100, healthy=False)
        with pytest.raises(DaemonError, match="does not answer as a Tzara server"):
            env.manager.stop()
        assert env.tools.signals == []
        assert env.info_file.read() is not None


class TestProcessTools:
    def test_this_process_is_alive(self):
        assert ProcessTools.is_alive(os.getpid()) is True

    def test_a_finished_process_is_not(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        assert ProcessTools.is_alive(child.pid) is False

    def test_a_process_owned_by_someone_else_still_counts_as_alive(self, monkeypatch):
        def denied(pid, sig):
            raise PermissionError

        monkeypatch.setattr(os, "kill", denied)
        assert ProcessTools.is_alive(1) is True

    def test_terminate_and_kill_send_the_right_signals(self, monkeypatch):
        sent = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
        ProcessTools.terminate(42)
        ProcessTools.kill(43)
        assert sent == [(42, signal.SIGTERM), (43, signal.SIGKILL)]

    def test_the_server_command_runs_this_interpreter(self):
        assert (sys.executable, "-m", "tzara", "serve") == ProcessTools.START_COMMAND

    def test_spawn_detaches_the_server_from_the_terminal(self, monkeypatch):
        captured: dict[str, Any] = {}

        def fake_popen(command, **kwargs):
            captured.update(command=command, **kwargs)
            return "process"

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        assert ProcessTools().spawn() == "process"
        assert captured["command"] == list(ProcessTools.START_COMMAND)
        assert captured["start_new_session"] is True
        assert captured["stdin"] == captured["stdout"] == captured["stderr"] == subprocess.DEVNULL

    def test_spawn_really_starts_a_process(self):
        process = ProcessTools().spawn([sys.executable, "-c", "pass"])
        deadline = time.monotonic() + 10
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert process.poll() == 0
