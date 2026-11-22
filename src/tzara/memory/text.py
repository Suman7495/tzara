"""Turning text into searches the database understands."""

from __future__ import annotations

import re
from typing import ClassVar

from tzara.stopwords import StopWords


class SearchText:
    """Builds an FTS5 query from free text.

    FTS5 has its own query syntax, so text from a person or a model cannot be passed in as it is:
    a stray quote or ``AND`` makes it fail, or changes what it means. Only the words are kept,
    each quoted, and any one of them may match.
    """

    WORD: ClassVar[re.Pattern[str]] = re.compile(r"\w{2,}")
    # Words that are common in what people say to an assistant, but say nothing about a topic.
    STOPWORDS: ClassVar[frozenset[str]] = StopWords.COMMON | frozenset(
        {
            "i",
            "it",
            "do",
            "so",
            "if",
            "no",
            "yes",
            "please",
            "remember",
            "know",
            "everything",
            "anything",
            "something",
            "thing",
            "things",
            "stuff",
            "all",
        }
    )
    MAX_TERMS: ClassVar[int] = 12

    @classmethod
    def terms(cls, text: str) -> list[str]:
        """The distinct words of ``text`` worth searching for, in order."""
        found: list[str] = []
        for word in cls.WORD.findall(text.lower()):
            if word not in cls.STOPWORDS and word not in found:
                found.append(word)
        return found[: cls.MAX_TERMS]

    @classmethod
    def match(cls, text: str) -> str | None:
        """An FTS5 query for ``text``, or ``None`` if it has no words to search for."""
        terms = cls.terms(text)
        return " OR ".join(f'"{term}"' for term in terms) if terms else None

    @staticmethod
    def key(text: str) -> str:
        """What makes two phrasings of a fact the same: lower case, words only."""
        return " ".join(re.findall(r"\w+", text.lower()))
