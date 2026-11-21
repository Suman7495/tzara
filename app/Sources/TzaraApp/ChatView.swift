import SwiftUI

/// The chat box: a title bar, the conversation, and an input line. A gear in the title bar
/// swaps the conversation for the settings.
struct ChatView: View {
    static let size = CGSize(width: 380, height: 440)
    /// Where the input line sits, measured from the box's bottom-left corner. The box is
    /// placed so the mouse pointer lands here, so these must match the layout below.
    static let inputLine = CGPoint(x: 28, y: 22)

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
                Divider()
                inputBar
            }
        }
        .frame(width: Self.size.width, height: Self.size.height)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(.separator))
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
            Button(action: onClose) { Image(systemName: "xmark.circle.fill").imageScale(.large) }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
                .help("Close (Esc)")
                .accessibilityLabel("Close")
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
                LazyVStack(alignment: .leading, spacing: 10) {
                    if model.messages.isEmpty { emptyState }
                    ForEach(model.messages) { message in
                        MessageRow(
                            message: message,
                            waiting: model.phase == .waiting && message.id == model.messages.last?.id
                        )
                        .id(message.id)
                    }
                }
                .padding(14)
            }
            .onChange(of: model.messages) { messages in
                guard let last = messages.last else { return }
                proxy.scrollTo(last.id, anchor: .bottom)
            }
            .onChange(of: model.scrollRequest) { _ in
                guard let last = model.messages.last else { return }
                proxy.scrollTo(last.id, anchor: .bottom)
            }
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
            .font(.callout)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(.quaternary)
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
    let waiting: Bool

    var body: some View {
        HStack {
            if message.role == .user { Spacer(minLength: 40) }
            Group {
                if waiting && message.text.isEmpty {
                    ProgressView().controlSize(.small)
                } else {
                    Text(message.text).textSelection(.enabled)
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(
                message.role == .user ? AnyShapeStyle(Color.accentColor.opacity(0.22)) : AnyShapeStyle(.quaternary),
                in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            if message.role == .assistant { Spacer(minLength: 40) }
        }
    }
}
