import logging
import warnings
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core._api import LangChainBetaWarning
from langchain_ollama import ChatOllama

from helpers import PromptAwareFakeModel, RecordingFakeModel
from tzara.agent import Tzara
from tzara.agent.memory import ConversationMemory
from tzara.config import TzaraConfig

# The fake model's provider name, so the harness profile applies to it.
FAKE_PROVIDER = "recordingfakemodel"

EXPECTED_TOOLS = {
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "task",
    "write_todos",
    "calculator",
}

# Cold start costs about 5 ms per tool-description character on a local model.
# Fail loudly if the prompt grows back towards DeepAgents' ~19k-character default.
TOOL_DESCRIPTION_BUDGET = 8000


def agent_with_fake_model(
    *replies: str, memory: ConversationMemory | None = None, **config_overrides
) -> tuple[Tzara, RecordingFakeModel]:
    """Build an agent whose chat model is a fake that gives ``replies`` in turn."""
    config = TzaraConfig(harness_providers=(FAKE_PROVIDER,), **config_overrides)
    agent = Tzara(config=config, memory=memory)
    model = RecordingFakeModel.replying(*(replies or ("Hello there",)))
    agent._factory.create = lambda _spec: model  # type: ignore[assignment,method-assign]
    agent._create_agent()
    return agent, model


def human_texts(messages) -> list[str]:
    """The user's messages among ``messages``, in order."""
    return [m.text for m in messages if m.type == "human"]


def http_client(agent: Tzara):
    """The httpx client behind an Ollama-backed agent."""
    assert isinstance(agent._chat_model, ChatOllama)
    return agent._chat_model._client._client


def tool_name(tool) -> str:
    return tool["name"] if isinstance(tool, dict) else tool.name


def tool_description(tool) -> str:
    return tool["description"] if isinstance(tool, dict) else tool.description


class TestConstruction:
    def test_uses_the_configured_model_by_default(self):
        assert Tzara(config=TzaraConfig(model="ollama:llama3.1:8b")).model == "ollama:llama3.1:8b"

    def test_explicit_model_overrides_the_config(self):
        assert Tzara(model="ollama:mistral").model == "ollama:mistral"

    def test_silences_the_beta_streaming_warning(self):
        Tzara()
        assert any(
            action == "ignore" and category is LangChainBetaWarning
            for action, _, category, _, _ in warnings.filters
        )


class TestHandle:
    def test_streams_the_reply_in_order(self):
        agent, _ = agent_with_fake_model("Hello there friend")
        assert "".join(agent.handle("hi")) == "Hello there friend"

    def test_forgets_the_stream_when_finished(self):
        agent, _ = agent_with_fake_model()
        list(agent.handle("hi"))
        assert agent._stream is None

    def test_closing_the_generator_early_aborts_the_run(self):
        agent, _ = agent_with_fake_model("one two three four")
        replies = agent.handle("hi")
        next(replies)
        stream = agent._stream
        assert stream is not None
        with patch.object(stream, "abort", wraps=stream.abort) as abort:
            replies.close()
        abort.assert_called_once()
        assert agent._stream is None


class TestWarmUp:
    def test_sends_the_configured_message(self):
        agent, model = agent_with_fake_model(warmup_message="ping")
        agent.warm_up()
        human_messages = [m.content for m in model.seen_messages[0] if m.type == "human"]
        assert human_messages == ["ping"]

    def test_propagates_failures(self):
        agent, _ = agent_with_fake_model()
        with (
            patch.object(agent._agent, "invoke", side_effect=ConnectionError("down")),
            pytest.raises(ConnectionError),
        ):
            agent.warm_up()


