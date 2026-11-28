"""Writing into other apps, and the memory that learns by itself."""

from __future__ import annotations

from helpers import RecordingFakeModel, personal_memory
from tzara.agent.writer import InlineWriter, WriteRequest
from tzara.config import TzaraConfig


def writer(model, personal=None):
    return InlineWriter(lambda: model, TzaraConfig(), personal)


class TestWhereAndWhat:
    def test_places_come_from_the_app_or_the_page(self):
        w = writer(None)
        assert w.place(WriteRequest(app="Slack", bundle="com.tinyspeck.slackmacgap")) == "chat"
        assert (
            w.place(WriteRequest(app="Google Chrome", address="https://www.linkedin.com/feed"))
            == "social"
        )
        assert (
            w.place(WriteRequest(app="Google Chrome", address="https://app.slack.com/client"))
            == "chat"
        )
        assert w.place(WriteRequest(app="Discord")) == "chat"
        assert w.place(WriteRequest(app="Microsoft Teams")) == "chat"
        assert w.place(WriteRequest(app="Microsoft Outlook")) == "email"
        assert w.place(WriteRequest(app="Preview")) == "other"

    def test_the_fields_state_decides_what_is_wanted(self):
        w = writer(None)
        assert w.mode(WriteRequest()) == "reply"
        assert w.mode(WriteRequest(field="reply that I agree")) == "instruct"
        assert w.mode(WriteRequest(field="/g what is 2+2")) == "instruct"
        assert w.mode(WriteRequest(field="are you free tomorow?")) == "polish"
        assert w.mode(WriteRequest(field="draft text", selection="some words")) == "rewrite"


class TestGuidanceFromTheBar:
    def test_guidance_edits_the_text_or_guides_an_empty_field(self):
        w = writer(None)
        assert w.mode(WriteRequest(field="see you at 5", instruction="make it shorter")) == "edit"
        assert w.mode(WriteRequest(selection="x y", instruction="more formal")) == "edit"
        assert w.mode(WriteRequest(instruction="say no politely")) == "guided"

    def test_the_request_and_the_text_are_both_in_the_prompt(self):
        model = RecordingFakeModel.replying("See you at 5.", "No, thanks.")
        w = writer(model)
        result = w.write(WriteRequest(app="Slack", field="see you at 5!!!", instruction="calmer"))
        assert (result.mode, result.replaces) == ("edit", "field")
        prompt = " ".join(m.text for m in model.seen_messages[0])
        assert "calmer" in prompt and "see you at 5!!!" in prompt
        result = w.write(WriteRequest(app="Slack", visible="Ann: coffee?", instruction="decline"))
        assert (result.mode, result.replaces) == ("guided", "nothing")

    def test_guidance_does_not_teach_the_style_memory(self):
        memory = personal_memory()
        writer(RecordingFakeModel.replying("ok"), memory).write(
            WriteRequest(app="Slack", field="a draft long enough to be a sample", instruction="x")
        )
        assert memory.styles.count() == 0


class TestWriting:
    def test_the_model_is_asked_once_with_the_conversation_and_no_tools(self):
        model = RecordingFakeModel.replying('"Sounds good, see you then!"')
        result = writer(model).write(
            WriteRequest(app="Slack", field="", visible="Ann: shall we meet at 5?")
        )
        assert result.text == "Sounds good, see you then!"
        assert (result.mode, result.place, result.replaces) == ("reply", "chat", "nothing")
        assert len(model.seen_messages) == 1
        prompt = " ".join(m.text for m in model.seen_messages[0])
        assert "Ann: shall we meet at 5?" in prompt
        assert "chat app" in prompt

    def test_a_selection_is_replaced_and_a_draft_replaces_the_field(self):
        model = RecordingFakeModel.replying("Better.", "Polished text here.")
        w = writer(model)
        assert w.write(WriteRequest(selection="bad txt")).replaces == "selection"
        assert w.write(WriteRequest(field="my draft")).replaces == "field"

    def test_thoughts_and_wrapping_quotes_are_removed(self):
        assert InlineWriter.clean("<think>hmm</think>\n“Hello there”") == "Hello there"
        assert InlineWriter.clean("It's fine, isn't it") == "It's fine, isn't it"

    def test_nothing_from_the_model_is_an_error(self):
        import pytest

        with pytest.raises(ValueError):
            writer(RecordingFakeModel.replying("<think>x</think>")).write(WriteRequest())

    def test_the_user_is_known_by_their_facts_and_their_own_drafts(self):
        memory = personal_memory()
        memory.facts.add("The user's name is Sam.")
        model = RecordingFakeModel.replying("ok", "ok")
        w = writer(model, memory)
        w.write(WriteRequest(app="Slack", field="hey can u send me the deck when ur free pls"))
        assert memory.styles.recent("chat", 3) == ["hey can u send me the deck when ur free pls"]
        w.write(WriteRequest(app="Slack"))
        prompt = model.seen_messages[1][0].text
        assert "The user's name is Sam." in prompt
        assert "hey can u send me the deck" in prompt
        assert "Wrote in Slack for" in memory.activity.recent()[-1].summary


