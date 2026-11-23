import SwiftUI
import TzaraCore

/// The chat box: a title bar, the conversation, and an input line. A gear in the title bar
/// swaps the conversation for the settings.
struct ChatView: View {
    static let size = CGSize(width: 380, height: 440)
    /// Where the input line sits, measured from the box's bottom-left corner. The box is
    /// placed so the mouse pointer lands here, so these must match the layout below.
    static let inputLine = CGPoint(x: 28, y: 22)
    /// An empty view after the conversation, which it scrolls to so nothing is hidden below.
    private static let endOfTranscript = "end-of-transcript"

    @ObservedObject var model: ChatModel
    let onClose: () -> Void
    let hotkeyIsActive: () -> Bool
    @FocusState private var inputFocused: Bool
    @FocusState private var nameFocused: Bool
    @State private var editingName = false
    @State private var nameDraft = ""

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if model.showingSettings {
                SettingsView(
                    settings: model.settings,
                    assistantName: model.assistantName,
                    isRenaming: model.isRenaming,
                    onRename: model.rename(to:),
                    hotkeyIsActive: hotkeyIsActive)
            } else {
                transcript
                if let notice = model.notice { noticeBar(notice) }
                contextTray
                Divider()
                inputBar
            }
        }
        .frame(width: Self.size.width, height: Self.size.height)
        // A thin material lets what is behind show through a little, for a liquid feel; the light
        // edge gives it a glassy rim.
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(
                    LinearGradient(
                        colors: [.white.opacity(0.28), .white.opacity(0.06)],
                        startPoint: .top, endPoint: .bottom),
                    lineWidth: 1))
        .onChange(of: model.focusRequest) { _ in inputFocused = true }
        .onExitCommand(perform: onClose)  // the Escape key
    }

    // MARK: - Title bar

    private var header: some View {
        HStack(spacing: 10) {
            if model.showingSettings {
                Button { model.showingSettings = false } label: { Image(systemName: "chevron.left") }
                    .buttonStyle(.plain)
                    .help("Back")
                    .accessibilityLabel("Back")
                Text("Settings").font(.headline)
            } else {
                nameLabel
            }
            Spacer()
            if !model.showingSettings {
                Button { model.showingSettings = true } label: { Image(systemName: "gearshape") }
                    .buttonStyle(.plain)
                    .help("Settings")
                    .accessibilityLabel("Settings")
                Button(action: model.newConversation) { Image(systemName: "square.and.pencil") }
                    .buttonStyle(.plain)
                    .help("New conversation")
                    .accessibilityLabel("New conversation")
            }
            // While a reply is being written, closing only hides the box: the reply carries on and
            // the menu-bar icon says when it is ready.
            let working = model.phase != .idle
            Button(action: onClose) {
                Image(systemName: working ? "minus.circle.fill" : "xmark.circle.fill").imageScale(.large)
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .help(working ? "Minimize \u{2013} keeps working (Esc)" : "Close (Esc)")
            .accessibilityLabel(working ? "Minimize" : "Close")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    /// The assistant's name. Double-click to rename it.
    @ViewBuilder private var nameLabel: some View {
        if editingName {
            TextField("Name", text: $nameDraft)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 190)
                .focused($nameFocused)
                .onSubmit { finishRenaming(save: true) }
                .onExitCommand { finishRenaming(save: false) }  // Escape cancels the rename, not the box
                .onChange(of: nameFocused) { focused in if !focused { finishRenaming(save: true) } }
        } else {
            HStack(spacing: 6) {
                Text(model.assistantName).font(.headline).lineLimit(1)
                if model.isRenaming { ProgressView().controlSize(.small) }
            }
            .contentShape(Rectangle())
            .onTapGesture(count: 2) { beginRenaming() }
            .help("Double-click to rename")
        }
    }

    private func beginRenaming() {
        guard !model.isRenaming else { return }
        nameDraft = model.assistantName
        editingName = true
        nameFocused = true
    }

    private func finishRenaming(save: Bool) {
        guard editingName else { return }
        editingName = false
        if save { model.rename(to: nameDraft) }
        inputFocused = true
    }

    // MARK: - Conversation

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                // Not a LazyVStack: it guesses the height of rows it has not drawn, so scrolling to
                // the end of a tall reply stopped short. A chat is short enough to draw in full.
                VStack(alignment: .leading, spacing: 10) {
                    if model.messages.isEmpty { emptyState }
                    ForEach(model.messages) { message in
                        let isLast = message.id == model.messages.last?.id
                        MessageRow(
                            message: message,
                            waiting: model.phase == .waiting && isLast,
                            writing: model.phase != .idle && isLast,
                            activity: isLast ? model.activity : nil
                        )
                        .id(message.id)
                    }
                    Color.clear.frame(height: 1).id(Self.endOfTranscript)
                }
                .padding(14)
            }
            .onChange(of: model.messages) { _ in scrollToEnd(proxy) }
            .onChange(of: model.scrollRequest) { _ in scrollToEnd(proxy) }
        }
    }

    /// Scrolls to the end once the new content has been laid out; asked at once, the end is
    /// still where it was before the content changed.
    private func scrollToEnd(_ proxy: ScrollViewProxy) {
        DispatchQueue.main.async {
            proxy.scrollTo(Self.endOfTranscript, anchor: .bottom)
        }
    }

    private var emptyState: some View {
        Text("Ask me anything!")
            .font(.title3)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, minHeight: 160)
    }

    private func noticeBar(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14)
            .padding(.vertical, 6)
            .background(Color.primary.opacity(0.06))
    }

    // MARK: - What Tzara can see

    /// One tidy tray above the input: what Tzara can see (so nothing goes out unseen) and the
    /// one-word actions. With something on screen it is a single card holding both; with only
    /// the last answer to work on, it is just the row of buttons.
    @ViewBuilder private var contextTray: some View {
        // The card stays while a reply is written and after it: it is what Tzara is aware of.
        // The buttons wait until the reply is finished.
        let showActions = model.phase == .idle && model.actionTarget != nil
        if let context = model.screen {
            VStack(alignment: .leading, spacing: 8) {
                contextLine(context)
                if model.screenExpanded { contextDetails(context) }
                if model.screenNeedsPermission { permissionNote }
                if showActions { actionRow }
            }
            .padding(10)
            .background(Color.primary.opacity(0.07), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .strokeBorder(Color.primary.opacity(0.08)))
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        } else if showActions {
            actionRow
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
        }
    }

    private func contextLine(_ context: ScreenContext) -> some View {
        HStack(spacing: 6) {
            // The whole line opens and closes the details; only the cross removes it.
            Button {
                withAnimation(.easeOut(duration: 0.15)) { model.screenExpanded.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "text.viewfinder")
                    Text(context.summary).lineLimit(1)
                    Image(systemName: "chevron.right")
                        .font(.caption2)
                        .rotationEffect(.degrees(model.screenExpanded ? 90 : 0))
                    Spacer(minLength: 4)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(model.screenExpanded ? "Hide the details" : "Show more of what Tzara can see")
            Button(action: model.dropScreen) { Image(systemName: "xmark") }
                .buttonStyle(.plain)
                .help("Don't send this with the message")
                .accessibilityLabel("Remove what Tzara can see")
        }
        .font(.callout)
        .foregroundStyle(.secondary)
    }

    /// The window, the address and the selected text, which scrolls when it is long.
    private func contextDetails(_ context: ScreenContext) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if !context.window.isEmpty { detailRow("Window", context.window) }
            if !context.address.isEmpty { detailRow("Address", context.address) }
            if !context.selection.isEmpty {
                ScrollView {
                    Text(context.selection)
                        .font(.callout)
                        .foregroundStyle(.primary)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(8)
                }
                .frame(height: Self.selectionHeight(for: context.selection))
                .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            } else if context.window.isEmpty && context.address.isEmpty {
                Text("Only the name of the app is known.").font(.caption).foregroundStyle(.secondary)
            }
        }
        .transition(.opacity)
    }

    /// How tall the selected text's area is: as much as the text needs, up to a limit, past which
    /// it scrolls. Fixed rather than flexible because the conversation above would squeeze it.
    private static func selectionHeight(for text: String) -> CGFloat {
        let lines = text.split(separator: "\n", omittingEmptySubsequences: false)
            .reduce(0) { $0 + max(1, ($1.count + 39) / 40) }
        return min(130, 22 + CGFloat(lines) * 18)
    }

    private func detailRow(_ name: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(name).font(.caption).foregroundStyle(.secondary).frame(width: 52, alignment: .leading)
            Text(value).font(.caption).lineLimit(2).truncationMode(.middle).textSelection(.enabled)
        }
    }

    /// Without this the selected text silently never arrives, and nothing says why.
    private var permissionNote: some View {
        HStack(spacing: 6) {
            Text("Selected text needs the Accessibility permission.").foregroundStyle(.orange)
            Button("Allow\u{2026}", action: SystemSettings.openAccessibility)
                .buttonStyle(.link)
        }
        .font(.caption)
    }

    /// The one-word actions. Each sends an ordinary message.
    private var actionRow: some View {
        HStack(spacing: 5) {
            Text("On \(model.actionTargetName):")
                .font(.caption)
                .foregroundStyle(.secondary)
            ForEach(QuickAction.allCases, id: \.label) { action in
                Button(action.label) { model.perform(action) }
                    .buttonStyle(.plain)
                    .font(.caption)
                    .lineLimit(1)
                    .fixedSize()
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Color.primary.opacity(0.09), in: Capsule())
            }
            Spacer(minLength: 0)
        }
        .help(model.actionTarget == .reply ? "These work on the last answer" : "These work on what Tzara can see")
    }

    // MARK: - Input line

    private var inputBar: some View {
        HStack(spacing: 8) {
            TextField("Ask me anything!", text: $model.draft)
                .textFieldStyle(.plain)
                .focused($inputFocused)
                .disabled(model.isRenaming)
                .onSubmit(model.send)

            if model.phase == .idle {
                Button(action: model.send) { Image(systemName: "arrow.up.circle.fill").imageScale(.large) }
                    .buttonStyle(.plain)
                    .disabled(!model.canSend)
                    .help("Send")
                    .accessibilityLabel("Send")
            } else {
                Button(action: model.cancelReply) { Image(systemName: "stop.circle.fill").imageScale(.large) }
                    .buttonStyle(.plain)
                    .help("Stop")
                    .accessibilityLabel("Stop")
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }
}

private struct MessageRow: View {
    let message: ChatMessage
    /// The message has been sent and nothing has come back yet.
    let waiting: Bool
    /// The reply is still being written, so it is not yet worth copying.
    let writing: Bool
    /// What the assistant is doing right now, such as searching the web.
    let activity: String?
    @State private var copied = false

    /// Room kept at the right of a bubble for the copy button, so the text does not move
    /// when the button appears after the reply is finished.
    private static let copyGutter: CGFloat = 26

    var body: some View {
        HStack {
            if message.role == .user { Spacer(minLength: 40) }
            bubble
            if message.role == .assistant { Spacer(minLength: 40) }
        }
        .frame(maxWidth: .infinity, alignment: message.role == .user ? .trailing : .leading)
    }

    private var hasText: Bool { !message.text.isEmpty }

    private var bubble: some View {
        VStack(alignment: .leading, spacing: 6) {
            if hasText {
                if message.role == .assistant {
                    MarkdownView(text: message.text)
                } else {
                    Text(message.text).textSelection(.enabled)
                }
            }
            // While the assistant works it says what it is doing, so it does not look stuck.
            if message.role == .assistant, waiting || activity != nil {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text(activity ?? "Thinking\u{2026}")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }
        }
        .padding(.leading, 10)
        .padding(.trailing, hasText ? Self.copyGutter : 10)
        .padding(.vertical, 7)
        .background(
            message.role == .user ? AnyShapeStyle(Color.accentColor.opacity(0.22)) : AnyShapeStyle(.quaternary),
            in: RoundedRectangle(cornerRadius: 10, style: .continuous)
        )
        .overlay(alignment: .topTrailing) {
            if hasText, !writing { copyButton }
        }
    }

    /// Copies the message's text, as written (with its Markdown), to the clipboard.
    private var copyButton: some View {
        Button(action: copy) {
            Image(systemName: copied ? "checkmark" : "doc.on.doc")
                .font(.caption)
                .frame(width: 22, height: 22)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(.secondary)
        .padding(.top, 3)
        .padding(.trailing, 3)
        .help(copied ? "Copied" : "Copy this message")
        .accessibilityLabel("Copy message")
    }

    private func copy() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(message.text, forType: .string)
        copied = true
        Task {
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            copied = false
        }
    }
}
