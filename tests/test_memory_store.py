"""Tests for the memory database, facts and conversation log."""

import os
import stat
import threading
from dataclasses import replace

import pytest

from tzara.config import ConfigError, MemorySettings
from tzara.memory import (
    ConversationLog,
    FactStore,
    MemoryDatabase,
    MemoryRefused,
    PersonalMemory,
)
from tzara.memory.text import SearchText

SETTINGS = MemorySettings()


@pytest.fixture(name="db")
def db_fixture():
    database = MemoryDatabase()
    yield database
    database.close()


class TestDatabase:
    def test_the_file_is_private_to_the_owner(self, tmp_path):
        directory = tmp_path / "data"
        MemoryDatabase(directory / "memory.db").close()
        assert stat.S_IMODE(os.stat(directory / "memory.db").st_mode) == 0o600
        assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700

    def test_what_is_stored_survives_closing_and_reopening(self, tmp_path):
        path = tmp_path / "memory.db"
        first = MemoryDatabase(path)
        FactStore(first, SETTINGS).add("The user likes tea.")
        first.close()
        second = MemoryDatabase(path)
        assert [f.text for f in FactStore(second, SETTINGS).all()] == ["The user likes tea."]
        second.close()

    def test_several_threads_can_use_it_at_once(self, db):
        facts = FactStore(db, SETTINGS)
        errors: list[Exception] = []

        def add(number):
            try:
                for n in range(20):
                    facts.add(f"fact {number} {n}")
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=add, args=(n,)) for n in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors and facts.count() == 100


class TestSearchText:
    @pytest.mark.parametrize(
        ("text", "terms"),
        [
            ("What is my favourite drink?", ["favourite", "drink"]),
            ("tea tea TEA", ["tea"]),
            ("", []),
            ("the and of", []),
            ("a b c", []),
            ("remember my name please", ["name"]),
        ],
    )
    def test_keeps_only_the_words_worth_searching_for(self, text, terms):
        assert SearchText.terms(text) == terms

    def test_the_query_is_made_of_quoted_words_only(self):
        assert SearchText.match('tea" OR "xyz') == '"tea" OR "xyz"'
        assert SearchText.match("coffee AND NOT tea; DROP TABLE") == (
            '"coffee" OR "tea" OR "drop" OR "table"'
        )

    def test_nothing_to_search_for_gives_none(self):
        assert SearchText.match("the of") is None

    def test_at_most_a_dozen_words(self):
        assert len(SearchText.terms(" ".join(f"word{n}" for n in range(30)))) == 12

    def test_two_phrasings_of_the_same_fact_share_a_key(self):
        assert SearchText.key("The user's NAME is Sam!") == SearchText.key("the user s name is sam")


