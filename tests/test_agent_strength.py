"""The agent does what a small model will not: edit text, memory commands, ask for a city."""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from helpers import RecordingFakeModel, personal_memory
from tzara.agent.commands import MemoryCommands
from tzara.agent.routing import QuestionRules
from tzara.agent.textedit import TextEdit
from tzara.agent.writer import InlineWriter, WriteRequest
from tzara.config import MemorySettings, TzaraConfig, WebSettings
from tzara.tools.weather import WeatherReport

LETTER = (
    "Hi Anna,\n\nI wanted to ask about Warsaw. Are you free?\n\n"
    "Also, lunch on Friday works.\n\nBest,\nSam"
)


EDIT = TextEdit(TzaraConfig().write)


class TestEditsDoneInCode:
    @pytest.mark.parametrize(
        ("instruction", "expected"),
        [
            (
                "delete the second paragraph",
                "Hi Anna,\n\nAlso, lunch on Friday works.\n\nBest,\nSam",
            ),
            (
                "delete the paragraph about lunch",
                "Hi Anna,\n\nI wanted to ask about Warsaw. Are you free?\n\nBest,\nSam",
            ),
            ("please delete paragraph 3", None),
            ("remove the last paragraph", None),
            ("remove the greeting", None),
            ("remove the sign-off", None),
            ("delete the first sentence", None),
            ("delete everything", ""),
        ],
    )
    def test_each_instruction_is_done(self, instruction, expected):
        result = EDIT.apply(instruction, LETTER)
        if expected is None:
            assert result is not None and result != LETTER
        else:
            assert result == expected

    @pytest.mark.parametrize(
        "instruction",
        [
            "change the tone to friendly",
            "delete the paragraph about nothing",
            "delete the ninth paragraph",
        ],
    )
    def test_what_is_not_clear_is_left_to_the_model(self, instruction):
        assert EDIT.apply(instruction, LETTER) is None

    def test_replace_changes_only_what_it_names(self):
        result = EDIT.apply("replace warsaw with Berlin", LETTER) or ""
        assert "Berlin" in result and "Hi Anna" in result

    def test_deleting_a_selection_is_recognised(self):
        assert EDIT.deletes_the_selection("delete this")
        assert EDIT.deletes_the_selection("remove the selected paragraph")
        assert not EDIT.deletes_the_selection("delete the second paragraph")


class TestTheWriterEdits:
    @staticmethod
    def writer(model):
        return InlineWriter(lambda: model, TzaraConfig(), None)

    def test_a_paragraph_is_deleted_with_no_model_request(self):
        model = RecordingFakeModel.replying("never used")
        result = self.writer(model).write(
            WriteRequest(app="Outlook", field=LETTER, instruction="delete the second paragraph")
        )
        assert "Warsaw" not in result.text and result.changed and result.replaces == "field"
        assert model.seen_messages == []

    def test_the_selection_can_be_deleted_to_nothing(self):
        model = RecordingFakeModel.replying("never used")
        result = self.writer(model).write(
            WriteRequest(
                app="Outlook", field=LETTER, selection="Also, lunch", instruction="delete this"
            )
        )
        assert result.text == "" and result.replaces == "selection" and result.changed

    def test_a_model_that_returns_the_text_unchanged_is_asked_again(self):
        model = RecordingFakeModel.replying(
            LETTER, "Hi Anna,\n\nShort and friendly. Free?\n\nBest,\nSam"
        )
        result = self.writer(model).write(
            WriteRequest(app="Outlook", field=LETTER, instruction="make it shorter")
        )
        assert result.changed and "Short and friendly" in result.text
        assert len(model.seen_messages) == 2
        assert "unchanged" in " ".join(m.text for m in model.seen_messages[1])

    def test_an_edit_that_cannot_be_applied_says_so(self):
        model = RecordingFakeModel.replying(LETTER, LETTER)
        result = self.writer(model).write(
            WriteRequest(app="Outlook", field=LETTER, instruction="make it shorter")
        )
        assert not result.changed and result.text == LETTER

    def test_paragraphs_are_numbered_for_the_model_and_the_numbers_do_not_come_back(self):
        model = RecordingFakeModel.replying("[1] Hi\n\n[2] Bye")
        result = self.writer(model).write(
            WriteRequest(app="Outlook", field="One\n\nTwo\n\nThree", instruction="shorten each")
        )
        assert "[2] Two" in " ".join(m.text for m in model.seen_messages[0])
        assert result.text == "Hi\n\nBye"


