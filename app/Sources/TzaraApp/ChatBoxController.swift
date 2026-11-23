import AppKit
import SwiftUI
import TzaraCore

/// Shows and hides the chat box, and puts it beside the mouse pointer.
@MainActor
final class ChatBoxController {
    private let model: ChatModel
    private let panel: ChatBoxPanel
    private let placement = PanelPlacement(pointerOffset: ChatView.inputLine)
    /// Whether *we* have opened the box and not yet closed it. Asking the window
    /// (`panel.isVisible`) is not a reliable way to know this.
    private(set) var isOpen = false
    private let screenPreferences = ScreenPreferences()
    private var watcher: Timer?
    private var reading = false
    /// The pasteboard count when the app in front came to the front: text copied after that was
    /// copied in that app.
    private var copiedSince = NSPasteboard.general.changeCount
    private var activation: NSObjectProtocol?
    private var lastLogged = ""

    /// What the menu-bar icon should show about the reply.
    enum Indicator { case idle, working, ready }
    var onIndicator: ((Indicator) -> Void)?
    /// Whether a reply is being written, which may be while the box is hidden.
    var isWorking: Bool { model.phase != .idle }

    init(client: ChatClient, hotkeyIsActive: @escaping () -> Bool) {
        model = ChatModel(client: client)
        panel = ChatBoxPanel(contentSize: ChatView.size)
        let view = ChatView(model: model, onClose: { [weak self] in self?.hide() }, hotkeyIsActive: hotkeyIsActive)
        panel.contentView = NSHostingView(rootView: view)
        panel.onCancel = { [weak self] in self?.hide() }
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
            }
        }
        model.onWorking = { [weak self] in self?.onIndicator?(.working) }
        model.onStopped = { [weak self] in self?.onIndicator?(.idle) }
        model.onReplyEnded = { [weak self] in
            guard let self else { return }
            onIndicator?(isOpen ? .idle : .ready)
        }
        FileLog.shared.info("chat box created; panel.isVisible at creation = \(panel.isVisible)")
    }

    func toggle() {
        FileLog.shared.info("toggle: box is \(isOpen ? "open" : "closed"); panel.isVisible = \(panel.isVisible)")
        isOpen ? hide() : show()
    }

    func show(settings: Bool = false) {
        // Before the box takes its place: the app the user was in is still the one in front.
        if !isOpen {
            if screenPreferences.enabled, let app = NSWorkspace.shared.frontmostApplication {
                ScreenReader().prepare(app)
            }
            let reading = screenPreferences.enabled ? ScreenReader().read(copiedSince: copiedSince) : nil
            model.attach(reading, needsPermission: !ScreenReader.isAllowed)
            record(reading)
            startWatching()
        }
        let cursor = NSEvent.mouseLocation
        let frame = placement.frame(
            cursor: cursor,
            size: panel.frame.size,
            screens: NSScreen.screens.map(\.visibleFrame))
        panel.setFrame(frame, display: true)
        panel.orderFrontRegardless()
        panel.makeKey()
        model.panelDidShow()
        model.showingSettings = settings
        isOpen = true
        onIndicator?(isWorking ? .working : .idle)
        FileLog.shared.info("chat box shown: pointer \(cursor.debugDescription), box \(frame.debugDescription)")
    }

    /// Puts a made-up context in the box, for `--demo-screen`, to look at the chip and buttons
    /// without needing the Accessibility permission or an app in front.
    func attachDemoContext(expanded: Bool = false) {
        let sentence = "Browsers block requests between origins unless the server allows them. "
        model.attach(
            ScreenContext(
                app: "Safari", window: "How CORS works", address: "https://example.com/cors",
                selection: String(repeating: sentence, count: expanded ? 8 : 1)))
        model.screenExpanded = expanded
    }

    /// Draws the box into a PNG file. Used by `--snapshot` to look at the layout without needing
    /// the Screen Recording permission, because the view draws itself rather than being captured.
    func snapshot(to path: String) {
        guard let view = panel.contentView, let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds)
        else { return }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        try? bitmap.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
        FileLog.shared.info("snapshot written to \(path)")
    }

    /// Sends Command+V to the box, as if it had been typed, through the same path a real key
    /// press takes. Used by `--press-paste` to check that pasting works without touching the
    /// keyboard, which would type into whatever app is in front.
    func pressPaste() {
        guard let event = NSEvent.keyEvent(
            with: .keyDown, location: .zero, modifierFlags: .command, timestamp: 0,
            windowNumber: panel.windowNumber, context: nil, characters: "v",
            charactersIgnoringModifiers: "v", isARepeat: false, keyCode: 9)
        else { return }
        NSApp.sendEvent(event)
        FileLog.shared.info("sent Command+V to the box")
    }

    /// While the box is open, follows what is selected in the app in front, once a second.
    private func startWatching() {
        watcher?.invalidate()
        watcher = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.watch() }
        }
    }

    /// Reads off the main thread: asking another app can take a moment, and the box must not stall.
    private func watch() {
        guard isOpen, screenPreferences.enabled, !reading else { return }
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

    /// Writes what was read to the log, in numbers and never the text, when it changes, so that
    /// a read that found nothing can be told from one that was not allowed.
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

    func hide() {
        watcher?.invalidate()
        watcher = nil
        // A reply being written carries on in the background; Stop is what cancels it.
        panel.orderOut(nil)
        isOpen = false
        FileLog.shared.info("chat box hidden")
    }
}
