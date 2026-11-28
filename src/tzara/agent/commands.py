"""Memory commands that are done in code: remember this, forget that, what do you know about me."""

from __future__ import annotations

import logging
import re

from tzara.config import MemorySettings
from tzara.memory import FactStore, MemoryRefused
from tzara.tools.web import Sanitizer

logger = logging.getLogger(__name__)


class MemoryCommands:
    """Does what the user says about memory, itself, with no model request.

    Asked to "remember this" or "forget that", the small model often says it did and does not call
    the tool, or is refused because the screen text came with the message. These are the user's own
    words and plain commands, so they are read here: the reply is certain and instant.
    :meth:`handle` returns the reply, or ``None`` when the message is not a memory command.
    """

    def __init__(self, facts: FactStore, settings: MemorySettings, listed: int = 12) -> None:
        """Create the commands.

        Args:
            facts: Where facts are kept.
            settings: Longest fact (the same limit the store applies) and the words of the commands.
            listed: Most facts named when asked what is known.
        """
        self._facts = facts
        self._max = settings.max_fact_chars
        flags = re.IGNORECASE
        self._remember_command = re.compile(settings.command_remember, flags | re.DOTALL)
        self._forget_command = re.compile(settings.command_forget, flags)
        self._recall_command = re.compile(settings.command_recall, flags)
        self._where_command = re.compile(settings.command_where, flags)
        self._secret = re.compile(settings.command_secret, flags)
        self._this = frozenset(settings.command_this)
        self._verbs = settings.command_verbs
        self._listed = listed

    def handle(
        self, text: str, selection: str = "", typing: str = "", place: str = ""
    ) -> str | None:
        """The reply to a memory command, or ``None`` if ``text`` is not one.

        Args:
            text: What the user wrote.
            selection: Text they had selected, for "remember this".
            typing: Text of the field they were in, if nothing was selected.
            place: Where their Mac says they are, if they allowed it.
        """
        if len(text) > 600:
            return None
        if match := self._remember_command.match(text):
            return self._remember(match.group(1).strip(), selection, typing)
        if match := self._forget_command.match(text):
            return self._forget(match.group(1).strip().rstrip(".!"))
        if self._recall_command.match(text):
            return self._recall()
        if self._where_command.match(text):
            return self._where(place)
        return None

    # -- remember --------------------------------------------------------------------------------

    def _remember(self, what: str, selection: str, typing: str) -> str | None:
        words = what.rstrip(".!").strip().lower()
        if not words or words in self._this:
            source = Sanitizer.clean(selection or typing).strip()
            if not source:
                return "Select the text you want me to remember, then ask again."
            fact = self._clip("The user saved this note: " + " ".join(source.split()))
        elif len(what) < 3:
            return None
        else:
            fact = self._statement(Sanitizer.clean(what))
        if self._secret.search(fact):
            return (
                "I don't keep passwords, keys or card numbers. "
                "Please store those in a password manager."
            )
        try:
            stored, new = self._facts.add(fact)
        except MemoryRefused as error:
            return str(error)
        logger.info("memory command: remembered one fact (%d characters)", len(stored.text))
        return f"Remembered: {stored.text}" if new else f"I already knew that: {stored.text}"

    def _statement(self, text: str) -> str:
        """The user's words as a statement about them: "my dentist is Lee" becomes a fact."""
        line = " ".join(text.split()).rstrip(".!")
        if match := re.match(r"(?i)^my\s+(.+)$", line):
            return self._clip(f"The user's {match.group(1)}.")
        if match := re.match(r"(?i)^i(?:'m| am)\s+(.+)$", line):
            return self._clip(f"The user is {match.group(1)}.")
        if match := re.match(r"(?i)^i\s+(\w+)\s+(.+)$", line):
            verb = match.group(1).lower()
            if verb in self._verbs:
                return self._clip(f"The user {self._verbs[verb]} {match.group(2)}.")
        return self._clip(f"The user said: {line}.")

    def _clip(self, fact: str) -> str:
        if len(fact) <= self._max:
            return fact
        return fact[: self._max - 1].rstrip() + "…"

    # -- forget ----------------------------------------------------------------------------------

    def _forget(self, what: str) -> str | None:
        if re.fullmatch(r"(?i)everything|all|all of it|my memory|your memory", what):
            return "To erase everything, use Erase all on the Memory page of the dashboard."
        matches = self._facts.search(what, 5)
        if not matches:
            return f"I have nothing remembered about “{what}”."
        if len(matches) > 1:
            lines = "\n".join(f"- {fact.text}" for fact in matches)
            return f"Several things match. Say more of the one to forget:\n{lines}"
        self._facts.remove(matches[0].id)
        logger.info("memory command: forgot one fact")
        return f"Forgot: {matches[0].text}"

    # -- where ----------------------------------------------------------------------------------

    def _where(self, place: str) -> str:
        if place:
            return f"You are in {place}, going by your Mac's location."
        home = self._facts.by_slot("city")
        if home:
            return (
                f"{home.text} I don't know where you are right now: turn on Use my location "
                "in the dashboard's Context & writing settings if you want me to."
            )
        return (
            "I don't know where you are. Turn on Use my location in the dashboard's Context & "
            "writing settings, or tell me: \u201cI live in Berlin\u201d."
        )

    # -- recall ----------------------------------------------------------------------------------

    def _recall(self) -> str:
        facts = self._facts.recent(self._listed)
        if not facts:
            return "I don't know anything about you yet. I learn as we talk."
        lines = "\n".join(f"- {fact.text}" for fact in facts)
        more = self._facts.count() - len(facts)
        tail = f"\n\nAnd {more} more on the Memory page of the dashboard." if more > 0 else ""
        return f"This is what I know about you:\n{lines}{tail}"
