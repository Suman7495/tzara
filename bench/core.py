"""The pieces of a benchmark task: what it gives the agent, and the checks on what comes back."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass
class Outcome:
    """What one step produced.

    Attributes:
        text: The reply (chat) or the text written (write).
        tools: Names of the tools that ran, in order.
        seconds: How long it took.
        changed: For a write, whether the edit changed anything.
        mode: For a write, how it was done.
        pages: The fake Notion's pages after the step, by title.
        error: What went wrong, if the step failed.
    """

    text: str = ""
    tools: list[str] = field(default_factory=list)
    seconds: float = 0.0
    changed: bool = True
    mode: str = ""
    pages: dict[str, str] = field(default_factory=dict)
    error: str = ""


class Check:
    """One thing that must be true of an outcome. Subclasses say how it is tested."""

    def test(self, outcome: Outcome) -> str | None:
        """``None`` if it holds, else why not."""
        raise NotImplementedError


class Has(Check):
    """Every given text appears in the answer (any case)."""

    def __init__(self, *texts: str) -> None:
        self.texts = texts

    def test(self, outcome: Outcome) -> str | None:
        lowered = outcome.text.lower()
        missing = [t for t in self.texts if t.lower() not in lowered]
        return f"missing {missing}" if missing else None


class HasAny(Check):
    """At least one of the given texts appears (any case)."""

    def __init__(self, *texts: str) -> None:
        self.texts = texts

    def test(self, outcome: Outcome) -> str | None:
        lowered = outcome.text.lower()
        return None if any(t.lower() in lowered for t in self.texts) else f"none of {self.texts}"


class Lacks(Check):
    """None of the given texts appears (any case)."""

    def __init__(self, *texts: str) -> None:
        self.texts = texts

    def test(self, outcome: Outcome) -> str | None:
        lowered = outcome.text.lower()
        found = [t for t in self.texts if t.lower() in lowered]
        return f"should not contain {found}" if found else None


class Matches(Check):
    """A regular expression matches somewhere in the answer."""

    def __init__(self, pattern: str, why: str = "") -> None:
        self.pattern = re.compile(pattern, re.IGNORECASE | re.DOTALL)
        self.why = why or pattern

    def test(self, outcome: Outcome) -> str | None:
        return None if self.pattern.search(outcome.text) else f"no match for {self.why}"


class NotMatches(Check):
    """A regular expression matches nowhere in the answer."""

    def __init__(self, pattern: str, why: str = "") -> None:
        self.pattern = re.compile(pattern, re.IGNORECASE | re.DOTALL)
        self.why = why or pattern

    def test(self, outcome: Outcome) -> str | None:
        found = self.pattern.search(outcome.text)
        return f"should not match {self.why}: {found.group(0)[:40]!r}" if found else None


class AnyOf(Check):
    """At least one of several checks holds (more than one acceptable way of doing it right)."""

    def __init__(self, *checks: Check) -> None:
        self.checks = checks

    def test(self, outcome: Outcome) -> str | None:
        reasons = [reason for check in self.checks if (reason := check.test(outcome))]
        return None if len(reasons) < len(self.checks) else " / ".join(reasons)


class Words(Check):
    """The answer has between ``low`` and ``high`` words."""

    def __init__(self, low: int = 0, high: int = 10_000) -> None:
        self.low, self.high = low, high

    def test(self, outcome: Outcome) -> str | None:
        count = len(outcome.text.split())
        if count < self.low:
            return f"{count} words, wanted at least {self.low}"
        return f"{count} words, wanted at most {self.high}" if count > self.high else None


class Chars(Check):
    """The answer has at most ``high`` characters."""

    def __init__(self, high: int) -> None:
        self.high = high

    def test(self, outcome: Outcome) -> str | None:
        return (
            f"{len(outcome.text)} characters, wanted at most {self.high}"
            if len(outcome.text) > self.high
            else None
        )


class ShorterThan(Check):
    """The answer is at most ``ratio`` times as long (in words) as a given text."""

    def __init__(self, original: str, ratio: float) -> None:
        self.original, self.ratio = original, ratio

    def test(self, outcome: Outcome) -> str | None:
        limit = len(self.original.split()) * self.ratio
        count = len(outcome.text.split())
        return f"{count} words, wanted at most {limit:.0f}" if count > limit else None


class Language(Check):
    """The answer is in a language, judged by how many of its common words appear."""

    WORDS: ClassVar[dict[str, frozenset[str]]] = {
        "en": frozenset(
            [
                "the",
                "and",
                "of",
                "to",
                "in",
                "is",
                "that",
                "for",
                "it",
                "with",
                "as",
                "was",
                "on",
                "are",
                "this",
                "be",
                "at",
                "have",
                "you",
                "not",
            ]
        ),
        "de": frozenset(
            [
                "der",
                "die",
                "das",
                "und",
                "ist",
                "nicht",
                "ich",
                "sie",
                "wir",
                "mit",
                "für",
                "auf",
                "von",
                "zu",
                "den",
                "dem",
                "ein",
                "eine",
                "bitte",
                "wir",
                "können",
            ]
        ),
        "es": frozenset(
            [
                "el",
                "la",
                "los",
                "las",
                "y",
                "es",
                "que",
                "de",
                "en",
                "un",
                "una",
                "por",
                "con",
                "para",
                "no",
                "se",
                "su",
                "al",
                "lo",
                "como",
                "más",
                "puedo",
            ]
        ),
        "fr": frozenset(
            [
                "le",
                "la",
                "les",
                "et",
                "est",
                "que",
                "de",
                "des",
                "un",
                "une",
                "pour",
                "dans",
                "pas",
                "ne",
                "vous",
                "nous",
                "je",
                "il",
                "elle",
                "avec",
                "sur",
                "ce",
            ]
        ),
        "it": frozenset(
            [
                "il",
                "lo",
                "la",
                "gli",
                "le",
                "e",
                "è",
                "che",
                "di",
                "un",
                "una",
                "per",
                "con",
                "non",
                "si",
                "da",
                "in",
                "del",
                "della",
                "sono",
                "ho",
            ]
        ),
    }

    def __init__(self, code: str) -> None:
        self.code = code

    @classmethod
    def detect(cls, text: str) -> str:
        words = re.findall(r"[a-zäöüßéèêàáíóúñç']+", text.lower())
        scores = {
            code: sum(w in vocabulary for w in words) for code, vocabulary in cls.WORDS.items()
        }
        return max(scores, key=lambda code: scores[code])

    def test(self, outcome: Outcome) -> str | None:
        found = self.detect(outcome.text)
        return None if found == self.code else f"looks {found}, wanted {self.code}"


class Used(Check):
    """A tool ran (by name; part of a name is enough)."""

    def __init__(self, *names: str) -> None:
        self.names = names

    def test(self, outcome: Outcome) -> str | None:
        missing = [n for n in self.names if not any(n in tool for tool in outcome.tools)]
        return f"tool not used: {missing} (ran {outcome.tools})" if missing else None


class NotUsed(Check):
    """No tool with this in its name ran."""

    def __init__(self, *names: str) -> None:
        self.names = names

    def test(self, outcome: Outcome) -> str | None:
        wrong = [t for t in outcome.tools if any(n in t for n in self.names)]
        return f"tool should not run: {wrong}" if wrong else None


class Changed(Check):
    """A write changed (or, with ``False``, honestly did not change) the text."""

    def __init__(self, expected: bool = True) -> None:
        self.expected = expected

    def test(self, outcome: Outcome) -> str | None:
        return None if outcome.changed == self.expected else f"changed={outcome.changed}"


class PageHas(Check):
    """A fake Notion page (found by part of its title) contains a text."""

    def __init__(self, title: str, *texts: str) -> None:
        self.title, self.texts = title, texts

    def test(self, outcome: Outcome) -> str | None:
        content = next(
            (c for t, c in outcome.pages.items() if self.title.lower() in t.lower()), None
        )
        if content is None:
            return f"no page called like {self.title!r}"
        missing = [t for t in self.texts if t.lower() not in content.lower()]
        return f"page lacks {missing}" if missing else None


class PageLacks(Check):
    """A fake Notion page does not contain a text."""

    def __init__(self, title: str, *texts: str) -> None:
        self.title, self.texts = title, texts

    def test(self, outcome: Outcome) -> str | None:
        content = next(
            (c for t, c in outcome.pages.items() if self.title.lower() in t.lower()), None
        )
        if content is None:
            return f"no page called like {self.title!r}"
        found = [t for t in self.texts if t.lower() in content.lower()]
        return f"page still has {found}" if found else None


class PageExists(Check):
    """A page exists (``True``) or does not (``False``)."""

    def __init__(self, title: str, exists: bool = True) -> None:
        self.title, self.exists = title, exists

    def test(self, outcome: Outcome) -> str | None:
        there = any(self.title.lower() in t.lower() for t in outcome.pages)
        return None if there == self.exists else f"page {self.title!r} exists={there}"


@dataclass(frozen=True)
class Step:
    """One message to the agent: a question in the chat, or a request to write in a field.

    Attributes:
        kind: ``chat`` or ``write``.
        prompt: What the user says (the instruction, for a write).
        screen: What the Mac app read: ``app``, ``window``, ``address``, ``visible``,
            ``selection``...
        field: The text already in the field, for a write.
        place: Where the user is, if known.
        checks: What must hold after this step (task-level checks apply to the last step).
    """

    kind: str = "chat"
    prompt: str = ""
    screen: dict[str, str] = field(default_factory=dict)
    field: str = ""
    place: str = ""
    checks: tuple[Check, ...] = ()


@dataclass(frozen=True)
class Task:
    """One benchmark task.

    Attributes:
        id: Short name.
        area: Where it happens: ``email``, ``notion``, ``teams``, ``linkedin``, ``context``,
            ``memory``,
            ``web``, ``recovery``.
        category: What it measures: ``context``, ``tools``, ``multistep``, ``research``, ``memory``,
            ``writing``, ``recovery``.
        steps: The messages, in order.
        checks: What must hold after the last step.
        facts: What Tzara is told to remember first.
        pages: The fake Notion's pages at the start (title to content).
        trashed: Titles of pages that are in the trash.
        network: Needs the internet (the weather, the web).
    """

    id: str
    area: str
    category: str
    steps: tuple[Step, ...]
    checks: tuple[Check, ...] = ()
    facts: tuple[str, ...] = ()
    pages: dict[str, str] = field(default_factory=dict)
    trashed: tuple[str, ...] = ()
    network: bool = False

    @property
    def last(self) -> Step:
        return self.steps[-1]


@dataclass
class Result:
    """How one task went."""

    task: Task
    outcome: Outcome
    failures: list[str] = field(default_factory=list)
    checks_total: int = 0

    @property
    def passed(self) -> bool:
        return not self.failures and not self.outcome.error

    @property
    def score(self) -> float:
        if self.outcome.error or not self.checks_total:
            return 0.0
        return (self.checks_total - len(self.failures)) / self.checks_total

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.task.id,
            "area": self.task.area,
            "category": self.task.category,
            "passed": self.passed,
            "score": round(self.score, 3),
            "failures": self.failures,
            "error": self.outcome.error,
            "seconds": round(self.outcome.seconds, 1),
            "tools": self.outcome.tools,
            "answer": self.outcome.text[:1200],
        }
