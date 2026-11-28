import SwiftUI
import TzaraCore

/// The small glass bar: an input line, a chip for what Tzara can see, and, once there is something
/// to say, the answer or how a write went.
struct BarView: View {
    @ObservedObject var model: ChatModel
    @ObservedObject var state: BarState
    let onSubmit: () -> Void
    let onStop: () -> Void
    let onSuggest: (String) -> Void
    @FocusState private var focused: Bool

    var body: some View {
        // Three looks: the prompt; only the response while Tzara answers; the response with the prompt
        // below it once it has finished. The response sits above the input, so it grows upward.
        VStack(spacing: 0) {
            if let height = state.answerHeight {
                answer.frame(height: height)
                if state.showsInput { Divider() }
            }
            if state.showsInput {
                if state.answerHeight == nil, let context = model.screen { chip(context) }
                inputRow
            }
        }
        .frame(width: BarLayout.width)
        .frame(maxHeight: .infinity, alignment: .bottom)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(
                    LinearGradient(
                        colors: [.white.opacity(0.35), .white.opacity(0.08)], startPoint: .top, endPoint: .bottom),
                    lineWidth: 1))
        .environment(\.controlActiveState, .key)
        .tint(.blue)
        .onAppear { focused = true }
        .onChange(of: model.focusRequest) { _ in focused = true }
        // The prompt comes back when the response is done: ready to type again.
        .onChange(of: state.showsInput) { shown in if shown { focused = true } }
    }

    // MARK: - Input

    private var inputRow: some View {
        HStack(spacing: 8) {
            Image(systemName: "sparkles").foregroundStyle(.secondary)
            TextField("Ask, or say what to write\u{2026}", text: $model.draft)
                .textFieldStyle(.plain)
                .font(.system(size: 14))
                .focused($focused)
                .disabled(model.isRenaming)
                .onSubmit(onSubmit)
            if model.phase != .idle || state.writing {
                Button(action: onStop) { Image(systemName: "stop.circle.fill").imageScale(.large) }
                    .buttonStyle(.plain)
                    .help("Stop")
                    .accessibilityLabel("Stop")
            } else {
                Button(action: onSubmit) { Image(systemName: "arrow.up.circle.fill").imageScale(.large) }
                    .buttonStyle(.plain)
                    .disabled(model.draft.trimmingCharacters(in: .whitespaces).isEmpty)
                    .help("Send")
                    .accessibilityLabel("Send")
            }
        }
        .padding(.horizontal, 14)
        .frame(height: BarLayout.inputHeight)
    }

    /// What Tzara can see goes with the message; the cross keeps it back.
    private func chip(_ context: ScreenContext) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "text.viewfinder")
            Text(context.summary).lineLimit(1)
            Spacer(minLength: 4)
            Button(action: model.dropScreen) { Image(systemName: "xmark") }
                .buttonStyle(.plain)
                .help("Don't send this with the message")
                .accessibilityLabel("Remove what Tzara can see")
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 14)
        .frame(height: BarLayout.chipHeight)
    }

    // MARK: - Answer

    private var answer: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    if let status = state.status {
                        Label(status, systemImage: state.writing ? "pencil.line" : "checkmark.circle")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    if state.showExchange { latest }
                    if let notice = model.notice {
                        Text(notice).font(.caption).foregroundStyle(.secondary)
                    }
                    if !(state.showExchange ? model.suggestions : model.screenSuggestions).isEmpty { suggestionRow }
                    Color.clear.frame(height: 1).id("end")
                }
                .padding(12)
            }
            .onChange(of: model.messages) { _ in
                DispatchQueue.main.async { proxy.scrollTo("end", anchor: .bottom) }
            }
        }
    }

    /// Only the latest message: what Tzara is doing while it works, then what it answered. Earlier
    /// ones are in the dashboard.
    @ViewBuilder private var latest: some View {
        if let last = model.messages.last, last.role != .user {
            MessageRow(
                message: last,
                waiting: model.phase == .waiting,
                writing: model.phase != .idle,
                activity: model.activity,
                onChoose: model.choose)
        }
    }

    private var suggestionRow: some View {
        HStack(spacing: 6) {
            ForEach(state.showExchange ? model.suggestions : model.screenSuggestions) { item in
                Button(item.label) { onSuggest(item.message) }
                    .buttonStyle(.plain)
                    .font(.callout)
                    .padding(.horizontal, 11)
                    .padding(.vertical, 5)
                    .background(Color.primary.opacity(0.08), in: Capsule())
            }
            Spacer(minLength: 0)
        }
    }
}
