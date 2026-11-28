import AppKit
import SwiftUI
import TzaraCore

/// The pages of the dashboard.
enum DashboardPage: Hashable, Identifiable {
    case chat, memory, apps, docs
    case settings(SettingsPage)

    var id: String {
        switch self {
        case .chat: "chat"
        case .memory: "memory"
        case .apps: "apps"
        case .docs: "docs"
        case let .settings(page): page.id
        }
    }

    var title: String {
        switch self {
        case .chat: "Chat"
        case .memory: "Memory"
        case .apps: "Apps"
        case .docs: "Docs"
        case let .settings(page): page.title
        }
    }

    var symbol: String {
        switch self {
        case .chat: "bubble.left.and.bubble.right"
        case .memory: "brain"
        case .apps: "square.grid.2x2"
        case .docs: "book"
        case let .settings(page): page.symbol
        }
    }

    static let main: [DashboardPage] = [.chat, .memory, .apps, .docs]
    static let settingsPages: [DashboardPage] = SettingsPage.allCases.map { .settings($0) }
}

/// The dashboard, laid out like a chat app: a sidebar on the left, and on the right the page. The
/// sidebar can shrink to a narrow column of icons (its button is at its top), so the pages never move
/// under the window buttons.
struct DashboardView: View {
    @ObservedObject var model: ChatModel
    let hotkeyIsActive: () -> Bool
    @State var page: DashboardPage
    @State private var expanded = true
    @State private var draft = ""
    @FocusState private var composerFocused: Bool

    private static let sidebarWidth: CGFloat = 250
    private static let railWidth: CGFloat = 64
    private static let columnWidth: CGFloat = 760
    /// The window's close, minimise and zoom buttons sit over the top-left corner.
    private static let titleBar: CGFloat = 40

    var body: some View {
        HStack(spacing: 0) {
            sidebar
                .frame(width: expanded ? Self.sidebarWidth : Self.railWidth)
                .background(Color.primary.opacity(0.04))
            Divider()
            content.frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .animation(.easeInOut(duration: 0.16), value: expanded)
        .frame(minWidth: 800, minHeight: 540)
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear { composerFocused = true }
    }

    // MARK: - Sidebar

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 2) {
            // The window buttons take this strip; the button that folds the sidebar is just under it.
            Color.clear.frame(height: Self.titleBar)
            HStack {
                if expanded {
                    Text(model.assistantName).font(.headline).padding(.leading, 10)
                    Spacer()
                }
                Button { expanded.toggle() } label: { Image(systemName: "sidebar.left").imageScale(.large) }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                    .frame(width: 32, height: 28)
                    .help(expanded ? "Fold the sidebar" : "Unfold the sidebar")
                    .accessibilityLabel(expanded ? "Fold the sidebar" : "Unfold the sidebar")
            }
            .frame(maxWidth: .infinity, alignment: expanded ? .leading : .center)
            .padding(.bottom, 8)

            row("New chat", symbol: "square.and.pencil", selected: false) {
                model.newConversation()  // the chat so far stays in the list under Chats
                page = .chat
                composerFocused = true
            }
            ForEach(DashboardPage.main) { item in
                row(item.title, symbol: item.symbol, selected: page == item) { page = item }
            }
            if expanded, !model.chats.isEmpty {
                Text("Chats")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 12)
                    .padding(.top, 18)
                    .padding(.bottom, 2)
                ScrollView {
                    VStack(spacing: 1) {
                        ForEach(model.chats.prefix(40)) { chat in
                            chatRow(chat)
                        }
                    }
                }
                .frame(maxHeight: 220)
            }
            if expanded {
                Text("Settings")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 12)
                    .padding(.top, 18)
                    .padding(.bottom, 2)
            } else {
                Divider().padding(.vertical, 10).padding(.horizontal, 12)
            }
            ForEach(DashboardPage.settingsPages) { item in
                row(item.title, symbol: item.symbol, selected: page == item) { page = item }
            }
            Spacer()
        }
        .padding(.horizontal, 8)
    }

    /// An earlier chat: one press brings it back, to read and to carry on.
    private func chatRow(_ chat: StoredChat) -> some View {
        Button {
            model.openChat(chat.id)
            page = .chat
        } label: {
            Text(chat.title.isEmpty ? "Untitled" : chat.title)
                .lineLimit(1)
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(
                    page == .chat && model.currentChat == chat.id ? Color.primary.opacity(0.10) : .clear,
                    in: RoundedRectangle(cornerRadius: 8))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(chat.title)
    }

    private func row(_ title: String, symbol: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: symbol).frame(width: 20)
                if expanded {
                    Text(title)
                    Spacer(minLength: 0)
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: expanded ? .leading : .center)
            .background(selected ? Color.primary.opacity(0.10) : .clear, in: RoundedRectangle(cornerRadius: 8))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(title)
    }

    // MARK: - Content

    @ViewBuilder private var content: some View {
        switch page {
        case .chat: chat
        case .memory: MemoryPage(settings: model.settings)
        case .apps: AppsPage(settings: model.settings)
        case .docs: DocsPage()
        case let .settings(item):
            SettingsView(
                page: item, settings: model.settings, assistantName: model.assistantName,
                isRenaming: model.isRenaming, onRename: model.rename(to:), hotkeyIsActive: hotkeyIsActive)
        }
    }

    // MARK: - Chat

    private var chat: some View {
        VStack(spacing: 0) {
            if model.messages.isEmpty {
                Text("How can I help?")
                    .font(.system(size: 30, weight: .semibold))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                transcript
            }
            composer
        }
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    ForEach(model.messages) { message in
                        let isLast = message.id == model.messages.last?.id
                        MessageRow(
                            message: message, waiting: model.phase == .waiting && isLast,
                            writing: model.phase != .idle && isLast, activity: isLast ? model.activity : nil,
                            plain: true)
                    }
                    if let notice = model.notice {
                        Text(notice).font(.callout).foregroundStyle(.secondary)
                    }
                    suggestionRow
                    Color.clear.frame(height: 1).id("end")
                }
                .frame(maxWidth: Self.columnWidth, alignment: .leading)
                .padding(.horizontal, 24)
                .padding(.top, 48)
                .padding(.bottom, 16)
                .frame(maxWidth: .infinity)
            }
            .onChange(of: model.messages) { _ in DispatchQueue.main.async { proxy.scrollTo("end", anchor: .bottom) } }
            .onAppear { DispatchQueue.main.async { proxy.scrollTo("end", anchor: .bottom) } }
        }
    }

    /// What to ask next, under the last answer.
    @ViewBuilder private var suggestionRow: some View {
        let offered = model.suggestions.filter { $0.label != "Explain" && $0.label != "Summarize" }
        if !offered.isEmpty {
            HStack(spacing: 8) {
                ForEach(offered) { item in
                    Button(item.label) { model.ask(item.message) }
                        .buttonStyle(.plain)
                        .padding(.horizontal, 13)
                        .padding(.vertical, 6)
                        .background(Color.primary.opacity(0.08), in: Capsule())
                }
            }
        }
    }

    /// The only input: at the bottom, as in a chat app.
    private var composer: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("Ask anything", text: $draft, axis: .vertical)
                .textFieldStyle(.plain)
                .font(.system(size: 15))
                .lineLimit(1...8)
                .focused($composerFocused)
                .onSubmit(send)
                .padding(.vertical, 4)
            if model.phase == .idle {
                Button(action: send) { Image(systemName: "arrow.up.circle.fill").font(.title2) }
                    .buttonStyle(.plain)
                    .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .help("Send")
                    .accessibilityLabel("Send")
            } else {
                Button(action: model.cancelReply) { Image(systemName: "stop.circle.fill").font(.title2) }
                    .buttonStyle(.plain)
                    .help("Stop")
                    .accessibilityLabel("Stop")
            }
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 10)
        .background(Color.primary.opacity(0.07), in: RoundedRectangle(cornerRadius: 24, style: .continuous))
        .frame(maxWidth: Self.columnWidth)
        .padding(.horizontal, 24)
        .padding(.bottom, 24)
        .padding(.top, 8)
    }

    private func send() {
        let text = draft
        draft = ""
        model.ask(text)
    }
}

