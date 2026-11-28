"""What the agent did is remembered; context says which page is open; promises are kept."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from langchain_core.messages import AIMessage

from tzara.agent.actions import ActionNote, ActionRecord
from tzara.agent.activity import Activity
from tzara.agent.routing import PromiseRetry
from tzara.agent.screen import ScreenBlock, ScreenContext
from tzara.config import McpSettings, ScreenSettings
from tzara.prompts import Prompts


class TestTheNoteOfWhatWasDone:
    def test_is_written_after_the_reply_and_taken_out_for_display(self):
        records = [ActionRecord("notion__notion-update-page", "MCP-Test-42", "ok")]
        stored = "Done, it is added." + ActionNote.render(records)
        assert "notion-update-page on MCP-Test-42: ok" in stored
        assert ActionNote.strip(stored) == "Done, it is added."

    def test_a_failure_is_in_it(self):
        note = ActionNote.render([ActionRecord("a__b", "", "failed: page is in the trash")])
        assert "failed: page is in the trash" in note

    def test_no_actions_no_note(self):
        assert ActionNote.render([]) == ""
        assert ActionNote.render([ActionRecord("recall", "", "ok")]) == ""


class TestActivityRecordsEveryTool:
    def run(self, activity, name, arguments, output):
        run_id = uuid4()
        activity.on_tool_start({"name": name}, "", inputs=arguments, run_id=run_id)
        activity.on_tool_end(SimpleNamespace(content=output), run_id=run_id)

    def test_records_target_and_result(self):
        activity = Activity(Prompts())
        self.run(activity, "notion__notion-fetch", {"id": "https://notion.so/p/abc"}, "page text")
        self.run(activity, "notion__notion-update-page", {"page_id": "abc"}, "Error: in the trash")
        assert [(a.tool, a.target, a.failed) for a in activity.actions] == [
            ("notion__notion-fetch", "https://notion.so/p/abc", False),
            ("notion__notion-update-page", "abc", True),
        ]

    def test_the_log_never_holds_what_was_written(self, caplog):
        caplog.set_level("INFO")
        activity = Activity(Prompts())
        self.run(activity, "a__b", {"content": "my secret paragraph", "command": "insert"}, "ok")
        assert "secret" not in caplog.text
        assert "command=insert" in caplog.text
        assert "content=<19 characters>" in caplog.text

    def test_a_new_turn_forgets_them(self):
        activity = Activity(Prompts())
        self.run(activity, "a__b", {}, "ok")
        activity.begin_turn()
        assert activity.actions == []


class TestContextSaysWhichPageIsOpen:
    context = ScreenContext(
        app="Google Chrome", window="MCP Test", address="https://app.notion.com/p/MCP-Test-42"
    )

    def test_the_address_is_called_the_page_to_act_on(self):
        text = ScreenBlock.wrap(self.context, "add a para", ScreenSettings(), open_in="notion")
        assert "open page in notion" in text
        assert "Address is the page" in text

    def test_nothing_is_said_when_no_app_matches(self):
        assert "open page in" not in ScreenBlock.wrap(self.context, "hi", ScreenSettings())

    def test_a_browser_notice_is_not_page_text(self):
        text = ScreenBlock.wrap(
            self.context,
            "add a para",
            ScreenSettings(),
            visible="MCP Test\nJavaScript must be enabled to use Notion.\nHello",
        )
        assert "JavaScript" not in text
        assert "Hello" in text


class TestPromisesAreKept:
    @staticmethod
    def request():
        request = SimpleNamespace(messages=[])
        request.override = lambda **kw: SimpleNamespace(messages=kw["messages"])
        return request

    @staticmethod
    def retry():
        return PromiseRetry(McpSettings().promise_pattern, "do it now")

    def test_a_promise_without_a_tool_call_is_asked_again(self):
        promise = SimpleNamespace(result=[AIMessage(content="Let me fetch the current page text.")])
        call = AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "1"}])
        answers = iter([promise, SimpleNamespace(result=[call])])
        seen = []

        def handler(request):
            seen.append(request)
            return next(answers)

        result = self.retry().wrap_model_call(self.request(), handler)
        assert result.result == [call]
        assert seen[1].messages[-1].content == "do it now"

    def test_an_answer_is_left_alone(self):
        answer = SimpleNamespace(result=[AIMessage(content="Paris is in France.")])
        calls: list[int] = []

        def handler(request):
            calls.append(1)
            return answer

        result = self.retry().wrap_model_call(self.request(), handler)
        assert result is answer and len(calls) == 1

    def test_still_no_tool_keeps_the_first_answer(self):
        promise = SimpleNamespace(result=[AIMessage(content="One moment, I will check.")])
        result = self.retry().wrap_model_call(self.request(), lambda request: promise)
        assert result is promise


class TestTheOpenPageIsReadFirst:
    @staticmethod
    def router(page):
        from tzara.agent.routing import AppRouter

        return AppRouter(
            {"notion": ("notion__notion-search", "query")},
            {"notion": ("notion__notion-fetch", "id")},
            lambda: page,
        )

    @staticmethod
    def request(text):
        from langchain_core.messages import HumanMessage

        return SimpleNamespace(messages=[HumanMessage(text)])

    def test_the_address_goes_to_the_apps_read_tool(self):
        router = self.router(("notion", "https://app.notion.com/p/abc"))
        answer = router.wrap_model_call(self.request("add a para"), lambda r: None)
        call = answer.tool_calls[0]
        assert call["name"] == "notion__notion-fetch"
        assert call["args"] == {"id": "https://app.notion.com/p/abc"}
        assert call["id"].startswith("appsearch-")

    def test_nothing_is_read_when_the_message_is_not_about_the_page(self):
        sentinel = object()
        router = self.router(None)
        assert router.wrap_model_call(self.request("hi"), lambda r: sentinel) is sentinel
