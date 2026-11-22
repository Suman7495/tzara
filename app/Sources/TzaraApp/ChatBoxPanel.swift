import AppKit
import TzaraCore

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

    /// Makes Command+V (and X, C, A, Z) work. Normally an Edit menu does that; this app has
    /// none, so the shortcut is sent to whatever has keyboard focus (the text field).
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        if let shortcut = EditShortcut.match(
            key: event.charactersIgnoringModifiers ?? "",
            command: flags.contains(.command), shift: flags.contains(.shift),
            option: flags.contains(.option), control: flags.contains(.control)),
            NSApp.sendAction(Self.action(for: shortcut), to: nil, from: self)
        {
            return true
        }
        return super.performKeyEquivalent(with: event)
    }

    private static func action(for shortcut: EditShortcut) -> Selector {
        switch shortcut {
        case .cut: return #selector(NSText.cut(_:))
        case .copy: return #selector(NSText.copy(_:))
        case .paste: return #selector(NSText.paste(_:))
        case .selectAll: return #selector(NSText.selectAll(_:))
        case .undo: return Selector(("undo:"))
        case .redo: return Selector(("redo:"))
        }
    }
}