class TestCancellation:
    def test_cancel_closes_the_http_connection(self):
        agent = Tzara(model="ollama:qwen3:8b")
        assert not http_client(agent).is_closed
        agent.cancel()
        assert http_client(agent).is_closed

    def test_interrupt_leaves_a_working_agent_with_a_fresh_client(self):
        agent = Tzara(model="ollama:qwen3:8b")
        old_model, old_client = agent._chat_model, http_client(agent)
        agent.interrupt()
        assert old_client.is_closed
        assert agent._chat_model is not old_model
        assert not http_client(agent).is_closed

    def test_interrupt_aborts_a_run_in_progress(self):
        agent, _ = agent_with_fake_model("one two three four")
        replies = agent.handle("hi")
        next(replies)
        stream = agent._stream
        assert stream is not None
        with patch.object(stream, "abort", wraps=stream.abort) as abort:
            agent.interrupt()
        assert abort.called
        assert agent._stream is None


class TestPromptSentToTheModel:
    @pytest.fixture(name="seen")
    def seen_fixture(self):
        agent, model = agent_with_fake_model()
        list(agent.handle("hi"))
        return model

    def test_every_tool_is_available(self, seen):
        assert {tool_name(tool) for tool in seen.bound_tools} == EXPECTED_TOOLS

    def test_tool_descriptions_stay_within_budget(self, seen):
        total = sum(len(tool_description(tool)) for tool in seen.bound_tools)
        assert total < TOOL_DESCRIPTION_BUDGET

    def test_todo_tool_uses_the_concise_description(self, seen):
        todo = next(tool for tool in seen.bound_tools if tool_name(tool) == "write_todos")
        assert tool_description(todo) == TzaraConfig().prompts.todo_tool

    def test_system_prompt_has_tzaras_persona_and_not_the_coding_agent_default(self, seen):
        system_prompt = seen.seen_messages[0][0].text
        assert TzaraConfig().prompts.system in system_prompt
        assert TzaraConfig().prompts.base in system_prompt
        assert "You are a deep agent" not in system_prompt

    def test_only_one_todo_prompt_section_is_present(self, seen):
        assert seen.seen_messages[0][0].text.count("## `write_todos`") == 1


class TestMemory:
    def test_the_second_turn_sees_the_first(self):
        agent, model = agent_with_fake_model("Nice to meet you, Sam", "Your name is Sam")
        list(agent.handle("My name is Sam"))
        list(agent.handle("What is my name?"))

        second_call = model.seen_messages[-1]
        assert human_texts(second_call) == ["My name is Sam", "What is my name?"]
        assert "Nice to meet you, Sam" in [m.text for m in second_call if m.type == "ai"]

    def test_a_new_conversation_forgets_the_old_one(self):
        agent, model = agent_with_fake_model("one", "two")
        list(agent.handle("remember this"))
        agent.memory.reset()
        list(agent.handle("who am I?"))
        assert human_texts(model.seen_messages[-1]) == ["who am I?"]

    def test_the_warm_up_message_is_not_part_of_the_conversation(self):
        agent, model = agent_with_fake_model("warm", "real", warmup_message="ping")
        agent.warm_up()
        list(agent.handle("hello"))
        assert human_texts(model.seen_messages[-1]) == ["hello"]

    def test_history_survives_an_interrupt(self):
        agent, model = agent_with_fake_model("first reply", "second reply")
        list(agent.handle("question one"))
        agent.interrupt()
        list(agent.handle("question two"))
        assert human_texts(model.seen_messages[-1]) == ["question one", "question two"]

    def test_history_carries_over_to_an_agent_built_on_the_same_memory(self):
        """What /set-model relies on: the new agent continues the conversation."""
        memory = ConversationMemory()
        first, _ = agent_with_fake_model("first reply", memory=memory)
        list(first.handle("question one"))

        second, model = agent_with_fake_model("second reply", memory=memory)
        list(second.handle("question two"))
        assert human_texts(model.seen_messages[-1]) == ["question one", "question two"]

    def test_memory_is_exposed(self):
        memory = ConversationMemory()
        assert Tzara(memory=memory).memory is memory

    def test_agents_without_shared_memory_do_not_share_history(self):
        first, _ = agent_with_fake_model("reply")
        second, model = agent_with_fake_model("reply")
        list(first.handle("private to the first agent"))
        list(second.handle("hello"))
        assert human_texts(model.seen_messages[-1]) == ["hello"]


