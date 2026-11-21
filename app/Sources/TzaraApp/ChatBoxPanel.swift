import AppKit

/// The floating window the chat box lives in.
///
/// It is a *non-activating panel*: clicking or typing in it does not make Tzara the active
/// application, so whatever you were doing keeps its focus and, importantly, its full-screen
/// Space. Joining all Spaces and being a "full-screen auxiliary" window is what lets it float
/// over a full-screen app.
final class ChatBoxPanel: NSPanel {
    /// Called when the user presses Escape with no text field handling it.
    var onCancel: (() -> Void)?

    init(contentSize: CGSize) {
        super.init(
            contentRect: NSRect(origin: .zero, size: contentSize),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false)
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        hidesOnDeactivate = false
        isMovableByWindowBackground = true
        isReleasedWhenClosed = false
    }

    // A borderless panel refuses keyboard focus unless told otherwise; the text field needs it.
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    override func cancelOperation(_ sender: Any?) {
        onCancel?()
    }
}
