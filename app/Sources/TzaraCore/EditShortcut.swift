import Foundation

/// The standard text-editing shortcuts: Command with X, C, V, A or Z.
///
/// On the Mac these do not belong to the text field. They belong to the *Edit menu*: macOS
/// looks for a menu item with that key, and the item acts on whatever has focus. Tzara is a
/// menu-bar app with no Edit menu, so without help Cut, Copy, Paste, Select All and Undo do
/// nothing in the chat box. The chat panel therefore recognises them itself.
public enum EditShortcut: Equatable, Sendable {
    case cut, copy, paste, selectAll, undo, redo

    /// The shortcut a key press stands for, or `nil` if it is not one of them.
    ///
    /// - Parameters:
    ///   - key: The key without modifiers applied (`charactersIgnoringModifiers`).
    ///   - command: Whether Command is held. Without it nothing matches.
    ///   - shift: Whether Shift is held. Only Command+Shift+Z means something (redo).
    ///   - option: Whether Option is held. Command+Option+V and the like are other shortcuts.
    ///   - control: Whether Control is held.
    public static func match(key: String, command: Bool, shift: Bool, option: Bool, control: Bool) -> EditShortcut? {
        guard command, !option, !control else { return nil }
        switch (key.lowercased(), shift) {
        case ("x", false): return .cut
        case ("c", false): return .copy
        case ("v", false): return .paste
        case ("a", false): return .selectAll
        case ("z", false): return .undo
        case ("z", true): return .redo
        default: return nil
        }
    }
}
