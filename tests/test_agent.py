import json
import logging
import sys
import warnings
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core._api import LangChainBetaWarning
from langchain_ollama import ChatOllama

from helpers import PromptAwareFakeModel, RecordingFakeModel, fake_creator
from tzara.agent import Tzara
from tzara.agent.memory import ConversationMemory, HistoryMessage
from tzara.agent.models import ModelFactory
from tzara.config import TzaraConfig

EXPECTED_TOOLS = {
    "read_webpage",
    "research",
}

# Only offered on a Mac, so the expected set depends on where the tests run.
MAC_TOOLS = {"open_app", "open_website", "open_path", "control_media"}

# Cold start costs about 5 ms per tool-description character on a local model.
# Fail loudly if the tool descriptions grow.
TOOL_DESCRIPTION_BUDGET = 3500


def agent_with_fake_model(
    *replies: str, memory: ConversationMemory | None = None, **config_overrides
) -> tuple[Tzara, RecordingFakeModel]:
    """Build an agent whose chat model is a fake that gives ``replies`` in turn."""
    config = TzaraConfig(**config_overrides)
    agent = Tzara(config=config, memory=memory)
    model = RecordingFakeModel.replying(*(replies or ("Hello there",)))
    agent._factory.create = fake_creator(model)  # type: ignore[method-assign]
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
        assert {tool_name(tool) for tool in seen.bound_tools} == EXPECTED_TOOLS | (
            MAC_TOOLS if sys.platform == "darwin" else set()
        )

    def test_tool_descriptions_stay_within_budget(self, seen):
        total = sum(len(tool_description(tool)) for tool in seen.bound_tools)
        assert total < TOOL_DESCRIPTION_BUDGET

    def test_system_prompt_has_tzaras_persona_and_not_the_coding_agent_default(self, seen):
        system_prompt = seen.seen_messages[0][0].text
        assert TzaraConfig().prompts.rendered_system() in system_prompt
        assert "You are a deep agent" not in system_prompt
        assert "Filesystem" not in system_prompt


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
    """The agent summarizes old turns by calling the same model mid-turn."""

    def test_the_summary_is_not_streamed_into_the_reply(self):
        config = TzaraConfig()
        agent = Tzara(config=config)
        # A tiny context budget, so a few long turns trigger summarization.
        model = PromptAwareFakeModel(
            messages=iter(()), bound_tools=[], seen_messages=[], profile={"max_input_tokens": 400}
        )
        agent._factory.create = fake_creator(model)  # type: ignore[method-assign]
        agent._create_agent()

        padding = "word " * 200
        replies = ["".join(agent.handle(f"turn {number}: {padding}")) for number in range(1, 5)]

        assert model.summaries_written >= 1, "the scenario did not trigger summarization"
        assert replies == [f"answer {number}" for number in range(1, 5)]
        assert PromptAwareFakeModel.SUMMARY not in "".join(replies)

    def test_the_summary_is_logged(self, caplog):
        config = TzaraConfig()
        agent = Tzara(config=config)
        model = PromptAwareFakeModel(
            messages=iter(()), bound_tools=[], seen_messages=[], profile={"max_input_tokens": 400}
        )
        agent._factory.create = fake_creator(model)  # type: ignore[method-assign]
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

    def test_a_turn_that_fails_because_it_was_cancelled_is_logged_as_cancelled(self, caplog):
        """Cancelling closes the model connection, which surfaces as an error."""
        agent, _ = agent_with_fake_model()

        class StreamKilledByCancel:
            @property
            def messages(self):
                agent.cancel()  # what another thread does to stop a reply
                raise ConnectionError("connection closed")

            def abort(self):
                pass

        agent._agent = SimpleNamespace(  # type: ignore[assignment]
            stream_events=lambda *_a, **_k: StreamKilledByCancel()
        )
        with caplog.at_level(logging.INFO, logger="tzara"), pytest.raises(ConnectionError):
            list(agent.handle("hi"))
        assert "turn cancelled" in caplog.text
        assert "turn failed" not in caplog.text

    def test_the_cancelled_flag_does_not_leak_into_the_next_turn(self, caplog):
        agent, _ = agent_with_fake_model("first", "second")
        agent.cancel()
        with caplog.at_level(logging.INFO, logger="tzara"):
            list(agent.handle("hi"))
        assert "turn completed" in caplog.text

    def test_loading_and_interrupting_are_logged(self, caplog):
        agent, _ = agent_with_fake_model("warm")
        with caplog.at_level(logging.INFO, logger="tzara"):
            agent.warm_up()
            agent.interrupt()
        assert "loaded in" in caplog.text
        assert "response interrupted" in caplog.text


