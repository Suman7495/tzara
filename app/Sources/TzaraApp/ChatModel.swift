import Foundation
import TzaraCore

struct ChatMessage: Identifiable, Equatable {
    /// `activity` is the box of what the assistant did (its steps); `assistant` is what it said.
    enum Role: String { case user, assistant, activity }
    let id = UUID()
    let role: Role
    var text: String
    /// What the assistant has done while working on this reply, so the user can see it is not stuck.
    var steps: [Step] = []

    struct Step: Equatable {
        let text: String
        var done = false
    }

    /// What the message says, ignoring the identity used to draw it.
    var content: String { "\(role.rawValue):\(text)" }
}

/// Everything the chat box shows, and what its buttons do.
///
/// The server holds the conversation, so each time the box opens it asks for it again: the
/// old chat is there even after the app has been restarted.
@MainActor
final class ChatModel: ObservableObject {
    enum Phase: Equatable {
        case idle
        /// Message sent, nothing back yet.
        case waiting
        case streaming
    }

    @Published private(set) var messages: [ChatMessage] = []
    @Published var draft = ""
    @Published private(set) var phase: Phase = .idle {
        didSet { if phase != .idle, oldValue == .idle { onWorking?() } }
    }
    /// Called when a reply starts being written.
    var onWorking: (() -> Void)?
    /// Called when the user stops the reply.
    var onStopped: (() -> Void)?
    /// Called when a reply ends by itself (finished or failed), not when the user stops it.
    var onReplyEnded: (() -> Void)?
    /// A problem or hint shown above the input, such as "the server isn't running".
    @Published private(set) var notice: String?
    /// What the assistant is doing right now ("Searching the web for ..."), while it works.
    @Published private(set) var activity: String?
    /// How many tools are running right now. The assistant can use several at once.
    private var runningTools = 0
    /// What the assistant is called.
    /// What the user was looking at when the box opened, to go with their next message. They can
    /// drop it before sending.
    @Published private(set) var screen: ScreenContext?
    /// True when macOS has not allowed Tzara to read selected text, so only the app name came.
    @Published private(set) var screenNeedsPermission = false
    private var follow = ScreenFollow()
    /// Reads what the user is doing right now, when a message is sent: the typed text and the
    /// window's text are only read then, not once a second. Given what the box shows, and
    /// returns what to send with the message (`nil` when the user has removed it).
    var freshContext: ((ScreenContext?) async -> ScreenContext?)?
    /// Where the user is (a city), when they allowed it: sent with each message.
    var placeProvider: (() async -> String?)?
    @Published private(set) var assistantName = "Tzara"
    @Published private(set) var isRenaming = false
    /// Bumped when the text field should take keyboard focus.
    @Published private(set) var focusRequest = 0

    let settings: SettingsModel
    private let client: ChatClient
    private var replyTask: Task<Void, Never>?
    init(client: ChatClient) {
        self.client = client
        settings = SettingsModel(client: client)
        settings.onMemoryErased = { [weak self] in
            self?.messages = []
        }
    }

    var canSend: Bool {
        phase == .idle && !isRenaming && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// Called when the box appears.
    func panelDidShow() {
        focusRequest += 1
        // Opening the box is the moment to get an idle model ready, so it is loaded while the
        // user is still typing.
        Task { await client.wake() }
        Task { await refresh() }
    }

    /// How long a message waits for a model that is still loading before giving up.
    private static let loadWaitSeconds = 90

    /// Sets what the user was looking at when the box opened, shown above the input until it is
    /// sent or dropped.
    func attach(_ context: ScreenContext?, needsPermission: Bool = false) {
        follow.start(with: context)
        screen = follow.current
        screenNeedsPermission = screen != nil && needsPermission
    }

    /// Follows the app in front while the box is open, also while a reply is being written and
    /// after it: when the user selects something else, or moves to another window, the chip
    /// changes with it. What the user removed stays away until it changes. Nothing changes when
    /// nothing could be read.
    func refresh(_ context: ScreenContext?, needsPermission: Bool) {
        guard follow.follow(context) else { return }
        screen = follow.current
        screenNeedsPermission = needsPermission
    }

    func dropScreen() {
        follow.drop()
        screen = nil
    }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard canSend, !text.isEmpty else { return }
        draft = ""
        deliver(text)
    }

    /// A question typed in the dashboard. The screen is not sent with it: the dashboard is not about
    /// what the user was looking at.
    func ask(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard phase == .idle, !isRenaming, !trimmed.isEmpty else { return }
        deliver(trimmed, useScreen: false)
    }