/// Opens the dashboard, filling the screen. While it is open Tzara is an ordinary app (Dock icon,
/// Cmd-Tab); closing it goes back to the menu bar only.
@MainActor
final class DashboardController: NSObject, NSWindowDelegate {
    private let model: ChatModel
    private let hotkeyIsActive: () -> Bool
    private var window: NSWindow?

    init(model: ChatModel, hotkeyIsActive: @escaping () -> Bool) {
        self.model = model
        self.hotkeyIsActive = hotkeyIsActive
    }

    var isOpen: Bool { window?.isVisible == true }

    func show(_ page: DashboardPage = .chat) {
        let window = self.window ?? makeWindow(page)
        self.window = window
        if !window.isVisible, let screen = NSScreen.main ?? NSScreen.screens.first {
            window.setFrame(screen.visibleFrame, display: true)  // opens filling the screen
        }
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        model.panelDidShow()  // loads the conversation, and wakes the model
        FileLog.shared.info("dashboard shown")
    }

    private func makeWindow(_ page: DashboardPage) -> NSWindow {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1100, height: 760),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered, defer: false)
        window.title = "Tzara"
        // The sidebar runs up under the title bar, as in a chat app.
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isReleasedWhenClosed = false
        window.contentView = NSHostingView(
            rootView: DashboardView(model: model, hotkeyIsActive: hotkeyIsActive, page: page))
        window.delegate = self
        return window
    }

    func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        FileLog.shared.info("dashboard closed")
    }

    /// Draws the window's content into a PNG file, for `--snapshot` with `--settings`.
    func snapshot(to path: String) {
        // Drawn in the light appearance: a view drawn into a bitmap does not get the dark one right.
        window?.appearance = NSAppearance(named: .aqua)
        window?.contentView?.layoutSubtreeIfNeeded()
        RunLoop.current.run(until: Date().addingTimeInterval(0.6))
        guard let view = window?.contentView, let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds)
        else { return }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        try? bitmap.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
        FileLog.shared.info("snapshot written to \(path)")
    }
}
