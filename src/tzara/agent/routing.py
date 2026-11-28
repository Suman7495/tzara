"""Deciding in code that a question needs the web, instead of hoping the model asks for it."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from typing import Any, ClassVar

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

from tzara.agent.screen import ScreenBlock
from tzara.config import ResearchSettings

logger = logging.getLogger(__name__)


class QuestionRules:
    """Says from the words alone whether a message asks for facts from the web.

    A small model told to research factual and buying questions does so only some of the time:
    asked for a good keyboard it answered from memory and named sources it had not read. Asking
    the model whether to research costs a second request, and that makes Ollama read the whole
    conversation again, which is several seconds. Rules cost nothing and give the same answer
    every time. They err towards research for questions; chat, arithmetic, questions about the
    user and commands for the Mac are not researched.
    """

    def __init__(self, settings: ResearchSettings) -> None:
        """Create the rules.

        Args:
            settings: The words and patterns that decide.
        """
        self._starts = settings.question_starts
        self._words = settings.question_words
        self._greeting = re.compile(settings.greeting, re.IGNORECASE)
        self._contractions = settings.contractions
        self._chat = [re.compile(pattern, re.IGNORECASE) for pattern in settings.chat_patterns]

    def topical(self, message: str) -> bool:
        """Whether the message has a word plainly about the outside world (weather, news...)."""
        bare = message.lower().replace("\u2019", "'").replace("'", "")
        return any(word in f" {bare} " for word in self._words)

    def needs_sources(self, message: str) -> bool:
        """Whether ``message`` should be answered from web pages.

        A greeting in front ("hey, whats the weather") is not part of the question, and
        "what's" is "what".
        """
        text = self._greeting.sub("", message.strip().lower().replace("\u2019", "'"))
        if not text or any(pattern.search(text) for pattern in self._chat):
            return False
        bare = text.replace("'", "")
        words = bare.split()
        first = self._contractions.get(words[0].strip(",.!?:"), words[0].strip(",.!?:"))
        return (
            "?" in text or first in self._starts or any(word in f" {bare} " for word in self._words)
        )


class ResearchRouter(AgentMiddleware):
    """Makes the research call itself when a message asks for facts from the web.

    Only the first model call of a turn is routed, when the last message is the user's. The call
    that follows the routed result is the model's to make, with the same tools as every other call:
    taking them away changes the start of the prompt, and Ollama then reads all of it again, which
    costs seconds, here and on the next message. If the model asks for another search anyway, that
    one call is made again without tools, so it writes the answer from the evidence it has.
    """

    ROUTED: ClassVar[str] = "route-"

    def __init__(
        self,
        rules: QuestionRules,
        tool: str = "research",
        report: Callable[[str], None] | None = None,
        writing: str = "",
        route: Callable[[], Any] | None = None,
        apps_connected: Callable[[], bool] | None = None,
        clearly_web: Callable[[str], bool] | None = None,
    ) -> None:
        """Create the router.

        Args:
            rules: Decides whether a message needs the web.
            tool: The tool that answers such messages.
            report: Told what is happening, so the wait for the answer is not a bare spinner.
            writing: Said while the model writes the answer from the evidence.
            route: Says what to do with this turn's message, if it was decided already:
                ``"skip"`` to answer without the web, or ``("search", question)`` to search for
                ``question``. ``None`` leaves it to the rules.
            apps_connected: Whether apps are connected. Then a message that merely starts like a
                question ("what did we decide") may be about the user's own content, so research is
                forced only for what clearly asks for the web, and the model decides the rest.
            clearly_web: Whether a message clearly asks for the web (weather, news, prices...).
        """
        super().__init__()
        self._apps_connected = apps_connected or (lambda: False)
        self._clearly_web = clearly_web or (lambda _text: True)
        self._route = route or (lambda: None)
        self._rules = rules
        self._tool = tool
        self._report = report or (lambda _text: None)
        self._writing = writing

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> Any:
        """Call the research tool for the user's message if it needs the web, else carry on."""
        last = request.messages[-1] if request.messages else None
        # A message that comes with new text from the user's screen is about that text; the model
        # may still call research itself if it needs to. The app and window alone change nothing:
        # the rules look at what the user wrote.
        written = ScreenBlock.remove(last.text) if last is not None else ""
        decided = self._route() if last is not None and last.type == "human" else None
        question = None
        if isinstance(decided, tuple):
            question = decided[1]
        elif (
            last is not None
            and last.type == "human"
            and decided != "skip"
            and not ScreenBlock.brings_text(last.text)
            and self._rules.needs_sources(written)
            and (not self._apps_connected() or self._clearly_web(written))
        ):
            question = written
        if question is not None and question.strip():
            call = {
                "name": self._tool,
                "args": {"question": question.strip()},
                "id": f"{self.ROUTED}{uuid.uuid4().hex}",
            }
            return AIMessage(content="", tool_calls=[call])
        if last is not None and last.type == "tool" and self.ROUTED in str(last.tool_call_id):
            self._report(self._writing)
            response = handler(request)
            if any(
                message.tool_calls for message in response.result if isinstance(message, AIMessage)
            ):
                return handler(request.override(tools=[]))
            return response
        return handler(request)


