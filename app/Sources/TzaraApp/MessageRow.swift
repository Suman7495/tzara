import SwiftUI
import TzaraCore

/// One message of the conversation, drawn as a bubble: its text as Markdown, what the assistant did
/// above it, the buttons under it, and a copy button.
struct MessageRow: View {
    let message: ChatMessage
    /// The message has been sent and nothing has come back yet.
    let waiting: Bool
    /// The reply is still being written, so it is not yet worth copying.
    let writing: Bool
    /// What the assistant is doing right now, such as searching the web.
    let activity: String?
    /// The dashboard's look: the assistant's words sit on the page with no bubble, the user's in a
    /// soft one, and the copy button is under the answer.
    var plain = false
    @State private var copied = false

    /// Room kept at the right of a bubble for the copy button, so the text does not move
    /// when the button appears after the reply is finished.
    private static let copyGutter: CGFloat = 26

    var body: some View {
        if message.role == .activity {
            activityBox
        } else {
            HStack {
                if message.role == .user { Spacer(minLength: 40) }
                bubble
                if message.role == .assistant { Spacer(minLength: 40) }
            }
            .frame(maxWidth: .infinity, alignment: message.role == .user ? .trailing : .leading)
        }
    }

    /// What the assistant did, in a box of its own.
    private var activityBox: some View {
        StepsList(steps: message.steps, thinking: activity)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Color.primary.opacity(0.07)))
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
        }
        .padding(.leading, plain && message.role == .assistant ? 0 : 10)
        .padding(.trailing, plain ? (message.role == .assistant ? 0 : 14) : (hasText ? Self.copyGutter : 10))
        .padding(.vertical, plain && message.role == .assistant ? 0 : 7)
        .background(background, in: RoundedRectangle(cornerRadius: plain ? 16 : 10, style: .continuous))
        .overlay(alignment: .topTrailing) {
            if hasText, !writing, !plain { copyButton }
        }
        .overlay(alignment: .bottomLeading) {
            if hasText, !writing, plain, message.role == .assistant { copyButton.offset(y: 26) }
        }
        .padding(.bottom, plain && message.role == .assistant && hasText && !writing ? 26 : 0)
    }

    private var background: AnyShapeStyle {
        if plain { return message.role == .user ? AnyShapeStyle(Color.primary.opacity(0.08)) : AnyShapeStyle(.clear) }
        return message.role == .user ? AnyShapeStyle(Color.accentColor.opacity(0.22)) : AnyShapeStyle(.quaternary)
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


/// What the assistant did: each step with a tick, the one in progress with a spinner. Before the first
/// step it says it is thinking, so it does not look stuck.
struct StepsList: View {
    let steps: [ChatMessage.Step]
    var thinking: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(steps.enumerated()), id: \.offset) { _, step in
                HStack(spacing: 6) {
                    if step.done {
                        Image(systemName: "checkmark").font(.caption2).foregroundStyle(.secondary).frame(width: 12)
                    } else {
                        ProgressView().controlSize(.mini).frame(width: 12)
                    }
                    Text(step.text)
                        .font(.callout)
                        .foregroundStyle(step.done ? .tertiary : .secondary)
                        .lineLimit(2)
                }
            }
            if steps.isEmpty {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text(thinking ?? "Thinking\u{2026}").font(.callout).foregroundStyle(.secondary)
                }
            }
        }
    }
}
