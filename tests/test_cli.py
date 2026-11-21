import logging
import os
import signal
import sys
from pathlib import Path

import pytest

from helpers import FakeAgent, ScriptedConsole
from tzara.agent.memory import ConversationMemory
from tzara.cli.run import TzaraCLI
from tzara.config import TzaraConfig

GOODBYE = TzaraConfig().cli.goodbye
BANNER = TzaraConfig().cli.banner


def make_cli(script, agent_class=None, **cli_options):
    """Build a CLI wired to a scripted console and a fake agent class."""
    agent_class = agent_class or FakeAgent.reset()
    console = ScriptedConsole(script)
    cli = TzaraCLI(config=TzaraConfig(), console=console, agent_factory=agent_class, **cli_options)
    return cli, console, agent_class


class TestStartup:
    def test_loads_the_model_before_showing_the_prompt(self):
        cli, console, agents = make_cli([])
        cli.run()

        assert agents.created[0].events == ["warm_up"]
        assert console.output.index(BANNER) < console.output.index("You:")

    def test_uses_the_configured_model(self):
        cli, _, agents = make_cli([])
        cli.run()
        assert agents.created[0].model == TzaraConfig().model

    def test_load_failure_warns_but_still_starts_the_chat(self):
        agents = FakeAgent.reset()
        agents.warm_up_error = ConnectionError("ollama is not running")
        cli, console, _ = make_cli(["hi"], agents)
        cli.run()

        assert "Could not load the model" in console.output
        assert "ollama is not running" in console.output
        assert "Hello there" in console.output

    def test_agent_creation_failure_ends_without_a_chat_loop(self):
        agents = FakeAgent.reset()
        agents.create_error = ValueError("unknown provider")
        cli, console, _ = make_cli(["hi"], agents)
        cli.run()

        assert "unknown provider" in console.output
        assert "You:" not in console.output

    def test_ctrl_c_while_loading_exits_without_starting(self):
        agents = FakeAgent.reset()
        agents.warm_up_error = KeyboardInterrupt()
        cli, console, _ = make_cli(["hi"], agents)
        cli.run()

        assert GOODBYE in console.output
        assert BANNER not in console.output


class TestChat:
    def test_streams_the_reply(self):
        cli, console, agents = make_cli(["hello"])
        cli.run()

        assert agents.created[0].messages == ["hello"]
        assert "Tzara: Hello there" in console.output

    def test_blank_lines_are_ignored(self):
        cli, _, agents = make_cli(["", "   ", "hi"])
        cli.run()
        assert agents.created[0].messages == ["hi"]

    def test_model_output_is_not_interpreted_as_markup(self):
        agents = FakeAgent.reset()
        agents.replies = ["Use [bold]brackets[/bold] and [/oops] safely"]
        cli, console, _ = make_cli(["hi"], agents)
        cli.run()
        assert "Use [bold]brackets[/bold] and [/oops] safely" in console.output

    def test_end_of_input_says_goodbye_once_and_stops(self):
        cli, console, _ = make_cli([])
        cli.run()
        assert console.output.count(GOODBYE) == 1

    def test_ctrl_c_at_the_prompt_says_goodbye(self):
        cli, console, _ = make_cli([KeyboardInterrupt])
        cli.run()
        assert console.output.count(GOODBYE) == 1

    def test_exit_command_says_goodbye_once_and_stops(self):
        cli, console, agents = make_cli(["/exit", "never reached"])
        cli.run()
        assert console.output.count(GOODBYE) == 1
        assert agents.created[0].messages == []

    def test_unknown_command_does_not_reach_the_agent(self):
        cli, console, agents = make_cli(["/bogus"])
        cli.run()
        assert "Unknown command: /bogus" in console.output
        assert agents.created[0].messages == []

    def test_an_agent_error_is_reported_and_the_chat_continues(self):
        agents = FakeAgent.reset()
        calls = []

        def fail_first(text):
            calls.append(text)
            if len(calls) == 1:
                raise RuntimeError("model exploded")

        agents.handle_hook = fail_first
        cli, console, _ = make_cli(["first", "second"], agents)
        cli.run()

        assert "model exploded" in console.output
        assert calls == ["first", "second"]

    def test_ctrl_c_during_a_reply_cancels_it_and_keeps_chatting(self):
        agents = FakeAgent.reset()

        def interrupt_first(text):
            if text == "first":
                raise KeyboardInterrupt

        agents.handle_hook = interrupt_first
        cli, console, _ = make_cli(["first", "second"], agents)
        cli.run()

        agent = agents.created[0]
        assert "interrupt" in agent.events
        assert "[interrupted]" in console.output
        assert agent.messages == ["first", "second"]
        assert console.output.count(GOODBYE) == 1  # only at end of input


class TestModelSwitching:
    def test_current_model_reflects_the_running_agent(self):
        cli, _, _ = make_cli(["/set-model ollama:mistral"])
        cli.run()
        assert cli.current_model == "ollama:mistral"

    def test_switch_replaces_the_agent_and_loads_the_new_model(self):
        cli, console, agents = make_cli(["/set-model ollama:mistral", "hi"])
        cli.run()

        first, second = agents.created
        assert second.model == "ollama:mistral"
        assert second.events == ["warm_up"]
        assert "Switched → ollama:mistral" in console.output
        assert first.messages == []
        assert second.messages == ["hi"]

    def test_failed_switch_keeps_the_current_model(self):
        agents = FakeAgent.reset()
        cli, console, _ = make_cli([], agents)
        cli.run()

        agents.warm_up_error = ConnectionError("model not found")
        cli.switch_model("ollama:missing")

        assert cli.current_model == TzaraConfig().model
        assert "Could not switch to ollama:missing" in console.output
        assert "model not found" in console.output

    def test_cancelled_switch_keeps_the_current_model(self):
        agents = FakeAgent.reset()
        cli, console, _ = make_cli([], agents)
        cli.run()

        agents.warm_up_error = KeyboardInterrupt()
        cli.switch_model("ollama:mistral")

        assert cli.current_model == TzaraConfig().model
        assert "Cancelled." in console.output

    def test_current_model_command_after_a_switch(self):
        cli, console, _ = make_cli(["/set-model ollama:mistral", "/current-model"])
        cli.run()
        assert "Current model: ollama:mistral" in console.output


