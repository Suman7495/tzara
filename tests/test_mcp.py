import json
import sys
import time
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest

from tzara.config import McpSettings, TzaraConfig
from tzara.mcp import McpConfigFile, McpHub, McpServerConfig, McpTools
from tzara.prompts import Prompts

SERVER = str(Path(__file__).with_name("mcp_demo_server.py"))


@pytest.fixture(autouse=True)
def never_run_third_party_programs(monkeypatch):
    """A test starts only the demo server: npx would download and run someone else's code."""
    import shutil

    real_which = shutil.which

    def which(command, *args, **kwargs):
        if command in ("npx", "uvx", "node"):
            raise AssertionError(f"a test tried to start {command}")
        return real_which(command, *args, **kwargs)

    monkeypatch.setattr("tzara.mcp.hub.shutil.which", which)


PROMPTS = Prompts()


def replace_mcp(config, **changes):
    from dataclasses import replace

    return replace(config, mcp=replace(config.mcp, **changes))


def demo(**overrides) -> McpServerConfig:
    return McpServerConfig(name="demo", command=sys.executable, args=(SERVER,), **overrides)


@pytest.fixture(name="hub")
def hub_fixture():
    created = []

    def make(*servers, **settings):
        hub = McpHub(McpSettings(startup_seconds=15, call_seconds=10, **settings), list(servers))
        hub.start()
        created.append(hub)
        return hub

    yield make
    for hub in created:
        hub.close()


@pytest.fixture(scope="module", name="shared")
def shared_fixture():
    """One connected demo server for the tests that only read from it."""
    hub = McpHub(McpSettings(startup_seconds=15, call_seconds=10), [demo()])
    hub.start()
    yield hub
    hub.close()


def offered(hub, *servers, **settings):
    return {
        tool.name: tool
        for tool in McpTools.create(hub, list(servers), McpSettings(**settings), PROMPTS)
    }


class TestConfigFile:
    def write(self, tmp_path, data):
        path = tmp_path / "mcp.json"
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return path

    def test_no_file_means_no_apps(self, tmp_path):
        assert McpConfigFile.load(tmp_path / "mcp.json") == ([], [])

    def test_reads_an_app_with_all_its_fields(self, tmp_path):
        path = self.write(
            tmp_path,
            {
                "servers": {
                    "files": {
                        "command": "npx",
                        "args": ["-y", "x"],
                        "env": {"TOKEN": "t"},
                        "tools": ["read_file"],
                        "allow_actions": True,
                        "enabled": False,
                    }
                }
            },
        )
        (server,), problems = McpConfigFile.load(path)
        assert problems == []
        assert server == McpServerConfig(
            "files", "npx", ("-y", "x"), {"TOKEN": "t"}, ("read_file",), True, False
        )

    def test_actions_are_off_by_default(self, tmp_path):
        (server,), _ = McpConfigFile.load(
            self.write(tmp_path, {"servers": {"a": {"command": "x"}}})
        )
        assert server.allow_actions is False and server.tools is None and server.enabled

    @pytest.mark.parametrize(
        "entry",
        [
            {"args": ["x"]},  # no command
            {"command": 5},
            {"command": "x", "args": "not a list"},
            {"command": "x", "env": {"A": 1}},
            {"command": "x", "tools": "read"},
        ],
    )
    def test_a_bad_entry_is_reported_and_skipped(self, tmp_path, entry):
        servers, problems = McpConfigFile.load(
            self.write(tmp_path, {"servers": {"bad": entry, "ok": {"command": "x"}}})
        )
        assert [server.name for server in servers] == ["ok"]
        assert len(problems) == 1 and problems[0].startswith("bad:")

    def test_a_name_that_could_break_a_tool_name_is_refused(self, tmp_path):
        servers, problems = McpConfigFile.load(
            self.write(
                tmp_path, {"servers": {"my_app": {"command": "x"}, "my app": {"command": "x"}}}
            )
        )
        assert servers == [] and len(problems) == 2

    def test_a_broken_file_is_a_problem_not_a_crash(self, tmp_path):
        assert McpConfigFile.load(self.write(tmp_path, "{nope"))[1]
        assert McpConfigFile.load(self.write(tmp_path, {"apps": {}}))[1]


class TestHub:
    def test_connects_to_a_real_server_and_lists_its_tools(self, shared):
        connected = shared
        (status,) = connected.status()
        assert status.connected and status.error is None
        assert {tool.name for tool in connected.tools("demo")} >= {"add", "delete_everything"}

    def test_calls_a_tool(self, shared):
        result = shared.call("demo", "add", {"a": 2, "b": 3})
        assert [part.text for part in result.content] == ["5"]

    def test_a_server_that_cannot_start_is_a_status_not_a_crash(self, hub):
        broken = McpServerConfig("broken", "/no/such/program")
        connected = hub(broken, demo())
        statuses = {status.name: status for status in connected.status()}
        assert not statuses["broken"].connected and statuses["broken"].error
        assert statuses["demo"].connected
        assert connected.call("demo", "add", {"a": 1, "b": 1}).content

    def test_a_server_that_hangs_costs_only_the_startup_time(self):
        import time

        hanging = McpServerConfig("hang", sys.executable, (SERVER, "hang"))
        hub = McpHub(McpSettings(startup_seconds=1.5, call_seconds=5), [hanging, demo()])
        started = time.monotonic()
        hub.start()
        try:
            statuses = {status.name: status for status in hub.status()}
            assert not statuses["hang"].connected and "in time" in (statuses["hang"].error or "")
            assert statuses["demo"].connected
            assert time.monotonic() - started < 12
        finally:
            hub.close()

    def test_calling_an_app_that_is_not_connected_says_so(self, hub):
        with pytest.raises(RuntimeError, match="not connected"):
            hub(demo()).call("nobody", "add", {})

    def test_no_apps_starts_nothing(self):
        hub = McpHub(McpSettings(), [])
        hub.start()
        assert hub.status() == []
        hub.close()

    def test_a_disabled_app_is_not_started(self, hub):
        assert hub(demo(enabled=False)).status() == []


