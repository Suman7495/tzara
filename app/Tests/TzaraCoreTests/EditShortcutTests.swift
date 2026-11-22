import Testing

@testable import TzaraCore

@Suite("EditShortcut")
struct EditShortcutTests {
    private func match(_ key: String, command: Bool = true, shift: Bool = false, option: Bool = false, control: Bool = false)
        -> EditShortcut?
    {
        EditShortcut.match(key: key, command: command, shift: shift, option: option, control: control)
    }

    @Test func theFiveStandardShortcuts() {
        #expect(match("x") == .cut)
        #expect(match("c") == .copy)
        #expect(match("v") == .paste)
        #expect(match("a") == .selectAll)
        #expect(match("z") == .undo)
    }

    @Test func shiftZIsRedo() {
        #expect(match("z", shift: true) == .redo)
    }

    @Test func aCapitalLetterFromCapsLockIsTheSameKey() {
        #expect(match("V") == .paste)
    }

    @Test func withoutCommandNothingMatches() {
        for key in ["x", "c", "v", "a", "z"] { #expect(match(key, command: false) == nil) }
    }

    @Test func otherModifiersMeanOtherShortcuts() {
        #expect(match("v", option: true) == nil)  // Command+Option+V: paste and match style, or a hotkey
        #expect(match("c", control: true) == nil)
        #expect(match("v", shift: true) == nil)
    }

    @Test func otherKeysAreNotEditShortcuts() {
        for key in ["q", "w", "s", "r", "", "vv"] { #expect(match(key) == nil) }
    }
}