    /// The box being written: the activity box (steps) or the answer box, so each stage of the reply
    /// is its own message instead of one blob.
    private var writing: (role: ChatMessage.Role, id: UUID)?
    /// Where this turn's messages begin.
    private var turnStart = 0

    private func deliver(_ text: String, useScreen: Bool = true) {
        notice = nil
        messages.append(ChatMessage(role: .user, text: text))
        turnStart = messages.count
        beginActivity()  // "Thinking" until the first step or word arrives
        phase = .waiting

        // The context belongs to this one message: later ones are answered from the conversation.
        // What Tzara is aware of goes with every message, and stays in the box afterwards.
        let context = useScreen ? follow.current : nil

        replyTask = Task {
            let sent = await freshContext?(context) ?? context
            let place = await placeProvider?()
            var waited = 0
            while true {
                do {
                    for try await event in client.stream(text, context: sent, place: place) {
                        switch event {
                        case let .status(text):
                            activity = text
                            addStep(text, toMessage: beginActivity())
                        case let .tool(_, state):
                            toolChanged(state)
                        case let .token(piece):
                            activity = nil  // writing the answer means the tools are done
                            append(piece, toMessage: beginAnswer())
                            phase = .streaming
                        case let .error(message):
                            notice = message
                        case .done:
                            break
                        }
                    }
                } catch ChatClientError.notReady where waited < Self.loadWaitSeconds {
                    // The model is still loading (a restart, an update): wait for it, as the
                    // user would have, instead of showing an error they must retry by hand.
                    activity = "Loading the model\u{2026}"
                    waited += 1
                    if (try? await Task.sleep(nanoseconds: 1_000_000_000)) != nil { continue }
                } catch {
                    notice = Self.describe(error)
                }
                break
            }
            endTurn()
            runningTools = 0
            phase = .idle
            replyTask = nil
            if !Task.isCancelled { onReplyEnded?() }
            Task {
                await loadChats()
                currentChat = (try? await client.conversation())?.id
            }
        }
    }

    /// The activity box of the turn: the one being written, or a new one (after words, or the first).
    @discardableResult
    private func beginActivity() -> UUID {
        if let writing, writing.role == .activity { return writing.id }
        let box = ChatMessage(role: .activity, text: "")
        messages.append(box)
        writing = (.activity, box.id)
        return box.id
    }

    /// The answer box of the turn. Words that follow steps start a new one, and a "Thinking" box that
    /// never got a step goes away.
    @discardableResult
    private func beginAnswer() -> UUID {
        if let writing, writing.role == .assistant { return writing.id }
        if let writing { closeActivity(writing.id) }
        let box = ChatMessage(role: .assistant, text: "")
        messages.append(box)
        writing = (.assistant, box.id)
        return box.id
    }

    /// Finishes the steps of an activity box, or removes it if it has none.
    private func closeActivity(_ id: UUID) {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        if messages[index].steps.isEmpty {
            messages.remove(at: index)
        } else {
            for step in messages[index].steps.indices { messages[index].steps[step].done = true }
        }
    }

    /// The turn is over (answered, stopped or failed): nothing is left spinning, empty boxes go, and a
    /// turn that did things but said nothing says so instead of simply vanishing.
    private func endTurn() {
        if let writing, writing.role == .activity { closeActivity(writing.id) }
        writing = nil
        let turn = messages[min(turnStart, messages.count)...]
        let didThings = turn.contains { $0.role == .activity }
        messages.removeAll { $0.role == .assistant && $0.text.isEmpty && turn.map(\.id).contains($0.id) }
        let said = messages[min(turnStart, messages.count)...].contains { $0.role == .assistant }
        if !said, didThings, !Task.isCancelled, notice == nil {
            messages.append(ChatMessage(role: .assistant, text: Self.noAnswer))
        }
    }

    /// Stops the reply being written, keeping what has arrived so far.
    func cancelReply() {
        guard phase != .idle else { return }
        replyTask?.cancel()  // closing the connection also tells the server to stop
        replyTask = nil
        endTurn()
        activity = nil
        runningTools = 0
        phase = .idle
        onStopped?()
        Task { await client.interrupt() }
    }

    func newConversation() {
        cancelReply()
        Task {
            await client.interrupt()
            // The server finishes cancelling a reply a moment after being told to.
            for attempt in 0..<5 {
                do {
                    try await client.newConversation()
                    messages = []
                    currentChat = nil
                    notice = nil
                    await loadChats()
                    return
                } catch ChatClientError.busy where attempt < 4 {
                    try? await Task.sleep(nanoseconds: 200_000_000)
                } catch {
                    notice = Self.describe(error)
                    return
                }
            }
        }
    }