class TestFactStore:
    def test_adds_and_lists_newest_first(self, db):
        facts = FactStore(db, SETTINGS)
        facts.add("The user's name is Sam.")
        facts.add("The user likes tea.")
        assert [f.text for f in facts.all()] == ["The user likes tea.", "The user's name is Sam."]

    def test_saying_it_again_does_not_store_it_twice(self, db):
        facts = FactStore(db, SETTINGS)
        first, new = facts.add("The user likes tea.")
        again, new_again = facts.add("the user LIKES tea")
        assert (new, new_again) == (True, False)
        assert again.id == first.id and facts.count() == 1
        assert again.updated_at >= first.updated_at

    def test_saying_it_again_makes_it_the_most_recent(self, db):
        facts = FactStore(db, SETTINGS)
        facts.add("one fact")
        facts.add("two fact")
        facts.add("one fact")
        assert facts.all()[0].text == "one fact"

    def test_whitespace_is_tidied(self, db):
        fact, _ = FactStore(db, SETTINGS).add("  The   user\nlikes tea.  ")
        assert fact.text == "The user likes tea."

    @pytest.mark.parametrize("text", ["", "   ", "!!!", "…"])
    def test_nothing_to_remember_is_refused(self, db, text):
        with pytest.raises(MemoryRefused, match="nothing to remember"):
            FactStore(db, SETTINGS).add(text)

    def test_a_long_fact_is_refused_with_advice(self, db):
        with pytest.raises(MemoryRefused, match="too long"):
            FactStore(db, replace(SETTINGS, max_fact_chars=20)).add("x" * 21)

    def test_a_full_memory_refuses_new_facts_but_not_repeats(self, db):
        facts = FactStore(db, replace(SETTINGS, max_facts=2))
        facts.add("one")
        facts.add("two")
        with pytest.raises(MemoryRefused, match="memory is full"):
            facts.add("three")
        assert facts.add("one")[1] is False

    def test_removing_a_fact(self, db):
        facts = FactStore(db, SETTINGS)
        fact, _ = facts.add("The user likes tea.")
        assert facts.remove(fact.id) is True
        assert facts.remove(fact.id) is False
        assert facts.all() == []

    def test_a_removed_fact_is_no_longer_found_by_search(self, db):
        facts = FactStore(db, SETTINGS)
        fact, _ = facts.add("The user likes tea.")
        facts.remove(fact.id)
        assert facts.search("tea", 5) == []

    def test_search_finds_facts_by_their_words_best_first(self, db):
        facts = FactStore(db, SETTINGS)
        facts.add("The user lives in Berlin.")
        facts.add("The user drinks green tea with honey.")
        facts.add("The user drinks coffee.")
        found = [f.text for f in facts.search("which tea do I drink", 5)]
        assert found[0] == "The user drinks green tea with honey."

    @pytest.mark.parametrize(
        "query", ["", "the of", 'tea" OR 1=1 --', "AND OR NOT", "'; DROP TABLE facts;--"]
    )
    def test_awkward_search_text_never_breaks_the_query(self, db, query):
        facts = FactStore(db, SETTINGS)
        facts.add("The user likes tea.")
        facts.search(query, 5)
        assert facts.count() == 1

    def test_recent_gives_the_most_recently_confirmed_facts_first(self, db):
        facts = FactStore(db, SETTINGS)
        for text in ["a one", "b two", "c three"]:
            facts.add(text)
        facts.add("a one")  # confirmed again: now the most recent
        assert [f.text for f in facts.recent(2)] == ["a one", "c three"]

    def test_recent_with_more_asked_for_than_there_are_gives_them_all(self, db):
        facts = FactStore(db, SETTINGS)
        facts.add("only one")
        assert [f.text for f in facts.recent(10)] == ["only one"]

    def test_clear_forgets_everything(self, db):
        facts = FactStore(db, SETTINGS)
        facts.add("one")
        facts.add("two")
        assert facts.clear() == 2 and facts.count() == 0