class TestMemoryLearnsByItself:
    def test_facts_are_picked_up_from_what_the_user_says(self):
        memory = personal_memory()
        learned = memory.capture.learn("my name is Sam and I work at Acme")
        assert [f.text for f in learned] == ["The user's name is Sam.", "The user works at Acme."]

    def test_a_new_answer_replaces_the_old_one(self):
        memory = personal_memory()
        memory.capture.learn("I live in Berlin")
        memory.capture.learn("I live in Paris")
        assert [f.text for f in memory.facts.all()] == ["The user lives in Paris."]

    def test_questions_pastes_and_small_talk_teach_nothing(self):
        memory = personal_memory()
        assert memory.capture.learn("what is my name?") == []
        assert memory.capture.learn("I like it") == []
        assert memory.capture.learn("never mind") == []
        assert memory.capture.learn("I work at Acme " + "x" * 500) == []

    def test_standing_wishes_are_kept(self):
        memory = personal_memory()
        memory.capture.learn("please always answer in short sentences")
        assert "always answer in short sentences" in memory.facts.all()[0].text

    def test_activity_is_kept_newest_last_and_searched(self):
        memory = personal_memory(max_activity=3)
        for n in range(5):
            memory.activity.record("app", f"changed page {n}")
        assert [a.summary for a in memory.activity.recent()] == [
            "changed page 2",
            "changed page 3",
            "changed page 4",
        ]
        assert memory.activity.search("page 4", 5)[0].summary == "changed page 4"

    def test_erasing_forgets_everything(self):
        memory = personal_memory()
        memory.capture.learn("I live in Rome")
        memory.activity.record("app", "x")
        memory.styles.add("chat", "a sample of my writing that is long enough")
        memory.erase()
        assert (memory.facts.count(), memory.activity.count(), memory.styles.count()) == (0, 0, 0)

    def test_an_old_database_gets_the_new_column(self, tmp_path):
        import sqlite3

        path = tmp_path / "memory.db"
        old = sqlite3.connect(path)
        old.execute(
            "CREATE TABLE facts (id INTEGER PRIMARY KEY, text TEXT NOT NULL, key TEXT NOT NULL "
            "UNIQUE, created_at REAL NOT NULL, updated_at REAL NOT NULL)"
        )
        old.execute(
            "INSERT INTO facts VALUES (1, 'The user likes tea.', 'the user likes tea', 1, 1)"
        )
        old.commit()
        old.close()
        memory = personal_memory(tmp_path)
        memory.capture.learn("I live in Oslo")
        assert memory.facts.count() == 2


class TestReadingTheMemory:
    def test_conversations_are_listed_with_their_title_and_can_be_read(self):
        memory = personal_memory()
        first = memory.log.start()
        memory.log.append(first, "user", "what is the weather in Berlin")
        memory.log.append(first, "assistant", "Cold.")
        second = memory.log.start()
        memory.log.append(second, "user", "write to Anna")
        listed = memory.log.recent(10)
        assert [(c.title, c.messages) for c in listed] == [
            ("write to Anna", 1),
            ("what is the weather in Berlin", 2),
        ]
        assert [m.text for m in memory.log.transcript(first)] == [
            "what is the weather in Berlin",
            "Cold.",
        ]

    def test_activity_and_samples_come_newest_first(self):
        memory = personal_memory()
        memory.activity.record("app", "one")
        memory.activity.record("app", "two")
        memory.styles.add("chat", "the first sample of my writing, long enough")
        memory.styles.add("chat", "the second sample of my writing, long enough")
        assert [a.summary for a in memory.activity.latest(5)] == ["two", "one"]
        assert next(text for _, text, _ in memory.styles.all()).startswith("the second")


class TestCarryingOnAnOldConversation:
    def test_an_earlier_conversation_comes_back_and_the_current_one_is_kept(self):
        from tzara.agent.memory import ConversationMemory

        memory = personal_memory()
        conversation = ConversationMemory(log=memory.log)
        conversation.record("user", "first question")
        conversation.record("assistant", "first answer")
        first = conversation.conversation_id
        assert first is not None
        conversation.reset()  # "New chat"
        conversation.record("user", "second question")
        second = conversation.conversation_id
        assert second is not None and first != second

        assert conversation.resume(first)
        assert conversation.conversation_id == first
        assert [m.text for m in conversation.take_restore()] == ["first question", "first answer"]
        # The one put aside is ended, so it is listed and can be carried on again.
        assert {c.id for c in memory.log.recent(10)} == {first, second}
        assert conversation.resume(second)

    def test_an_unknown_conversation_changes_nothing(self):
        from tzara.agent.memory import ConversationMemory

        conversation = ConversationMemory(log=personal_memory().log)
        conversation.record("user", "hi")
        before = conversation.conversation_id
        assert not conversation.resume(999)
        assert conversation.conversation_id == before