class TestRenaming:
    def test_the_assistants_name_is_what_the_model_is_told(self):
        prompts = replace(TzaraConfig().prompts, assistant_name="Jarvis")
        agent, model = agent_with_fake_model(prompts=prompts)
        list(agent.handle("who are you?"))
        system_prompt = model.seen_messages[0][0].text
        assert "You are Jarvis," in system_prompt
        assert "You are Tzara" not in system_prompt


class TestHistory:
    def test_is_empty_before_anything_is_said(self):
        agent, _ = agent_with_fake_model()
        assert agent.history() == []

    def test_lists_what_the_user_and_the_assistant_said(self):
        agent, _ = agent_with_fake_model("Hello there", "Fine, thanks")
        list(agent.handle("hi"))
        list(agent.handle("how are you?"))
        assert agent.history() == [
            HistoryMessage("user", "hi"),
            HistoryMessage("assistant", "Hello there"),
            HistoryMessage("user", "how are you?"),
            HistoryMessage("assistant", "Fine, thanks"),
        ]

    def test_leaves_out_the_warm_up_message(self):
        agent, _ = agent_with_fake_model("warm", "real", warmup_message="ping")
        agent.warm_up()
        list(agent.handle("hello"))
        assert [item.text for item in agent.history() if item.role == "user"] == ["hello"]

    def test_is_empty_again_after_a_new_conversation(self):
        agent, _ = agent_with_fake_model("reply")
        list(agent.handle("hi"))
        agent.memory.reset()
        assert agent.history() == []

    def test_survives_an_interrupt(self):
        agent, _ = agent_with_fake_model("first reply", "second reply")
        list(agent.handle("question one"))
        agent.interrupt()
        assert [item.text for item in agent.history()] == ["question one", "first reply"]


class TestSpentEvidence:
    def test_the_evidence_is_replaced_by_a_note_after_the_answer(self):
        from langchain_core.messages import ToolMessage

        agent, _ = agent_with_fake_model()
        note = agent._config.prompts.research_spent
        agent._agent.update_state(
            agent.memory.run_config,
            {"messages": [ToolMessage(content="x" * 5000, tool_call_id="t1", name="research")]},
            as_node="tools",
        )
        agent._drop_spent_evidence()
        messages = agent._agent.get_state(agent.memory.run_config).values["messages"]
        assert [m.text for m in messages if m.type == "tool"] == [note]


class TestClosedConnection:
    @staticmethod
    def counting(agent):
        """Count how often the agent builds a new graph and model connection."""
        built = []
        original = agent._create_agent

        def create():
            built.append(1)
            original()

        agent._create_agent = create
        return built

    def test_a_message_after_a_stop_gets_a_fresh_connection(self):
        agent, _ = agent_with_fake_model("one", "two")
        built = self.counting(agent)
        agent.cancel()  # a stop that was not followed by interrupt()
        assert "".join(agent.handle("hello")) == "one"
        assert built == [1]

    def test_no_new_connection_when_none_was_closed(self):
        agent, _ = agent_with_fake_model("one")
        built = self.counting(agent)
        list(agent.handle("hello"))
        assert built == []

    def test_the_warm_up_after_a_stop_also_reopens(self):
        agent, _ = agent_with_fake_model("one")
        agent.cancel()
        agent.warm_up()
        assert agent._connection_closed is False

    def test_a_connection_closed_by_anything_else_is_noticed_and_replaced(self):
        agent = Tzara(config=TzaraConfig())  # a real Ollama client; nothing is sent
        http_client(agent).close()  # closed without cancel(), as nothing flagged it
        assert agent._factory.is_closed(agent._chat_model)
        agent._reopen_if_closed()
        assert not agent._factory.is_closed(agent._chat_model)
        assert not http_client(agent).is_closed

    def test_other_models_are_never_reported_closed(self):
        from helpers import RecordingFakeModel

        assert ModelFactory.is_closed(RecordingFakeModel.replying("x")) is False


