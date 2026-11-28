"""Picks up lasting facts from what the user says about themselves, with no model request.

"My name is Sam", "I work at Acme", "I prefer short answers": a person does not say "remember
this" for these, and a small model asked to call ``remember`` often does not. They are found here,
in code, from the user's own words only (never a web page or the screen), so memory fills as they
chat. A side request to the model would cost seconds of reading on the next reply.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import ClassVar

from tzara.config import MemorySettings
from tzara.memory.facts import Fact, FactStore, MemoryRefused

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Rule:
    """One way of saying a thing about oneself, and the fact it gives."""

    slot: str
    pattern: re.Pattern[str]
    template: str


class FactCapture:
    """Reads a message for facts about its writer and stores them."""

    ROLES: ClassVar[str] = (
        r"engineer|developer|designer|founder|co-?founder|ceo|cto|coo|cfo|manager|director|"
        r"student|teacher|professor|doctor|nurse|lawyer|researcher|writer|consultant|analyst|"
        r"scientist|architect|marketer|accountant|freelancer|entrepreneur|investor|recruiter"
    )
    RELATIONS: ClassVar[str] = (
        r"wife|husband|partner|girlfriend|boyfriend|son|daughter|brother|sister|mother|father|"
        r"mom|dad|boss|manager|cofounder|co-founder|colleague|dog|cat"
    )
    END: ClassVar[str] = (
        r"(?=[.,;!?]|\s*$|\s+(?:and|but|so|because|since|though|now|currently|right now"
        r"|at|for|in|with|from|near)\b)"
    )
    NAME: ClassVar[str] = r"([A-Za-z][\w'\u2019-]*(?:\s+[A-Z][\w'\u2019-]*)?)"
    NOT_NAMES: ClassVar[frozenset[str]] = frozenset(
        {"a", "an", "the", "not", "so", "very", "just", "here", "there", "sorry", "fine", "ok"}
    )
    VAGUE: ClassVar[frozenset[str]] = frozenset(
        {"it", "this", "that", "them", "these", "those", "him", "her", "you", "so", "too", "to"}
    )
    PREFERENCE_VERBS: ClassVar[dict[str, str]] = {
        "prefer": "prefers",
        "like": "likes",
        "love": "loves",
        "enjoy": "enjoys",
        "hate": "hates",
        "dislike": "dislikes",
        "don't like": "does not like",
        "do not like": "does not like",
    }

    def __init__(self, facts: FactStore, settings: MemorySettings) -> None:
        """Create it.

        Args:
            facts: Where what is found is kept.
            settings: ``capture_max_chars``: longer messages are pastes, not self-description.
        """
        self._facts = facts
        self._settings = settings
        flags = re.IGNORECASE
        self._rules = (
            _Rule(
                "name",
                re.compile(rf"\b(?:my name is|call me|i am called)\s+{self.NAME}{self.END}", flags),
                "The user's name is {0}.",
            ),
            _Rule(
                "employer",
                re.compile(
                    r"\bi(?:'m| am)?\s*(?:work|working)\s+(?:at|for)\s+"
                    rf"([\w&.'\u2019 -]{{2,40}}?){self.END}",
                    flags,
                ),
                "The user works at {0}.",
            ),
            _Rule(
                "role",
                re.compile(
                    r"\bi(?:'m| am)\s+(?:a |an |the )?((?:[\w-]+ ){0,2}"
                    rf"(?:{self.ROLES})){self.END}",
                    flags,
                ),
                "The user is a {0}.",
            ),
            _Rule(
                "city",
                re.compile(
                    r"\bi\s*(?:live|am living|'m living|am based|'m based)\s+in\s+"
                    rf"([\w.'\u2019 -]{{2,40}}?){self.END}",
                    flags,
                ),
                "The user lives in {0}.",
            ),
            _Rule(
                "city",
                re.compile(
                    r"\bmy\s+(?:home\s*town|city|town|home city)\s+is\s+"
                    rf"([\w.'\u2019 -]{{2,40}}?){self.END}",
                    flags,
                ),
                "The user lives in {0}.",
            ),
            _Rule(
                "email",
                re.compile(r"\bmy e-?mail(?: address)? is\s+([\w.+-]+@[\w-]+\.[\w.-]+[\w])", flags),
                "The user's email address is {0}.",
            ),
        )
        self._relation = re.compile(
            rf"\bmy\s+({self.RELATIONS})(?:'s name)?\s+(?:is|are)\s+(?:called\s+|named\s+)?"
            rf"([A-Z][\w'\u2019-]*(?:\s+[A-Z][\w'\u2019-]*)?){self.END}"
        )
        verbs = "|".join(re.escape(verb) for verb in self.PREFERENCE_VERBS)
        self._preference = re.compile(rf"\bi\s+({verbs})\s+(.{{3,80}}?)(?=[.!?]|\s*$)", flags)
        self._standing = re.compile(
            r"^\s*(?:please\s+|from now on,?\s+)?(always|never)\s+(.{4,100}?)(?=[.!?]|\s*$)",
            flags,
        )

    def learn(self, text: str) -> list[Fact]:
        """Store the lasting facts ``text`` states about its writer; return the new ones.

        Nothing is learned from a question, a paste (long or with code) or an empty message.
        """
        if not self._worth_reading(text):
            return []
        learned: list[Fact] = []
        for slot, sentence in self._candidates(text):
            try:
                fact, new = self._facts.add(sentence, slot)
            except MemoryRefused:
                continue
            if new:
                learned.append(fact)
        if learned:
            logger.info("learned %d fact(s) from the message", len(learned))
        return learned

    def _worth_reading(self, text: str) -> bool:
        stripped = text.strip()
        return (
            bool(stripped)
            and len(stripped) <= self._settings.capture_max_chars
            and "```" not in stripped
            and not stripped.endswith("?")
        )

    def _candidates(self, text: str) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for rule in self._rules:
            match = rule.pattern.search(text)
            if match and self._plausible(match.group(1)):
                found.append((rule.slot, rule.template.format(self._tidy(match.group(1)))))
        for match in self._relation.finditer(text):
            relation, name = match.group(1).lower(), self._tidy(match.group(2))
            found.append((f"relation:{relation}", f"The user's {relation}'s name is {name}."))
        for match in self._preference.finditer(text):
            verb, thing = match.group(1).lower(), self._tidy(match.group(2))
            if thing.split()[0].lower() not in self.VAGUE:
                found.append(("", f"The user {self.PREFERENCE_VERBS[verb]} {thing}."))
        standing = self._standing.search(text)
        if standing and standing.group(2).strip().lower() not in {"mind", "been"}:
            first = standing.group(2).split()[0].lower()
            if first not in {"mind", "been", "gets", "get", "was", "were"}:
                found.append(
                    (
                        "",
                        f"The user wants you to {standing.group(1).lower()} "
                        f"{self._tidy(standing.group(2))}.",
                    )
                )
        return found

    def _plausible(self, value: str) -> bool:
        words = value.lower().split()
        return bool(words) and words[0] not in self.NOT_NAMES and len(value) >= 2

    @staticmethod
    def _tidy(value: str) -> str:
        return " ".join(value.split()).strip(" .,;!?")
