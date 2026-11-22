import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from helpers import StreamingFakeAgent, personal_memory
from tzara.agent.memory import ConversationMemory, HistoryMessage
from tzara.config import ServerSettings, TzaraConfig
from tzara.server.app import ShutdownAwareServer, TzaraServer
from tzara.server.files import ServerInfo, ServerInfoFile
from tzara.server.service import ChatService, DoneEvent, ServiceState

PORT = 8927
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def parse_events(text: str) -> list[tuple[str, dict]]:
    """Parse a server-sent-event body into (event name, data) pairs."""
    events = []
    for block in text.strip().split("\n\n"):
        name, data = "", {}
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        events.append((name, data))
    return events


def wait_until(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


@pytest.fixture(name="agents")
def agents_fixture():
    return StreamingFakeAgent.isolated()


@pytest.fixture(name="build")
def build_fixture(agents, tmp_path):
    """Return a factory for (client, service, server); everything is shut down afterwards."""
    clients = []

    def factory(*, started=True, settings=None, info_file=None, memory=None):
        service = ChatService(TzaraConfig(), agent_factory=agents, memory=memory)
        if started:
            service.start()
        server = TzaraServer(service, TOKEN, settings or ServerSettings(port=PORT), info_file)
        client = TestClient(server.app, base_url=BASE)
        client.__enter__()  # runs the lifespan
        clients.append(client)
        return client, service, server

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


class TestGuards:
    def test_browser_requests_are_refused_even_with_the_token(self, build):
        client, _, _ = build()
        response = client.get("/v1/status", headers={**AUTH, "Origin": "https://evil.example"})
        assert response.status_code == 403

    def test_even_a_same_site_looking_origin_is_refused(self, build):
        client, _, _ = build()
        response = client.get("/v1/health", headers={"Origin": BASE})
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "host", ["evil.example:8927", "127.0.0.1", "127.0.0.1:1", "0.0.0.0:8927"]
    )
    def test_requests_for_other_hosts_are_refused(self, build, host):
        """Defeats DNS rebinding: a web page cannot reach us under its own domain name."""
        client, _, _ = build()
        response = client.get("/v1/health", headers={"Host": host})
        assert response.status_code == 400

    @pytest.mark.parametrize(
        "host", ["127.0.0.1:8927", "localhost:8927", "LOCALHOST:8927", "[::1]:8927"]
    )
    def test_loopback_hosts_are_accepted(self, build, host):
        client, _, _ = build()
        assert client.get("/v1/health", headers={"Host": host}).status_code == 200

    def test_the_port_in_the_host_must_match_the_configured_port(self, build):
        client, _, _ = build(settings=ServerSettings(port=9100))
        assert client.get("/v1/health", headers={"Host": "127.0.0.1:9100"}).status_code == 200
        assert client.get("/v1/health", headers={"Host": "127.0.0.1:8927"}).status_code == 400

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_interactive_docs_are_not_exposed(self, build, path):
        client, _, _ = build()
        assert client.get(path, headers=AUTH).status_code == 404


class TestAuthentication:
    def test_health_needs_no_token_and_reveals_nothing_else(self, build):
        client, _, _ = build()
        response = client.get("/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/v1/status"),
            ("get", "/v1/models"),
            ("post", "/v1/chat"),
            ("post", "/v1/interrupt"),
            ("post", "/v1/conversation/new"),
            ("put", "/v1/model"),
            ("put", "/v1/name"),
            ("get", "/v1/conversation"),
        ],
    )
    def test_every_other_endpoint_needs_the_token(self, build, method, path):
        client, _, _ = build()
        assert client.request(method, path).status_code == 401

    @pytest.mark.parametrize(
        "header",
        [
            "Bearer wrong",
            "Bearer",
            "Bearer ",
            TOKEN,
            f"Basic {TOKEN}",
            f"Token {TOKEN}",
            f"Bearer {TOKEN}x",
            f"Bearer {TOKEN[:-1]}",
        ],
    )
    def test_wrong_or_malformed_credentials_are_rejected(self, build, header):
        client, _, _ = build()
        response = client.get("/v1/status", headers={"Authorization": header})
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    def test_the_token_is_compared_in_constant_time(self, build, monkeypatch):
        """A plain == would let a local attacker guess the token one byte at a time."""
        calls = []
        real = secrets.compare_digest

        def spy(first, second):
            calls.append((first, second))
            return real(first, second)

        monkeypatch.setattr(secrets, "compare_digest", spy)
        client, _, _ = build()
        client.get("/v1/status", headers=AUTH)
        assert calls == [(TOKEN.encode(), TOKEN.encode())]

    def test_the_scheme_name_is_case_insensitive(self, build):
        client, _, _ = build()
        response = client.get("/v1/status", headers={"Authorization": f"bearer {TOKEN}"})
        assert response.status_code == 200


