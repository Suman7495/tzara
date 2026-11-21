import httpx
import pytest

from tzara.server.client import ServerClient
from tzara.server.files import ServerInfo

INFO = ServerInfo("127.0.0.1", 8927, 1)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, bad_json=False):
        self.status_code = status_code
        self._payload = payload
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


@pytest.fixture(name="respond")
def respond_fixture(monkeypatch):
    """Make httpx.get return (or raise) whatever a test says; record the requests."""
    requests = []

    def install(result):
        def fake_get(url, **kwargs):
            requests.append((url, kwargs))
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(httpx, "get", fake_get)
        return requests

    return install


class TestIsHealthy:
    def test_true_when_the_server_says_ok(self, respond):
        requests = respond(FakeResponse(payload={"status": "ok"}))
        assert ServerClient(INFO).is_healthy() is True
        assert requests[0][0] == "http://127.0.0.1:8927/v1/health"

    @pytest.mark.parametrize(
        "response",
        [
            FakeResponse(500, {"status": "ok"}),
            FakeResponse(200, {"status": "starting"}),
            FakeResponse(200, ["unexpected"]),
            FakeResponse(200, bad_json=True),
        ],
    )
    def test_false_when_the_answer_is_not_a_healthy_tzara(self, respond, response):
        respond(response)
        assert ServerClient(INFO).is_healthy() is False

    @pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
    def test_false_when_nothing_answers(self, respond, error):
        respond(error)
        assert ServerClient(INFO).is_healthy() is False

    def test_uses_the_configured_timeout(self, respond):
        requests = respond(FakeResponse(payload={"status": "ok"}))
        ServerClient(INFO, timeout=0.5).is_healthy()
        assert requests[0][1]["timeout"] == 0.5


class TestStatus:
    def test_returns_the_servers_state(self, respond):
        payload = {"state": "ready", "model": "ollama:qwen3:8b", "busy": False, "error": None}
        requests = respond(FakeResponse(payload=payload))
        assert ServerClient(INFO, token="secret").status() == payload
        assert requests[0][1]["headers"] == {"Authorization": "Bearer secret"}

    def test_needs_a_token(self, respond):
        requests = respond(FakeResponse(payload={}))
        assert ServerClient(INFO).status() is None
        assert requests == []

    @pytest.mark.parametrize(
        "response",
        [FakeResponse(401, {}), FakeResponse(200, ["list"]), FakeResponse(200, bad_json=True)],
    )
    def test_none_for_a_rejected_or_malformed_answer(self, respond, response):
        respond(response)
        assert ServerClient(INFO, token="secret").status() is None

    def test_none_when_nothing_answers(self, respond):
        respond(httpx.ConnectError("refused"))
        assert ServerClient(INFO, token="secret").status() is None
