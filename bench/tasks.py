"""The benchmark tasks, by area: email, Teams, LinkedIn, Notion, context, memory, web, recovery."""

from __future__ import annotations

from bench.core import (
    AnyOf,
    Changed,
    Chars,
    Check,
    Has,
    HasAny,
    Lacks,
    Language,
    Matches,
    NotMatches,
    NotUsed,
    PageExists,
    PageHas,
    PageLacks,
    ShorterThan,
    Step,
    Task,
    Used,
    Words,
)
from bench.fixtures import Fixtures as F

CLAIM = r"\b(?:i(?:'ve| have)|done[,.!]?\s+i|i)\s+(?:just\s+|now\s+)?(?:sent|deleted|removed|booked|paid|taken care|handled|archived)\b"
DAY_2DEC = HasAny("2 December", "December 2", "Dec 2", "2 Dec")
WED = HasAny("Wednesday", "3 December", "December 3", "Dec 3")


def thread(
    prompt: str,
    *checks,
    category: str = "context",
    id: str = "",
    kind: str = "chat",
    field: str = "",
) -> Task:
    """A question about, or a write in, the invoice email thread."""
    return Task(
        id or "email-" + prompt[:24].lower().replace(" ", "-"),
        "email",
        category,
        (Step(kind, prompt, {**F.INVOICE_SCREEN, "visible": F.INVOICE_THREAD}, field),),
        tuple(checks),
    )


def chat_in(
    screen: dict[str, str],
    visible: str,
    prompt: str,
    *checks,
    id: str,
    area: str,
    category: str = "context",
) -> Task:
    return Task(
        id, area, category, (Step("chat", prompt, {**screen, "visible": visible}),), tuple(checks)
    )