class TestStatusAndModels:
    def test_status_of_a_ready_server(self, build):
        client, _, _ = build()
        assert client.get("/v1/status", headers=AUTH).json() == {
            "state": "ready",
            "model": TzaraConfig().model,
            "name": "Tzara",
            "busy": False,
            "error": None,
        }

    def test_status_reports_why_loading_failed(self, build, agents):
        agents.warm_up_error = ConnectionError("ollama is not running")
        client, _, _ = build()
        status = client.get("/v1/status", headers=AUTH).json()
        assert status["state"] == "failed"
        assert status["error"] == "ollama is not running"

    def test_models_lists_the_catalogue_in_display_order(self, build):
        client, _, _ = build()
        providers = client.get("/v1/models", headers=AUTH).json()["providers"]
        assert [provider["name"] for provider in providers] == [
            "ollama",
            "anthropic",
            "openai",
            "google_genai",
        ]
        ollama = providers[0]["models"]
        assert ollama[0] == {
            "id": "ollama:qwen3:8b",
            "description": "Qwen3 8B (default, fast, reliable tools, local)",
        }
        assert all(set(model) == {"id", "description"} for model in ollama)


class TestScreenContext:
    def test_the_context_reaches_the_agent(self, build, agents):
        client, _, _ = build()
        body = {"message": "translate this", "context": {"app": "Notes", "selection": "hola"}}
        assert client.post("/v1/chat", headers=AUTH, json=body).status_code == 200
        context = agents.created[0].contexts[0]
        assert (context.app, context.selection, context.window) == ("Notes", "hola", "")

    def test_a_message_without_context_has_none(self, build, agents):
        client, _, _ = build()
        client.post("/v1/chat", headers=AUTH, json={"message": "hi"})
        assert agents.created[0].contexts == [None]

    def test_a_context_with_a_wrong_type_is_refused(self, build):
        client, _, _ = build()
        body = {"message": "hi", "context": {"selection": 5}}
        assert client.post("/v1/chat", headers=AUTH, json=body).status_code == 422


class TestChat:
    def post(self, client, message="hi", **kwargs):
        return client.post("/v1/chat", headers=AUTH, json={"message": message}, **kwargs)

    def test_streams_tokens_then_done_as_server_sent_events(self, build, agents):
        client, _, _ = build()
        response = self.post(client, "hello")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        events = parse_events(response.text)
        assert [name for name, _ in events] == ["token", "token", "token", "done"]
        assert "".join(data["text"] for name, data in events if name == "token") == "Hello there"
        done = events[-1][1]
        assert done["characters"] == len("Hello there")
        assert done["first_token_seconds"] is not None
        assert agents.created[0].messages == ["hello"]

    def test_text_with_newlines_and_unicode_survives_the_wire_format(self, build, agents):
        agents.tokens = ["line one\nline two", " → ", "“quoted”"]
        client, _, _ = build()
        events = parse_events(self.post(client).text)
        text = "".join(data["text"] for name, data in events if name == "token")
        assert text == "line one\nline two → “quoted”"

    def test_a_failing_model_is_reported_in_the_stream(self, build, agents):
        agents.reply_error = RuntimeError("the model fell over")
        client, _, _ = build()
        response = self.post(client)

        assert response.status_code == 200
        events = parse_events(response.text)
        assert events[-1] == ("error", {"message": "the model fell over"})
        assert "done" not in [name for name, _ in events]

    def test_503_while_the_model_is_loading(self, build, agents):
        agents.warm_up_gate = threading.Event()
        client, service, _ = build(started=False)
        wait_until(lambda: service.state is ServiceState.LOADING)

        response = self.post(client)
        assert response.status_code == 503
        assert "loading" in response.json()["detail"]
        agents.warm_up_gate.set()

    def test_503_after_a_failed_load(self, build, agents):
        agents.warm_up_error = ConnectionError("down")
        client, _, _ = build()
        assert self.post(client).status_code == 503

    def test_409_while_another_reply_is_being_written(self, build, agents):
        agents.reply_gate = threading.Event()
        client, service, _ = build()
        service.start_turn("first", lambda event: None)
        wait_until(lambda: service.busy)

        # Look at the status line only: if the guard were missing this would be a
        # reply that never ends, and reading its body would hang the test.
        with client.stream("POST", "/v1/chat", headers=AUTH, json={"message": "hi"}) as response:
            assert response.status_code == 409
            assert "still being written" in response.read().decode()
        agents.reply_gate.set()

    @pytest.mark.parametrize("message", ["", "   ", "\n\t"])
    def test_blank_messages_are_rejected(self, build, message):
        client, _, _ = build()
        assert self.post(client, message).status_code == 422

    def test_a_missing_or_malformed_body_is_rejected(self, build):
        client, _, _ = build()
        assert client.post("/v1/chat", headers=AUTH, json={}).status_code == 422
        assert client.post("/v1/chat", headers=AUTH, content=b"nope").status_code == 422

    def test_a_message_over_the_limit_is_rejected(self, build, agents):
        client, _, _ = build(settings=ServerSettings(port=PORT, max_message_chars=10))
        assert self.post(client, "x" * 11).status_code == 413
        assert self.post(client, "x" * 10).status_code == 200
        assert agents.created[0].messages == ["x" * 10]