class TestSummaryOutputIsNotShown:
    """DeepAgents summarizes old turns by calling the same model mid-turn."""

    def test_the_summary_is_not_streamed_into_the_reply(self):
        config = TzaraConfig(harness_providers=(FAKE_PROVIDER,))
        agent = Tzara(config=config)
        # A tiny context budget, so a few long turns trigger summarization.
        model = PromptAwareFakeModel(
            messages=iter(()), bound_tools=[], seen_messages=[], profile={"max_input_tokens": 400}
        )
        agent._factory.create = lambda _spec: model  # type: ignore[assignment,method-assign]
        agent._create_agent()

        padding = "word " * 200
        replies = ["".join(agent.handle(f"turn {number}: {padding}")) for number in range(1, 5)]

        assert model.summaries_written >= 1, "the scenario did not trigger summarization"
        assert replies == [f"answer {number}" for number in range(1, 5)]
        assert PromptAwareFakeModel.SUMMARY not in "".join(replies)

    def test_the_summary_is_logged(self, caplog):
        config = TzaraConfig(harness_providers=(FAKE_PROVIDER,))
        agent = Tzara(config=config)
        model = PromptAwareFakeModel(
            messages=iter(()), bound_tools=[], seen_messages=[], profile={"max_input_tokens": 400}
        )
        agent._factory.create = lambda _spec: model  # type: ignore[assignment,method-assign]
        agent._create_agent()

        with caplog.at_level(logging.INFO, logger="tzara"):
            for number in range(1, 5):
                list(agent.handle(f"turn {number}: " + "word " * 200))
        assert "history summarized" in caplog.text


class TestLogging:
    def test_turns_are_logged_without_their_text(self, caplog):
        agent, _ = agent_with_fake_model("a reply with a secret")
        with caplog.at_level(logging.INFO, logger="tzara"):
            list(agent.handle("my password is hunter2"))

        text = caplog.text
        assert "turn started: 22 characters" in text
        assert "turn completed" in text
        assert "hunter2" not in text
        assert "a reply with a secret" not in text

    def test_message_text_is_logged_at_debug_level_only(self, caplog):
        agent, _ = agent_with_fake_model("reply")
        with caplog.at_level(logging.DEBUG, logger="tzara"):
            list(agent.handle("hello there"))
        assert "user message: hello there" in caplog.text

    def test_a_turn_closed_early_is_logged_as_cancelled(self, caplog):
        agent, _ = agent_with_fake_model("one two three four")
        with caplog.at_level(logging.INFO, logger="tzara"):
            replies = agent.handle("hi")
            next(replies)
            replies.close()
        assert "turn cancelled" in caplog.text

    def test_a_failing_turn_is_logged_as_failed_and_still_raises(self, caplog):
        agent, _ = agent_with_fake_model()

        class BrokenStream:
            @property
            def messages(self):
                raise ConnectionError("ollama went away")

            def abort(self):
                pass

        agent._agent = SimpleNamespace(  # type: ignore[assignment]
            stream_events=lambda *_a, **_k: BrokenStream()
        )
        with caplog.at_level(logging.INFO, logger="tzara"), pytest.raises(ConnectionError):
            list(agent.handle("hi"))
        assert "turn failed" in caplog.text

    def test_loading_and_interrupting_are_logged(self, caplog):
        agent, _ = agent_with_fake_model("warm")
        with caplog.at_level(logging.INFO, logger="tzara"):
            agent.warm_up()
            agent.interrupt()
        assert "loaded in" in caplog.text
        assert "response interrupted" in caplog.text