class TestConversationLog:
    def make(self, db, **settings):
        return ConversationLog(db, replace(SETTINGS, **settings))

    def test_stores_messages_in_order(self, db):
        log = self.make(db)
        conversation = log.start()
        log.append(conversation, "user", "hello")
        log.append(conversation, "assistant", "hi there")
        assert [(m.role, m.text) for m in log.messages(conversation)] == [
            ("user", "hello"),
            ("assistant", "hi there"),
        ]

    def test_the_first_user_message_becomes_the_title(self, db):
        log = self.make(db)
        conversation = log.start()
        log.append(conversation, "assistant", "ignored")
        log.append(conversation, "user", "Plan a trip to the Alps " + "x" * 100)
        log.append(conversation, "user", "second")
        title = db.execute("SELECT title FROM conversations")[0]["title"]
        assert title.startswith("Plan a trip to the Alps") and len(title) == 60

    def test_the_latest_open_conversation_is_found_after_a_restart(self, db):
        log = self.make(db)
        old = log.start()
        log.append(old, "user", "old")
        log.finish(old)
        current = log.start()
        log.append(current, "user", "current")
        assert log.latest_open() == current

    def test_an_empty_or_finished_conversation_is_not_picked_up(self, db):
        log = self.make(db)
        log.start()
        assert log.latest_open() is None
        done = log.start()
        log.append(done, "user", "x")
        log.finish(done)
        log.finish(done)  # finishing twice is harmless
        assert log.latest_open() is None

    def test_a_restored_conversation_is_the_end_of_it_within_the_budget(self, db):
        log = self.make(db, restore_chars=25)
        conversation = log.start()
        for n in range(10):
            log.append(conversation, "user", f"message number {n}")  # 16 characters each
        texts = [m.text for m in log.messages(conversation)]
        assert texts == ["message number 9"]
        log2 = self.make(db, restore_chars=40)
        assert [m.text for m in log2.messages(conversation)] == [
            "message number 8",
            "message number 9",
        ]

    def test_one_message_longer_than_the_budget_is_still_kept(self, db):
        log = self.make(db, restore_chars=5)
        conversation = log.start()
        log.append(conversation, "user", "a very long message")
        assert len(log.messages(conversation)) == 1

    def test_search_finds_what_was_said_before(self, db):
        log = self.make(db)
        first = log.start()
        log.append(first, "user", "I am planning a cycling trip through the Alps")
        log.append(first, "assistant", "Lovely, how many days?")
        second = log.start()
        log.append(second, "user", "Recommend a pasta recipe")
        found = log.search("cycling Alps", 5)
        assert [(s.conversation_id, s.role) for s in found] == [(first, "user")]
        assert found[0].title.startswith("I am planning")
        assert "cycling" in found[0].text

    def test_search_can_leave_out_the_conversation_in_progress(self, db):
        log = self.make(db)
        first = log.start()
        log.append(first, "user", "cycling plans")
        second = log.start()
        log.append(second, "user", "more cycling plans")
        assert [s.conversation_id for s in log.search("cycling", 5, exclude=second)] == [first]

    def test_search_with_no_words_finds_nothing(self, db):
        log = self.make(db)
        log.append(log.start(), "user", "hello")
        assert log.search("the of", 5) == []

    def test_a_long_message_is_cut_around_the_match(self, db):
        log = self.make(db, snippet_chars=60)
        conversation = log.start()
        log.append(
            conversation, "user", "filler " * 40 + "the secret word is pineapple " + "more " * 40
        )
        text = log.search("pineapple", 1)[0].text
        assert "pineapple" in text and len(text) <= 64
        assert text.startswith("…") and text.endswith("…")

    def test_the_start_of_a_long_message_is_cut_only_at_the_end(self, db):
        log = self.make(db, snippet_chars=30)
        conversation = log.start()
        log.append(conversation, "user", "pineapple " + "filler " * 30)
        text = log.search("pineapple", 1)[0].text
        assert text.startswith("pineapple") and text.endswith("…")

    def test_deleting_a_conversation_removes_it_from_search(self, db):
        log = self.make(db)
        conversation = log.start()
        log.append(conversation, "user", "pineapple")
        assert log.clear() == 1
        assert log.search("pineapple", 5) == [] and log.count() == 0

    def test_counts_conversations(self, db):
        log = self.make(db)
        log.start()
        log.start()
        assert log.count() == 2


class TestPersonalMemory:
    def test_is_none_when_turned_off(self, tmp_path):
        assert PersonalMemory.open(replace(SETTINGS, enabled=False), tmp_path) is None

    def test_opens_a_file_in_the_directory(self, tmp_path):
        memory = PersonalMemory.open(SETTINGS, tmp_path)
        assert memory is not None and (tmp_path / "memory.db").exists()
        memory.close()

    def test_without_a_directory_it_lives_in_ram(self):
        memory = PersonalMemory.open(SETTINGS, None)
        assert memory is not None
        memory.facts.add("one")
        assert memory.facts.count() == 1

    def test_erase_forgets_facts_and_conversations(self):
        memory = PersonalMemory.open(SETTINGS, None)
        assert memory is not None
        memory.facts.add("one")
        memory.log.append(memory.log.start(), "user", "hello")
        memory.erase()
        assert memory.facts.count() == 0 and memory.log.count() == 0


class TestMemorySettings:
    @pytest.mark.parametrize(
        "field",
        [
            "max_fact_chars",
            "max_facts",
            "inject_facts",
            "inject_chars",
            "recall_results",
            "snippet_chars",
            "restore_chars",
        ],
    )
    def test_limits_must_be_positive(self, field):
        with pytest.raises(ConfigError, match="positive"):
            MemorySettings(**{field: 0})  # type: ignore[arg-type]

    def test_env_can_turn_it_off(self):
        from tzara.config import TzaraConfig

        assert TzaraConfig.from_env({"TZARA_MEMORY": "false"}).memory.enabled is False
        assert TzaraConfig.from_env({}).memory.enabled is True