class TestControlEndpoints:
    def test_interrupt_with_nothing_running_is_a_harmless_204(self, build):
        client, _, _ = build()
        response = client.post("/v1/interrupt", headers=AUTH)
        assert response.status_code == 204
        assert response.content == b""

    def test_interrupt_cancels_the_reply_being_written(self, build, agents):
        agents.reply_gate = threading.Event()
        client, service, _ = build()
        finished = threading.Event()
        events = []

        def sink(event):
            finished.set() if event is None else events.append(event)

        service.start_turn("hi", sink)
        wait_until(lambda: service.busy)

        assert client.post("/v1/interrupt", headers=AUTH).status_code == 204
        assert finished.wait(5)
        assert not any(isinstance(event, DoneEvent) for event in events)

    def test_new_conversation_forgets_the_history(self, build):
        memory = ConversationMemory()
        client, _, _ = build(memory=memory)
        before = memory.thread_id
        response = client.post("/v1/conversation/new", headers=AUTH)
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert memory.thread_id != before

    def test_new_conversation_is_refused_while_busy(self, build, agents):
        agents.reply_gate = threading.Event()
        client, service, _ = build()
        service.start_turn("hi", lambda event: None)
        wait_until(lambda: service.busy)

        assert client.post("/v1/conversation/new", headers=AUTH).status_code == 409
        agents.reply_gate.set()


class TestSetModel:
    def put(self, client, model):
        return client.put("/v1/model", headers=AUTH, json={"model": model})

    def test_switches_the_model(self, build):
        client, service, _ = build()
        response = self.put(client, "ollama:mistral")
        assert response.status_code == 200
        assert response.json() == {"model": "ollama:mistral"}
        assert service.model == "ollama:mistral"

    def test_a_name_without_a_provider_is_rejected(self, build):
        client, service, _ = build()
        assert self.put(client, "mistral").status_code == 422
        assert service.model == TzaraConfig().model

    def test_a_model_that_cannot_be_loaded_is_a_502_and_keeps_the_old_one(self, build, agents):
        client, service, _ = build()
        agents.warm_up_error = ConnectionError("model not found")
        response = self.put(client, "ollama:missing")

        assert response.status_code == 502
        assert "ollama:missing" in response.json()["detail"]
        assert "model not found" in response.json()["detail"]
        assert service.model == TzaraConfig().model

    def test_409_while_a_reply_is_being_written(self, build, agents):
        agents.reply_gate = threading.Event()
        client, service, _ = build()
        service.start_turn("hi", lambda event: None)
        wait_until(lambda: service.busy)

        assert self.put(client, "ollama:mistral").status_code == 409
        agents.reply_gate.set()


