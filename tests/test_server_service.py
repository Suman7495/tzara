import threading
import time

import pytest

from helpers import StreamingFakeAgent, personal_memory
from tzara.agent.events import (
    AssistantDelta,
    Progress,
    RunCompleted,
    ToolCompleted,
    ToolStarted,
)
from tzara.agent.memory import ConversationMemory, HistoryMessage
from tzara.config import TzaraConfig
from tzara.server.files import NameStore
from tzara.server.service import (
    ChatService,
    ChatTurn,
    DoneEvent,
    ErrorEvent,
    ServiceBusy,
    ServiceNotReady,
    ServiceState,
    StatusEvent,
    TokenEvent,
    ToolEvent,
)


class Collector:
    """An event sink that remembers events and signals when a turn ends."""

    def __init__(self):
        self.events = []
        self.finished = threading.Event()

    def __call__(self, event):
        if event is None:
            self.finished.set()
        else:
            self.events.append(event)

    def wait(self, timeout=5):
        assert self.finished.wait(timeout), "the turn never finished"
        return self.events


def make_service(agent_class=None, **kwargs):
    agent_class = agent_class or StreamingFakeAgent.isolated()
    return ChatService(TzaraConfig(), agent_factory=agent_class, **kwargs), agent_class


def wait_until(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


class TestStart:
    def test_starts_out_new(self):
        service, _ = make_service()
        assert service.state is ServiceState.NEW
        assert service.model == TzaraConfig().model

    def test_loading_makes_the_service_ready(self):
        service, agents = make_service()
        service.start()
        assert service.state is ServiceState.READY
        assert service.error is None
        assert agents.created[0].events == ["warm_up"]
        assert agents.created[0].model == TzaraConfig().model

    def test_state_is_loading_while_the_model_loads(self):
        agents = StreamingFakeAgent.isolated()
        agents.warm_up_gate = threading.Event()
        service, _ = make_service(agents)
        threading.Thread(target=service.start, daemon=True).start()
        wait_until(lambda: service.state is ServiceState.LOADING)
        agents.warm_up_gate.set()
        wait_until(lambda: service.state is ServiceState.READY)

    def test_a_model_that_fails_to_load_is_recorded_not_raised(self):
        agents = StreamingFakeAgent.isolated()
        agents.warm_up_error = ConnectionError("ollama is not running")
        service, _ = make_service(agents)
        service.start()
        assert service.state is ServiceState.FAILED
        assert service.error == "ollama is not running"

    def test_an_agent_that_cannot_be_built_is_recorded_too(self):
        agents = StreamingFakeAgent.isolated()
        agents.create_error = ValueError("unknown provider")
        service, _ = make_service(agents)
        service.start()
        assert service.state is ServiceState.FAILED
        assert service.error == "unknown provider"

    def test_agents_share_the_services_memory(self):
        memory = ConversationMemory()
        service, agents = make_service(memory=memory)
        service.start()
        assert agents.created[0].memory is memory


class TestTurns:
    def test_a_turn_streams_tokens_then_a_done_event(self):
        service, agents = make_service()
        service.start()
        sink = Collector()
        service.start_turn("hi", sink)

        events = sink.wait()
        assert events[:3] == [TokenEvent("Hello"), TokenEvent(" "), TokenEvent("there")]
        done = events[3]
        assert isinstance(done, DoneEvent)
        assert done.characters == len("Hello there")
        assert done.first_token_seconds is not None
        assert agents.created[0].messages == ["hi"]

    def test_what_the_agent_is_doing_is_passed_on_as_tool_and_status_events(self):
        agents = StreamingFakeAgent.isolated()
        agents.statuses = ["Searching the web for “x”…"]
        service, _ = make_service(agents)
        service.start()
        sink = Collector()
        service.start_turn("hi", sink)

        events = sink.wait()
        assert events[:3] == [
            ToolEvent("fake_tool", "started"),
            StatusEvent("Searching the web for “x”…"),
            ToolEvent("fake_tool", "completed"),
        ]
        assert isinstance(events[-1], DoneEvent)

    def test_each_kind_of_agent_event_becomes_the_right_server_events(self):
        translate = ChatTurn._translate
        assert translate(AssistantDelta("Hi")) == [TokenEvent("Hi")]
        assert translate(ToolStarted("web_search", "Searching…")) == [
            ToolEvent("web_search", "started"),
            StatusEvent("Searching…"),
        ]
        assert translate(ToolCompleted("web_search")) == [ToolEvent("web_search", "completed")]
        assert translate(ToolCompleted("web_search", failed=True)) == [
            ToolEvent("web_search", "failed")
        ]
        assert translate(Progress("Reading 5 pages…")) == [StatusEvent("Reading 5 pages…")]
        assert translate(RunCompleted(0.5, 12)) == [DoneEvent(0.5, 12)]

    def test_the_service_is_busy_only_while_a_turn_runs(self):
        agents = StreamingFakeAgent.isolated()
        agents.reply_gate = threading.Event()
        service, _ = make_service(agents)
        service.start()
        sink = Collector()
        service.start_turn("hi", sink)
        wait_until(lambda: service.busy)

        agents.reply_gate.set()
        sink.wait()
        wait_until(lambda: not service.busy)

    def test_a_turn_before_the_model_is_ready_is_refused(self):
        service, _ = make_service()
        with pytest.raises(ServiceNotReady, match="new"):
            service.start_turn("hi", Collector())

    def test_a_turn_after_a_failed_load_is_refused(self):
        agents = StreamingFakeAgent.isolated()
        agents.warm_up_error = ConnectionError("down")
        service, _ = make_service(agents)
        service.start()
        with pytest.raises(ServiceNotReady, match="failed"):
            service.start_turn("hi", Collector())

    def test_a_second_turn_while_one_runs_is_refused(self):
        agents = StreamingFakeAgent.isolated()
        agents.reply_gate = threading.Event()
        service, _ = make_service(agents)
        service.start()
        first = Collector()
        service.start_turn("one", first)
        wait_until(lambda: service.busy)

        with pytest.raises(ServiceBusy):
            service.start_turn("two", Collector())
        agents.reply_gate.set()
        first.wait()

    def test_a_new_turn_can_start_once_the_last_is_over(self):
        service, _ = make_service()
        service.start()
        first = Collector()
        service.start_turn("one", first)
        first.wait()
        second = Collector()
        service.start_turn("two", second)
        assert isinstance(second.wait()[-1], DoneEvent)

    def test_an_agent_error_becomes_an_error_event_and_frees_the_service(self):
        agents = StreamingFakeAgent.isolated()
        agents.reply_error = RuntimeError("the model fell over")
        service, _ = make_service(agents)
        service.start()
        sink = Collector()
        turn = service.start_turn("hi", sink)

        events = sink.wait()
        assert events[-1] == ErrorEvent("the model fell over")
        assert not any(isinstance(event, DoneEvent) for event in events)
        assert not turn.cancelled
        wait_until(lambda: not service.busy)

    def test_a_failed_turn_leaves_the_service_usable(self):
        agents = StreamingFakeAgent.isolated()
        agents.reply_error = RuntimeError("the model fell over")
        service, _ = make_service(agents)
        service.start()
        failed = Collector()
        service.start_turn("hi", failed)
        failed.wait()
        wait_until(lambda: not service.busy)

        agents.reply_error = None
        again = Collector()
        service.start_turn("again", again)
        assert isinstance(again.wait()[-1], DoneEvent)


class TestFailuresInsideTheService:
    def test_an_agent_that_cannot_be_reset_after_a_cancel_still_frees_the_service(self, caplog):
        """A failure while cleaning up must not leave the service stuck busy."""
        agents = StreamingFakeAgent.isolated()
        agents.reply_gate = threading.Event()
        service, _ = make_service(agents)
        service.start()

        def broken_interrupt():
            raise RuntimeError("cannot rebuild the model")

        agents.created[0].interrupt = broken_interrupt  # type: ignore[method-assign]
        sink = Collector()
        service.start_turn("hi", sink)
        wait_until(lambda: service.busy)

        with caplog.at_level("ERROR", logger="tzara"):
            service.interrupt()
            sink.wait()
            wait_until(lambda: not service.busy)
        assert "could not reset the agent" in caplog.text

    def test_a_turn_that_cannot_be_started_releases_the_service(self, monkeypatch):
        service, _ = make_service()
        service.start()

        def refuse_to_start(self):
            raise RuntimeError("no threads left")

        monkeypatch.setattr(ChatTurn, "start", refuse_to_start)
        with pytest.raises(RuntimeError, match="no threads left"):
            service.start_turn("hi", Collector())

        assert not service.busy
        monkeypatch.undo()
        again = Collector()
        service.start_turn("again", again)
        assert isinstance(again.wait()[-1], DoneEvent)


class TestInterrupt:
    def test_nothing_to_interrupt_when_idle(self):
        service, _ = make_service()
        service.start()
        assert service.interrupt() is False

    def test_interrupting_a_turn_ends_it_quietly_and_resets_the_agent(self):
        agents = StreamingFakeAgent.isolated()
        agents.reply_gate = threading.Event()
        service, _ = make_service(agents)
        service.start()
        sink = Collector()
        service.start_turn("hi", sink)
        wait_until(lambda: service.busy)

        assert service.interrupt() is True
        events = sink.wait()

        assert not any(isinstance(event, (DoneEvent, ErrorEvent)) for event in events)
        agent = agents.created[0]
        assert agent.events[-2:] == ["cancel", "interrupt"]
        wait_until(lambda: not service.busy)

    def test_the_agent_works_again_after_an_interrupt(self):
        agents = StreamingFakeAgent.isolated()
        agents.reply_gate = threading.Event()
        service, _ = make_service(agents)
        service.start()
        sink = Collector()
        service.start_turn("hi", sink)
        wait_until(lambda: service.busy)
        service.interrupt()
        sink.wait()
        wait_until(lambda: not service.busy)

        agents.reply_gate.set()
        again = Collector()
        service.start_turn("again", again)
        assert isinstance(again.wait()[-1], DoneEvent)


class TestNewConversation:
    def test_forgets_the_conversation(self):
        memory = ConversationMemory()
        service, _ = make_service(memory=memory)
        service.start()
        before = memory.thread_id
        service.new_conversation()
        assert memory.thread_id != before

    def test_refused_while_a_reply_is_being_written(self):
        agents = StreamingFakeAgent.isolated()
        agents.reply_gate = threading.Event()
        service, _ = make_service(agents)
        service.start()
        sink = Collector()
        service.start_turn("hi", sink)
        wait_until(lambda: service.busy)

        with pytest.raises(ServiceBusy):
            service.new_conversation()
        agents.reply_gate.set()
        sink.wait()


class TestSwitchModel:
    def test_loads_the_new_model_and_uses_it(self):
        service, agents = make_service()
        service.start()
        service.switch_model("ollama:mistral")

        assert service.model == "ollama:mistral"
        assert service.state is ServiceState.READY
        assert agents.created[1].events == ["warm_up"]

    def test_the_conversation_carries_over(self):
        memory = ConversationMemory()
        service, agents = make_service(memory=memory)
        service.start()
        service.switch_model("ollama:mistral")
        assert agents.created[1].memory is memory

    def test_a_failed_switch_keeps_the_previous_model_and_state(self):
        service, agents = make_service()
        service.start()
        agents.warm_up_error = ConnectionError("model not found")
        with pytest.raises(ConnectionError):
            service.switch_model("ollama:missing")

        assert service.model == TzaraConfig().model
        assert service.state is ServiceState.READY

    def test_a_successful_switch_recovers_from_a_failed_load(self):
        agents = StreamingFakeAgent.isolated()
        agents.warm_up_error = ConnectionError("down")
        service, _ = make_service(agents)
        service.start()
        assert service.state is ServiceState.FAILED

        agents.warm_up_error = None
        service.switch_model("ollama:mistral")
        # Compared by value: mypy would narrow the property to FAILED from the assert above.
        assert service.state.value == "ready"
        assert service.error is None

    def test_the_state_reads_as_loading_during_a_switch(self):
        agents = StreamingFakeAgent.isolated()
        service, _ = make_service(agents)
        service.start()
        agents.warm_up_gate = threading.Event()
        worker = threading.Thread(
            target=service.switch_model, args=("ollama:mistral",), daemon=True
        )
        worker.start()
        wait_until(lambda: service.state is ServiceState.LOADING)
        with pytest.raises(ServiceNotReady):
            service.start_turn("hi", Collector())
        agents.warm_up_gate.set()
        worker.join(5)
        assert service.state is ServiceState.READY

    def test_refused_while_a_reply_is_being_written(self):
        agents = StreamingFakeAgent.isolated()
        agents.reply_gate = threading.Event()
        service, _ = make_service(agents)
        service.start()
        sink = Collector()
        service.start_turn("hi", sink)
        wait_until(lambda: service.busy)

        with pytest.raises(ServiceBusy):
            service.switch_model("ollama:mistral")
        agents.reply_gate.set()
        sink.wait()


class TestClose:
    def test_cancels_a_reply_in_progress(self):
        agents = StreamingFakeAgent.isolated()
        agents.reply_gate = threading.Event()
        service, _ = make_service(agents)
        service.start()
        sink = Collector()
        service.start_turn("hi", sink)
        wait_until(lambda: service.busy)

        service.close()
        sink.wait()
        assert "cancel" in agents.created[0].events

    def test_cancels_a_model_that_is_still_loading(self):
        agents = StreamingFakeAgent.isolated()
        agents.warm_up_gate = threading.Event()
        service, _ = make_service(agents)
        threading.Thread(target=service.start, daemon=True).start()
        wait_until(lambda: service.state is ServiceState.LOADING and agents.created)

        service.close()
        assert "cancel" in agents.created[0].events
        agents.warm_up_gate.set()

    def test_is_harmless_when_idle(self):
        service, _ = make_service()
        service.close()


class TestRename:
    def test_rebuilds_the_agent_with_the_new_name(self):
        service, agents = make_service()
        service.start()
        service.rename("Jarvis")

        assert service.name == "Jarvis"
        assert agents.created[1].config.prompts.assistant_name == "Jarvis"
        assert agents.created[1].events == ["warm_up"]
        assert service.state is ServiceState.READY

    def test_the_default_name_is_tzara(self):
        service, _ = make_service()
        assert service.name == "Tzara"

    def test_the_conversation_and_model_carry_over(self):
        memory = ConversationMemory()
        service, agents = make_service(memory=memory)
        service.start()
        service.switch_model("ollama:mistral")
        service.rename("Jarvis")

        assert agents.created[-1].memory is memory
        assert agents.created[-1].model == "ollama:mistral"

    def test_the_name_survives_a_later_model_switch(self):
        service, agents = make_service()
        service.start()
        service.rename("Jarvis")
        service.switch_model("ollama:mistral")
        assert agents.created[-1].config.prompts.assistant_name == "Jarvis"

    def test_surrounding_whitespace_is_ignored(self):
        service, _ = make_service()
        service.start()
        service.rename("  Jarvis \n")
        assert service.name == "Jarvis"

    def test_the_same_name_does_nothing(self):
        service, agents = make_service()
        service.start()
        service.rename("Tzara")
        service.rename(" Tzara ")
        assert len(agents.created) == 1

    @pytest.mark.parametrize("name", ["", "   ", "two\nlines", "x" * 41])
    def test_an_unusable_name_is_refused_without_touching_the_agent(self, name):
        service, agents = make_service()
        service.start()
        with pytest.raises(ValueError, match="1 to 40 characters"):
            service.rename(name)
        assert service.name == "Tzara"
        assert len(agents.created) == 1

    def test_a_forty_character_name_is_fine(self):
        service, _ = make_service()
        service.start()
        service.rename("x" * 40)
        assert service.name == "x" * 40

    def test_a_failed_rename_keeps_the_old_name_and_agent(self):
        service, agents = make_service()
        service.start()
        agents.warm_up_error = ConnectionError("model went away")
        with pytest.raises(ConnectionError):
            service.rename("Jarvis")

        assert service.name == "Tzara"
        assert service.state is ServiceState.READY

    def test_refused_while_a_reply_is_being_written(self):
        agents = StreamingFakeAgent.isolated()
        agents.reply_gate = threading.Event()
        service, _ = make_service(agents)
        service.start()
        sink = Collector()
        service.start_turn("hi", sink)
        wait_until(lambda: service.busy)

        with pytest.raises(ServiceBusy):
            service.rename("Jarvis")
        agents.reply_gate.set()
        sink.wait()


class TestRememberedName:
    def make(self, tmp_path, agents=None):
        store = NameStore(tmp_path / "name")
        service, agents = make_service(agents, name_store=store)
        service.start()
        return service, agents, store

    def test_a_rename_is_saved(self, tmp_path):
        service, _, store = self.make(tmp_path)
        service.rename("Jarvis")
        assert store.load() == "Jarvis"

    def test_a_failed_rename_is_not_saved(self, tmp_path):
        agents = StreamingFakeAgent.isolated()
        service, agents, store = self.make(tmp_path, agents)
        agents.warm_up_error = ConnectionError("down")
        with pytest.raises(ConnectionError):
            service.rename("Jarvis")
        assert store.load() is None

    def test_an_unchanged_name_is_not_saved(self, tmp_path):
        service, _, store = self.make(tmp_path)
        service.rename("Tzara")
        assert store.load() is None

    def test_renaming_works_without_a_store(self):
        service, _ = make_service()
        service.start()
        service.rename("Jarvis")
        assert service.name == "Jarvis"


class TestHistory:
    def test_is_empty_before_the_model_has_loaded(self):
        service, _ = make_service()
        assert service.history() == []

    def test_comes_from_the_agent(self):
        agents = StreamingFakeAgent.isolated()
        agents.conversation = [HistoryMessage("user", "hi"), HistoryMessage("assistant", "hello")]
        service, _ = make_service(agents)
        service.start()
        assert service.history() == agents.conversation


class TestMemory:
    @staticmethod
    def personal(*facts):

        memory = personal_memory()
        assert memory is not None
        for fact in facts:
            memory.facts.add(fact)
        return memory

    def test_the_agent_is_given_the_memory(self):
        personal = self.personal()
        service, agents = make_service(personal=personal)
        service.start()
        assert agents.created[0].personal is personal

    def test_the_overview_lists_the_facts_and_counts_conversations(self):
        personal = self.personal("The user likes tea.")
        personal.log.append(personal.log.start(), "user", "hello")
        service, _ = make_service(personal=personal)
        overview = service.memory_overview()
        assert overview["enabled"] is True and overview["conversations"] == 1
        assert [fact["text"] for fact in overview["facts"]] == ["The user likes tea."]
        assert isinstance(overview["facts"][0]["id"], int)

    def test_the_overview_says_when_memory_is_off(self):
        service, _ = make_service()
        assert service.memory_overview() == {"enabled": False, "facts": [], "conversations": 0}

    def test_one_fact_can_be_forgotten(self):
        personal = self.personal("one fact", "two fact")
        service, _ = make_service(personal=personal)
        target = next(f for f in personal.facts.all() if f.text == "one fact")
        assert service.forget_fact(target.id) is True
        assert service.forget_fact(target.id) is False
        assert [f.text for f in personal.facts.all()] == ["two fact"]

    def test_forgetting_with_memory_off_finds_nothing(self):
        service, _ = make_service()
        assert service.forget_fact(1) is False

    def test_erasing_forgets_facts_conversations_and_the_current_conversation(self):
        personal = self.personal("one fact")
        conversation = ConversationMemory(log=personal.log)
        conversation.record("user", "hello")
        service, _ = make_service(personal=personal, memory=conversation)
        service.erase_memory()
        assert personal.facts.count() == 0 and personal.log.count() == 0
        assert conversation.conversation_id is None

    def test_erasing_with_memory_off_still_starts_a_new_conversation(self):
        service, _ = make_service()
        service.erase_memory()

    def test_erasing_is_refused_while_a_reply_is_being_written(self):
        agents = StreamingFakeAgent.isolated()
        agents.reply_gate = threading.Event()
        service, _ = make_service(agents, personal=self.personal("one fact"))
        service.start()
        service.start_turn("hi", Collector())
        wait_until(lambda: service.busy)
        with pytest.raises(ServiceBusy):
            service.erase_memory()
        agents.reply_gate.set()


class TestWake:
    @staticmethod
    def service(after=0.0):
        from tzara.config import ServerSettings, TzaraConfig

        agents = StreamingFakeAgent.isolated()
        config = TzaraConfig(server=ServerSettings(wake_after_seconds=after))
        return ChatService(config, agent_factory=agents), agents  # type: ignore[arg-type]

    @staticmethod
    def warm_ups(agents):
        return sum(agent.events.count("warm_up") for agent in agents.created)

    def test_an_idle_model_is_woken_in_the_background(self):
        service, agents = self.service()
        service.start()
        assert service.wake() is True
        wait_until(lambda: self.warm_ups(agents) == 2)  # the start, and the wake

    def test_it_waits_until_the_model_has_been_idle_long_enough(self):
        service, agents = self.service(after=3600)
        service.start()
        assert service.wake() is False
        assert self.warm_ups(agents) == 1

    def test_one_wake_is_enough_until_it_has_been_idle_again(self):
        service, agents = self.service(after=3600)
        service.start()
        service._last_active -= 7200
        assert service.wake() is True
        assert service.wake() is False
        wait_until(lambda: self.warm_ups(agents) == 2)

    def test_a_finished_reply_counts_as_activity(self):
        service, _ = self.service(after=3600)
        service.start()
        service._last_active -= 7200
        collector = Collector()
        service.start_turn("hi", collector)
        collector.wait()
        wait_until(lambda: not service.busy)
        assert service.wake() is False

    def test_it_does_nothing_before_the_model_is_loaded(self):
        service, _ = self.service()
        assert service.wake() is False

    def test_it_does_nothing_while_a_reply_is_being_written(self):
        service, agents = self.service()
        agents.reply_gate = threading.Event()
        service.start()
        service.start_turn("hi", Collector())
        wait_until(lambda: service.busy)
        assert service.wake() is False
        agents.reply_gate.set()

    def test_a_wake_that_fails_is_logged_not_raised(self, caplog):
        service, agents = self.service()
        service.start()
        agents.warm_up_error = ConnectionError("ollama is down")
        assert service.wake() is True
        wait_until(lambda: "could not wake the model" in caplog.text)

    def test_the_waiting_time_must_not_be_negative(self):
        from tzara.config import ConfigError, ServerSettings

        with pytest.raises(ConfigError, match="wake_after_seconds"):
            ServerSettings(wake_after_seconds=-1)