class TestMemoryCommands:
    @staticmethod
    def commands():
        memory = personal_memory()
        return MemoryCommands(memory.facts, MemorySettings()), memory

    def test_remember_a_statement_keeps_it_as_a_fact_about_the_user(self):
        commands, memory = self.commands()
        assert commands.handle("remember that my dentist is Dr Lee") == (
            "Remembered: The user's dentist is Dr Lee."
        )
        assert (
            commands.handle("memorize I like green tea") == "Remembered: The user likes green tea."
        )
        assert len(memory.facts.all()) == 2

    def test_remember_this_keeps_the_selection(self):
        commands, memory = self.commands()
        reply = commands.handle("remember this", selection="Room 4.12, 3pm on Tuesday")
        assert reply and "Room 4.12" in reply
        assert "Room 4.12" in memory.facts.all()[0].text

    def test_remember_this_with_nothing_selected_says_what_to_do(self):
        commands, _ = self.commands()
        assert "Select the text" in (commands.handle("remember this") or "")

    def test_secrets_are_not_kept(self):
        commands, memory = self.commands()
        assert "password manager" in (commands.handle("remember my password is hunter2") or "")
        assert memory.facts.count() == 0

    def test_forget_deletes_the_one_fact_it_matches(self):
        commands, memory = self.commands()
        commands.handle("remember my dentist is Dr Lee")
        assert (commands.handle("forget about the dentist") or "").startswith("Forgot")
        assert memory.facts.count() == 0
        assert "nothing remembered" in (commands.handle("forget unicorns") or "")

    def test_what_do_you_know_lists_the_facts(self):
        commands, _ = self.commands()
        assert "don't know anything" in (commands.handle("what do you know about me?") or "")
        commands.handle("remember I live in Berlin")
        assert "lives in Berlin" in (commands.handle("what do you remember about me") or "")

    def test_other_messages_are_left_to_the_model(self):
        commands, _ = self.commands()
        for text in [
            "what is the weather",
            "note that this is long " + "x" * 700,
            "hi",
            "write an email",
        ]:
            assert commands.handle(text) is None


class TestWeatherWithoutAPlace:
    def test_the_home_city_is_used_and_otherwise_the_user_is_asked(self):
        from tzara.config import ResearchSettings
        from tzara.prompts import Prompts
        from tzara.tools.research import Researcher

        def service(request: httpx.Request) -> httpx.Response:
            if "geocoding" in request.url.host:
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "name": "Darmstadt",
                                "country": "Germany",
                                "latitude": 49.8,
                                "longitude": 8.6,
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "current": {
                        "time": "2026-11-28T01:00",
                        "temperature_2m": 1,
                        "apparent_temperature": 0,
                        "relative_humidity_2m": 80,
                        "precipitation": 0,
                        "weather_code": 3,
                        "wind_speed_10m": 5,
                    },
                    "daily": {
                        "time": ["2026-11-28"],
                        "weather_code": [3],
                        "temperature_2m_max": [3],
                        "temperature_2m_min": [0],
                        "precipitation_probability_max": [10],
                    },
                },
            )

        weather = WeatherReport(WebSettings(), httpx.Client(transport=httpx.MockTransport(service)))
        prompts = Prompts()

        def researcher(**more):
            return Researcher(
                ResearchSettings(),
                None,  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
                prompts,
                weather=weather,
                **more,
            )

        assert researcher().research("whats the weather") == prompts.weather_no_place
        known = researcher(home=lambda: "Darmstadt")
        assert "Darmstadt" in known.research("whats the weather")