class TestConversation:
    def test_is_empty_to_begin_with(self, build):
        client, _, _ = build()
        response = client.get("/v1/conversation", headers=AUTH)
        assert response.status_code == 200
        assert response.json() == {"messages": []}

    def test_returns_what_was_said_in_order(self, build, agents):
        agents.conversation = [
            HistoryMessage("user", "What is 6 * 7?"),
            HistoryMessage("assistant", "42"),
        ]
        client, _, _ = build()
        assert client.get("/v1/conversation", headers=AUTH).json() == {
            "messages": [
                {"role": "user", "text": "What is 6 * 7?"},
                {"role": "assistant", "text": "42"},
            ]
        }

    def test_can_be_read_while_a_reply_is_being_written(self, build, agents):
        agents.reply_gate = threading.Event()
        client, service, _ = build()
        service.start_turn("hi", lambda event: None)
        wait_until(lambda: service.busy)
        assert client.get("/v1/conversation", headers=AUTH).status_code == 200
        agents.reply_gate.set()


class TestSetName:
    def put(self, client, name):
        return client.put("/v1/name", headers=AUTH, json={"name": name})

    def test_renames_the_assistant(self, build):
        client, service, _ = build()
        response = self.put(client, "Jarvis")
        assert response.status_code == 200
        assert response.json() == {"name": "Jarvis"}
        assert service.name == "Jarvis"
        assert client.get("/v1/status", headers=AUTH).json()["name"] == "Jarvis"

    @pytest.mark.parametrize("name", ["", "   ", "line one\nline two", "x" * 41])
    def test_an_unusable_name_is_a_422(self, build, name):
        client, service, _ = build()
        response = self.put(client, name)
        assert response.status_code == 422
        assert "1 to 40 characters" in response.json()["detail"]
        assert service.name == "Tzara"

    def test_a_missing_name_is_a_422(self, build):
        client, _, _ = build()
        assert client.put("/v1/name", headers=AUTH, json={}).status_code == 422

    def test_409_while_a_reply_is_being_written(self, build, agents):
        agents.reply_gate = threading.Event()
        client, service, _ = build()
        service.start_turn("hi", lambda event: None)
        wait_until(lambda: service.busy)
        assert self.put(client, "Jarvis").status_code == 409
        agents.reply_gate.set()

    def test_a_failed_reload_is_a_502_and_keeps_the_old_name(self, build, agents):
        client, service, _ = build()
        agents.warm_up_error = ConnectionError("model went away")
        response = self.put(client, "Jarvis")
        assert response.status_code == 502
        assert "model went away" in response.json()["detail"]
        assert service.name == "Tzara"


class TestLifespan:
    def test_loads_the_model_in_the_background_when_the_service_is_new(self, build, agents):
        client, service, _ = build(started=False)
        wait_until(lambda: service.state is ServiceState.READY)
        assert client.get("/v1/status", headers=AUTH).json()["state"] == "ready"
        assert len(agents.created) == 1

    def test_does_not_reload_a_service_that_has_already_started(self, build, agents):
        build(started=True)
        assert len(agents.created) == 1

    def test_publishes_and_then_removes_the_servers_address(self, agents, tmp_path):
        info_file = ServerInfoFile(tmp_path / "server.json")
        service = ChatService(TzaraConfig(), agent_factory=agents)
        service.start()
        server = TzaraServer(service, TOKEN, ServerSettings(port=PORT), info_file)

        with TestClient(server.app, base_url=BASE):
            info = info_file.read()
            assert info is not None
            assert (info.host, info.port) == ("127.0.0.1", PORT)
        assert info_file.read() is None

    def test_leaves_another_servers_record_in_place_on_exit(self, agents, tmp_path):
        """A server that failed to bind its port must not erase the one that did."""
        info_file = ServerInfoFile(tmp_path / "server.json")
        service = ChatService(TzaraConfig(), agent_factory=agents)
        service.start()
        server = TzaraServer(service, TOKEN, ServerSettings(port=PORT), info_file)

        with TestClient(server.app, base_url=BASE):
            info_file.write(ServerInfo("127.0.0.1", PORT, 1))  # someone else took over
        assert info_file.read() == ServerInfo("127.0.0.1", PORT, 1)

    def test_closes_the_service_on_shutdown(self, agents, monkeypatch):
        service = ChatService(TzaraConfig(), agent_factory=agents)
        service.start()
        closed = []
        monkeypatch.setattr(service, "close", lambda: closed.append(True))
        with TestClient(TzaraServer(service, TOKEN, ServerSettings(port=PORT)).app, base_url=BASE):
            assert closed == []
        assert closed == [True]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(name="start_live")