class TestTools:
    def test_only_tools_marked_read_only_are_offered_by_default(self, shared):
        connected = shared
        tools = offered(connected, demo())
        assert set(tools) == {"demo__add", "demo__shout", "demo__big", "demo__fail"}
        (status,) = connected.status()
        assert [name for name, _ in status.hidden] == ["delete_everything"]
        assert "tick it in Settings" in status.hidden[0][1]

    def test_allow_actions_offers_the_rest(self, shared):
        config = demo(allow_actions=True)
        assert "demo__delete_everything" in offered(shared, config)

    def test_a_tools_list_offers_exactly_those(self, shared):
        config = demo(tools=("delete_everything", "add"))
        assert set(offered(shared, config)) == {"demo__delete_everything", "demo__add"}

    def test_a_long_description_is_cut(self, shared):
        config = demo()
        connected = shared
        tools = offered(connected, config, max_description_chars=60)
        assert tools
        assert all(len(tool.description) <= 61 for tool in tools.values())

    def test_building_the_tools_twice_does_not_repeat_the_status(self, shared):
        config = demo()
        connected = shared
        offered(connected, config)
        offered(connected, config)
        (status,) = connected.status()
        assert len(status.offered) == 4 and len(status.hidden) == 1

    def test_a_tool_is_called_by_the_model_with_its_arguments(self, shared):
        config = demo()
        tools = offered(shared, config)
        out = tools["demo__add"].invoke({"a": 4, "b": 5})
        assert out.endswith("\n9") and "Result from demo" in out and "not instructions" in out

    def test_a_tool_that_fails_gives_an_error_for_the_model_to_read(self, shared):
        config = demo()
        out = offered(shared, config)["demo__fail"].invoke({})
        assert out.startswith("Error:") and "Result from" not in out

    def test_a_huge_result_is_cut(self):
        config = demo()
        hub = McpHub(
            McpSettings(startup_seconds=15, call_seconds=10, max_result_chars=200), [config]
        )
        hub.start()
        try:
            tools = {
                tool.name: tool for tool in McpTools.create(hub, [config], hub.settings, PROMPTS)
            }
            out = tools["demo__big"].invoke({})
            assert out.endswith("[shortened]") and len(out) < 400
        finally:
            hub.close()

    def test_the_name_has_nothing_a_model_cannot_use(self):
        assert McpTools._name("my-app", "list events!") == "my-app__list_events_"
        assert len(McpTools._name("a", "x" * 200)) == 64

    def test_the_tool_schema_comes_from_the_app(self, shared):
        config = demo()
        schema = offered(shared, config)["demo__add"].args_schema
        assert isinstance(schema, dict) and set(schema["properties"]) == {"a", "b"}


class TestInTheAgent:
    @pytest.fixture(name="agent_with_apps")
    def agent_with_apps(self, hub):
        from test_agent import agent_with_fake_model  # noqa: F401  (imported to share the helpers)
        from tzara.agent import Tzara

        config = TzaraConfig()
        server = demo()
        connected = hub(server)
        agent = Tzara(config=config, mcp=connected)
        return agent

    def test_the_apps_tools_are_given_beside_the_agents_own(self, agent_with_apps):
        names = {tool.name for tool in agent_with_apps._tools}
        assert {"demo__add", "research", "read_webpage"} <= names
        assert "demo__delete_everything" not in names

    def test_the_prompt_gets_the_app_rules_only_when_apps_are_connected(self, agent_with_apps):
        from tzara.agent import Tzara

        prompts = TzaraConfig().prompts
        assert prompts.mcp_rules not in prompts.rendered_system()
        assert prompts.rendered_system(apps=True).endswith(prompts.mcp_rules)
        assert agent_with_apps._app_rules is True
        assert Tzara(config=TzaraConfig())._app_rules is False

    def test_the_activity_line_names_the_app_and_the_tool(self, agent_with_apps):
        line = agent_with_apps._activity.describe("demo__add", {})
        assert line == "Asking demo: add…"
        assert agent_with_apps._activity.describe("calendar__list_events", {}) == (
            "Asking calendar: list events…"
        )

    def test_what_an_app_returns_is_untrusted_so_nothing_is_remembered_on_its_say(
        self, agent_with_apps
    ):
        agent_with_apps._activity.begin_turn()
        assert agent_with_apps._activity.read_the_web is False
        agent_with_apps._activity.on_tool_start({"name": "demo__add"}, "", inputs={})
        assert agent_with_apps._activity.read_the_web is True

    def test_a_model_can_call_an_app_tool_and_use_the_result(self, hub):
        from helpers import ToolCallingFakeModel, fake_creator
        from tzara.agent import Tzara

        agent = Tzara(config=TzaraConfig(), mcp=hub(demo()))
        model = ToolCallingFakeModel(
            messages=iter([]),
            tool_name="demo__add",
            tool_args={"a": 20, "b": 22},
            final_answer="42.",
        )
        agent._factory.create = fake_creator(model)  # type: ignore[method-assign]
        agent._create_agent()
        assert "".join(agent.handle("add 20 and 22")) == "42."
        tool_results = [m.text for m in model.seen_messages[-1] if m.type == "tool"]
        assert tool_results and tool_results[0].endswith("\n42")


