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

from tzara.agent.textedit import TextEdit
from tzara.config import TzaraConfig
from tzara.memory import PersonalMemory, Task
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
        changed: Whether an edit changed anything. ``False``: the instruction could not be applied
            and the text must be left as it is (said to the user, not pasted).
    """

    text: str
    mode: str
    place: str
    replaces: str
    changed: bool = True


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
        gather: Callable[[str, str], str] | None = None,
        record: Callable[[str, str, str], None] | None = None,
    ) -> None:
        """Create the writer.

        Args:
            model: Gives the chat model to ask (it can change when the user switches models).
            config: Settings and prompts.
            personal: What is known about the user and how they write; ``None``: nothing.
            gather: Finds what a task needs besides the screen (the weather, the web, an app's page)
                from the task's words and the app it is for. ``None``: nothing is gathered.
            record: Told what was asked and written, to keep it in the conversation, so the chat
                knows what the last task was.
        """
        self._model = model
        self._config = config
        self._personal = personal
        self._gather = gather
        self._record = record

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

    EDIT_VERBS: ClassVar[frozenset[str]] = frozenset(
        {
            "delete", "remove", "add", "insert", "make", "change", "replace", "shorten", "lengthen",
            "rewrite", "rephrase", "translate", "fix", "correct", "capitalize", "capitalise",
            "move", "swap", "expand", "simplify", "soften", "tighten", "rename", "drop", "cut",
        }
    )  # fmt: skip
    STEP_SPLIT: ClassVar[re.Pattern[str]] = re.compile(
        r"\s*(?:;|,\s*and then\b|\band then\b|,\s*then\b|\bthen\b|,\s*and\b|,|\band\b)\s*",
        re.IGNORECASE,
    )
    REFERS: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(?:again|same|redo|re-do|previous|before|last (?:one|email|message|reply|time)|"
        r"that (?:email|message|reply|one)|like (?:that|the last)|instead)\b",
        re.IGNORECASE,
    )

    @classmethod
    def steps(cls, instruction: str) -> list[str]:
        """An instruction of several edits ("remove the greeting and make it shorter") as its steps.

        A small model does one clear change well and several at once badly, so each is done by
        itself. An instruction is split only if every part starts with an editing verb.
        """
        parts = [p.strip(" ,.;") for p in cls.STEP_SPLIT.split(instruction) if p.strip(" ,.;")]
        if len(parts) > 1 and all(part.split()[0].lower() in cls.EDIT_VERBS for part in parts):
            return parts
        return [instruction]

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
        asked = guidance or (field if mode == "instruct" else "")
        visible = Sanitizer.clean(request.visible).strip()[-settings.max_visible_chars :]
        where = ", ".join(part for part in (request.app, request.window) if part) or "an app"
        context = prompts.write_context.format(where=where, visible=visible or "(nothing read)")
        system = self._system(place, asked)

        if mode == "edit":
            result = self._edit(place, context, system, guidance, text, bool(selection))
        else:
            materials = self._materials(asked, request) if mode in {"guided", "instruct"} else ""
            if materials:
                context += prompts.write_materials.format(materials=materials)
            task = prompts.write_tasks[mode].format(text=text, instruction=guidance)
            written = self.clean(
                self._model().invoke([SystemMessage(system), HumanMessage(context + task)]).text
            )[: settings.max_output_chars]
            if not written:
                raise ValueError("the model wrote nothing")
            if place == "email":
                written = self._without_invented_name(written)
            replaced = "selection" if selection and mode == "rewrite" else ""
            replaces = replaced or ("field" if field and mode != "guided" else "nothing")
            if mode == "guided" and field:
                replaces = "field"
            result = WriteResult(written, mode, place, replaces)

        if result.changed:
            self._remember(request, asked or "reply to the conversation", result, mode, field)
        logger.info(
            "write: app=%s place=%s mode=%s field %d chars, selection %d, visible %d -> %d chars%s",
            request.app,
            place,
            mode,
            len(field),
            len(selection),
            len(visible),
            len(result.text),
            "" if result.changed else " (nothing changed)",
        )
        return result

    def _edit(
        self, place: str, context: str, system: str, guidance: str, text: str, selected: bool
    ) -> WriteResult:
        """Apply an instruction of one or several edits to ``text``, each by itself.

        Exact ones are done in code, the rest by the model, which is asked again once if it hands
        the text back unchanged. Nothing changed at all: ``changed`` is ``False``.
        """
        current = text
        steps = self.steps(guidance)
        for step in steps:
            if selected and len(steps) == 1 and TextEdit.deletes_the_selection(step):
                current = ""
                continue
            exact = TextEdit.apply(step, current)
            if exact is not None:
                current = exact
                continue
            revised = self._revise(place, context, system, step, current)
            if revised is None:
                logger.info("write: could not apply the step %r", step[:60])
            else:
                current = revised
        if current != "" and self._same(current, text):
            return WriteResult(text, "edit", place, "nothing", changed=False)
        return WriteResult(current, "edit", place, "selection" if selected else "field")

    def _revise(self, place: str, context: str, system: str, step: str, text: str) -> str | None:
        """The model's version of ``text`` with one change made, or ``None`` if it made none."""
        prompts = self._config.prompts
        limit = self._config.write.max_output_chars
        for kind in ("edit", "edit_retry"):
            task = prompts.write_tasks[kind].format(text=self._numbered(text), instruction=step)
            written = self.clean(
                self._model().invoke([SystemMessage(system), HumanMessage(context + task)]).text
            )[:limit]
            if written and not self._same(written, text):
                return written
            logger.info("write: the edit came back unchanged (%s)", kind)
        return None

    SIGN_OFF: ClassVar[re.Pattern[str]] = re.compile(
        r"(?P<head>(?:^|\n)(?:best|best regards|kind regards|warm regards|regards|sincerely|cheers|"
        r"thanks|thank you|yours|many thanks)[^\n]{0,15}[,.]?)[ \t]*\n+(?P<name>[A-Z][\w'-]*"
        r"(?: [A-Z][\w'-]*)?)\s*$",
        re.IGNORECASE,
    )

    def _without_invented_name(self, text: str) -> str:
        """The email without a name after the sign-off, unless that is the user's own name.

        A small model signs emails with a name it made up. The user's name is known only if they
        said so (a fact "The user's name is X"); otherwise the sign-off stands alone.
        """
        found = self.SIGN_OFF.search(text)
        if found is None:
            return text
        known = ""
        if self._personal is not None:
            fact = self._personal.facts.by_slot("name")
            known = re.sub(r"^The user's name is |\.$", "", fact.text) if fact else ""
        name = found.group("name").strip()
        if known and name.lower().split()[0] == known.lower().split()[0]:
            return text
        return text[: found.end("head")].rstrip()

    def _materials(self, asked: str, request: WriteRequest) -> str:
        """What the task needs that is not on the screen: the weather, the web, a page in an app.

        Gathered in code before the text is written, so "write an email to Anna with the weather in
        Berlin" has the weather in it.
        """
        if self._gather is None or not asked:
            return ""
        try:
            return self._gather(asked, request.app)
        except Exception:
            logger.warning("write: could not gather what the task needs", exc_info=True)
            return ""

    def _remember(
        self, request: WriteRequest, asked: str, result: WriteResult, mode: str, field: str
    ) -> None:
        """Keep the task as the last one, in the activity list, and in the conversation."""
        if self._personal is None:
            return
        if mode == "polish":
            self._personal.styles.add(result.place, field)  # their own words: how they write
        app = request.app or "an app"
        self._personal.tasks.remember(Task("write", asked, app, mode, result.text))
        excerpt = " ".join(result.text.split())[:140]
        self._personal.activity.record("write", f"Wrote in {app} for “{asked[:80]}”: {excerpt}")
        if self._record is not None:
            self._record(asked, app, result.text)

    @staticmethod
    def _same(first: str, second: str) -> bool:
        return " ".join(first.split()) == " ".join(second.split())

    @staticmethod
    def _numbered(text: str) -> str:
        """The text with its paragraphs numbered, so "the second paragraph" can be found."""
        blocks = [block for block in re.split(r"\n\s*\n", text.strip()) if block.strip()]
        if len(blocks) < 2:
            return text
        return "\n\n".join(f"[{number}] {block}" for number, block in enumerate(blocks, 1))

    def _system(self, place: str, asked: str = "") -> str:
        prompts = self._config.prompts
        facts = samples = last = ""
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
            task = self._personal.tasks.last("write")
            if task is not None:
                # Only what it needs: the request, and the text too if this one refers back to it.
                text = f"\nWhat you wrote: {task.result}" if self.REFERS.search(asked) else ""
                last = prompts.write_last_task.format(asked=task.asked, app=task.app, text=text)
        style = prompts.write_styles.get(place, prompts.write_styles["other"])
        return prompts.write_system.format(style=style, facts=facts, samples=samples) + last

    @classmethod
    def clean(cls, text: str) -> str:
        """The model's text without reasoning, wrapping quotes or surrounding blank lines."""
        text = cls.THINK.sub("", text).strip()
        text = re.sub(r"(?m)^\[\d{1,2}\]\s+", "", text)
        wrapped = cls.WRAP.match(text)
        if (wrapped and wrapped.group(1)[0] == wrapped.group(3)[-1]) or (
            wrapped and wrapped.group(1) in {"“", '"'} and wrapped.group(3) in {"”", '"'}
        ):
            text = wrapped.group(2).strip()
        return text