def start_live_fixture(agents):
    """Return a function that starts a real uvicorn server on a loopback port."""
    running = []

    def start(**setting_overrides):
        port = free_port()
        settings = ServerSettings(port=port, **setting_overrides)
        service = ChatService(TzaraConfig(), agent_factory=agents)
        service.start()
        app = TzaraServer(service, TOKEN, settings).app
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False)
        server = ShutdownAwareServer(config, service)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        wait_until(lambda: server.started)
        running.append((server, thread))
        return f"http://127.0.0.1:{port}", service, server, thread

    yield start
    for server, thread in running:
        server.should_exit = True
        thread.join(10)


@pytest.fixture(name="live")
def live_fixture(start_live):
    """A real uvicorn server on a loopback port, serving a fake agent."""
    return start_live()


class TestOverRealHttp:
    def test_a_silent_stretch_in_a_reply_is_filled_with_keep_alive_comments(
        self, start_live, agents
    ):
        """Without them a client's read timeout would drop a healthy connection."""
        base, _, _, _ = start_live(heartbeat_seconds=0.05)
        agents.reply_gate = threading.Event()  # the reply pauses after its first token
        lines = []
        with httpx.stream("POST", f"{base}/v1/chat", headers=AUTH, json={"message": "hi"}) as r:
            deadline = time.monotonic() + 5
            incoming = r.iter_lines()  # one iterator: a stream can only be read once
            for line in incoming:
                lines.append(line)
                if lines.count(": keep-alive") >= 2 or time.monotonic() > deadline:
                    break
            agents.reply_gate.set()
            lines.extend(incoming)

        assert lines.count(": keep-alive") >= 2
        events = parse_events("\n".join(line for line in lines if line != ": keep-alive") + "\n\n")
        names = [name for name, _ in events if name]
        assert names == ["token", "token", "token", "done"]
        text = "".join(data["text"] for name, data in events if name == "token")
        assert text == "Hello there"

    def test_no_keep_alive_is_sent_while_tokens_are_flowing(self, live):
        base, _, _, _ = live  # default 10s heartbeat; the fake replies instantly
        with httpx.stream("POST", f"{base}/v1/chat", headers=AUTH, json={"message": "hi"}) as r:
            body = "".join(r.iter_text())
        assert ": keep-alive" not in body

    def test_a_reply_streams_over_a_real_socket(self, live):
        base, _, _, _ = live
        with httpx.stream("POST", f"{base}/v1/chat", headers=AUTH, json={"message": "hi"}) as r:
            body = "".join(r.iter_text())
        assert [name for name, _ in parse_events(body)] == ["token", "token", "token", "done"]

    def test_a_client_that_disconnects_mid_reply_stops_the_reply_and_frees_the_agent(
        self, live, agents
    ):
        base, service, _, _ = live
        agents.reply_gate = threading.Event()
        with httpx.stream("POST", f"{base}/v1/chat", headers=AUTH, json={"message": "hi"}) as r:
            next(r.iter_lines())  # the first event arrives; the reply is now paused
            wait_until(lambda: service.busy)
        # leaving the block closed the connection

        wait_until(lambda: not service.busy)
        assert "cancel" in agents.created[0].events
        assert service.state is ServiceState.READY

    def test_the_agent_works_after_a_disconnect(self, live, agents):
        base, service, _, _ = live
        agents.reply_gate = threading.Event()
        with httpx.stream("POST", f"{base}/v1/chat", headers=AUTH, json={"message": "hi"}) as r:
            next(r.iter_lines())
        wait_until(lambda: not service.busy)

        agents.reply_gate.set()
        with httpx.stream("POST", f"{base}/v1/chat", headers=AUTH, json={"message": "again"}) as r:
            body = "".join(r.iter_text())
        assert parse_events(body)[-1][0] == "done"

    def test_a_server_told_to_exit_mid_reply_shuts_down_promptly(self, live, agents):
        """So `tzara stop` does not wait out uvicorn's grace period."""
        base, service, server, thread = live
        agents.reply_gate = threading.Event()
        with httpx.stream("POST", f"{base}/v1/chat", headers=AUTH, json={"message": "hi"}) as r:
            next(r.iter_lines())
            wait_until(lambda: service.busy)

            started = time.monotonic()
            server.handle_exit(signal.SIGTERM, None)
            thread.join(10)
        assert not thread.is_alive()
        assert time.monotonic() - started < TzaraServer.GRACEFUL_SHUTDOWN_SECONDS
        assert "cancel" in agents.created[0].events