class TestKnowingWhereTheUserIs:
    def test_where_am_i_is_answered_from_the_macs_location_or_says_how_to_turn_it_on(self):
        memory = personal_memory()
        commands = MemoryCommands(memory.facts, MemorySettings())
        assert commands.handle("where am i?", place="Darmstadt, Germany") == (
            "You are in Darmstadt, Germany, going by your Mac's location."
        )
        assert "Use my location" in (commands.handle("where am i") or "")
        commands.handle("my city is Hamburg")  # not a command: learned by the capture instead
        memory.capture.learn("my city is Hamburg")
        assert "lives in Hamburg" in (commands.handle("where do i live") or "")

    def test_my_city_is_learned_as_where_the_user_lives(self):
        memory = personal_memory()
        assert [f.text for f in memory.capture.learn("my city is Darmstadt")] == [
            "The user lives in Darmstadt."
        ]

    def test_the_agent_turns_near_me_into_the_place(self):
        from tzara.agent.agent import Tzara

        agent = Tzara.__new__(Tzara)
        agent._turn_place = "Darmstadt, Germany"
        agent._near_me = re.compile(TzaraConfig().write.near_me, re.IGNORECASE)
        assert agent._localised("best pizza near me") == "best pizza in Darmstadt, Germany"
        assert agent._localised("what is the weather here") == (
            "what is the weather in Darmstadt, Germany"
        )


class TestTasksAreCombinedAndRemembered:
    @staticmethod
    def writer(model, memory=None, gather=None, record=None):
        return InlineWriter(lambda: model, TzaraConfig(), memory, gather=gather, record=record)

    def test_several_edits_are_done_one_at_a_time(self):
        writer = self.writer(RecordingFakeModel.replying(""))
        assert writer.steps("remove the greeting and make the rest shorter") == [
            "remove the greeting",
            "make the rest shorter",
        ]
        assert writer.steps("make it shorter and friendlier") == ["make it shorter and friendlier"]
        model = RecordingFakeModel.replying("Anna, can we meet on Friday? Free?")
        result = self.writer(model).write(
            WriteRequest(
                app="Outlook", field=LETTER, instruction="remove the greeting and make it shorter"
            )
        )
        assert result.changed and not result.text.startswith("Hi Anna")
        assert (
            len(model.seen_messages) == 1
        )  # the greeting was removed in code, only "shorter" asked

    def test_what_the_task_needs_is_gathered_before_the_text_is_written(self):
        seen = []

        def gather(asked, app):
            seen.append((asked, app))
            return "Weather for Berlin: 3 degrees, rain."

        model = RecordingFakeModel.replying("Hi Anna, it is 3 degrees and rainy in Berlin.")
        result = self.writer(model, gather=gather).write(
            WriteRequest(
                app="Outlook", instruction="write an email to Anna about the weather in Berlin"
            )
        )
        assert seen == [("write an email to Anna about the weather in Berlin", "Outlook")]
        assert "3 degrees, rain" in " ".join(m.text for m in model.seen_messages[0])
        assert "3 degrees" in result.text

    def test_the_last_task_is_remembered_and_shown_to_the_next_one(self):
        memory = personal_memory()
        kept = []
        model = RecordingFakeModel.replying("Dear Anna, lunch Friday?", "Dear Ben, lunch Friday?")
        w = self.writer(model, memory, record=lambda *args: kept.append(args))
        w.write(
            WriteRequest(app="Outlook", instruction="write an email to Anna about lunch on Friday")
        )
        task = memory.tasks.last()
        assert (
            task and task.asked.startswith("write an email to Anna") and "Dear Anna" in task.result
        )
        assert kept == [
            ("write an email to Anna about lunch on Friday", "Outlook", "Dear Anna, lunch Friday?")
        ]

        w.write(WriteRequest(app="Outlook", instruction="the same email again, but for Ben"))
        system = model.seen_messages[1][0].text
        assert "write an email to Anna" in system and "Dear Anna, lunch Friday?" in system

    def test_the_last_task_is_not_lost_by_chat(self):
        memory = personal_memory()
        w = self.writer(RecordingFakeModel.replying("Hello!"), memory)
        w.write(WriteRequest(app="Slack", instruction="say hello to the team"))
        assert memory.tasks.last("write") is not None and memory.tasks.last("chat") is None
        memory.erase()
        assert memory.tasks.last() is None


