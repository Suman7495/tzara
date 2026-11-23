import AppKit
import ApplicationServices
import TzaraCore

/// Reads what the user is doing in the app in front: its name, the window's title, the page or
/// document, and the selected text.
///
/// The name of the app needs no permission. The rest comes from the Accessibility API, which the
/// user must allow (System Settings → Privacy & Security → Accessibility). Without it only the
/// name is read. Nothing is read from password managers or from password fields.
///
/// Apps expose a selection in different ways, so several are tried in turn: the selected text
/// itself, a selected range inside the element's value, a selected range of text markers (web
/// content), and the same looked for in the elements under the focused one. When the app shows
/// the user's selection in none of these, text the user has just copied in that app is used.
struct ScreenReader: Sendable {
    /// Apps whose windows are never read, even with permission.
    static let neverRead: Set<String> = [
        "com.apple.keychainaccess",
        "com.apple.Passwords",
        "com.1password.1password",
        "com.agilebits.onepassword7",
        "com.bitwarden.desktop",
    ]

    /// How long to wait for an app to answer before giving up on it, in seconds.
    static let answerTimeout: Float = 1.0

    /// Most elements and seconds spent looking for a selection under the focused element.
    static let searchLimit = 250
    static let searchSeconds = 0.25

    /// What a read of the app in front found, and how, for the log and the diagnostic.
    struct Reading: Sendable {
        var context: ScreenContext
        /// Which way found the selected text: `text`, `range`, `markers`, `search`, `copied` or `none`.
        var via = "none"
        var focusedRole = "-"
        var windowRead = false
        var allowed = true
    }

    /// Whether macOS has allowed this app to use the Accessibility API.
    static var isAllowed: Bool { AXIsProcessTrusted() }

    /// Asks macOS to show its permission prompt (once; afterwards the user must use Settings).
    static func askForPermission() {
        let options = ["AXTrustedCheckOptionPrompt": true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)
    }

