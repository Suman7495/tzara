"""Writing text for the user into a field of another app (LinkedIn, Slack, Teams, Discord...)."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlparse

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from tzara.config import TzaraConfig
from tzara.memory import PersonalMemory
from tzara.tools.web import Sanitizer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WriteRequest:
    """What the Mac app read from the field and the screen when the user pressed the write key.

    Attributes:
        app: The app in front.
        bundle: Its identifier.
        window: The window title.
        address: The page address, if it is a browser.
        field: All the text in the focused field (empty if there is none).
        selection: The selected text in the field, if any.
        visible: The text visible in the window: the conversation or page being answered.
        instruction: Guidance the user typed in the guidance bar ("make it shorter", "say no
            politely"), or empty when they only tapped the key.
    """

    app: str = ""
    bundle: str = ""
    window: str = ""
    address: str = ""
    field: str = ""
    selection: str = ""
    visible: str = ""
    instruction: str = ""


@dataclass(frozen=True)
class WriteResult:
    """The text to put in the field, and how it was decided.

    Attributes:
        text: What to write.
        mode: ``reply``, ``instruct``, ``polish``, ``rewrite``, ``guided`` or ``edit``.
        place: ``chat``, ``social``, ``email``, ``code`` or ``other``.
        replaces: ``selection``, ``field`` or ``nothing``: what the text takes the place of.
    """

    text: str
    mode: str
    place: str
    replaces: str


class InlineWriter:
    """Decides, in code, what the user wants written, and asks the model once, with no tools.

    Which kind of place it is (a chat, LinkedIn, an email) and whether the field holds an
    instruction, a draft or nothing are decided from words and the field's state, not by asking
    the model: a side request costs seconds. The model is asked once for the text.

    What is on the screen is data and may hold instructions; it can only change what is written
    into a field. Nothing is sent: the user reads the text and sends it themselves.
    """

    THINK: ClassVar[re.Pattern[str]] = re.compile(r"<think>.*?</think>", re.DOTALL)
    WRAP: ClassVar[re.Pattern[str]] = re.compile(
        r'^\s*(["“\'`]{1,3})(.*?)(["”\'`]{1,3})\s*$', re.DOTALL
    )

    def __init__(
        self,
        model: Callable[[], BaseChatModel],
        config: TzaraConfig,
        personal: PersonalMemory | None,
    ) -> None:
        """Create the writer.

        Args:
            model: Gives the chat model to ask (it can change when the user switches models).
            config: Settings and prompts.
            personal: What is known about the user and how they write; ``None``: nothing.
        """
        self._model = model
        self._config = config
        self._personal = personal

    def place(self, request: WriteRequest) -> str:
        """The kind of place the field is in: chat, social, email, code or other.

        Found from the app's name and identifier, the page's host and the window title.
        """
        host = urlparse(request.address).hostname or ""
        haystack = " ".join([request.app, request.bundle, host, request.window]).lower()
        # The page decides over the browser: Slack in Chrome is a chat, not "a browser".
        for name, words in self._config.write.places.items():
            if any(word in host.lower() for word in words):
                return name
        for name, words in self._config.write.places.items():
            if any(word in haystack for word in words):
                return name
        return "other"

    def mode(self, request: WriteRequest) -> str:
        """What is wanted: rewrite a selection, follow an instruction, polish a draft, or reply.

        With guidance from the bar, the text there is changed as asked (``edit``), or, if there
        is none, written from the conversation as asked (``guided``).
        """
        if request.instruction.strip():
            has_text = request.selection.strip() or request.field.strip()
            return "edit" if has_text else "guided"
        if request.selection.strip():
            return "rewrite"
        text = request.field.strip().lower()
        if not text:
            return "reply"
        starts = self._config.write.instruction_starts
        return "instruct" if any(text.startswith(word) for word in starts) else "polish"

    def write(self, request: WriteRequest) -> WriteResult:
        """Write the text for ``request``.

        Raises:
            ValueError: If the model returned nothing usable.
        """
        place, mode = self.place(request), self.mode(request)
        settings = self._config.write
        prompts = self._config.prompts
        field = Sanitizer.clean(request.field).strip()[: settings.max_field_chars]
        selection = Sanitizer.clean(request.selection).strip()[: settings.max_field_chars]
        if mode == "instruct":
            field = re.sub(r"^/g\s*", "", field)
        text = selection if (mode == "rewrite" or (mode == "edit" and selection)) else field
        guidance = Sanitizer.clean(request.instruction).strip()[: settings.max_field_chars]
        visible = Sanitizer.clean(request.visible).strip()[-settings.max_visible_chars :]
        where = ", ".join(part for part in (request.app, request.window) if part) or "an app"
        context = prompts.write_context.format(where=where, visible=visible or "(nothing read)")
        task = prompts.write_tasks[mode].format(text=text, instruction=guidance)
        messages = [
            SystemMessage(self._system(place)),
            HumanMessage(context + task),
        ]
        result = self._model().invoke(messages)
        written = self.clean(result.text)[: settings.max_output_chars]
        if not written:
            raise ValueError("the model wrote nothing")
        if self._personal is not None:
            if mode == "polish":
                self._personal.styles.add(place, field)  # their own words: how they write
            self._personal.activity.record(
                "write", f"Wrote {len(written)} characters in {request.app or 'an app'} ({mode})"
            )
        logger.info(
            "write: app=%s place=%s mode=%s field %d chars, selection %d, visible %d -> %d chars",
            request.app,
            place,
            mode,
            len(field),
            len(selection),
            len(visible),
            len(written),
        )
        replaced = "selection" if selection and mode in {"rewrite", "edit"} else ""
        replaces = replaced or ("field" if field and mode != "guided" else "nothing")
        if mode == "guided" and field:
            replaces = "field"
        return WriteResult(written, mode, place, replaces)

    def _system(self, place: str) -> str:
        prompts = self._config.prompts
        facts = samples = ""
        if self._personal is not None:
            lines = [
                f"- {fact.text}"
                for fact in sorted(
                    self._personal.facts.recent(self._personal.settings.inject_facts),
                    key=lambda fact: fact.id,
                )
            ]
            facts = prompts.write_facts.format(lines="\n".join(lines)) if lines else ""
            examples = self._personal.styles.recent(place, self._config.write.sample_count)
            if examples:
                samples = prompts.write_samples.format(
                    lines="\n".join(f"- {' '.join(example.split())}" for example in examples)
                )
        style = prompts.write_styles.get(place, prompts.write_styles["other"])
        return prompts.write_system.format(style=style, facts=facts, samples=samples)

    @classmethod
    def clean(cls, text: str) -> str:
        """The model's text without reasoning, wrapping quotes or surrounding blank lines."""
        text = cls.THINK.sub("", text).strip()
        wrapped = cls.WRAP.match(text)
        if (wrapped and wrapped.group(1)[0] == wrapped.group(3)[-1]) or (
            wrapped and wrapped.group(1) in {"“", '"'} and wrapped.group(3) in {"”", '"'}
        ):
            text = wrapped.group(2).strip()
        return text