class TestInTheServer:
    def test_the_service_reports_the_apps_and_closes_them(self, hub):
        from helpers import StreamingFakeAgent
        from tzara.server.service import ChatService

        connected = hub(demo(), McpServerConfig("broken", "/no/such/program"))
        service = ChatService(
            TzaraConfig(), agent_factory=cast(Any, lambda **_: StreamingFakeAgent()), mcp=connected
        )
        service.start()
        apps = {app["name"]: app for app in service.apps()}
        assert apps["demo"]["connected"] is True and apps["broken"]["connected"] is False
        assert apps["broken"]["error"]
        service.close()
        assert connected._thread is None

    def test_no_apps_is_an_empty_list(self):
        from tzara.server.service import ChatService

        assert ChatService(TzaraConfig()).apps() == []

    def test_the_apps_endpoint_needs_the_token_and_lists_them(self, hub):
        from fastapi.testclient import TestClient

        from helpers import StreamingFakeAgent
        from tzara.config import ServerSettings
        from tzara.server.app import TzaraServer
        from tzara.server.service import ChatService

        connected = hub(demo())
        McpTools.create(
            connected, [demo()], McpSettings(), PROMPTS
        )  # what an agent does when built
        service = ChatService(
            TzaraConfig(), agent_factory=cast(Any, lambda **_: StreamingFakeAgent()), mcp=connected
        )
        service.start()
        server = TzaraServer(service, "t0ken", ServerSettings(port=8927))
        with TestClient(server.app, base_url="http://127.0.0.1:8927") as client:
            assert client.get("/v1/apps").status_code == 401
            body = client.get("/v1/apps", headers={"Authorization": "Bearer t0ken"}).json()
        assert body["apps"][0]["name"] == "demo" and "add" in body["apps"][0]["offered"]


class TestConfigurationAndCommands:
    def test_the_apps_can_be_turned_off_from_the_environment(self):
        assert TzaraConfig.from_env({}).mcp.enabled is True
        assert TzaraConfig.from_env({"TZARA_MCP": "false"}).mcp.enabled is False

    def test_limits_must_be_positive(self):
        from tzara.config import ConfigError

        with pytest.raises(ConfigError):
            McpSettings(max_prompt_chars=0)

    def test_the_server_reads_the_apps_from_the_data_folder(self, tmp_path):
        from tzara.server.app import TzaraServer

        (tmp_path / "mcp.json").write_text('{"servers": {"demo": {"command": "x"}}}')
        hub = TzaraServer._connected_apps(TzaraConfig(), tmp_path)
        assert hub is not None and [s.name for s in hub.servers] == ["demo"]
        assert TzaraServer._connected_apps(TzaraConfig(), tmp_path / "nowhere") is None
        off = TzaraConfig.from_env({"TZARA_MCP": "false"})
        assert TzaraServer._connected_apps(off, tmp_path) is None


class TestSavingTheList:
    def test_saves_and_reads_back_with_nothing_lost(self, tmp_path):
        path = tmp_path / "mcp.json"
        servers = [
            McpServerConfig(
                "files", "npx", ("-y", "x"), {"TOKEN": "t"}, ("read_file",), True, False
            ),
            McpServerConfig("plain", "uvx", ("tool",)),
        ]
        McpConfigFile.save(path, servers)
        assert McpConfigFile.load(path) == (servers, [])

    def test_the_file_is_private_because_an_app_may_hold_a_token(self, tmp_path):
        path = tmp_path / "mcp.json"
        McpConfigFile.save(path, [McpServerConfig("a", "x", env={"TOKEN": "secret"})])
        assert path.stat().st_mode & 0o777 == 0o600
        assert not [p for p in tmp_path.iterdir() if p.name != "mcp.json"]  # no leftover temp file

    def test_adding_replaces_an_app_of_the_same_name(self):
        old = [McpServerConfig("a", "one"), McpServerConfig("b", "two")]
        added = McpConfigFile.with_server(old, McpServerConfig("a", "three"))
        assert [(s.name, s.command) for s in added] == [("b", "two"), ("a", "three")]

    def test_a_bad_name_is_refused_before_anything_is_written(self):
        with pytest.raises(ValueError, match="letters"):
            McpConfigFile.with_server([], McpServerConfig("my app", "x"))

    def test_removing_leaves_the_others(self):
        old = [McpServerConfig("a", "one"), McpServerConfig("b", "two")]
        assert McpConfigFile.without_server(old, "a") == [McpServerConfig("b", "two")]