class TestNoInventedSignature:
    def test_a_made_up_name_after_the_sign_off_is_dropped_but_the_users_is_kept(self):
        memory = personal_memory()
        w = InlineWriter(lambda: None, TzaraConfig(), memory)  # type: ignore[arg-type,return-value]
        text = "Hi Anna,\n\nSee you Friday.\n\nBest regards,  \nSarah"
        assert w._without_invented_name(text).endswith("Best regards,")
        memory.capture.learn("my name is Sam")
        assert w._without_invented_name("Hi,\n\nOk.\n\nBest,\nSam").endswith("Best,\nSam")


class TestTheModelCanReadTheScreenItself:
    @staticmethod
    def tool(context):
        from tzara.tools.screen import ReadScreen

        return ReadScreen("d", lambda: context, lambda c: "", (500, 200), "[notice]")

    def test_it_returns_what_the_app_sent_and_marks_it_as_data(self):
        from tzara.agent.screen import ScreenContext

        context = ScreenContext(
            app="Outlook",
            window="Re: Invoice",
            visible="Please pay 4,200 EUR by Friday.",
            typing="Hi,",
        )
        text = self.tool(context)._run("all")
        assert text.startswith("[notice]") and "4,200 EUR" in text and "Typing here:\nHi," in text
        assert "Window text" not in self.tool(context)._run("typing")

    def test_nothing_on_the_screen_is_said_plainly(self):
        assert "Nothing was read" in self.tool(None)._run("all")

    def test_the_message_says_what_more_is_on_the_screen(self):
        from tzara.agent.screen import ScreenBlock, ScreenContext
        from tzara.config import ScreenSettings

        context = ScreenContext(app="Outlook", window="Re: Invoice")
        block = ScreenBlock.wrap(
            context, "what amount is due", ScreenSettings(), more="More on screen: window text 40."
        )
        assert "More on screen: window text 40." in block
        assert block.index("More on screen") < block.index(ScreenBlock.END)

    def test_recall_includes_the_last_task_for_like_last_time(self):
        from tzara.memory import Task
        from tzara.prompts import Prompts
        from tzara.tools.memory import Memory

        memory = personal_memory()
        memory.tasks.remember(
            Task("write", "email Anna about lunch", "Outlook", "guided", "Dear Anna, lunch?")
        )
        tool = Memory(memory, Prompts(), lambda: False, lambda: None)
        assert "email Anna about lunch" in tool.recall("everything")
        assert "Dear Anna, lunch?" in tool.recall("last time")


class TestAClaimWithNoToolIsNotLetStand:
    def test_taken_care_of_is_a_claim_even_with_no_app_connected(self):
        from tzara.agent.agent import Tzara

        agent = Tzara.__new__(Tzara)
        agent._config = TzaraConfig()
        agent._activity = SimpleNamespace(tools_run=set(), actions=[])  # type: ignore[assignment]
        for reply in [
            "I've taken care of the invoice and confirmed the meeting.",
            "I have booked it.",
        ]:
            assert agent._unconfirmed_change(reply) == agent._config.prompts.unconfirmed_change
        assert agent._unconfirmed_change("The invoice is 4,200 EUR, due Friday.") == ""
        agent._activity.tools_run = {"remember"}
        assert agent._unconfirmed_change("I've saved that.") == ""


