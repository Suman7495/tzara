"""Memory commands that are done in code: remember this, forget that, what do you know about me."""

from __future__ import annotations

import logging
import re
from typing import ClassVar

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

    REMEMBER: ClassVar[re.Pattern[str]] = re.compile(
        r"^\s*(?:please\s+|hey\s+|can you\s+|could you\s+)?(?:remember|memori[sz]e|make a note|"
        r"note down|note|save|keep in mind|don'?t forget|do not forget)\b(?:\s+that)?"
        r"\s*[:,-]?\s*(.*)$",
        re.IGNORECASE | re.DOTALL,
    )
    FORGET: ClassVar[re.Pattern[str]] = re.compile(
        r"^\s*(?:please\s+)?forget\b(?:\s+(?:about|that))?\s+(.+)$", re.IGNORECASE
    )
    RECALL: ClassVar[re.Pattern[str]] = re.compile(
        r"^\s*(?:hey\s+)?(?:what|which)\b.*\b(?:do you|have you|did you)\b.*\b(?:know|remember|"
        r"learned|learnt|stored|saved)\b.*\b(?:about me|about us|so far)?\s*\??\s*$|"
        r"^\s*(?:show|list)\s+(?:me\s+)?(?:my\s+)?(?:memory|memories|what you remember)\b",
        re.IGNORECASE,
    )
    WHERE: ClassVar[re.Pattern[str]] = re.compile(
        r"^\s*(?:hey\s+)?(?:where am i|where are we|where do i live|"
        r"what(?:'s| is) my (?:current )?(?:location|city|place))\b",
        re.IGNORECASE,
    )
    THIS: ClassVar[frozenset[str]] = frozenset({"this", "it", "that", "this one", "all this"})
    SECRET: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(?:password|passcode|passphrase|secret|api[ -]?key|token|credit card|card number|"
        r"cvv|iban|ssn|social security|private key)\b",
        re.IGNORECASE,
    )
    VERBS: ClassVar[dict[str, str]] = {
        "am": "is",
        "have": "has",
        "like": "likes",
        "love": "loves",
        "hate": "hates",
        "prefer": "prefers",
        "work": "works",
        "live": "lives",
        "need": "needs",
        "want": "wants",
        "use": "uses",
        "study": "studies",
        "own": "owns",
        "drive": "drives",
        "speak": "speaks",
        "play": "plays",
    }

    def __init__(self, facts: FactStore, max_chars: int, listed: int = 12) -> None:
        """Create the commands.

        Args:
            facts: Where facts are kept.
            max_chars: Longest fact (the same limit the store applies).
            listed: Most facts named when asked what is known.
        """
        self._facts = facts
        self._max = max_chars
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
        if match := self.REMEMBER.match(text):
            return self._remember(match.group(1).strip(), selection, typing)
        if match := self.FORGET.match(text):
            return self._forget(match.group(1).strip().rstrip(".!"))
        if self.RECALL.match(text):
            return self._recall()
        if self.WHERE.match(text):
            return self._where(place)
        return None

    # -- remember --------------------------------------------------------------------------------

    def _remember(self, what: str, selection: str, typing: str) -> str | None:
        words = what.rstrip(".!").strip().lower()
        if not words or words in self.THIS:
            source = Sanitizer.clean(selection or typing).strip()
            if not source:
                return "Select the text you want me to remember, then ask again."
            fact = self._clip("The user saved this note: " + " ".join(source.split()))
        elif len(what) < 3:
            return None
        else:
            fact = self._statement(Sanitizer.clean(what))
        if self.SECRET.search(fact):
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
            if verb in self.VERBS:
                return self._clip(f"The user {self.VERBS[verb]} {match.group(2)}.")
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
