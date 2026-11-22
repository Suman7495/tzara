from collections.abc import Sequence

import pytest

from helpers import ScriptedConsole
from tzara.cli.commands import BuiltinCommands, CommandRegistry, ExitRequested


class FakeController:
    def __init__(self):
        self.current_model = "ollama:qwen3:8b"
        self.switched_to: list[str] = []
        self.new_conversations = 0
        self.shown_memory = 0
        self.erased_memory = 0

    def switch_model(self, model: str) -> None:
        self.switched_to.append(model)

    def new_conversation(self) -> None:
        self.new_conversations += 1

    def show_memory(self) -> None:
        self.shown_memory += 1

    def erase_memory(self) -> None:
        self.erased_memory += 1


@pytest.fixture(name="console")
def console_fixture():
    return ScriptedConsole()


class TestCommandRegistry:
    def test_ordinary_text_is_not_a_command(self, console):
        assert CommandRegistry(console).execute("hello") is False

    def test_runs_the_handler_with_the_remaining_words(self, console):
        registry = CommandRegistry(console)
        received: list[Sequence[str]] = []
        registry.register("/echo", "Echo.", received.append)

        assert registry.execute("/echo  one   two") is True
        assert received == [["one", "two"]]

    def test_unknown_commands_are_reported_and_swallowed(self, console):
        assert CommandRegistry(console).execute("/nope") is True
        assert "Unknown command: /nope" in console.output

    def test_bare_slash_is_an_unknown_command_not_an_error(self, console):
        # Previously a lone "/" was silently ignored; now it is reported.
        assert CommandRegistry(console).execute("/") is True
        assert "Unknown command: /" in console.output

    def test_registering_twice_replaces_the_command(self, console):
        registry = CommandRegistry(console)
        calls = []
        registry.register("/x", "first", lambda _: calls.append("first"))
        registry.register("/x", "second", lambda _: calls.append("second"))
        registry.execute("/x")
        assert calls == ["second"]

    def test_help_lists_commands_sorted_with_descriptions(self, console):
        registry = CommandRegistry(console)
        registry.register("/zeta", "Last.", lambda _: None)
        registry.register("/alpha", "First.", lambda _: None)

        lines = registry.help_text().splitlines()
        assert lines[0] == "Commands:"
        assert lines[1].split() == ["/alpha", "First."]
        assert lines[2].split() == ["/zeta", "Last."]


class TestBuiltinCommands:
    @pytest.fixture(name="setup")
    def setup_fixture(self, console):
        registry = CommandRegistry(console)
        controller = FakeController()
        BuiltinCommands(registry, console, controller)
        return registry, controller, console

    def test_registers_the_documented_commands(self, setup):
        registry, _, _ = setup
        help_text = registry.help_text()
        for name in ("/help", "/list-models", "/current-model", "/set-model", "/new", "/exit"):
            assert name in help_text

    def test_help_prints_the_command_list(self, setup):
        registry, _, console = setup
        registry.execute("/help")
        assert "/set-model" in console.output

    def test_list_models_shows_every_provider_and_the_default(self, setup):
        registry, _, console = setup
        registry.execute("/list-models")
        for provider in ("OLLAMA", "ANTHROPIC", "OPENAI", "GOOGLE_GENAI"):
            assert provider in console.output
        assert "ollama:qwen3:8b" in console.output

    def test_current_model_reports_the_controllers_model(self, setup):
        registry, controller, console = setup
        controller.current_model = "openai:gpt-4o"
        registry.execute("/current-model")
        assert "Current model: openai:gpt-4o" in console.output

    def test_set_model_hands_the_name_to_the_controller(self, setup):
        registry, controller, _ = setup
        registry.execute("/set-model ollama:llama3.1:8b")
        assert controller.switched_to == ["ollama:llama3.1:8b"]

    def test_set_model_without_a_name_prints_usage(self, setup):
        registry, controller, console = setup
        registry.execute("/set-model")
        assert "Usage: /set-model <model>" in console.output
        assert controller.switched_to == []

    def test_set_model_rejects_a_name_without_a_provider(self, setup):
        registry, controller, console = setup
        registry.execute("/set-model qwen3")
        assert "provider:model-name" in console.output
        assert controller.switched_to == []

    def test_new_starts_a_new_conversation(self, setup):
        registry, controller, _ = setup
        registry.execute("/new")
        assert controller.new_conversations == 1

    def test_memory_shows_what_is_remembered(self, setup):
        registry, controller, _ = setup
        registry.execute("/memory")
        assert controller.shown_memory == 1

    @pytest.mark.parametrize(
        "line", ["/erase-memory", "/erase-memory no", "/erase-memory yes please"]
    )
    def test_erase_memory_needs_an_explicit_yes(self, setup, line):
        registry, controller, console = setup
        registry.execute(line)
        assert controller.erased_memory == 0
        assert "/erase-memory yes" in console.output

    @pytest.mark.parametrize("line", ["/erase-memory yes", "/erase-memory YES"])
    def test_erase_memory_with_yes_forgets_everything(self, setup, line):
        registry, controller, _ = setup
        registry.execute(line)
        assert controller.erased_memory == 1

    def test_the_memory_commands_are_in_the_help(self, setup):
        registry, _, console = setup
        registry.execute("/help")
        assert "/memory" in console.output and "/erase-memory" in console.output

    def test_exit_ends_the_session(self, setup):
        registry, _, _ = setup
        with pytest.raises(ExitRequested):
            registry.execute("/exit")
