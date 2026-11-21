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
    @Published private(set) var phase: Phase = .idle
    /// A problem or hint shown above the input, such as "the server isn't running".
    @Published private(set) var notice: String?
    /// What the assistant is called.
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
    }

    var canSend: Bool {
        phase == .idle && !isRenaming && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// Called when the box appears.
    func panelDidShow() {
        showingSettings = false
        focusRequest += 1
        scrollRequest += 1
        Task { await refresh() }
    }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard canSend, !text.isEmpty else { return }

        draft = ""
        notice = nil
        messages.append(ChatMessage(role: .user, text: text))
        let reply = ChatMessage(role: .assistant, text: "")
        messages.append(reply)
        phase = .waiting

        replyTask = Task {
            do {
                for try await event in client.stream(text) {
                    switch event {
                    case let .token(piece):
                        append(piece, toMessage: reply.id)
                        phase = .streaming
                    case let .error(message):
                        notice = message
                    case .done:
                        break
                    }
                }
            } catch {
                notice = Self.describe(error)
            }
            removeIfEmpty(messageID: reply.id)
            phase = .idle
            replyTask = nil
        }
    }

    /// Stops the reply being written, keeping what has arrived so far.
    func cancelReply() {
        guard phase != .idle else { return }
        replyTask?.cancel()  // closing the connection also tells the server to stop
        replyTask = nil
        phase = .idle
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
