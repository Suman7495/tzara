import AppKit
import Combine
import SwiftUI
import TzaraCore

/// What the bar shows besides the conversation.
@MainActor
final class BarState: ObservableObject {
    /// The prompt is shown: not while Tzara is answering or writing, when only the response is.
    @Published var showsInput = true
    /// How a write went ("Writing…", "Written. ⌘Z undoes it."), shown under the input.
    @Published var status: String?
    /// Text is being written for a field.
    @Published var writing = false
    /// Show the last question and answer: set when one is asked in this opening of the bar.
    @Published var showExchange = false
    /// The height of the area under the input, or `nil` when there is nothing to show there.
    @Published var answerHeight: CGFloat?
    /// How tall the bubble's content is, measured by the view (0 until it is).
    @Published var contentHeight: CGFloat = 0
}

/// The small bar: the only window a key press opens. It opens beside the mouse pointer, takes a
/// line, and either writes it into the text field of the app the user was in (a mail, a LinkedIn
/// reply...) or answers it in the bar, as a chat. It is a silent companion that covers little: the
/// prompt while you type, only the response while Tzara answers, then the response with the prompt
/// again. It can be dragged, and it goes away when you click another window or switch app; the key
/// brings it back.
@MainActor
final class BarController {
    let model: ChatModel
    let state = BarState()
    private let panel: BarPanel
    private let writer: FieldWriter
    private let screenPreferences = ScreenPreferences()
    private var writeTask: Task<Void, Never>?
    private var targetApp: NSRunningApplication?
    private var clickMonitor: Any?
    /// A question was asked from the bar and its answer is not here yet.
    private var awaitingReply = false
    private var watcher: Timer?
    private var reading = false
    private var copiedSince = NSPasteboard.general.changeCount
    private var activation: NSObjectProtocol?
    private var lastLogged = ""
    private var observers = Set<AnyCancellable>()
    private(set) var isOpen = false

    /// What the menu-bar icon should show about the reply.
    enum Indicator { case idle, working, ready }
    var onIndicator: ((Indicator) -> Void)?
    /// Whether a reply or a write is going on, which may be while the bar is hidden.
    var isWorking: Bool { model.phase != .idle || state.writing }