class TestPresets:
    def test_microsoft_365_is_offered_read_only_in_three_parts(self):
        from tzara.mcp import McpPresets

        presets = {
            preset.id: preset for preset in McpPresets.all() if preset.id.startswith("microsoft")
        }
        assert set(presets) == {
            "microsoft-365-outlook",
            "microsoft-365-files",
            "microsoft-365-teams",
            "microsoft-365-excel",
        }
        for preset in presets.values():
            server = preset.server
            assert server.command == "npx" and server.args[:2] == (
                "-y",
                "@softeria/ms-365-mcp-server",
            )
            assert "--read-only" in server.args  # the server itself leaves out every action
            assert preset.first_use and "sign in" in preset.first_use
            McpConfigFile.with_server([], server)  # the name and fields are allowed
        assert presets["microsoft-365-outlook"].server.args[2:4] == ("--preset", "outlook")
        assert "--org-mode" in presets["microsoft-365-teams"].server.args

    def test_token_apps_say_what_token_and_to_keep_it_read_only(self):
        from tzara.mcp import McpPresets

        for preset_id, variable in (("hubspot", "PRIVATE_APP_ACCESS_TOKEN"),):
            preset = McpPresets.find(preset_id)
            assert preset is not None
            assert preset.token_env == variable and preset.token_label and preset.token_help
            # the token's scopes decide what is possible, so the text says to keep them read-only
            assert "read" in preset.token_help
            assert variable not in preset.server.env  # the token is never in the code

    def test_every_preset_has_its_own_app_name(self):
        from tzara.mcp import McpPresets

        names = [preset.server.name for preset in McpPresets.all()]
        assert len(set(names)) == len(names)

    def test_an_unknown_preset_is_none(self):
        from tzara.mcp import McpPresets

        assert McpPresets.find("nothing") is None


