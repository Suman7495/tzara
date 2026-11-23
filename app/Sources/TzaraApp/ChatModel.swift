import Foundation
import TzaraCore

struct ChatMessage: Identifiable, Equatable {
    enum Role: String { case user, assistant }
    let id = UUID()
    let role: Role
    var text: String

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
    /// Whether the chip is opened to show more of what is selected.
    @Published var screenExpanded = false
    private var follow = ScreenFollow()
    @Published private(set) var assistantName = "Tzara"
    @Published private(set) var isRenaming = false
    @Published var showingSettings = false
    /// Bumped when the text field should take keyboard focus.
    @Published private(set) var focusRequest = 0
    /// Bumped when the conversation should scroll to its end.
    @Published private(set) var scrollRequest = 0

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
        showingSettings = false
        focusRequest += 1
        scrollRequest += 1
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
        screenExpanded = false
    }

    /// What the one-word buttons work on: what the user has in front of them if there is
    /// something, else the assistant's last answer. `nil` while there is nothing to work on.
    var actionTarget: QuickAction.Target? {
        if screen?.actionable == true { return .screen }
        guard let last = messages.last, last.role == .assistant, !last.text.isEmpty else { return nil }
        return .reply
    }

    /// Which of those it is, in words, shown beside the buttons so it is never a surprise.
    var actionTargetName: String {
        switch actionTarget {
        case .screen: screen?.selection.isEmpty == false ? "the selection" : "this page"
        case .reply: "the last answer"
        case nil: ""
        }
    }

    /// Sends a one-word action, as if the matching sentence had been typed.
    func perform(_ action: QuickAction) {
        guard phase == .idle, !isRenaming, let target = actionTarget else { return }
        deliver(action.prompt(translateInto: ScreenPreferences().translateInto, on: target))
    }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard canSend, !text.isEmpty else { return }
        draft = ""
        deliver(text)
    }

    private func deliver(_ text: String) {
        notice = nil
        messages.append(ChatMessage(role: .user, text: text))
        let reply = ChatMessage(role: .assistant, text: "")
        messages.append(reply)
        phase = .waiting

        // The context belongs to this one message: later ones are answered from the conversation.
        // What Tzara is aware of goes with every message, and stays in the box afterwards.
        let context = follow.current

        replyTask = Task {
            var waited = 0
            while true {
                do {
                    for try await event in client.stream(text, context: context) {
                        switch event {
                        case let .status(text):
                            activity = text
                        case let .tool(_, state):
                            toolChanged(state)
                        case let .token(piece):
                            activity = nil  // writing the answer means the tools are done
                            append(piece, toMessage: reply.id)
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
            removeIfEmpty(messageID: reply.id)
            activity = nil
            runningTools = 0
            phase = .idle
            replyTask = nil
            if !Task.isCancelled { onReplyEnded?() }
        }
    }

    /// Stops the reply being written, keeping what has arrived so far.
    func cancelReply() {
        guard phase != .idle else { return }
        replyTask?.cancel()  // closing the connection also tells the server to stop
        replyTask = nil
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
                    notice = nil
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
        guard phase == .idle, let items = try? await client.history() else { return }
        let loaded = items.map { ChatMessage(role: $0.role == .user ? .user : .assistant, text: $0.text) }
        guard phase == .idle, loaded.map(\.content) != messages.map(\.content) else { return }
        messages = loaded
        scrollRequest += 1
        FileLog.shared.info("restored \(loaded.count) messages of the conversation from the server")
    }

    /// Keeps count of running tools, and forgets what was being done once the last one is over.
    private func toolChanged(_ state: ToolState) {
        switch state {
        case .started: runningTools += 1
        case .completed, .failed: runningTools = max(runningTools - 1, 0)
        }
        if runningTools == 0 { activity = nil }
    }

    private func append(_ piece: String, toMessage id: UUID) {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        messages[index].text += piece
    }

    private func removeIfEmpty(messageID id: UUID) {
        guard let index = messages.firstIndex(where: { $0.id == id }), messages[index].text.isEmpty else { return }
        messages.remove(at: index)
    }

    private static func describe(_ error: Error) -> String {
        (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
    }
}
