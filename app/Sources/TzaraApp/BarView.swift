import SwiftUI
import TzaraCore

/// The bar: not one box but up to three pieces that float apart on a clear panel. The response is a plain
/// chat bubble; under it come small suggestion pills; at the bottom the prompt pill. While Tzara answers
/// only the bubble is shown.
struct BarView: View {
    @ObservedObject var model: ChatModel
    @ObservedObject var state: BarState
    let onSubmit: () -> Void
    let onStop: () -> Void
    let onSuggest: (String) -> Void
    @FocusState private var focused: Bool

    private static let bubbleRadius: CGFloat = 18

    var body: some View {
        VStack(spacing: BarLayout.gap) {
            if let height = state.answerHeight { bubble.frame(height: height) }
            if state.showsInput, !offered.isEmpty { suggestionRow }
            if state.showsInput { prompt }
        }
        .frame(width: BarLayout.width)
        .frame(maxHeight: .infinity, alignment: .bottom)
        .environment(\.controlActiveState, .key)
        .tint(.blue)
        .onAppear { focused = true }
        .onChange(of: model.focusRequest) { _ in focused = true }
        // The prompt comes back when the response is done: ready to type again.
        .onChange(of: state.showsInput) { shown in if shown { focused = true } }
    }

    /// A piece of glass: material with a light rim.
    private func glass(_ radius: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: radius, style: .continuous)
            .fill(.ultraThinMaterial)
            .overlay(
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(
                        LinearGradient(
                            colors: [.white.opacity(0.35), .white.opacity(0.08)], startPoint: .top,
                            endPoint: .bottom),
                        lineWidth: 1))
    }

    // MARK: - The response bubble

    private var bubble: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    if let status = state.status {
                        Label(status, systemImage: state.writing ? "pencil.line" : "checkmark.circle")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    if state.showExchange { content }
                    if let notice = model.notice { Text(notice).font(.caption).foregroundStyle(.secondary) }
                    Color.clear.frame(height: 1).id("end")
                }
                .padding(.leading, 16)
                .padding(.trailing, 36)  // room for the copy button
                .padding(.vertical, 12)
                .frame(maxWidth: .infinity, alignment: .leading)
                // How tall the content really is: the bubble is made as tall as it needs, no taller.
                .background(
                    GeometryReader { proxy in
                        Color.clear.preference(key: ContentHeightKey.self, value: proxy.size.height)
                    })
            }
            .onPreferenceChange(ContentHeightKey.self) { height in
                DispatchQueue.main.async { if abs(state.contentHeight - height) > 0.5 { state.contentHeight = height } }
            }
            .onChange(of: model.messages) { _ in DispatchQueue.main.async { proxy.scrollTo("end", anchor: .bottom) } }
        }
        .background(glass(Self.bubbleRadius))
        .overlay(alignment: .topTrailing) { copyButton }
    }

    /// The latest message only: what Tzara is doing while it works, then what it said.
    @ViewBuilder private var content: some View {
        if let last = model.messages.last, last.role != .user {
            if last.role == .activity {
                StepsList(steps: last.steps, thinking: model.activity)
            } else {
                if !last.text.isEmpty { MarkdownView(text: last.text) }
            }
        }
    }

    @ViewBuilder private var copyButton: some View {
        if state.showExchange, model.phase == .idle, let last = model.messages.last, last.role == .assistant, !last.text.isEmpty {
            Button {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(last.text, forType: .string)
            } label: {
                Image(systemName: "doc.on.doc").font(.caption).frame(width: 24, height: 24).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .padding(6)
            .help("Copy")
            .accessibilityLabel("Copy the answer")
        }
    }

    // MARK: - Suggestions

    private var offered: [Suggestion] { state.showExchange ? model.suggestions : model.screenSuggestions }

    private var suggestionRow: some View {
        HStack(spacing: 6) {
            ForEach(offered) { item in
                Button(item.label) { onSuggest(item.message) }
                    .buttonStyle(.plain)
                    .font(.callout)
                    .padding(.horizontal, 12)
                    .frame(height: BarLayout.suggestionHeight - 4)
                    .background(glass(16))
            }
            Spacer(minLength: 0)
        }
        .frame(height: BarLayout.suggestionHeight)
    }

    // MARK: - The prompt

    private var prompt: some View {
        VStack(spacing: 0) {
            if state.answerHeight == nil, let context = model.screen { chip(context) }
            inputRow
        }
        .background(glass(22))
    }

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
        .padding(.horizontal, 16)
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
        .padding(.horizontal, 16)
        .frame(height: BarLayout.chipHeight)
        .padding(.top, 2)
    }
}


private struct ContentHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}