class TestHiStaysFast:
    """A plain "hi" must stay quick, whatever apps are connected.

    A local model reads the prompt at about 250 tokens a second and Ollama caches only its start,
    so the size of the system prompt and the tool definitions IS the time to the first word. With
    Microsoft 365 connected (hundreds of tools, long argument lists) "hi" took over a minute.
    """

    #: Characters of system prompt and tool definitions (about four to a token).
    BASE_BUDGET = 6_500
    WITH_APPS_EXTRA = 14_500

    @staticmethod
    def prompt_size(agent, model) -> int:
        from langchain_core.utils.function_calling import convert_to_openai_tool

        system = model.seen_messages[0][0].text
        tools = [json.dumps(convert_to_openai_tool(tool)) for tool in model.bound_tools]
        return len(system) + sum(len(text) for text in tools)

    def test_hi_with_no_apps_is_small(self):
        agent, model = agent_with_fake_model()
        assert "".join(agent.handle("hi"))
        assert self.prompt_size(agent, model) < self.BASE_BUDGET

    def test_hi_with_a_huge_app_connected_is_barely_bigger(self):
        """A stand-in for Microsoft 365: 150 tools, each with 30 arguments and long notes."""
        from mcp.types import Tool, ToolAnnotations

        from tzara.mcp import McpServerConfig, ServerStatus

        class HugeApp:
            """What the agent asks of a connected app, without starting anything."""

            settings = TzaraConfig().mcp
            servers = (McpServerConfig("huge", "x"),)

            def __init__(self) -> None:
                note = "It is described at length. " * 20
                self._tools = [
                    Tool(
                        name=f"thing_{number}",
                        description=f"Does thing number {number}. {note}",
                        input_schema={
                            "type": "object",
                            "title": "Arguments",
                            "properties": {
                                f"field_{k}": {"type": "string", "title": "F", "description": note}
                                for k in range(30)
                            },
                        },
                        annotations=ToolAnnotations(read_only_hint=True),
                    )
                    for number in range(150)
                ]

            _status = None

            def status(self):
                if self._status is None:
                    self._status = [ServerStatus("huge", connected=True)]
                return self._status

            def tools(self, _server):
                return self._tools

        from langchain_core.utils.function_calling import convert_to_openai_tool

        from tzara.mcp import McpTools

        config = TzaraConfig()
        app = HugeApp()
        tools = McpTools.create(app, list(app.servers), config.mcp, config.prompts)  # type: ignore[arg-type]
        assert tools  # something is given...
        assert len(tools) < 150  # ...but not all 150: they would not fit the prompt
        assert len(app.status()[0].hidden) == 150 - len(tools)  # and each left out is accounted for
        with_apps = sum(len(json.dumps(convert_to_openai_tool(tool))) for tool in tools)
        assert with_apps < self.WITH_APPS_EXTRA


class TestAnEmptyReplyIsNeverSilent:
    """The model sometimes returns nothing; the chat used to delete the bubble and lose the turn."""

    class EmptyThenText(RecordingFakeModel):
        """Gives no words the first ``empty_times`` times it is asked, then an answer."""

        asked: int = 0
        empty_times: int = 1

        def _generate(self, messages, *args, **kwargs):
            from langchain_core.messages import AIMessage
            from langchain_core.outputs import ChatGeneration, ChatResult

            self.asked += 1
            text = "" if self.asked <= self.empty_times else "Here you go."
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

        def _stream(self, messages, *args, **kwargs):
            from langchain_core.messages import AIMessageChunk
            from langchain_core.outputs import ChatGenerationChunk

            self.asked += 1
            text = "" if self.asked <= self.empty_times else "Here you go."
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))

    def test_an_empty_answer_is_asked_for_again_once(self):
        agent = Tzara(config=TzaraConfig())
        model = self.EmptyThenText(messages=iter([]))
        agent._factory.create = fake_creator(model)  # type: ignore[method-assign]
        agent._create_agent()
        assert "".join(agent.handle("hello there")) == "Here you go."
        assert model.asked == 2

    def test_an_answer_that_stays_empty_is_replaced_by_a_sentence_that_is_also_remembered(self):
        agent = Tzara(config=TzaraConfig())
        model = self.EmptyThenText(messages=iter([]), empty_times=99)
        agent._factory.create = fake_creator(model)  # type: ignore[method-assign]
        agent._create_agent()
        reply = "".join(agent.handle("hello there"))
        sentence = TzaraConfig().prompts.empty_reply
        assert reply == sentence
        assert agent.history()[-1].text == sentence  # so reopening the chat still shows it