class TestStartingPrograms:
    def test_thesearch_path_gets_the_folders_homebrew_uses_when_they_exist(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        extra = tmp_path / "brew"
        extra.mkdir()
        hub = McpHub(McpSettings(extra_paths=(str(extra), str(tmp_path / "missing"))), [])
        path = hub.search_path().split(":")
        assert str(extra) in path and str(tmp_path / "missing") not in path
        assert path.index("/usr/bin") < path.index(str(extra))

    def test_a_program_that_is_not_installed_says_what_to_install(self):
        hub = McpHub(McpSettings(startup_seconds=5), [McpServerConfig("x", "no-such-program-here")])
        hub.start()
        try:
            (status,) = hub.status()
            assert not status.connected and "was not found" in (status.error or "")
            assert "Node.js" in (status.error or "")
        finally:
            hub.close()


def wait_for_apps(service):
    """Wait until the service has finished applying a change to the connected apps."""
    import time

    for _ in range(100):
        if not service.apps_overview()["reloading"]:
            return
        time.sleep(0.1)
    raise AssertionError("still applying the change")


class TestChangingAppsWhileRunning:
    def make(self, tmp_path):
        from helpers import StreamingFakeAgent
        from tzara.server.service import ChatService

        file = tmp_path / "mcp.json"
        built = []

        def factory(**kwargs):
            built.append(kwargs["mcp"])
            return StreamingFakeAgent()

        service = ChatService(
            TzaraConfig(), agent_factory=cast(Any, factory), mcp=None, mcp_file=file
        )
        service.start()
        return service, file, built

    def test_adding_an_app_saves_it_connects_and_gives_the_tools_to_a_new_agent(self, tmp_path):
        service, file, built = self.make(tmp_path)
        try:
            service.add_app(demo())
            wait_for_apps(service)
            assert [s.name for s in McpConfigFile.load(file)[0]] == ["demo"]
            overview = service.apps_overview()
            assert overview["apps"][0]["connected"] is True and overview["editable"] is True
            assert built[-1] is not None  # the new agent was built with the new apps
        finally:
            service.close()

    def test_removing_an_app_disconnects_it(self, tmp_path):
        service, file, built = self.make(tmp_path)
        try:
            service.add_app(demo())
            wait_for_apps(service)
            service.remove_app("demo")
            wait_for_apps(service)
            assert McpConfigFile.load(file)[0] == []
            assert service.apps_overview()["apps"] == [] and built[-1] is None
        finally:
            service.close()

    def test_removing_an_app_that_is_not_there_is_refused(self, tmp_path):
        service, _, _ = self.make(tmp_path)
        with pytest.raises(ValueError, match="no such app"):
            service.remove_app("ghost")

    def test_a_bad_app_is_refused_and_nothing_is_written(self, tmp_path):
        service, file, _ = self.make(tmp_path)
        with pytest.raises(ValueError):
            service.add_app(McpServerConfig("bad name", "x"))
        assert not file.exists()

    def test_nothing_changes_while_a_reply_is_being_written(self, tmp_path):
        from tzara.server.service import ServiceBusy

        service, file, _ = self.make(tmp_path)
        service._turn = object()
        with pytest.raises(ServiceBusy):
            service.add_app(demo())
        assert not file.exists()

    def test_without_a_file_the_apps_cannot_be_changed_here(self):
        from tzara.server.service import ChatService

        service = ChatService(TzaraConfig())
        with pytest.raises(ValueError, match="cannot be changed"):
            service.add_app(demo())
        assert service.apps_overview()["editable"] is False

    def test_the_overview_lists_the_ready_made_apps_and_marks_the_ones_added(self, tmp_path):
        service, _, _ = self.make(tmp_path)
        try:
            presets = {p["id"]: p for p in service.apps_overview()["presets"]}
            assert presets["microsoft-365-outlook"]["added"] is False
            assert "Microsoft 365" in presets["microsoft-365-outlook"]["title"]
        finally:
            service.close()


class TestTheAppsEndpoints:
    AUTH: ClassVar[dict[str, str]] = {"Authorization": "Bearer t0ken"}

    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from helpers import StreamingFakeAgent
        from tzara.config import ServerSettings
        from tzara.server.app import TzaraServer
        from tzara.server.service import ChatService

        service = ChatService(
            TzaraConfig(),
            agent_factory=cast(Any, lambda **_: StreamingFakeAgent()),
            mcp_file=tmp_path / "mcp.json",
        )
        service.start()
        server = TzaraServer(service, "t0ken", ServerSettings(port=8927))
        return TestClient(server.app, base_url="http://127.0.0.1:8927"), service

    def added_demo(self, client, service):
        body = {"name": "demo", "command": sys.executable, "args": [SERVER]}
        assert client.post("/v1/apps", headers=self.AUTH, json=body).status_code == 202
        wait_for_apps(service)

    def test_the_tools_of_an_app_are_listed_and_the_user_can_choose_them(self, tmp_path):
        client, service = self.client(tmp_path)
        with client:
            assert client.get("/v1/apps/demo/tools").status_code == 401
            self.added_demo(client, service)
            listed = client.get("/v1/apps/demo/tools", headers=self.AUTH).json()
            states = {t["name"]: t["enabled"] for t in listed["tools"]}
            assert states["add"] is True and states["delete_everything"] is False
            assert listed["limit"] == TzaraConfig().mcp.max_prompt_chars
            chosen = {"tools": ["add", "delete_everything"]}
            assert (
                client.put("/v1/apps/demo/tools", headers=self.AUTH, json=chosen).status_code == 202
            )
            wait_for_apps(service)
            after = client.get("/v1/apps/demo/tools", headers=self.AUTH).json()["tools"]
        assert {t["name"] for t in after if t["enabled"]} == {"add", "delete_everything"}
        saved = McpConfigFile.load(tmp_path / "mcp.json")[0]
        assert saved[0].tools == ("add", "delete_everything")  # and it is kept for next time
        service.close()

    def test_an_unknown_tool_or_too_many_tools_are_refused_with_the_reason(self, tmp_path):
        client, service = self.client(tmp_path)
        with client:
            self.added_demo(client, service)
            unknown = client.put("/v1/apps/demo/tools", headers=self.AUTH, json={"tools": ["nope"]})
            assert unknown.status_code == 422 and "nope" in unknown.json()["detail"]
            service._config = replace_mcp(service._config, max_prompt_chars=50)
            big = client.put(
                "/v1/apps/demo/tools", headers=self.AUTH, json={"tools": ["add", "shout"]}
            )
            assert big.status_code == 422 and "Untick some" in big.json()["detail"]
            assert client.get("/v1/apps/nothing/tools", headers=self.AUTH).status_code == 404
        service.close()

    def test_they_all_need_the_token(self, tmp_path):
        client, service = self.client(tmp_path)
        with client:
            assert client.post("/v1/apps", json={"preset": "x"}).status_code == 401
            assert client.delete("/v1/apps/x").status_code == 401
        service.close()

    def test_an_app_is_added_by_its_parts_and_listed(self, tmp_path):
        client, service = self.client(tmp_path)
        body = {"name": "demo", "command": sys.executable, "args": [SERVER]}
        with client:
            assert client.post("/v1/apps", headers=self.AUTH, json=body).status_code == 202
            wait_for_apps(service)
            listed = client.get("/v1/apps", headers=self.AUTH).json()
        assert listed["apps"][0]["name"] == "demo" and listed["apps"][0]["connected"] is True
        service.close()

    def test_an_app_that_needs_a_token_is_refused_without_one(self, tmp_path):
        """Nothing is written and nothing started: the token is checked first."""
        client, service = self.client(tmp_path)
        with client:
            for token in ("", "two words"):
                response = client.post(
                    "/v1/apps", headers=self.AUTH, json={"preset": "hubspot", "token": token}
                )
                assert response.status_code == 422
        assert not (tmp_path / "mcp.json").exists()
        service.close()

    def test_a_ready_made_app_is_added_by_its_id(self, tmp_path, monkeypatch):
        """The preset's real command is never run in a test: a stand-in with the same shape is."""
        from tzara.mcp import McpPreset, McpPresets

        real = McpPresets.find("microsoft-365-outlook")
        assert real is not None
        stand_in = McpPreset(real.id, real.title, real.description, real.first_use, demo())
        monkeypatch.setattr(McpPresets, "find", classmethod(lambda cls, _id: stand_in))
        client, service = self.client(tmp_path)
        with client:
            response = client.post("/v1/apps", headers=self.AUTH, json={"preset": real.id})
            assert response.status_code == 202
            wait_for_apps(service)
        saved = McpConfigFile.load(tmp_path / "mcp.json")[0]
        assert [s.name for s in saved] == ["demo"]
        service.close()

    @pytest.mark.parametrize(
        ("body", "status"),
        [
            ({"preset": "nothing"}, 404),
            ({"name": "x"}, 422),  # no command
            ({"name": "bad name", "command": "x"}, 422),
            ({"name": "x", "command": "y", "args": "not a list"}, 422),
        ],
    )
    def test_a_bad_request_is_refused(self, tmp_path, body, status):
        client, service = self.client(tmp_path)
        with client:
            assert client.post("/v1/apps", headers=self.AUTH, json=body).status_code == status
        assert not (tmp_path / "mcp.json").exists()
        service.close()

    def test_an_app_is_removed(self, tmp_path):
        client, service = self.client(tmp_path)
        with client:
            client.post(
                "/v1/apps",
                headers=self.AUTH,
                json={"name": "demo", "command": sys.executable, "args": [SERVER]},
            )
            wait_for_apps(service)
            assert client.delete("/v1/apps/demo", headers=self.AUTH).status_code == 202
            wait_for_apps(service)
            assert client.delete("/v1/apps/demo", headers=self.AUTH).status_code == 404
        service.close()


class TestSignIn:
    """Running an app's login shows the user its link and code, and reconnects when it worked."""

    def run(self, tmp_path, script: str):
        import threading

        from tzara.mcp.signin import SignIn

        done = threading.Event()
        sign_in = SignIn(seconds=10)
        server = McpServerConfig("demo", sys.executable, ("-c", script))
        state = sign_in.start(server, sys.executable, "", (), done.set)
        for _ in range(100):
            if not state.running:
                break
            time.sleep(0.05)
        return state, done

    def test_the_link_and_the_code_are_picked_out_of_the_message(self, tmp_path):
        script = (
            "print('To sign in, open https://microsoft.com/devicelogin and enter the code "
            "AB12CD34 to authenticate.')"
        )
        state, done = self.run(tmp_path, script)
        assert state.url == "https://microsoft.com/devicelogin"
        assert state.code == "AB12CD34"
        assert state.done and not state.error and done.is_set()

    def test_a_failed_login_says_so_and_reconnects_nothing(self, tmp_path):
        state, done = self.run(tmp_path, "import sys; print('no way'); sys.exit(3)")
        assert not state.done and "no way" in state.error and not done.is_set()

    def test_a_missing_program_is_reported(self, tmp_path):
        from tzara.mcp.signin import SignIn

        state = SignIn().start(
            McpServerConfig("demo", "x"), "/no/such/program", "", (), lambda: None
        )
        for _ in range(100):
            if not state.running:
                break
            time.sleep(0.05)
        assert "could not start" in state.error

    def test_only_apps_with_a_login_offer_sign_in(self):
        from tzara.server.service import ChatService

        assert ChatService._signin_args("microsoft-365") == ("--login",)
        assert ChatService._signin_args("notion") is None


class TestRemoteApps:
    """Apps with an address (Notion's own server) instead of a program."""

    def test_notion_is_its_own_remote_server_signed_in_through_the_browser(self):
        from tzara.mcp import McpPresets

        preset = McpPresets.find("notion")
        assert preset is not None
        assert preset.server.url == "https://mcp.notion.com/mcp" and not preset.server.command
        assert not preset.token_env  # no token to find or paste

    def test_an_address_is_saved_and_read_back(self, tmp_path):
        path = tmp_path / "mcp.json"
        server = McpServerConfig(name="notion", url="https://mcp.notion.com/mcp")
        McpConfigFile.save(path, [server])
        assert McpConfigFile.load(path) == ([server], [])

    @pytest.mark.parametrize(
        "url", ["http://example.com/mcp", "file:///etc/passwd", "mcp.notion.com"]
    )
    def test_only_https_addresses_are_allowed(self, url):
        with pytest.raises(ValueError, match="https"):
            McpConfigFile.with_server([], McpServerConfig(name="x", url=url))

    def test_tokens_are_kept_private_per_app_and_forgotten_with_the_app(self, tmp_path):
        import asyncio
        import stat

        from mcp.shared.auth import OAuthToken

        from tzara.mcp.oauth import TokenFile

        file = TokenFile(tmp_path / "auth.json")
        storage = file.for_app("notion")
        assert asyncio.run(storage.get_tokens()) is None and not file.has_tokens("notion")
        asyncio.run(storage.set_tokens(OAuthToken(access_token="a", token_type="Bearer")))
        assert file.has_tokens("notion") and not file.has_tokens("other")
        assert stat.S_IMODE((tmp_path / "auth.json").stat().st_mode) == 0o600
        saved = asyncio.run(storage.get_tokens())
        assert saved is not None and saved.access_token == "a"
        file.forget("notion")
        assert not file.has_tokens("notion")

    def test_without_signing_in_the_app_says_so_instead_of_opening_a_browser(self, tmp_path):
        """No sign-in means 'sign in needed': never a browser that opens by itself at start-up."""
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Refuses(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Bearer resource_metadata="x"')
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args):
                return

        httpd = HTTPServer(("127.0.0.1", 0), Refuses)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            server = McpServerConfig(name="remote", url=f"http://127.0.0.1:{httpd.server_port}/mcp")
            hub = McpHub(McpSettings(startup_seconds=10), [server], tmp_path / "auth.json")
            hub.start()
            status = hub.status()[0]
            hub.close()
        finally:
            httpd.shutdown()
        assert not status.connected and status.error
        assert status.error == "sign in needed"

    def test_the_return_address_catches_the_code_and_nothing_else(self):
        import asyncio
        import urllib.request

        from tzara.mcp.oauth import CallbackServer

        with socket_free_port() as port:
            pass
        server = CallbackServer(port)
        server.start()
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=abc&state=s", timeout=5)
            result = asyncio.run(server.wait(2))
        finally:
            server.stop()
        assert (result.code, result.state) == ("abc", "s")


import contextlib  # noqa: E402


@contextlib.contextmanager
def socket_free_port():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        yield sock.getsockname()[1]


class TestWhichToolsTheModelGets:
    """The user ticks the tools; the prompt has room for only so many, and says what is left out."""

    class Hub:
        """A stand-in for connected apps: their tools, and a call that records its arguments."""

        settings = McpSettings()

        def __init__(self, tools):
            from tzara.mcp import ServerStatus

            self._tools = tools
            self._statuses = [ServerStatus(name, connected=True) for name in tools]

        def status(self):
            return self._statuses

        def tools(self, name):
            return self._tools[name]

    @staticmethod
    def tool(name, size=2, read_only=True):
        from mcp.types import Tool, ToolAnnotations

        return Tool(
            name=name,
            description=f"does {name}",
            input_schema={
                "type": "object",
                "properties": {f"p{k}": {"type": "string"} for k in range(size)},
            },
            annotations=ToolAnnotations(read_only_hint=read_only),
        )

    def create(self, hub, *servers, **config):
        return McpTools.create(hub, list(servers), McpSettings(**config), PROMPTS)

    def test_with_no_ticks_the_read_only_tools_are_given_and_the_others_say_why_not(self):
        hub = self.Hub({"app": [self.tool("read"), self.tool("write", read_only=False)]})
        names = {t.name for t in self.create(hub, McpServerConfig("app", "x"))}
        status = hub.status()[0]
        assert names == {"app__read"} and status.offered == ["read"]
        assert status.hidden[0][0] == "write" and "tick it in Settings" in status.hidden[0][1]

    def test_ticked_tools_are_given_even_if_they_change_things_and_the_rest_are_not(self):
        hub = self.Hub({"app": [self.tool("read"), self.tool("write", read_only=False)]})
        server = McpServerConfig("app", "x", tools=("write",))
        assert {t.name for t in self.create(hub, server)} == {"app__write"}
        assert hub.status()[0].hidden == [("read", "not ticked in Settings")]

    def test_what_does_not_fit_the_prompt_is_left_out_and_the_status_says_by_how_much(self):
        hub = self.Hub({"app": [self.tool(f"t{n}", size=40) for n in range(20)]})
        tools = self.create(hub, McpServerConfig("app", "x"), max_prompt_chars=2000)
        status = hub.status()[0]
        assert 0 < len(tools) < 20 and len(status.offered) == len(tools)
        assert "characters of prompt" in status.hidden[0][1]
        assert sum(t.size for t in tools) <= 2000

    def test_the_listing_for_settings_shows_every_tool_with_its_state_and_size(self):
        hub = self.Hub({"app": [self.tool("read"), self.tool("write", read_only=False)]})
        server = McpServerConfig("app", "x")
        rows = McpTools.listing(hub, server, McpSettings(), PROMPTS)  # type: ignore[arg-type]
        assert [(r["name"], r["enabled"], r["changes_things"]) for r in rows] == [
            ("read", True, False),
            ("write", False, True),
        ]
        assert all(r["chars"] > 0 and r["description"] for r in rows)

    def test_notion_starts_with_the_basics_only(self):
        from tzara.mcp import McpPresets

        preset = McpPresets.find("notion")
        assert preset is not None
        assert preset.server.tools == (
            "notion-search",
            "notion-fetch",
            "notion-create-pages",
            "notion-update-page",
        )
        assert not preset.server.allow_actions  # nothing else may change things

    def test_the_app_rules_forbid_claiming_a_change_no_tool_confirmed(self):
        rules = TzaraConfig().prompts.mcp_rules
        assert "Never say you added" in rules and "cannot do that" in rules

    def test_a_hint_replaces_a_long_description(self):
        from tzara.mcp import McpTools

        hub = self.Hub({"notion": [self.tool("notion-update-page", read_only=False)]})
        server = McpServerConfig(name="notion", tools=("notion-update-page",))
        tools = McpTools.create(hub, [server], McpSettings(), Prompts())  # type: ignore[arg-type]
        assert "command=insert_content" in tools[0].description


class TestAddAndRemoveOneApp:
    """Adding or removing an app must not restart the others (it used to: a minute's wait)."""

    def test_adding_and_dropping_leave_the_other_apps_connected(self, hub):
        first = hub(demo())
        before = first._sessions["demo"]
        first.add(McpServerConfig(name="second", command=sys.executable, args=(SERVER,)))
        assert {s.name for s in first.status() if s.connected} == {"demo", "second"}
        assert first._sessions["demo"] is before  # the same connection: not started again
        first.drop("second")
        assert [s.name for s in first.status()] == ["demo"]
        assert first._sessions["demo"] is before and "second" not in first._remote
        assert first.call("demo", "add", {"a": 1, "b": 2})  # and it still answers

    def test_a_hub_that_started_with_no_apps_can_get_one(self, hub):
        empty = hub()
        empty.add(demo())
        assert [s.name for s in empty.status() if s.connected] == ["demo"]

    def test_adding_an_app_again_replaces_its_connection(self, hub):
        connected = hub(demo())
        before = connected._sessions["demo"]
        connected.add(demo())
        assert connected._sessions["demo"] is not before
        assert [s.name for s in connected.status()] == ["demo"]


class TestHonestReplies:
    """A small model says 'I've added it' without calling anything: the user must not be fooled."""

    def agent(self, hub, model):
        from helpers import fake_creator
        from tzara.agent import Tzara

        agent = Tzara(config=TzaraConfig(), mcp=hub(demo()))
        agent._factory.create = fake_creator(model)  # type: ignore[method-assign]
        agent._create_agent()
        return agent

    def test_a_claimed_change_with_no_tool_run_gets_a_note_that_is_also_remembered(self, hub):
        from helpers import RecordingFakeModel

        note = TzaraConfig().prompts.unconfirmed_change
        model = RecordingFakeModel.replying("I've added the paragraph to your page.")
        agent = self.agent(hub, model)
        reply = "".join(agent.handle("add a paragraph to my page"))
        assert reply.endswith(note)
        assert "Nothing was done" in agent.history()[-1].text  # so the model does not believe it

    def test_a_change_made_by_a_tool_gets_no_note(self, hub):
        from helpers import ToolCallingFakeModel

        model = ToolCallingFakeModel(
            messages=iter([]),
            tool_name="demo__add",
            tool_args={"a": 1, "b": 2},
            final_answer="I've added them: 3.",
        )
        reply = "".join(self.agent(hub, model).handle("add 1 and 2"))
        assert "Nothing was changed" not in reply

    def test_an_ordinary_answer_gets_no_note(self, hub):
        from helpers import RecordingFakeModel

        model = RecordingFakeModel.replying("Paris is the capital of France.")
        assert "Nothing was changed" not in "".join(self.agent(hub, model).handle("capital?"))

    def test_without_apps_there_is_never_a_note(self):
        from helpers import RecordingFakeModel, fake_creator
        from tzara.agent import Tzara

        agent = Tzara(config=TzaraConfig())
        agent._factory.create = fake_creator(  # type: ignore[method-assign]
            RecordingFakeModel.replying("I've added that to my notes.")
        )
        agent._create_agent()
        assert "Nothing was changed" not in "".join(agent.handle("hi"))


class TestTheUserIsToldWhatIsGoingOn:
    """A silent model is a hang to the user: say what is happening while it reads."""

    def test_a_slow_reply_reports_progress_before_the_first_word(self):
        import time as _time

        from helpers import RecordingFakeModel, fake_creator
        from tzara.agent import Tzara
        from tzara.agent.events import AssistantDelta, Progress

        class Slow(RecordingFakeModel):
            def _stream(self, *args, **kwargs):
                _time.sleep(0.5)
                return super()._stream(*args, **kwargs)

        config = TzaraConfig(waiting_seconds=(0.1, 0.2, 0.3))
        agent = Tzara(config=config)
        agent._factory.create = fake_creator(Slow.replying("Hello there"))  # type: ignore[method-assign]
        agent._create_agent()
        events = list(agent.run("hi"))
        texts = [e.text for e in events if isinstance(e, Progress)]
        prompts = config.prompts
        assert texts[:2] == [prompts.waiting_reading, prompts.waiting_still]
        first_word = next(i for i, e in enumerate(events) if isinstance(e, AssistantDelta))
        assert all(isinstance(e, Progress) for e in events[:first_word])  # said before the answer

    def test_a_quick_reply_says_nothing_extra(self):
        from helpers import RecordingFakeModel, fake_creator
        from tzara.agent import Tzara
        from tzara.agent.events import Progress

        agent = Tzara(config=TzaraConfig())
        agent._factory.create = fake_creator(RecordingFakeModel.replying("Hello there"))  # type: ignore[method-assign]
        agent._create_agent()
        assert not [e for e in agent.run("hi") if isinstance(e, Progress)]


class TestTheAppIsSearchedForTheModel:
    """The model will not search for the page before writing to it: that step is made in code."""

    @staticmethod
    def router():
        from tzara.agent.routing import AppRouter

        return AppRouter({"notion": ("notion__notion-search", "query")})

    def test_a_message_naming_the_app_and_a_page_searches_for_the_page_name(self):
        call = self.router().route(
            "Add a short paragraph about Paris to my Notion page called MCP Test."
        )
        assert call is not None
        assert (call["name"], call["args"]) == ("notion__notion-search", {"query": "MCP Test"})

    def test_a_quoted_name_and_other_wordings_work(self):
        route = self.router().route
        assert route('write in Notion the page named "Trip plan" something')["args"] == {
            "query": "Trip plan"
        }
        assert route("search Notion for budgets")["args"] == {"query": "search Notion for budgets"}

    def test_a_message_that_names_no_app_is_left_alone(self):
        assert self.router().route("what is the capital of France") is None
        assert self.router().route("a notional idea") is None  # whole words only

    def test_the_search_call_cannot_be_mistaken_for_researchs(self):
        from tzara.agent.routing import AppRouter, ResearchRouter

        assert ResearchRouter.ROUTED not in AppRouter.ROUTED

    def test_only_a_search_tool_among_the_given_ones_is_used(self, hub):
        from helpers import RecordingFakeModel, fake_creator
        from tzara.agent import Tzara

        agent = Tzara(config=TzaraConfig(), mcp=hub(demo()))
        assert agent._app_searches() == {}  # the demo app has no search tool
        agent._factory.create = fake_creator(RecordingFakeModel.replying("Hi"))  # type: ignore[method-assign]
        agent._create_agent()
        assert "".join(agent.handle("hello"))

    def test_no_search_is_made_when_the_user_is_already_on_the_apps_page(self):
        router = self.router()
        on_notion = "[Screen context]\nApp: Google Chrome\nAddress: https://app.notion.com/p/x-42\n"
        assert router.route("add a paragraph to this Notion page", on_notion) is None
        elsewhere = "[Screen context]\nApp: Code\nWindow: tzara\n"
        assert router.route("add a paragraph to my Notion page called MCP Test", elsewhere)