class TestConversationMemory:
    def test_new_command_starts_a_fresh_conversation(self):
        memory = ConversationMemory()
        before = memory.thread_id
        cli, console, _ = make_cli(["/new"], memory=memory)
        cli.run()

        assert memory.thread_id != before
        assert TzaraConfig().cli.new_conversation in console.output

    def test_every_agent_shares_the_same_memory_so_history_survives_a_switch(self):
        memory = ConversationMemory()
        cli, _, agents = make_cli(["/set-model ollama:mistral"], memory=memory)
        cli.run()

        first, second = agents.created
        assert first.memory is memory
        assert second.memory is memory

    def test_the_cli_creates_its_own_memory_by_default(self):
        cli, _, agents = make_cli([])
        cli.run()
        assert isinstance(agents.created[0].memory, ConversationMemory)

    def test_new_conversation_is_logged(self, caplog):
        cli, _, _ = make_cli(["/new"])
        with caplog.at_level(logging.INFO, logger="tzara"):
            cli.run()
        assert "new conversation started" in caplog.text


class TestErrorReporting:
    def test_errors_are_logged_with_their_traceback(self, caplog):
        agents = FakeAgent.reset()
        agents.handle_hook = lambda _text: (_ for _ in ()).throw(RuntimeError("model exploded"))
        cli, _, _ = make_cli(["hi"], agents)
        with caplog.at_level(logging.ERROR, logger="tzara"):
            cli.run()

        record = next(r for r in caplog.records if r.levelno == logging.ERROR)
        assert record.exc_info is not None
        assert "model exploded" in str(record.exc_info[1])

    def test_the_log_location_is_mentioned_when_there_is_one(self):
        agents = FakeAgent.reset()
        agents.handle_hook = lambda _text: (_ for _ in ()).throw(RuntimeError("model exploded"))
        log_file = Path("/var/log/tzara.log")  # short, so the console does not wrap it
        cli, console, _ = make_cli(["hi"], agents, log_path=log_file)
        cli.run()

        assert "model exploded" in console.output
        assert f"Details are in the log: {log_file}" in console.output

    def test_no_hint_is_shown_without_a_log_file(self):
        agents = FakeAgent.reset()
        agents.handle_hook = lambda _text: (_ for _ in ()).throw(RuntimeError("model exploded"))
        cli, console, _ = make_cli(["hi"], agents)
        cli.run()

        assert "model exploded" in console.output
        assert "log" not in console.output.lower().replace("Tzara: Goodbye.", "")

    def test_load_failures_and_switch_failures_are_logged_too(self, caplog):
        agents = FakeAgent.reset()
        agents.warm_up_error = ConnectionError("ollama is not running")
        cli, _, _ = make_cli(["/set-model ollama:mistral"], agents)
        with caplog.at_level(logging.ERROR, logger="tzara"):
            cli.run()

        labels = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert "Could not load the model" in labels
        assert "Could not switch to ollama:mistral" in labels

    def test_ctrl_c_during_a_reply_is_logged_by_the_agent_not_as_an_error(self, caplog):
        agents = FakeAgent.reset()
        agents.handle_hook = lambda _text: (_ for _ in ()).throw(KeyboardInterrupt())
        cli, _, _ = make_cli(["hi"], agents)
        with caplog.at_level(logging.ERROR, logger="tzara"):
            cli.run()
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


class TestSigint:
    def test_handler_cancels_the_request_then_raises(self):
        agent = FakeAgent()
        with TzaraCLI._sigint_cancels(agent):  # type: ignore[arg-type]
            handler = signal.getsignal(signal.SIGINT)
            assert callable(handler)
            with pytest.raises(KeyboardInterrupt):
                handler(signal.SIGINT, None)
        assert agent.events == ["cancel"]

    def test_previous_handler_is_restored(self):
        before = signal.getsignal(signal.SIGINT)
        with TzaraCLI._sigint_cancels(FakeAgent()):  # type: ignore[arg-type]
            assert signal.getsignal(signal.SIGINT) is not before
        assert signal.getsignal(signal.SIGINT) is before

    def test_previous_handler_is_restored_after_an_error(self):
        before = signal.getsignal(signal.SIGINT)
        with pytest.raises(RuntimeError), TzaraCLI._sigint_cancels(FakeAgent()):  # type: ignore[arg-type]
            raise RuntimeError
        assert signal.getsignal(signal.SIGINT) is before

    @pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX signals")
    def test_a_real_sigint_cancels_and_interrupts(self):
        agent = FakeAgent()
        with (
            pytest.raises(KeyboardInterrupt),
            TzaraCLI._sigint_cancels(agent),  # type: ignore[arg-type]
        ):
            os.kill(os.getpid(), signal.SIGINT)
        assert agent.events == ["cancel"]

    def test_does_nothing_outside_the_main_thread(self):
        import threading

        results = []

        def worker():
            before = signal.getsignal(signal.SIGINT)
            with TzaraCLI._sigint_cancels(FakeAgent()):  # type: ignore[arg-type]
                results.append(signal.getsignal(signal.SIGINT) is before)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        assert results == [True]