class TaskSet:
    """Builds every task."""

    @classmethod
    def all(cls) -> list[Task]:
        return [
            *cls.email(),
            *cls.teams(),
            *cls.linkedin(),
            *cls.notion(),
            *cls.context(),
            *cls.memory(),
            *cls.web(),
            *cls.recovery(),
        ]

    # -- email -------------------------------------------------------------------------------

    @classmethod
    def email(cls) -> list[Task]:
        draft = F.DRAFT_REPLY
        return [
            thread(
                "What is Anna asking me for in her last message?",
                Has("2,000", "2,200"),
                HasAny("Tuesday", "Wednesday"),
                Has("Thursday"),
                id="email-ask",
            ),
            thread(
                "What did Tom say about the budget?",
                HasAny("1 December", "December 1", "Dec 1"),
                HasAny("approval", "finance"),
                id="email-tom-budget",
            ),
            thread(
                "Can we accept the partial payment Anna proposed?",
                Matches(r"\b(not|no|can't|cannot|isn't)\b"),
                Has("Tom"),
                id="email-partial",
                category="multistep",
            ),
            thread(
                "Summarize this thread in three bullet points.",
                Matches(r"(?m)^\s*[-*•]\s+\S"),
                Has("4,200"),
                Words(high=120),
                id="email-summary",
            ),
            thread(
                "When was the original payment due?",
                HasAny("28 November", "November 28", "Nov 28", "28 Nov"),
                id="email-due-date",
            ),
            thread(
                "What do I have to answer Anna, and by when?",
                Has("Thursday"),
                HasAny("slot", "call", "meeting", "Wednesday", "Tuesday"),
                HasAny("payment", "partial"),
                id="email-todo",
            ),
            thread("Who is on this thread?", Has("Anna", "Tom"), id="email-people"),
            thread(
                "Did Anna agree to the extension I asked for?",
                Matches(r"\b(no|not|didn't|did not|isn't|difficult)\b"),
                HasAny("2,000", "partial", "full extension"),
                id="email-extension",
                category="multistep",
            ),
            thread(
                "reply to Anna: we will pay in full on 2 December and Wednesday 3 December at 14:00 works for the call",
                DAY_2DEC,
                WED,
                Has("Anna"),
                Words(25, 170),
                Lacks("I hope this email finds you well", "Sarah"),
                NotMatches(r"(?m)^[A-Z][a-z]+\s*$(?![\s\S])", "a made-up name after the sign-off"),
                id="email-draft-reply",
                kind="write",
                category="writing",
            ),
            thread(
                "politely decline the partial payment and propose paying in full on 2 December",
                DAY_2DEC,
                HasAny("full", "whole"),
                Words(25, 150),
                Lacks("Sarah"),
                id="email-draft-decline",
                kind="write",
                category="writing",
            ),
            thread(
                "make it shorter",
                ShorterThan(draft, 0.8),
                WED,
                Changed(),
                id="email-edit-shorter",
                kind="write",
                field=draft,
                category="writing",
            ),
            thread(
                "make it more formal",
                Lacks("hey", "gonna", "lol", "!!"),
                WED,
                Changed(),
                id="email-edit-formal",
                kind="write",
                field=F.CASUAL_DRAFT,
                category="writing",
            ),
            thread(
                "remove the last paragraph",
                Changed(),
                Lacks("Kind regards"),
                Has("Anna"),
                Has("Please let me know if that is acceptable"),
                id="email-edit-delete",
                kind="write",
                field=draft,
                category="tools",
            ),
            thread(
                "translate this into German",
                Language("de"),
                Matches(r"2[.,]000|4[.,]200"),
                Changed(),
                id="email-translate-de",
                kind="write",
                field=draft,
                category="writing",
            ),
            thread(
                "translate this into Spanish",
                Language("es"),
                Changed(),
                id="email-translate-es",
                kind="write",
                field=draft,
                category="writing",
            ),
            thread(
                "remove the greeting and make it shorter",
                Changed(),
                Lacks("Hi Anna"),
                ShorterThan(draft, 0.9),
                id="email-edit-two-steps",
                kind="write",
                field=draft,
                category="multistep",
            ),
        ]

    # -- Teams -------------------------------------------------------------------------------

    @classmethod
    def teams(cls) -> list[Task]:
        def ask(prompt: str, *checks: Check, id: str, category: str = "context") -> Task:
            return chat_in(
                F.TEAMS_SCREEN,
                F.TEAMS_CHAT,
                prompt,
                *checks,
                id=id,
                area="teams",
                category=category,
            )

        def reply(prompt: str, *checks: Check, id: str) -> Task:
            return Task(
                id,
                "teams",
                "writing",
                (Step("write", prompt, {**F.TEAMS_SCREEN, "visible": F.TEAMS_CHAT}),),
                tuple(checks),
            )

        chat_style = NotMatches(
            r"(?i)\b(dear|kind regards|best regards|sincerely)\b",
            "an email-style greeting or sign-off",
        )
        return [
            ask(
                "What did Priya ask me to do?",
                Has("deck", "pricing"),
                HasAny("Wednesday", "noon"),
                id="teams-asks",
            ),
            ask(
                "Is anyone still blocked?",
                HasAny("unblocked", "no one", "nobody", "resolved", "not anymore", "no longer"),
                id="teams-blocked",
            ),
            ask(
                "Who is confirmed for the Berlin offsite?",
                Has("Priya", "Marco", "Lena", "Jonas"),
                id="teams-offsite",
            ),
            ask(
                "Summarize this chat in three bullets.",
                Matches(r"(?m)^\s*[-*•]\s+\S"),
                Words(high=110),
                id="teams-summary",
            ),
            ask(
                "What decisions are waiting for me?",
                HasAny("16:00", "4 pm", "review", "Thursday"),
                HasAny("offsite", "pricing", "sign-off", "TBD"),
                id="teams-pending",
                category="multistep",
            ),
            ask(
                "What is Jonas's phone number?",
                Matches(r"\b(not|no|isn't|doesn't|don't|cannot|can't)\b"),
                NotMatches(r"\d{5,}", "an invented number"),
                id="teams-missing",
                category="recovery",
            ),
            ask(
                "Which meeting time change was proposed, and who agreed?",
                Has("16:00"),
                Has("Priya"),
                id="teams-change",
            ),
            reply(
                "reply that I will send the deck by Wednesday noon",
                HasAny("Wednesday", "noon"),
                Words(3, 35),
                chat_style,
                id="teams-reply-deck",
            ),
            reply(
                "say 16:00 works for me", Has("16"), Words(2, 25), chat_style, id="teams-reply-time"
            ),
            reply(
                "tell Priya yes, I will join the offsite",
                HasAny("offsite", "join", "yes", "count me"),
                Words(3, 35),
                chat_style,
                id="teams-reply-offsite",
            ),
        ]

    # -- LinkedIn ----------------------------------------------------------------------------

    @classmethod
    def linkedin(cls) -> list[Task]:
        def ask(prompt: str, *checks: Check, id: str) -> Task:
            return chat_in(
                F.LINKEDIN_SCREEN, F.LINKEDIN_PROFILE, prompt, *checks, id=id, area="linkedin"
            )

        def write(
            prompt: str,
            *checks: Check,
            id: str,
            field: str = "",
            facts: tuple[str, ...] = (),
            screen: dict[str, str] | None = None,
            visible: str = "",
        ) -> Task:
            return Task(
                id,
                "linkedin",
                "writing",
                (
                    Step(
                        "write",
                        prompt,
                        {
                            **(screen or F.LINKEDIN_COMPOSE),
                            **({"visible": visible} if visible else {}),
                        },
                        field,
                    ),
                ),
                tuple(checks),
                facts,
            )

        hashtags = NotMatches(r"(#\w+\W+){6,}", "more than five hashtags")
        return [
            ask(
                "What are my strongest skills based on my profile?",
                Has("machine learning"),
                HasAny("robotics", "Python", "leadership"),
                id="linkedin-skills",
            ),
            ask("Where did I work before Telekinesis?", Has("Nimbus"), id="linkedin-history"),
            ask(
                "How many people did I lead at Nimbus Labs?",
                Matches(r"\b(9|nine)\b"),
                id="linkedin-team",
            ),
            ask(
                "Suggest a better headline for my profile.",
                HasAny("local-first", "on-device", "private", "agents"),
                Words(high=60),
                id="linkedin-headline",
            ),
            write(
                "draft a LinkedIn post announcing our seed round of 3 million euros led by Atlas Ventures",
                Has("Atlas"),
                HasAny("3 million", "3M", "€3", "3 Mio"),
                HasAny("Telekinesis", "Tzara"),
                Words(40, 230),
                hashtags,
                id="linkedin-post-seed",
                facts=("I am the founder and CTO of Telekinesis AI, which builds Tzara",),
            ),
            write(
                "write a connection request to Jo Lee, a partner at Atlas Ventures, about on-device AI",
                Has("Jo"),
                Chars(300),
                NotMatches(r"(?i)dear sir", "an impersonal greeting"),
                id="linkedin-connect",
            ),
            write(
                "make it more concise",
                ShorterThan(F.LINKEDIN_ABOUT, 0.8),
                HasAny("local-first", "private", "agents"),
                Changed(),
                id="linkedin-about",
                field=F.LINKEDIN_ABOUT,
                screen=F.LINKEDIN_SCREEN,
                visible=F.LINKEDIN_PROFILE,
            ),
            write(
                "write a post about three lessons from launching to our first 300 users",
                Has("300"),
                Words(50, 240),
                HasAny("lesson", "learn"),
                hashtags,
                id="linkedin-post-lessons",
                facts=("I am the founder and CTO of Telekinesis AI, which builds Tzara",),
            ),
        ]

    # -- Notion ------------------------------------------------------------------------------

    @classmethod
    def notion(cls) -> list[Task]:
        pages = F.NOTION_PAGES
        notes = "Acme sync"
        roadmap = F.notion_screen("pg_2", "Project Phoenix Roadmap", "project-phoenix-roadmap")

        def ask(
            prompt: str,
            *checks: Check,
            id: str,
            category: str = "tools",
            screen: dict[str, str] | None = None,
            pages_: dict[str, str] | None = None,
            trashed: tuple[str, ...] = (),
        ) -> Task:
            return Task(
                id,
                "notion",
                category,
                (Step("chat", prompt, screen or {}),),
                tuple(checks),
                pages=pages_ if pages_ is not None else pages,
                trashed=trashed,
            )

        return [
            ask(
                "Which documents are still missing from my data room?",
                Used("notion"),
                Has("cap table", "Q3"),
                Lacks("Pitch deck"),
                id="notion-missing",
            ),
            ask(
                "What is the status of the customer contracts in my data room?",
                Used("notion"),
                Has("in review"),
                id="notion-status",
            ),
            ask(
                "What are the action items from my Acme sync notes, and who owns them?",
                Used("notion"),
                Has("NDA", "Tom", "SLA"),
                id="notion-actions",
                category="context",
            ),
            ask(
                "What did we decide at the Acme sync?",
                Used("notion"),
                DAY_2DEC,
                WED,
                id="notion-decisions",
            ),
            ask(
                "Summarize my Project Phoenix Roadmap in one sentence.",
                Used("notion"),
                Words(high=45),
                HasAny("dashboard", "onboarding", "Windows"),
                id="notion-summary",
            ),
            ask(
                "Draft an agenda for the follow-up call on Wednesday based on my Acme notes.",
                Used("notion"),
                Has("SLA"),
                HasAny("premium support", "support tier"),
                HasAny("NDA", "contract draft", "2026 contract"),
                id="notion-agenda",
                category="multistep",
            ),
            ask(
                "Find my data room index and tell me how many documents are missing.",
                Used("notion"),
                Matches(r"\b(2|two)\b"),
                id="notion-count",
                category="multistep",
            ),
            ask(
                "Add a section called Action items to the end of my Acme sync notes, with the to-dos as a checklist.",
                Used("notion-update-page"),
                PageHas(notes, "Action items", "NDA", "- [ ]"),
                PageHas(notes, "Attendees"),
                id="notion-edit-checklist",
                category="multistep",
            ),
            ask(
                "Mark the cap table as uploaded in my data room index.",
                Used("notion-update-page"),
                PageLacks("Data Room", "Cap table (fully diluted): MISSING"),
                PageHas("Data Room", "Cap table", "Q3 management accounts: MISSING"),
                id="notion-edit-status",
                category="multistep",
            ),
            ask(
                "Create a page called Acme follow-up with the open questions from my Acme sync notes.",
                Used("notion"),
                PageExists("Acme follow-up"),
                PageHas("Acme follow-up", "premium support"),
                id="notion-create",
                category="multistep",
            ),
            ask(
                "add a short paragraph about customer onboarding feedback to this page",
                Used("notion-update-page"),
                NotUsed("notion-search"),
                PageHas("Phoenix", "feedback"),
                PageHas("Phoenix", "Windows beta"),
                id="notion-open-page",
                screen=roadmap,
                category="context",
            ),
            ask(
                "Reorganize my data room index into sections called Uploaded, In review and Missing.",
                Used("notion-update-page"),
                PageHas(
                    "Data Room",
                    "Uploaded",
                    "Missing",
                    "Certificate of incorporation",
                    "Term sheet draft",
                ),
                id="notion-organize",
                category="multistep",
            ),
            ask(
                "Add a line saying Revenue up 12% to my Q4 Board Update page.",
                Used("notion"),
                PageLacks("Q4 Board", "Revenue up 12%"),
                HasAny("trash", "restore", "can't", "cannot", "couldn't"),
                NotMatches(r"(?i)\b(i've|i have) (added|updated)\b"),
                id="notion-trashed",
                category="recovery",
                pages_={**pages, "Q4 Board Update": "Revenue and hiring summary."},
                trashed=("Q4 Board Update",),
            ),
            ask(
                "Delete my Project Phoenix Roadmap page.",
                PageExists("Phoenix"),
                NotMatches(r"(?i)\b(i've|i have) (deleted|removed)\b"),
                HasAny("can't", "cannot", "unable", "don't have", "not able", "isn't"),
                id="notion-no-delete",
                category="recovery",
            ),
        ]

    # -- context -----------------------------------------------------------------------------

    @classmethod
    def context(cls) -> list[Task]:
        contract = "The Provider shall indemnify the Customer against third-party IP claims, capped at 12 months' fees."
        sheet = "Q3 Revenue by Region\nEMEA 1.2M\nAPAC 0.8M\nAMER 2.1M\nTotal 4.1M"
        code = "def average(values):\n    total = 0\n    for v in values:\n        total += v\n    return total / len(values)"
        return [
            Task(
                "context-selection",
                "context",
                "context",
                (
                    Step(
                        "chat",
                        "explain this",
                        {"app": "Word", "window": "MSA.docx", "selection": contract},
                    ),
                ),
                (
                    HasAny("cap", "limit", "12 months"),
                    HasAny("indemn", "protect", "cover", "reimburse"),
                    NotUsed("research"),
                    Words(high=130),
                ),
            ),
            chat_in(
                {"app": "Microsoft Excel", "window": "Q3 revenue.xlsx"},
                sheet,
                "what am I looking at?",
                HasAny("4.1", "Q3", "revenue"),
                id="context-sheet",
                area="context",
            ),
            chat_in(
                {"app": "Visual Studio Code", "window": "stats.py"},
                code,
                "what does this function do?",
                HasAny("average", "mean"),
                id="context-code",
                area="context",
            ),
            Task(
                "context-ambiguous",
                "context",
                "context",
                (
                    Step(
                        "chat",
                        "what amount is due and by when",
                        {**F.INVOICE_SCREEN, "visible": F.INVOICE_THREAD},
                    ),
                ),
                (
                    Has("4,200"),
                    HasAny("28 November", "Friday", "28 Nov"),
                    NotMatches(r"(?i)search online"),
                ),
            ),
            Task(
                "context-take-care",
                "context",
                "recovery",
                (
                    Step(
                        "chat",
                        "take care of this",
                        {**F.INVOICE_SCREEN, "visible": F.INVOICE_THREAD},
                    ),
                ),
                (NotMatches(CLAIM, "a claim that it was done"),),
            ),
            Task(
                "context-translate",
                "context",
                "context",
                (
                    Step(
                        "chat",
                        "translate this into French",
                        {
                            "app": "Word",
                            "window": "Note",
                            "selection": "Please send the signed agreement before Friday.",
                        },
                    ),
                ),
                (Language("fr"),),
            ),
            chat_in(
                {"app": "Microsoft Outlook", "window": "Re: Invoice 4471 - extension request"},
                F.INVOICE_THREAD,
                "who is Tom?",
                HasAny("colleague", "finance", "budget", "your", "team", "Cc", "Tom Becker"),
                id="context-who",
                area="context",
            ),
            Task(
                "context-no-screen",
                "context",
                "context",
                (Step("chat", "what is on my screen?", {}),),
                (
                    HasAny(
                        "can't",
                        "cannot",
                        "couldn't",
                        "could not",
                        "don't",
                        "no screen",
                        "not able",
                        "nothing",
                        "unable",
                        "no context",
                    ),
                    NotMatches(r"(?i)you are (looking|viewing) at"),
                ),
            ),
        ]

    # -- memory ------------------------------------------------------------------------------

    @classmethod
    def memory(cls) -> list[Task]:
        return [
            Task(
                "memory-remember-recall",
                "memory",
                "memory",
                (
                    Step("chat", "remember that my dentist is Dr Lee"),
                    Step("chat", "who is my dentist?"),
                ),
                (Has("Lee"),),
            ),
            Task(
                "memory-recall-all",
                "memory",
                "memory",
                (
                    Step("chat", "remember that I prefer short answers"),
                    Step("chat", "what do you remember about me?"),
                ),
                (Has("short"),),
            ),
            Task(
                "memory-forget",
                "memory",
                "memory",
                (
                    Step("chat", "remember that my dentist is Dr Lee"),
                    Step("chat", "forget about the dentist"),
                    Step("chat", "who is my dentist?"),
                ),
                (Lacks("Dr Lee"),),
            ),
            Task(
                "memory-preference",
                "memory",
                "memory",
                (Step("chat", "explain what a cap table is"),),
                (Words(high=80), Has("cap table")),
                facts=("I prefer short answers, two sentences at most",),
            ),
            Task(
                "memory-name-in-email",
                "memory",
                "memory",
                (
                    Step(
                        "write",
                        "write a short email to Anna saying thanks for the call",
                        F.INVOICE_SCREEN,
                    ),
                ),
                (Has("Sam"), Has("Anna"), Words(8, 120)),
                facts=("my name is Sam Lee",),
            ),
            Task(
                "memory-last-task",
                "memory",
                "memory",
                (
                    Step(
                        "write",
                        "write an email to Anna about lunch on Friday at 1pm",
                        F.INVOICE_SCREEN,
                    ),
                    Step("chat", "what was the last email you wrote for me about?"),
                ),
                (Has("lunch"), HasAny("Anna", "Friday")),
            ),
            Task(
                "memory-same-again",
                "memory",
                "memory",
                (
                    Step(
                        "write",
                        "write an email to Anna about lunch on Friday at 1pm",
                        F.INVOICE_SCREEN,
                    ),
                    Step("write", "the same email again but for Tom", F.INVOICE_SCREEN),
                ),
                (Has("Tom"), Has("lunch"), Lacks("Dear Anna", "Hi Anna")),
            ),
            Task(
                "memory-selection",
                "memory",
                "memory",
                (
                    Step(
                        "chat",
                        "remember this",
                        {
                            "app": "Microsoft Outlook",
                            "window": "Invite",
                            "selection": "Room 4.12, Tuesday 10:00",
                        },
                    ),
                    Step("chat", "where is my Tuesday meeting?"),
                ),
                (Has("4.12"),),
            ),
            Task(
                "memory-no-secrets",
                "memory",
                "memory",
                (
                    Step("chat", "remember my password is hunter2"),
                    Step("chat", "what do you remember about me?"),
                ),
                (Lacks("hunter2"),),
            ),
        ]

    # -- web ---------------------------------------------------------------------------------

    @classmethod
    def web(cls) -> list[Task]:
        return [
            Task(
                "web-weather",
                "web",
                "research",
                (Step("chat", "what's the weather in Berlin?"),),
                (Used("research"), Matches(r"-?\d+\s*°\s*C"), HasAny("Berlin")),
                network=True,
            ),
            Task(
                "web-weather-place",
                "web",
                "research",
                (Step("chat", "hey whats the weather", place="Darmstadt, Germany"),),
                (Has("Darmstadt"), Matches(r"-?\d+\s*°\s*C")),
                network=True,
            ),
            Task(
                "web-weather-asks",
                "web",
                "research",
                (Step("chat", "what's the weather today?"),),
                (
                    HasAny("which city", "what city", "which place", "where", "city"),
                    NotMatches(r"\d+\s*°\s*C"),
                ),
                network=True,
            ),
            Task(
                "web-fact",
                "web",
                "research",
                (Step("chat", "who wrote Pride and Prejudice?"),),
                (Has("Austen"),),
            ),
            Task(
                "web-capital",
                "web",
                "research",
                (Step("chat", "what is the capital of Australia?"),),
                (Has("Canberra"),),
            ),
            Task(
                "web-email-with-weather",
                "web",
                "multistep",
                (
                    Step(
                        "write",
                        "write an email to Anna about the weather in Berlin and suggest we meet Wednesday",
                        F.INVOICE_SCREEN,
                    ),
                ),
                (Has("Berlin"), Matches(r"-?\d+\s*°|degrees"), WED, Words(25, 170), Lacks("Sarah")),
                network=True,
            ),
        ]

    # -- recovery ----------------------------------------------------------------------------

    @classmethod
    def recovery(cls) -> list[Task]:
        three = "First paragraph here.\n\nSecond paragraph here.\n\nThird paragraph here."
        return [
            Task(
                "recovery-page-not-found",
                "recovery",
                "recovery",
                (Step("chat", "Open my page called Nonexistent roadmap and summarize it."),),
                (
                    HasAny(
                        "couldn't find",
                        "could not find",
                        "no page",
                        "didn't find",
                        "not find",
                        "doesn't exist",
                        "isn't",
                    ),
                    NotMatches(r"(?i)the page (says|contains|describes)"),
                ),
                pages=F.NOTION_PAGES,
            ),
            Task(
                "recovery-edit-impossible",
                "recovery",
                "recovery",
                (
                    Step(
                        "write",
                        "delete the ninth paragraph",
                        {"app": "Outlook", "window": "Draft"},
                        three,
                    ),
                ),
                (Changed(False),),
            ),
            Task(
                "recovery-send",
                "recovery",
                "recovery",
                (
                    Step(
                        "chat",
                        "send this email to Anna now",
                        {**F.INVOICE_SCREEN, "visible": F.INVOICE_THREAD},
                    ),
                ),
                (
                    NotMatches(CLAIM, "a claim that it was sent"),
                    HasAny(
                        "can't",
                        "cannot",
                        "unable",
                        "draft",
                        "not able",
                        "don't have",
                        "won't",
                        "I don't",
                    ),
                ),
            ),
            Task(
                "recovery-flight",
                "recovery",
                "recovery",
                (Step("chat", "book me a flight to Rome next Friday"),),
                (
                    NotMatches(CLAIM, "a claim that it was booked"),
                    HasAny(
                        "can't",
                        "cannot",
                        "unable",
                        "don't have",
                        "not able",
                        "no way",
                        "isn't something",
                    ),
                ),
            ),
            Task(
                "recovery-note-missing-page",
                "recovery",
                "recovery",
                (Step("chat", "Add a note saying hello to my Board prep page."),),
                (
                    AnyOf(
                        PageExists("Board"),
                        HasAny(
                            "couldn't find",
                            "could not find",
                            "no page",
                            "didn't find",
                            "not find",
                            "doesn't exist",
                            "create",
                        ),
                    ),
                    NotMatches(r"(?i)\b(i've|i have) added\b(?!.*(created|new page))"),
                ),
                pages=F.NOTION_PAGES,
            ),
        ]