class AppRouter(AgentMiddleware):
    """Searches an app itself first when the message names that app and the app can search.

    A small model asked to "add a paragraph to my Notion page called MCP Test" calls the update
    tool with no page id, then asks the user for the id. As with research, the first step is
    made here, in code: the search runs for the name in the message, and the model starts from
    its result, which holds the id. Only the first model call of a turn is routed.
    """

    ROUTED: ClassVar[str] = "appsearch-"  # must not contain ResearchRouter.ROUTED
    SEARCH: ClassVar[re.Pattern[str]] = re.compile(r"(?:^|[-_])search$")
    MIN_WORD: ClassVar[int] = 4

    FETCH: ClassVar[re.Pattern[str]] = re.compile(r"(?:^|[-_])fetch$")

    def __init__(
        self,
        searches: dict[str, tuple[str, str]],
        fetches: dict[str, tuple[str, str]] | None = None,
        open_page: Callable[[], tuple[str, str] | None] | None = None,
        settings: ResearchSettings | None = None,
    ) -> None:
        """Create the router.

        Args:
            searches: For each app that can search, its tool's full name and the name of that
                tool's query argument.
            fetches: For each app that can read a page, its tool's full name and the name of
                the argument that takes the page's address.
            open_page: The app and address of the page the user has open in that app, if this
                turn's message is about it. That page is read first, here, in code: the page
                text on the screen is a browser notice, and the model otherwise answered about
                the page from its own earlier words.
            settings: The words that tell a request for the user's own content, and an app's
                generic name words. Defaults to the standard settings.
        """
        super().__init__()
        settings = settings or ResearchSettings()
        self._own = re.compile(settings.own_content, re.IGNORECASE)
        self._title = re.compile(settings.title_after, re.IGNORECASE)
        self._generic = frozenset(settings.app_generic_words)
        self._fetches = fetches or {}
        self._open_page = open_page or (lambda: None)
        self._searches = searches
        self._words: dict[str, str] = {}
        for app in searches:
            for word in [app, *app.split("-")]:
                if len(word) >= self.MIN_WORD and word.lower() not in self._generic:
                    self._words.setdefault(word.lower(), app)

    CONTEXT_LINE: ClassVar[re.Pattern[str]] = re.compile(r"^(?:App|Window|Address): (.+)$", re.M)

    def _on_screen(self, word: str, context: str) -> bool:
        """Whether the screen context shows the app's own page: then the page is known already."""
        return any(word in line.lower() for line in self.CONTEXT_LINE.findall(context))

    def route(self, text: str, context: str = "") -> dict[str, Any] | None:
        """The search call to make for ``text``, or ``None`` if none is needed.

        None if the message names no app that can search, or if what the user is looking at is
        that app's own page: the screen context has its address, so there is nothing to look up.

        Args:
            text: What the user wrote.
            context: The screen context block that came with it, if any.
        """
        lowered = text.lower()
        chosen = None
        for word, app in self._words.items():
            if self._on_screen(word, context):
                continue
            if re.search(rf"\b{re.escape(word)}\b", lowered):
                chosen = app
                break
        own = self._searches and not self._on_screen_any(context) and self._own.search(text)
        if chosen is None and own:
            # "my meeting notes", "the data room": the user's own content lives in a connected app
            # even when the message does not name it. A small model otherwise reaches for the web
            # or its memory.
            chosen = next(iter(self._searches))
        if chosen is None:
            return None
        tool, argument = self._searches[chosen]
        found = self._title.search(text)
        query = (found.group("quoted") or found.group("plain")).strip() if found else text.strip()
        return {
            "name": tool,
            "args": {argument: query},
            "id": f"{self.ROUTED}{uuid.uuid4().hex}",
        }

    ADDRESS: ClassVar[re.Pattern[str]] = re.compile(r"https?://[^\s)\]>,\"']+")

    def _read_the_top_result(self, message: Any) -> AIMessage | None:
        """After the search made here, read the page it found first.

        The model then starts from the page and not from a list of titles (it answered "your data
        room has these two pages").
        """
        app = next((a for a, (tool, _) in self._searches.items() if tool == message.name), None)
        if app is None or app not in self._fetches:
            return None
        found = self.ADDRESS.search(str(message.text))
        if found is None:
            return None
        tool, argument = self._fetches[app]
        call = {
            "name": tool,
            "args": {argument: found.group(0).rstrip(".")},
            "id": f"{self.ROUTED}{uuid.uuid4().hex}",
        }
        return AIMessage(content="", tool_calls=[call])

    def _on_screen_any(self, context: str) -> bool:
        """Whether the screen context shows a page of one of the connected apps."""
        return any(self._on_screen(app.lower(), context) for app in self._searches)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> Any:
        """Make the search for the user's message if it names an app, else carry on."""
        last = request.messages[-1] if request.messages else None
        if last is not None and last.type == "tool" and self.ROUTED in str(last.tool_call_id):
            read = self._read_the_top_result(last)
            if read is not None:
                logger.info(
                    "the top result of the search is read with %s", read.tool_calls[0]["name"]
                )
                return read
        if last is not None and last.type == "human":
            page = self._open_page()
            if page is not None and page[0] in self._fetches:
                tool, argument = self._fetches[page[0]]
                logger.info("the open page of %s is read first with %s", page[0], tool)
                opened = {
                    "name": tool,
                    "args": {argument: page[1]},
                    "id": f"{self.ROUTED}{uuid.uuid4().hex}",
                }
                return AIMessage(content="", tool_calls=[opened])
            call = self.route(ScreenBlock.remove(last.text), last.text)
            if call is not None:
                logger.info("the message names an app: searching it first with %s", call["name"])
                return AIMessage(content="", tool_calls=[call])
        return handler(request)