class TestShutdownAwareServer:
    def test_cancelling_the_reply_happens_in_the_exit_handler_itself(self):
        """Not left to uvicorn's own shutdown sequence, which waits for open streams."""
        closed = []

        class FakeService:
            def close(self):
                closed.append(True)

        server = ShutdownAwareServer(uvicorn.Config(lambda *args: None), FakeService())  # type: ignore[arg-type]
        server.handle_exit(signal.SIGTERM, None)

        assert closed == [True]
        assert server.should_exit is True


class TestExitHandling:
    def make_server(self):
        class FakeService:
            def close(self):
                pass

        return ShutdownAwareServer(uvicorn.Config(lambda *args: None), FakeService())  # type: ignore[arg-type]

    def test_a_stop_signal_does_not_leave_a_signal_for_uvicorn_to_resend(self):
        """Resending it ends the process by signal, which launchd takes for a crash."""
        server = self.make_server()
        server.handle_exit(signal.SIGTERM, None)
        assert server.should_exit is True
        assert server._captured_signals == []

    def test_a_second_ctrl_c_stops_waiting_for_open_connections(self):
        server = self.make_server()
        server.handle_exit(signal.SIGINT, None)
        assert server.force_exit is False
        server.handle_exit(signal.SIGINT, None)
        assert server.force_exit is True

    def test_a_second_terminate_does_not_force_the_exit(self):
        server = self.make_server()
        server.handle_exit(signal.SIGTERM, None)
        server.handle_exit(signal.SIGTERM, None)
        assert server.force_exit is False

    def test_a_real_server_asked_to_stop_exits_with_status_zero(self, tmp_path):
        """Not -15 (killed by SIGTERM): service managers read that as a crash and restart it."""
        port = free_port()
        environment = {
            **os.environ,
            "TZARA_SERVER_PORT": str(port),
            "TZARA_DATA_DIR": str(tmp_path / "data"),
            "TZARA_LOG_FILE": str(tmp_path / "tzara.log"),
        }
        process = subprocess.Popen([sys.executable, "-m", "tzara", "serve"], env=environment)
        try:
            wait_until(
                lambda: (
                    httpx.get(f"http://127.0.0.1:{port}/v1/health", timeout=1).status_code == 200
                    if _answers(port)
                    else False
                ),
                timeout=30,
            )
            process.send_signal(signal.SIGTERM)
            assert process.wait(timeout=20) == 0
        finally:
            if process.poll() is None:
                process.kill()


def _answers(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


class TestServe:
    def test_wires_up_the_token_files_and_a_loopback_only_listener(
        self, tmp_path, agents, monkeypatch
    ):
        captured = {}

        def fake_run(self):
            captured["config"] = self.config
            captured["service"] = self._service

        monkeypatch.setattr(ShutdownAwareServer, "run", fake_run)
        config = TzaraConfig(server=ServerSettings(port=9321, data_dir=tmp_path))

        TzaraServer.serve(config)

        uvicorn_config = captured["config"]
        assert uvicorn_config.host == "127.0.0.1"
        assert uvicorn_config.port == 9321
        assert uvicorn_config.access_log is False
        assert (tmp_path / "token").read_text(encoding="utf-8").strip()
        assert isinstance(captured["service"], ChatService)

    def test_starts_with_the_name_it_was_given_last_time(self, tmp_path, monkeypatch):
        (tmp_path / "name").write_text("Jarvis", encoding="utf-8")
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            ShutdownAwareServer, "run", lambda self: captured.update(service=self._service)
        )
        TzaraServer.serve(TzaraConfig(server=ServerSettings(port=9322, data_dir=tmp_path)))
        assert captured["service"].name == "Jarvis"

    def test_ignores_a_saved_name_that_is_too_long(self, tmp_path, monkeypatch):
        (tmp_path / "name").write_text("x" * 500, encoding="utf-8")
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            ShutdownAwareServer, "run", lambda self: captured.update(service=self._service)
        )
        TzaraServer.serve(TzaraConfig(server=ServerSettings(port=9323, data_dir=tmp_path)))
        assert captured["service"].name == "Tzara"

    def test_a_rename_is_saved_for_next_time(self, tmp_path, monkeypatch):
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            ShutdownAwareServer, "run", lambda self: captured.update(service=self._service)
        )
        TzaraServer.serve(TzaraConfig(server=ServerSettings(port=9324, data_dir=tmp_path)))
        service = captured["service"]
        service._agent_factory = StreamingFakeAgent.isolated()
        service.start()
        service.rename("Friday")
        assert (tmp_path / "name").read_text(encoding="utf-8") == "Friday"