    init(client: ChatClient) {
        model = ChatModel(client: client)
        writer = FieldWriter(client: client)
        panel = BarPanel(contentSize: CGSize(width: BarLayout.width, height: BarLayout.inputHeight))
        panel.isMovableByWindowBackground = true
        panel.onCancel = { [weak self] in self?.escape() }
        let hosting = NSHostingView(
            rootView: BarView(
                model: model, state: state,
                onSubmit: { [weak self] in self?.submit() },
                onStop: { [weak self] in self?.stop() },
                onSuggest: { [weak self] in self?.suggest($0) }))
        // The panel's size is set here, from what it shows. Left to itself the hosting view resizes the
        // window to its content from the top-left corner, so the bar flowed down the screen.
        hosting.sizingOptions = []
        panel.contentView = hosting
        model.freshContext = { [weak self] shown in await self?.freshContext(for: shown) ?? shown }
        model.placeProvider = { await LocationProvider.shared.place() }
        // An app that only describes itself when asked needs a few seconds after being asked, so
        // ask the moment it comes to the front.
        activation = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
        ) { [weak self] note in
            let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
            MainActor.assumeIsolated {
                guard let self, let app else { return }
                self.copiedSince = NSPasteboard.general.changeCount
                if self.screenPreferences.enabled { ScreenReader().prepare(app) }
                // Another app came to the front: the bar steps away until the key brings it back.
                if self.isOpen, app.bundleIdentifier != Bundle.main.bundleIdentifier { self.hide() }
            }
        }
        model.onWorking = { [weak self] in self?.onIndicator?(.working) }
        model.onStopped = { [weak self] in
            self?.awaitingReply = false
            self?.onIndicator?(.idle)
        }
        model.onReplyEnded = { [weak self] in
            guard let self else { return }
            let wasAsked = awaitingReply
            awaitingReply = false
            // The user went on to something else while Tzara worked: the answer pops up when it is ready.
            if wasAsked, !isOpen { popUp() }
            onIndicator?(isOpen ? .idle : .ready)
        }
        // The panel follows what it shows. Run after the change has landed (a publisher speaks
        // just before its property changes).
        Publishers.MergeMany([
            model.$messages.map { _ in () }.eraseToAnyPublisher(),
            model.$phase.map { _ in () }.eraseToAnyPublisher(),
            model.$screen.map { _ in () }.eraseToAnyPublisher(),
            model.$notice.map { _ in () }.eraseToAnyPublisher(),
            state.$status.map { _ in () }.eraseToAnyPublisher(),
            state.$writing.map { _ in () }.eraseToAnyPublisher(),
            state.$contentHeight.map { _ in () }.eraseToAnyPublisher(),
            state.$showExchange.map { _ in () }.eraseToAnyPublisher(),
        ])
        .sink { [weak self] in DispatchQueue.main.async { self?.layout() } }
        .store(in: &observers)
    }

    // MARK: - Showing and hiding

    func toggle() {
        isOpen ? hide() : show()
    }

    func show() {
        // Before the bar takes its place: the app the user was in is still the one in front.
        let front = NSWorkspace.shared.frontmostApplication
        targetApp = front?.bundleIdentifier == Bundle.main.bundleIdentifier ? nil : front
        if screenPreferences.enabled, let front { ScreenReader().prepare(front) }
        let reading = screenPreferences.enabled ? ScreenReader().read(copiedSince: copiedSince) : nil
        model.attach(reading, needsPermission: !ScreenReader.isAllowed)
        record(reading)
        startWatching()

        // Opened again while Tzara is still answering: the response is shown, not a fresh prompt.
        if model.phase == .idle {
            state.status = nil
            state.showExchange = false
            state.answerHeight = nil
            state.contentHeight = 0
        }
        let size = CGSize(width: BarLayout.width, height: targetHeight())
        // The pointer lands on the input line, which is at the bottom of the bar.
        let placement = PanelPlacement(pointerOffset: CGPoint(x: 28, y: BarLayout.inputHeight / 2))
        let cursor = NSEvent.mouseLocation
        panel.setFrame(
            placement.frame(cursor: cursor, size: size, screens: NSScreen.screens.map(\.visibleFrame)), display: true)
        panel.orderFrontRegardless()
        panel.makeKey()
        model.panelDidShow()
        isOpen = true
        onIndicator?(isWorking ? .working : .idle)
        listenForClicks()
        FileLog.shared.info("bar shown near the pointer")
    }

    func hide() {
        stopListeningForClicks()
        watcher?.invalidate()
        watcher = nil
        // A reply being written carries on in the background; Stop is what cancels it.
        panel.orderOut(nil)
        isOpen = false
        FileLog.shared.info("bar hidden")
    }

    /// Esc: stops a write in progress, else closes the bar.
    private func escape() {
        if state.writing { stop() }
        hide()
    }

    func stop() {
        writeTask?.cancel()
        writeTask = nil
        if state.writing {
            state.writing = false
            state.status = nil
        }
        model.cancelReply()
        FileLog.shared.info("bar: stopped")
    }

    // MARK: - Going away when you move on

    /// A click anywhere outside the bar (in another window or app) closes it. The bar never takes the
    /// front, so those clicks reach other apps, and a global monitor hears them; clicks in the bar
    /// itself are not reported to it. A write in progress carries on in the window it was asked for.
    private func listenForClicks() {
        stopListeningForClicks()
        clickMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown, .otherMouseDown]) {
            [weak self] _ in
            MainActor.assumeIsolated {
                guard let self, self.isOpen else { return }
                FileLog.shared.info("bar: click outside")
                self.hide()
            }
        }
    }

    private func stopListeningForClicks() {
        if let clickMonitor { NSEvent.removeMonitor(clickMonitor) }
        clickMonitor = nil
    }

    // MARK: - A line from the user

    /// Enter: write it into the field in the other app, or answer it here.
    private func submit() {
        let text = model.draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isWorking else { return }
        Task { await route(text) }
    }

    /// The app the user is working in now: the one in front (the bar never takes the front), not the
    /// one that was in front when the bar opened, which they may have left since.
    private func currentTarget() -> NSRunningApplication? {
        let front = NSWorkspace.shared.frontmostApplication
        if let front, front.bundleIdentifier != Bundle.main.bundleIdentifier { return front }
        return targetApp
    }

    private func route(_ text: String) async {
        targetApp = currentTarget()
        guard let app = targetApp else { return chat() }
        let target = await writer.read(app: app)
        let field = target.field
        let intent = BarIntent.decide(
            text, hasField: field != nil,
            hasSelection: !(field?.selection.isEmpty ?? true),
            hasDraft: !(field?.value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ?? true))
        FileLog.shared.info(
            "bar: \(intent == .write ? "write" : "chat") for \(app.localizedName ?? "?"): field \(field?.role ?? "none"), "
                + "draft \(field?.value.count ?? 0) chars, selection \(field?.selection.count ?? 0), line \(text.split(separator: " ").count) words")
        if intent == .chat { return chat() }

        model.draft = ""
        state.writing = true
        state.status = "Writing\u{2026}"
        writeTask = Task { [weak self] in
            guard let self else { return }
            let outcome = await writer.write(
                text, to: target, in: app,
                status: { [weak self] in self?.state.status = $0 },
                beforePaste: { [weak self] in
                    // The paste goes to the field, so the bar steps aside for it, then comes back.
                    self?.panel.orderOut(nil)
                    try? await Task.sleep(nanoseconds: 120_000_000)
                })
            if Task.isCancelled { return }
            state.writing = false
            if outcome == .failed { model.draft = text }  // the user can send it again
            writeTask = nil
            if isOpen {
                panel.orderFrontRegardless()  // without taking the keyboard from the field
            } else if outcome != .wrote {
                popUp()  // put away meanwhile, and it did not go in: the user must hear how it went
            }
        }
    }

    /// A suggestion was pressed: it is a question to answer here, never a write.
    private func suggest(_ message: String) {
        guard !isWorking else { return }
        state.showExchange = true
        state.status = nil
        awaitingReply = true
        state.contentHeight = 0
        model.draft = message
        model.send()
    }

    private func chat() {
        state.showExchange = true
        state.status = nil
        awaitingReply = true
        state.contentHeight = 0
        model.send()
    }

    // MARK: - Size

    /// What the panel should be: the response bubble (once something is asked), the suggestions, the
    /// prompt pill. Only the bubble while Tzara answers. Updates what the view shows to match.
    private func targetHeight() -> CGFloat {
        let last = model.messages.last
        var answer: CGFloat?
        if state.showExchange, let last, last.role != .user {
            // The measured height of the content once the view has reported it, else an estimate.
            let estimate = BarLayout.answerHeight(
                characters: last.text.count + (model.notice?.count ?? 0), steps: last.steps.count)
            answer = state.contentHeight > 0
                ? min(BarLayout.maximumAnswer, max(BarLayout.minimumAnswer, state.contentHeight))
                : estimate
        } else if state.status != nil {
            answer = BarLayout.minimumAnswer
        }
        if state.answerHeight != answer { state.answerHeight = answer }
        let showsInput = !isWorking
        if state.showsInput != showsInput { state.showsInput = showsInput }
        let offered = state.showExchange ? model.suggestions : model.screenSuggestions
        return BarLayout.height(
            hasChip: model.screen != nil && answer == nil, answer: answer, showsInput: showsInput,
            suggestions: !offered.isEmpty)
    }

    /// Sizes the panel to what it shows, keeping the prompt where it is: the response grows upward.
    private func layout() {
        let height = targetHeight()
        guard isOpen, abs(panel.frame.height - height) > 0.5 else { return }
        var frame = panel.frame
        frame.size = CGSize(width: BarLayout.width, height: height)
        // Near the top of the screen there is no room to grow: the bar moves down instead.
        if let area = NSScreen.screens.first(where: { $0.frame.contains(CGPoint(x: frame.midX, y: frame.minY + 1)) })?.visibleFrame,
            frame.maxY > area.maxY - 8
        {
            frame.origin.y = area.maxY - 8 - height
        }
        panel.setFrame(frame, display: true)
    }

    /// The answer is ready and the bar had been put away: it comes back where it was, showing the answer,
    /// without taking the keyboard from what the user is doing. A click elsewhere puts it away again.
    private func popUp() {
        isOpen = true
        layout()
        panel.orderFrontRegardless()  // not makeKey: typing in the other app goes on
        listenForClicks()
        FileLog.shared.info("bar: popped up with the answer")
    }

    // MARK: - What Tzara can see

    private func startWatching() {
        watcher?.invalidate()
        watcher = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.watch() }
        }
    }

    /// What to send with a message: what the bar shows, read again now, with the text being typed
    /// and the window's text, which are only read when a message goes out.
    private func freshContext(for shown: ScreenContext?) async -> ScreenContext? {
        guard let shown, screenPreferences.enabled else { return shown }
        let since = copiedSince
        let fresh = await Task.detached(priority: .userInitiated) { ScreenReader().readFull(copiedSince: since) }.value
        guard var fresh else { return shown }
        if fresh.selection.isEmpty, !shown.selection.isEmpty {
            fresh.selection = shown.selection
            fresh.copied = shown.copied
        }
        FileLog.shared.info(
            "screen sent: \(fresh.app); focus \(fresh.focus.isEmpty ? "none" : "yes"); typed \(fresh.typing.count) chars; "
                + "window text \(fresh.visible.count) chars; selection \(fresh.selection.count) chars")
        return fresh
    }

    /// Reads off the main thread: asking another app can take a moment, and the bar must not stall.
    private func watch() {
        guard isOpen, screenPreferences.enabled, !reading, !state.writing else { return }
        reading = true
        let since = copiedSince
        Task.detached(priority: .utility) { [weak self] in
            let found = ScreenReader().read(copiedSince: since)
            await self?.finishWatch(found)
        }
    }

    private func finishWatch(_ found: ScreenContext?) {
        reading = false
        guard isOpen else { return }
        model.refresh(found, needsPermission: !ScreenReader.isAllowed)
        record(found)
    }

    /// Writes what was read to the log, in numbers and never the text, when it changes.
    private func record(_ context: ScreenContext?) {
        let line: String
        if let context {
            line = "screen: \(context.app); window \(context.window.count) chars; "
                + "address \(context.address.isEmpty ? "none" : "read"); selection \(context.selection.count) chars"
                + (ScreenReader.isAllowed ? "" : "; Accessibility not allowed")
        } else {
            line = "screen: nothing read"
        }
        if line != lastLogged {
            lastLogged = line
            FileLog.shared.info(line)
        }
    }

    // MARK: - Test aids (command-line flags)

    func attachDemoContext() {
        model.attach(
            ScreenContext(app: "Safari", window: "How CORS works", address: "https://example.com/cors", selection: "Browsers block requests."))
    }

    /// Sends a message from the bar, for `--bar-ask`.
    func ask(_ text: String) { suggest(text) }

    /// Draws the bar into a PNG file, to look at its layout without the Screen Recording permission.
    func snapshot(to path: String) {
        guard let view = panel.contentView, let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds)
        else { return }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        try? bitmap.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
        FileLog.shared.info("snapshot written to \(path)")
    }
}