class EmptyReplyRetry(AgentMiddleware):
    """Asks the model once more when it returns nothing: no words and no tool call.

    qwen3:8b sometimes does, after reading a long tool result, and the user was left with no
    reply at all. Asked again, it nearly always answers. Nothing was shown by the empty call.
    """

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> Any:
        """Call the model; call it once more if the answer was empty."""
        response = handler(request)
        if self._empty(response):
            logger.info("the model returned nothing; asking it once more")
            response = handler(request)
        if self._empty(response):
            # It keeps trying to call a tool in a form that cannot be read (a translation of a
            # selection, for one). Without the tools it can only answer.
            logger.info("the model returned nothing again; asking it once more without tools")
            response = handler(request.override(tools=[]))
        return response

    @staticmethod
    def _empty(response: Any) -> bool:
        messages = [m for m in getattr(response, "result", []) if isinstance(m, AIMessage)]
        return bool(messages) and all(not m.text.strip() and not m.tool_calls for m in messages)


class PromiseRetry(AgentMiddleware):
    """Asks the model once more when it ends its turn by promising a step it did not take.

    qwen3:8b answered "I see the issue. Let me fetch the current page text. One moment please."
    and stopped: no tool ran, and the user waited for nothing. Decided from the words of the
    reply and the lack of a tool call (no side model request); the model is asked again with
    a reminder to call the tool. Once only, and only if it promised.
    """

    def __init__(self, pattern: str, reminder: str) -> None:
        """Create the check.

        Args:
            pattern: A regular expression for a reply that only promises a step.
            reminder: What the model is told when it did.
        """
        super().__init__()
        self._pattern = re.compile(pattern, re.IGNORECASE | re.DOTALL)
        self._reminder = reminder

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> Any:
        """Call the model; if it only promised a step, call it again with a reminder."""
        response = handler(request)
        if self._promised(response):
            logger.info("the model promised a step without taking it; asking it to do it")
            promise = [m for m in response.result if isinstance(m, AIMessage)]
            again = request.override(
                messages=[*request.messages, *promise, HumanMessage(self._reminder)]
            )
            retried = handler(again)
            if any(
                getattr(m, "tool_calls", None) for m in retried.result if isinstance(m, AIMessage)
            ):
                return retried
        return response

    def _promised(self, response: Any) -> bool:
        messages = [m for m in getattr(response, "result", []) if isinstance(m, AIMessage)]
        return (
            bool(messages)
            and not any(m.tool_calls for m in messages)
            and any(self._pattern.search(m.text) for m in messages)
        )