class TestStatusEvents:
    def test_what_the_agent_is_doing_is_streamed_before_the_reply(self, build, agents):
        agents.statuses = ["Searching the web for “cats”…"]
        client, _, _ = build()
        events = parse_events(client.post("/v1/chat", json={"message": "hi"}, headers=AUTH).text)
        names = [name for name, _ in events]
        assert names[:3] == ["tool", "status", "tool"] and names[-1] == "done"
        assert events[0][1] == {"name": "fake_tool", "state": "started"}
        assert events[1][1] == {"text": "Searching the web for “cats”…"}
        assert events[2][1] == {"name": "fake_tool", "state": "completed"}


class TestMemoryEndpoints:
    @staticmethod
    def personal(*facts):

        memory = personal_memory()
        assert memory is not None
        for fact in facts:
            memory.facts.add(fact)
        return memory

    def build_with(self, agents, personal):
        service = ChatService(TzaraConfig(), agent_factory=agents, personal=personal)
        service.start()
        server = TzaraServer(service, TOKEN, ServerSettings(port=PORT))
        client = TestClient(server.app, base_url=BASE)
        client.__enter__()
        return client, service

    def test_lists_what_is_remembered(self, agents):
        client, _ = self.build_with(agents, self.personal("The user likes tea."))
        body = client.get("/v1/memory", headers=AUTH).json()
        assert body["enabled"] is True and body["conversations"] == 0
        assert [f["text"] for f in body["facts"]] == ["The user likes tea."]

    def test_says_when_memory_is_off(self, build):
        client, _, _ = build()
        assert client.get("/v1/memory", headers=AUTH).json() == {
            "enabled": False,
            "facts": [],
            "conversations": 0,
        }

    def test_forgets_one_fact(self, agents):
        personal = self.personal("one fact", "two fact")
        client, _ = self.build_with(agents, personal)
        target = next(f for f in personal.facts.all() if f.text == "one fact")
        assert client.delete(f"/v1/memory/facts/{target.id}", headers=AUTH).status_code == 204
        assert client.delete(f"/v1/memory/facts/{target.id}", headers=AUTH).status_code == 404
        assert [f.text for f in personal.facts.all()] == ["two fact"]

    def test_erases_everything(self, agents):
        personal = self.personal("one fact")
        personal.log.append(personal.log.start(), "user", "hello")
        client, _ = self.build_with(agents, personal)
        assert client.delete("/v1/memory", headers=AUTH).status_code == 204
        assert personal.facts.count() == 0 and personal.log.count() == 0

    def test_erasing_is_refused_while_a_reply_is_being_written(self, build, agents):
        agents.reply_gate = threading.Event()
        client, service, _ = build()
        service.start_turn("hi", lambda event: None)
        wait_until(lambda: service.busy)
        assert client.delete("/v1/memory", headers=AUTH).status_code == 409
        agents.reply_gate.set()

    @pytest.mark.parametrize(
        ("method", "path"),
        [("get", "/v1/memory"), ("delete", "/v1/memory"), ("delete", "/v1/memory/facts/1")],
    )
    def test_all_of_it_needs_the_token(self, build, method, path):
        client, _, _ = build()
        assert getattr(client, method)(path).status_code == 401


class TestWakeEndpoint:
    def test_answers_at_once_and_says_whether_it_is_waking(self, agents):
        from tzara.config import ServerSettings

        config = TzaraConfig(server=ServerSettings(port=PORT, wake_after_seconds=0))
        service = ChatService(config, agent_factory=agents)
        service.start()
        client = TestClient(TzaraServer(service, TOKEN, config.server).app, base_url=BASE)
        client.__enter__()
        response = client.post("/v1/wake", headers=AUTH)
        assert response.status_code == 202 and response.json() == {"waking": True}

    def test_a_recently_used_model_is_left_alone(self, build):
        client, _, _ = build()
        assert client.post("/v1/wake", headers=AUTH).json() == {"waking": False}

    def test_needs_the_token(self, build):
        client, _, _ = build()
        assert client.post("/v1/wake").status_code == 401
