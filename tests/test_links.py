"""Tests for keeping made-up links out of replies."""

import random

import pytest

from helpers import (
    EchoTool,
    RecordingFakeModel,
    ToolCallingFakeModel,
    fake_creator,
    personal_memory,
)
from test_agent import agent_with_fake_model
from tzara.agent.events import AssistantDelta, RunCompleted
from tzara.agent.links import LinkGuard, TrustedLinks
from tzara.agent.memory import ConversationMemory

REAL = "https://www.dm.de/p/d/1589788/balea-conditioner"
OTHER = "https://example.com/a_(b)"


def trusted(*texts):
    links = TrustedLinks()
    for text in texts:
        links.collect(text)
    return links


def run(links, text, chunk=None):
    guard = LinkGuard(links.trusts)
    if chunk is None:
        return guard.feed(text) + guard.finish()
    shown = ""
    for index in range(0, len(text), chunk):
        shown += guard.feed(text[index : index + chunk])
    return shown + guard.finish()


class TestTrustedLinks:
    def test_remembers_addresses_found_in_text(self):
        links = trusted(f"1. Balea\n   {REAL}\n   snippet", "[x](https://a.test/page)")
        assert links.trusts(REAL) and links.trusts("https://a.test/page")

    def test_a_page_is_the_same_page_whatever_the_query_fragment_scheme_or_www(self):
        links = trusted("https://www.shop.test/p/1?utm=a#top")
        assert links.trusts("http://shop.test/p/1/")
        assert links.trusts("https://SHOP.test/p/1?x=2")

    def test_a_different_path_on_a_real_site_is_not_trusted(self):
        assert not trusted(REAL).trusts("https://www.dm.de/p/d/999999/made-up-product")

    def test_another_site_is_not_trusted(self):
        assert not trusted(REAL).trusts("https://rossmann.de/p/d/1589788/balea-conditioner")

    def test_trailing_punctuation_is_not_part_of_the_address(self):
        links = trusted("see https://a.test/page.")
        assert links.trusts("https://a.test/page") and links.trusts("https://a.test/page.")

    def test_addresses_with_parentheses_are_kept_whole(self):
        assert trusted(f"see {OTHER}.").trusts(OTHER)

    def test_only_web_addresses_count(self):
        links = trusted("mailto:a@b.c file:///etc/passwd ftp://a.test/f javascript:alert(1)")
        assert not links.trusts("ftp://a.test/f") and not links.trusts("file:///etc/passwd")
        assert not links.trusts("not an address") and not links.trusts("")

    def test_text_that_looks_like_an_address_but_has_no_site_is_ignored(self):
        links = trusted("see http://:80/x and https://?q=1")
        assert not links.trusts("http://:80/x")

    def test_clear_forgets_everything(self):
        links = trusted(REAL)
        links.clear()
        assert not links.trusts(REAL)


class TestLinkGuard:
    links = trusted(REAL, OTHER)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("plain words only", "plain words only"),
            (f"Buy [Balea]({REAL}) now", f"Buy [Balea]({REAL}) now"),
            ("Buy [Fake](https://www.dm.de/p/d/999/fake) now", "Buy Fake now"),
            (f"See {REAL}. And https://fake.example/x!", f"See {REAL}. And !"),
            (f"({REAL})", f"({REAL})"),
            ("(https://fake.example/x)", "()"),
            (f"[Wiki]({OTHER}) ok", f"[Wiki]({OTHER}) ok"),
            ("an array a[0] and [x] and more", "an array a[0] and [x] and more"),
            ("[unclosed link text and more", "[unclosed link text and more"),
            ("[text] then (not a link)", "[text] then (not a link)"),
            ("end with http", "end with http"),
            ("end with https:/", "end with https:/"),
            ("end with [", "end with ["),
            ("[a](https://unfinished", "[a]("),
            ("[a](", "[a]("),
            ("[a]", "[a]"),
            ("two [A](https://f.test/1) and [B](https://f.test/2)", "two A and B"),
            (
                f"mixed [Fake](https://f.test/1) and [Real]({REAL})",
                f"mixed Fake and [Real]({REAL})",
            ),
            ("HTTPS://FAKE.TEST/X gone", " gone"),
            ("line one [broken\nline two](https://f.test)", "line one [broken\nline two]()"),
            ("[]( )", "[]( )"),
        ],
    )
    def test_keeps_seen_links_and_removes_the_others(self, text, expected):
        assert run(self.links, text) == expected

    @pytest.mark.parametrize("chunk", [1, 2, 3, 5, 7, 13])
    def test_the_result_does_not_depend_on_how_the_stream_is_cut(self, chunk):
        text = (
            f"Try [Balea]({REAL}) or [Fake](https://www.dm.de/p/d/0/x), see {REAL}. "
            f"Also https://fake.example/y and a[1] [z] ({OTHER}) [w](https://q.test/z)."
        )
        assert run(self.links, text, chunk) == run(self.links, text)

    def test_random_cuts_never_change_the_result(self):
        rng = random.Random(7)
        text = (
            f"[A]({REAL}) x [B](https://nope.test/1) y https://nope.test/2 z {REAL}, "
            "[unclosed and (parens) a[0] done"
        )
        expected = run(self.links, text)
        for _ in range(200):
            cuts = sorted(rng.sample(range(1, len(text)), k=rng.randint(1, 12)))
            pieces = [text[a:b] for a, b in zip([0, *cuts], [*cuts, len(text)], strict=True)]
            guard = LinkGuard(self.links.trusts)
            shown = "".join(guard.feed(piece) for piece in pieces) + guard.finish()
            assert shown == expected

    def test_text_that_cannot_be_a_link_is_not_held_back(self):
        guard = LinkGuard(self.links.trusts)
        assert guard.feed("Hello there, ") == "Hello there, "

    def test_the_start_of_a_possible_link_is_held_back_until_it_is_clear(self):
        guard = LinkGuard(self.links.trusts)
        assert guard.feed("see ht") == "see "
        assert guard.feed("tps://fake.test/x") == ""  # still could be longer
        assert guard.feed(" more") == " more"  # the address ended: it was not seen, so it is gone

    def test_a_very_long_bracket_is_released_as_text(self):
        guard = LinkGuard(self.links.trusts)
        shown = guard.feed("[" + "x" * 400) + guard.finish()
        assert shown == "[" + "x" * 400

    def test_a_very_long_unclosed_address_part_is_released_as_text(self):
        guard = LinkGuard(self.links.trusts)
        shown = guard.feed("[a](" + "x" * 2500) + guard.finish()
        assert shown.startswith("[a]") and "x" * 100 in shown

    def test_finish_with_nothing_held_gives_nothing(self):
        assert LinkGuard(self.links.trusts).finish() == ""