    /// Gives the assistant a new name. The server reloads its model to use it, which takes a
    /// few seconds, so the old name is restored if that fails.
    func rename(to proposed: String) {
        guard let name = AssistantName.clean(proposed) else {
            notice = "A name is one line, up to \(AssistantName.maximumLength) characters."
            return
        }
        guard name != assistantName, phase == .idle, !isRenaming else { return }

        let previous = assistantName
        assistantName = name
        isRenaming = true
        notice = "Renaming\u{2026} this takes a few seconds."
        Task {
            do {
                assistantName = try await client.rename(name)
                notice = nil
            } catch {
                assistantName = previous
                notice = Self.describe(error)
            }
            isRenaming = false
        }
    }

    // MARK: - Keeping in step with the server

    private func refresh() async {
        do {
            let status = try await client.status()
            if !isRenaming { assistantName = status.name }
            switch status.state {
            case "loading": notice = "Tzara is still loading its model\u{2026}"
            case "failed": notice = "Tzara could not load its model: \(status.error ?? "unknown reason")"
            default: if phase == .idle, !isRenaming { notice = nil }
            }
        } catch {
            notice = Self.describe(error)
            return
        }
        await loadHistory()
    }

    private func loadHistory() async {
        guard phase == .idle, let snapshot = try? await client.conversation() else { return }
        currentChat = snapshot.id
        let loaded = snapshot.messages.map { ChatMessage(role: $0.role == .user ? .user : .assistant, text: $0.text) }
        guard phase == .idle, loaded.map(\.content) != messages.filter({ $0.role != .activity }).map(\.content) else {
            await loadChats()
            return
        }
        messages = loaded
        FileLog.shared.info("restored \(loaded.count) messages of the conversation from the server")
        await loadChats()
    }

    // MARK: - Earlier chats

    /// The stored conversations, newest first, and the one in progress.
    @Published private(set) var chats: [StoredChat] = []
    @Published private(set) var currentChat: Int?

    func loadChats() async {
        guard let memory = try? await client.memory(), memory.enabled else { return }
        chats = memory.chats
    }

    /// Carries on an earlier conversation: its messages come back and the next one continues it.
    func openChat(_ id: Int) {
        guard phase == .idle, !isRenaming, id != currentChat else { return }
        Task {
            do {
                let snapshot = try await client.resumeConversation(id)
                messages = snapshot.messages.map { ChatMessage(role: $0.role == .user ? .user : .assistant, text: $0.text) }
                currentChat = snapshot.id
                notice = nil
            } catch {
                notice = Self.describe(error)
            }
        }
    }

    /// What to offer above the input before anything is asked: only what works on the screen.
    var screenSuggestions: [Suggestion] {
        phase == .idle ? Suggestions.make(hasScreenText: screen?.actionable == true, lastAnswer: nil) : []
    }

    /// What to offer under the answer: follow-ups to it, or, with no answer, what works on the screen.
    var suggestions: [Suggestion] {
        let answer = messages.last(where: { $0.role != .activity })
        let lastAnswer = answer?.role == .assistant && phase == .idle ? answer?.text : nil
        return phase == .idle ? Suggestions.make(hasScreenText: screen?.actionable == true, lastAnswer: lastAnswer) : []
    }

    /// Keeps count of running tools, and forgets what was being done once the last one is over.
    private func toolChanged(_ state: ToolState) {
        switch state {
        case .started: runningTools += 1
        case .completed, .failed: runningTools = max(runningTools - 1, 0)
        }
        if runningTools == 0 { activity = nil }
    }

    /// Notes what the assistant is doing now; the step before it is finished. They stay under the reply once it is written: what was done is never replaced by the answer.
    private func addStep(_ text: String, toMessage id: UUID) {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        if messages[index].steps.last?.text == text { return }
        for step in messages[index].steps.indices { messages[index].steps[step].done = true }
        messages[index].steps.append(.init(text: text))
        if messages[index].steps.count > Self.visibleSteps {  // only a runaway turn gets here
            messages[index].steps.removeFirst(messages[index].steps.count - Self.visibleSteps)
        }
    }

    private static let visibleSteps = 40

    private func append(_ piece: String, toMessage id: UUID) {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        messages[index].text += piece
    }

    private static let noAnswer = "I could not come up with an answer this time. Please try again."

    private static func describe(_ error: Error) -> String {
        (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
    }
}
