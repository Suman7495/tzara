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

    func hide() {
        // A reply being written carries on in the background; Stop is what cancels it.
        panel.orderOut(nil)
        isOpen = false
        FileLog.shared.info("chat box hidden")
    }
}
