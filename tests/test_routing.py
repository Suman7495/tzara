from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tzara.agent.routing import QuestionRules, ResearchRouter
from tzara.agent.screen import ScreenBlock, ScreenContext
from tzara.config import ResearchSettings, ScreenSettings

RULES = QuestionRules(ResearchSettings())


class Request(SimpleNamespace):
    def override(self, **changes):
        return Request(**{**vars(self), **changes})


class TestRules:
    @pytest.mark.parametrize(
        "message",
        [
            "how do I enable CORS in FastAPI",
            "what is a good budget mechanical keyboard",
            "recommend a keyboard",
            "who won the match on Sunday",
            "best laptop under 800 euro",
            "Is it safe to take ibuprofen with coffee?",
            "competitors of telekinesis.ai",
            "is it safe to take ibuprofen with coffee",
        ],
    )
    def test_questions_for_facts_need_sources(self, message):
        assert RULES.needs_sources(message)

    @pytest.mark.parametrize(
        "message",
        [
            "hi",
            "thanks!",
            "tell me a short joke",
            "what is 12 times 12",
            "what is my favourite tea?",
            "do you like cats?",
            "can you open Safari",
            "how are you?",
            "open Safari",
            "remember that I like green tea",
            "write a poem about rain",
            "",
        ],
    )
    def test_chat_maths_personal_and_commands_do_not(self, message):
        assert not RULES.needs_sources(message)


class TestRouter:
    def route(self, *messages, writing="writing"):
        said: list[str] = []
        router = ResearchRouter(RULES, report=said.append, writing=writing)

        def handler(request):
            return SimpleNamespace(result=[AIMessage(content="ok")], tools=request.tools)

        request = Request(messages=list(messages), tools=["research"])
        return router.wrap_model_call(cast(Any, request), cast(Any, handler)), said

    def test_a_question_is_sent_to_research_without_asking_the_model(self):
        reply, _ = self.route(HumanMessage(content="how do I x?"))
        assert isinstance(reply, AIMessage)
        (call,) = reply.tool_calls
        assert call["name"] == "research" and call["args"] == {"question": "how do I x?"}
        assert str(call["id"]).startswith("route-")

    def test_a_message_with_screen_context_is_left_to_the_model(self):
        wrapped = ScreenBlock.wrap(
            ScreenContext("Notes", selection="Is this right?"),
            "what does this mean?",
            ScreenSettings(),
        )
        reply, _ = self.route(HumanMessage(content=wrapped))
        assert reply.tools == ["research"]

    def test_other_messages_go_to_the_model_with_its_tools(self):
        reply, _ = self.route(HumanMessage(content="hi"))
        assert reply.tools == ["research"]

    def test_after_routed_research_the_model_keeps_its_tools(self):
        result = ToolMessage(content="evidence", tool_call_id="route-abc")
        reply, said = self.route(result)
        assert reply.tools == ["research"] and said == ["writing"]

    def test_a_second_search_is_answered_again_without_tools(self):
        calls = []

        def handler(request):
            calls.append(request.tools)
            wants_tool = len(calls) == 1
            message = AIMessage(
                content="", tool_calls=[{"name": "research", "args": {}, "id": "x"}]
            )
            return SimpleNamespace(result=[message if wants_tool else AIMessage(content="answer")])

        router = ResearchRouter(RULES)
        request = Request(
            messages=[ToolMessage(content="e", tool_call_id="route-1")], tools=["research"]
        )
        reply = router.wrap_model_call(cast(Any, request), cast(Any, handler))
        assert calls == [["research"], []] and reply.result[0].text == "answer"

    def test_a_tool_result_the_model_asked_for_keeps_its_tools(self):
        reply, said = self.route(ToolMessage(content="x", tool_call_id="call-1"))
        assert reply.tools == ["research"] and said == []