    /// Asks an app to describe itself to assistive tools. Chrome and apps built on it (VS Code,
    /// Slack...) build that description only once asked, and need a few seconds to do it, so this
    /// is called as soon as the app comes to the front: by the time the user has selected
    /// something and opened the box, it is ready. Does nothing without the permission.
    func prepare(_ app: NSRunningApplication) {
        guard Self.isAllowed, Self.readable(app) else { return }
        let element = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(element, Self.answerTimeout)
        AXUIElementSetAttributeValue(element, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    }

    /// What the user has in front of them, or `nil` if the app in front is Tzara itself or one
    /// that is never read. `copiedSince` is the pasteboard count when the app came to the front.
    func read(copiedSince: Int? = nil) -> ScreenContext? {
        inspect(app: NSWorkspace.shared.frontmostApplication, copiedSince: copiedSince)?.context
    }

    private static func readable(_ app: NSRunningApplication) -> Bool {
        app.bundleIdentifier != Bundle.main.bundleIdentifier
            && !neverRead.contains(app.bundleIdentifier ?? "")
    }

    /// The full result of reading `app`, with how it was found.
    func inspect(app: NSRunningApplication?, copiedSince: Int? = nil) -> Reading? {
        guard let app, Self.readable(app) else { return nil }
        var reading = Reading(context: ScreenContext(app: app.localizedName ?? ""))
        guard Self.isAllowed else {
            reading.allowed = false
            return reading
        }

        let root = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(root, Self.answerTimeout)
        // Chrome and apps built on it only describe themselves once a client asks for it.
        AXUIElementSetAttributeValue(root, "AXManualAccessibility" as CFString, kCFBooleanTrue)

        var context = reading.context
        if let window = Self.element(root, kAXFocusedWindowAttribute) ?? Self.element(root, kAXMainWindowAttribute) {
            reading.windowRead = true
            context.window = Self.text(window, kAXTitleAttribute) ?? ""
            context.address = ScreenContext.cleanAddress(Self.text(window, kAXDocumentAttribute) ?? "")
        }
        if let focused = Self.element(root, kAXFocusedUIElementAttribute) {
            reading.focusedRole = Self.text(focused, kAXRoleAttribute) ?? "?"
            if reading.focusedRole != "AXSecureTextField", let found = selection(in: focused) {
                context.selection = String(found.text.prefix(ScreenContext.maximumSelection))
                reading.via = found.via
            }
        }
        // Teams, Outlook and others show a selection to the user but not to Accessibility. What
        // the user has just copied in the app is the next best thing.
        if context.selection.isEmpty, let copiedSince, let copied = Self.copiedText(since: copiedSince) {
            context.selection = String(copied.prefix(ScreenContext.maximumSelection))
            context.copied = true
            reading.via = "copied"
        }
        reading.context = context
        return reading
    }

    /// What a read of `app` found, in numbers and names, never the text, for `--probe-screen`.
    func report(for app: NSRunningApplication?) -> String {
        guard let app else { return "no app in front" }
        guard let found = inspect(app: app, copiedSince: nil) else {
            return "\(app.localizedName ?? "?"): not read (Tzara itself, or a protected app)"
        }
        let context = found.context
        return [
            "app: \(context.app) (\(app.bundleIdentifier ?? "?"))",
            "Accessibility allowed: \(found.allowed)",
            "window read: \(found.windowRead), title \(context.window.count) chars, address \(context.address.isEmpty ? "none" : "read")",
            "focused element: \(found.focusedRole)",
            "selection: \(context.selection.count) chars, found by: \(found.via)",
        ].joined(separator: "\n")
    }

    // MARK: - Finding the selection

    /// The selected text of `element`, trying each way an app may expose it; if the focused
    /// element is only a container, the elements under it are tried too.
    private func selection(in element: AXUIElement) -> (text: String, via: String)? {
        if let found = Self.selection(of: element) { return found }
        var queue = Self.children(of: element).map { ($0, 1) }
        let started = Date()
        var visited = 0
        while !queue.isEmpty, visited < Self.searchLimit, Date().timeIntervalSince(started) < Self.searchSeconds {
            let (candidate, depth) = queue.removeFirst()
            visited += 1
            let role = Self.text(candidate, kAXRoleAttribute) ?? ""
            // Not text fields: the address bar and search boxes would answer for the page.
            if ["AXWebArea", "AXTextArea", "AXStaticText"].contains(role),
                let found = Self.selection(of: candidate)
            {
                return (found.text, "search")
            }
            if depth < 8 { queue += Self.children(of: candidate).map { ($0, depth + 1) } }
        }
        return nil
    }

    private static func selection(of element: AXUIElement) -> (text: String, via: String)? {
        if let text = text(element, kAXSelectedTextAttribute), !text.isEmpty { return (text, "text") }
        if let value = text(element, kAXValueAttribute), let range = selectedRange(element), range.length > 0 {
            let units = Array(value.utf16)
            if range.location >= 0, range.location + range.length <= units.count {
                let slice = String(decoding: units[range.location..<(range.location + range.length)], as: UTF16.self)
                if !slice.isEmpty { return (slice, "range") }
            }
        }
        if let markers = self.value(element, "AXSelectedTextMarkerRange") {
            var result: CFTypeRef?
            let status = AXUIElementCopyParameterizedAttributeValue(
                element, "AXStringForTextMarkerRange" as CFString, markers, &result)
            if status == .success, let text = result as? String, !text.isEmpty { return (text, "markers") }
        }
        return nil
    }

    private static func selectedRange(_ element: AXUIElement) -> CFRange? {
        guard let raw = value(element, kAXSelectedTextRangeAttribute), CFGetTypeID(raw) == AXValueGetTypeID() else {
            return nil
        }
        var range = CFRange()
        return AXValueGetValue(raw as! AXValue, .cfRange, &range) ? range : nil  // swiftlint:disable:this force_cast
    }

    /// Text the user copied since `count`, if it is plain text of a sensible size.
    private static func copiedText(since count: Int) -> String? {
        let board = NSPasteboard.general
        guard board.changeCount > count, let text = board.string(forType: .string) else { return nil }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty || trimmed.count > ScreenContext.maximumSelection ? nil : trimmed
    }

    // MARK: - Accessibility helpers

    private static func value(_ element: AXUIElement, _ attribute: String) -> CFTypeRef? {
        var result: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(element, attribute as CFString, &result)
        return status == .success ? result : nil
    }

    private static func element(_ element: AXUIElement, _ attribute: String) -> AXUIElement? {
        guard let found = value(element, attribute), CFGetTypeID(found) == AXUIElementGetTypeID() else {
            return nil
        }
        return (found as! AXUIElement)  // swiftlint:disable:this force_cast
    }

    private static func children(of element: AXUIElement) -> [AXUIElement] {
        value(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
    }

    private static func text(_ element: AXUIElement, _ attribute: String) -> String? {
        value(element, attribute) as? String
    }
}
