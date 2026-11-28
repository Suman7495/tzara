import Foundation

/// The documentation shown in the dashboard: short topics, each written in Markdown.
public struct DocsTopic: Equatable, Identifiable, Sendable {
    public let id: String
    public let title: String
    public let symbol: String
    public let body: String
}

public enum DocsContent {
    public static let topics: [DocsTopic] = [
        DocsTopic(id: "start", title: "Getting started", symbol: "sparkles", body: """
        # Getting started

        Tzara is a personal assistant that runs on your Mac. Its model runs on your computer through Ollama, so what you ask stays here.

        1. **Tap Right Option** anywhere. A small bar opens next to your pointer.
        2. **Type** and press Enter. Ask a question, or say what to write.
        3. Click another window and it goes away. Tap Right Option to bring it back.

        The **dashboard** (this window) is for the conversation, memory, apps and settings. Open it from the menu-bar icon.

        ## The first time
        - macOS asks for **Input Monitoring** (so the key works) and **Accessibility** (so Tzara can read the text field and the window you are in). Allow both in System Settings, then reopen Tzara.
        - The first message after a restart is slower: the model loads into memory.
        """),
        DocsTopic(id: "bar", title: "The bar", symbol: "keyboard", body: """
        # The bar

        Tap **Right Option** to open the bar beside your pointer, and tap it again to close it. You can change the key in Settings.

        - **Enter** sends. **Esc** closes. Drag the bar by its background to move it.
        - It is a quiet companion that covers little of the screen: **the prompt** while you type, **only the response** (as a bubble) while Tzara answers, then the response with **the prompt again** below it.
        - It **goes away when you click another window** or switch to another app. Tap Right Option to bring it back.
        - The small chip above the input shows what Tzara can see (the app and window you were in, and any selected text). The cross removes it for this message.

        ## Write or answer?
        Tzara decides from your words, with no extra thinking step:

        - A request for text, such as "write an email to Anna about Friday", "reply that I agree" or "shorter", is **written into the text field** you were in.
        - A question, or a request to explain or summarize, is **answered in the bar**. "Explain this" never replaces your selection.
        """),
        DocsTopic(id: "writing", title: "Writing in other apps", symbol: "pencil.line", body: """
        # Writing in other apps

        Open the bar in a text field (a mail in Outlook, a reply on LinkedIn, a message in Slack, Discord or Teams) and say what you want.

        | You have | You say | Tzara |
        | --- | --- | --- |
        | An empty field | "reply that I agree" | Writes the reply |
        | A draft | "friendlier" | Rewrites your draft |
        | Selected text | "shorter" | Rewrites the selection |
        | Anything | "write an email to Anna about Friday" | Writes the email body |

        ## What to know
        - It **never presses Return**. The text is pasted, you read it, and you send it yourself.
        - **⌘Z** undoes it in one step.
        - The style follows the place: chat, LinkedIn, email, code. It also uses facts it knows about you and your own earlier drafts, so it sounds like you.
        - If an app shows Tzara no text field, the text goes on the clipboard instead, and the bar says **Press ⌘V**.
        - Teams shows very little to the Mac, so it may need the clipboard route.
        """),
        DocsTopic(id: "ask", title: "Asking questions", symbol: "questionmark.bubble", body: """
        # Asking questions

        Ask in the bar or in the dashboard chat.

        - **Facts, news, prices, products, how-to**: Tzara searches the web, reads the best pages and answers in a few sentences with sources.
        - **Weather**: "what's the weather in Berlin" is answered from a weather service with real numbers: now, today and the next two days.
        - **Your screen**: "explain this", "summarize this page", "reply to this" use what is selected or open in the app in front.
        - **Mac**: "open Safari", "play", "pause", "next".

        Answers are meant to be short. Ask for more detail when you want it.
        """),
        DocsTopic(id: "screen", title: "Your screen", symbol: "text.viewfinder", body: """
        # Your screen

        With **Use the window in front** on (Settings → Context & writing), Tzara knows which app and window you were in when you opened the bar, and what text you had selected.

        - The chip in the bar shows exactly what will be sent. Remove it with the cross.
        - It reads through macOS Accessibility, as text. It takes **no screenshots**.
        - Password managers, password fields and private text are never read.
        - Text from your screen is treated as **data, not instructions**: a web page cannot tell Tzara to do something.
        """),
        DocsTopic(id: "memory", title: "Memory", symbol: "brain", body: """
        # Memory

        Tzara learns as you chat. You never have to say "remember".

        - **Facts**: "my name is…", "I work at…", "I live in…", "my wife is…", "I prefer short answers", "always reply in English". A newer answer replaces the older one.
        - **Activity**: what Tzara did for you, such as a page it changed or text it wrote, so "did that work?" has an answer.
        - **Writing samples**: your own drafts, kept to match your tone.
        - **Conversations**: everything said, searchable.

        Open **Memory** in the sidebar to read all of it, search it, delete a fact, or erase everything. It is one file on this Mac, readable only by you.
        """),
        DocsTopic(id: "apps", title: "Apps", symbol: "square.grid.2x2", body: """
        # Apps

        Connect apps so Tzara can read from them: Notion, Microsoft 365 (Outlook, OneDrive, Teams, Excel), HubSpot, or any app that follows the MCP standard.

        1. Open **Apps** and press **Add** on a card. Nothing is connected until you do.
        2. Sign in if the app asks (a browser page opens, or a code is shown).
        3. Press **Tools** to choose exactly what Tzara may use. Each tool adds to what the model reads before every reply, so keep the list short.

        ## Safe by default
        Only tools that **read** are offered. Tools that change things are listed as "may change things" and stay off until you tick them. Tzara never claims it changed something in an app unless a tool reported success.
        """),
        DocsTopic(id: "privacy", title: "Privacy and safety", symbol: "lock.shield", body: """
        # Privacy and safety

        - The **model runs on your Mac**. Messages, memory and the screen text never go to a server of ours.
        - Web searches and page reads are made from your Mac, honestly, as a program.
        - **Nothing is sent for you.** Tzara writes into a field and stops.
        - Pages, emails and app results are untrusted data: they cannot make Tzara remember something or act.
        - The local server only listens on your own Mac and needs a secret token.
        - Logs record that things happened (timings, sizes), not what you or Tzara said.
        """),
        DocsTopic(id: "shortcuts", title: "Shortcuts", symbol: "command", body: """
        # Shortcuts

        | Keys | What it does |
        | --- | --- |
        | **Right Option** (tap) | Open or close the bar |
        | **Enter** | Send |
        | **Esc** | Close the bar, or stop a write |
        | **⌘Z** | Undo a write in the other app |
        | **⌘V** | Paste, when the bar says the text is on the clipboard |
        """),
        DocsTopic(id: "help", title: "Troubleshooting", symbol: "wrench.and.screwdriver", body: """
        # Troubleshooting

        ## The key does nothing
        Allow Tzara under **System Settings → Privacy & Security → Input Monitoring**, then quit and reopen it. Settings → About shows whether the hotkey is active.

        ## It cannot read my text field or window
        Allow **Accessibility** for Tzara. Some apps (Teams) show very little; then the text goes on the clipboard.

        ## "The model is still loading"
        The first message after a restart takes a few seconds while the model loads. Choose a smaller model in Settings → General if it is too slow.

        ## An answer is wrong or empty
        Ask again, or put it another way. Tzara's local model is small; for facts it searches the web, and says when sources disagree.

        ## Logs
        `~/Library/Logs/tzara/tzara.log` (server) and `app.log` (this app). They contain timings and sizes, never your messages.
        """),
    ]
}