class TestTheUsersOwnContentIsSearchedInTheirApps:
    def test_my_notes_and_the_data_room_go_to_the_connected_app_without_naming_it(self):
        from tzara.agent.routing import AppRouter

        router = AppRouter({"notion": ("notion__notion-search", "query")})
        for text in [
            "Which documents are still missing from my data room?",
            "What did we decide in my meeting notes?",
            "Add a line to my Q4 Board Update page",
        ]:
            call = router.route(text)
            assert call and call["name"] == "notion__notion-search", text
        assert router.route("what is the capital of Australia") is None
        assert (
            router.route(
                "which documents are missing from my data room", "Address: https://notion.so/x"
            )
            is None
        )

    def test_a_paragraph_the_text_does_not_have_is_not_improvised(self):
        three = "One.\n\nTwo.\n\nThree."
        assert EDIT.target_missing("delete the ninth paragraph", three)
        assert not EDIT.target_missing("delete the second paragraph", three)
        assert not EDIT.target_missing("delete the last paragraph", three)
        writer = InlineWriter(lambda: RecordingFakeModel.replying("never"), TzaraConfig(), None)
        result = writer.write(
            WriteRequest(app="Outlook", field=three, instruction="delete the ninth paragraph")
        )
        assert not result.changed and result.text == three


class TestVerifyAndRetrieve:
    @staticmethod
    def agent(tools_run, actions):
        from tzara.agent.agent import Tzara

        agent = Tzara.__new__(Tzara)
        agent._config = TzaraConfig()
        agent._activity = SimpleNamespace(tools_run=set(tools_run), actions=actions)  # type: ignore[assignment]
        return agent

    def test_a_reply_that_claims_success_after_a_tool_failed_gets_the_error(self):
        from tzara.agent.actions import ActionRecord

        failed = ActionRecord(
            "notion__notion-update-page", "pg_1", "failed: Error: old_str not found"
        )
        agent = self.agent({"notion__notion-update-page"}, [failed])
        note = agent._unconfirmed_change("The cap table has been marked as uploaded.")
        assert "notion-update-page" in note and "old_str not found" in note
        assert agent._unconfirmed_change("I could not change it.") == ""

    def test_an_empty_reply_after_tools_says_what_was_looked_into(self):
        agent = self.agent({"notion__notion-search", "notion__notion-fetch"}, [])
        agent._app_rules = True
        text = agent._closing_note("")
        assert "notion-fetch" in text and "notion-search" in text

    def test_the_page_found_first_is_read_for_the_model(self):
        from langchain_core.messages import HumanMessage, ToolMessage

        from tzara.agent.routing import AppRouter

        router = AppRouter(
            {"notion": ("notion__notion-search", "query")},
            {"notion": ("notion__notion-fetch", "id")},
        )
        found = ToolMessage(
            content="Results:\n- Data Room (id: pg_0, url: https://www.notion.so/data-room-pg_0)",
            tool_call_id="appsearch-1",
            name="notion__notion-search",
        )
        answer = cast(Any, router).wrap_model_call(
            SimpleNamespace(messages=[HumanMessage("x"), found]), lambda r: None
        )
        call = answer.tool_calls[0]
        assert call["name"] == "notion__notion-fetch"
        assert call["args"] == {"id": "https://www.notion.so/data-room-pg_0"}
        # What the fetch returns is for the model: no further call is made here.
        fetched = ToolMessage(content="page", tool_call_id=call["id"], name="notion__notion-fetch")
        sentinel = object()
        assert (
            cast(Any, router).wrap_model_call(
                SimpleNamespace(messages=[fetched]), lambda r: sentinel
            )
            is sentinel
        )

    def test_with_apps_connected_only_a_clear_web_question_is_forced_to_research(self):
        from langchain_core.messages import HumanMessage

        from tzara.agent.routing import ResearchRouter
        from tzara.config import ResearchSettings

        rules = QuestionRules(ResearchSettings())
        router = ResearchRouter(rules, apps_connected=lambda: True, clearly_web=rules.topical)
        request = SimpleNamespace(
            messages=[HumanMessage("What did we decide at the Acme sync?")], override=None
        )
        sentinel = object()
        assert (
            cast(Any, router).wrap_model_call(request, lambda r: sentinel) is sentinel
        )  # the model decides
        weather = SimpleNamespace(
            messages=[HumanMessage("what is the weather in Berlin")], override=None
        )
        assert (
            cast(Any, router).wrap_model_call(weather, lambda r: sentinel).tool_calls[0]["name"]
            == "research"
        )
