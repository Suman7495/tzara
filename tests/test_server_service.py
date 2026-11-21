import threading
import time

import pytest

from helpers import StreamingFakeAgent
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
    TokenEvent,
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