class TestAgentKeepsOnlySeenLinks:
    def agent_saying(self, reply):
        """An agent whose model calls a tool and then says ``reply``."""
        agent, _ = agent_with_fake_model()
        agent._tools = [*agent._tools, EchoTool()]
        model = ToolCallingFakeModel(messages=iter([]), final_answer=reply)
        agent._factory.create = fake_creator(model)  # type: ignore[method-assign]
        agent._create_agent()
        return agent

    def test_a_link_from_a_tool_result_stays_and_an_invented_one_goes(self):
        # a tool's result stands in for a search result that contains an address
        agent = self.agent_saying(
            f"Try [Balea]({REAL}) or [Made up](https://www.dm.de/p/d/0/fake)."
        )
        agent._activity.links.collect(REAL)
        agent._activity.begin_turn = lambda: None
        shown = "".join(agent.handle("2+2?"))
        assert shown == f"Try [Balea]({REAL}) or Made up."

    def test_with_no_tool_at_all_no_link_survives(self):
        agent, _ = agent_with_fake_model("See [here](https://made.up/x) and https://also.fake/y")
        assert "".join(agent.handle("hi")) == "See here and "

    def test_a_link_the_user_typed_may_be_repeated(self):
        agent, _ = agent_with_fake_model("You mean [this](https://user.test/page)?")
        assert "".join(agent.handle("what about https://user.test/page")) == (
            "You mean [this](https://user.test/page)?"
        )

    def test_links_do_not_carry_over_to_the_next_turn(self):
        agent, _ = agent_with_fake_model("one https://a.test/x", "two https://a.test/x")
        "".join(agent.handle("look at https://a.test/x"))
        assert "".join(agent.handle("and now?")) == "two "

    def test_the_run_events_carry_the_cleaned_text_and_length(self):
        agent, _ = agent_with_fake_model("go [x](https://fake.test/1) now")
        events = list(agent.run("hi"))
        text = "".join(e.text for e in events if isinstance(e, AssistantDelta))
        done = events[-1]
        assert isinstance(done, RunCompleted)
        assert text == "go x now" and done.characters == len(text)

    def test_the_stored_conversation_loses_the_link_too(self):
        agent, _ = agent_with_fake_model("go [x](https://fake.test/1) now")
        "".join(agent.handle("hi"))
        assert [m.text for m in agent.history()] == ["hi", "go x now"]

    def test_the_log_keeps_only_what_the_user_saw(self):
        memory = personal_memory()
        assert memory is not None
        conversation = ConversationMemory(log=memory.log)
        agent, _ = agent_with_fake_model("go [x](https://fake.test/1) now", memory=conversation)
        agent._memory = conversation
        "".join(agent.handle("hi"))
        assert conversation.conversation_id is not None
        assert [m.text for m in memory.log.messages(conversation.conversation_id)] == [
            "hi",
            "go x now",
        ]

    def test_a_reply_without_links_is_left_alone(self):
        agent, _ = agent_with_fake_model("just words")
        "".join(agent.handle("hi"))
        assert [m.text for m in agent.history()] == ["hi", "just words"]

    def test_a_failure_to_correct_the_stored_reply_is_logged_not_raised(self, caplog):
        agent, _ = agent_with_fake_model("go [x](https://fake.test/1) now")
        agent._agent.update_state = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no"))  # type: ignore[method-assign]
        assert "".join(agent.handle("hi")) == "go x now"
        assert "could not remove unseen links" in caplog.text


def test_the_rules_tell_the_model_not_to_invent_addresses():
    from tzara.prompts import Prompts

    assert "Never write a web address you did not get from a tool" in Prompts().web_rules


def test_the_helpers_import_is_used():
    assert RecordingFakeModel.replying("x")
