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
        var reading = Reading(
            context: ScreenContext(app: app.localizedName ?? "", bundle: app.bundleIdentifier ?? ""))
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
            if reading.focusedRole != "AXSecureTextField" {
                context.focus = Self.describe(focused, role: reading.focusedRole)
                context.typing = Self.typedText(in: focused, role: reading.focusedRole)
                if let found = selection(in: focused) {
                    context.selection = String(found.text.prefix(ScreenContext.maximumSelection))
                    reading.via = found.via
                }
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

    // MARK: - The focused field

    /// How a role is said in words.
    private static let roleNames: [String: String] = [
        "AXTextArea": "text area", "AXTextField": "text field", "AXComboBox": "combo box",
        "AXWebArea": "web page", "AXTable": "table", "AXOutline": "outline", "AXList": "list",
        "AXGroup": "group", "AXScrollArea": "scroll area", "AXSplitGroup": "split view",
    ]

    /// The focused element in words: `text area "Message body"`.
    private static func describe(_ element: AXUIElement, role: String) -> String {
        let name = roleNames[role] ?? role.replacingOccurrences(of: "AX", with: "").lowercased()
        let label = [kAXTitleAttribute, kAXDescriptionAttribute, "AXPlaceholderValue", kAXHelpAttribute]
            .lazy.compactMap { text(element, $0) }.first { !$0.isEmpty }
        guard let label else { return name }
        return "\(name) \"\(label.prefix(60))\""
    }

    /// Most characters read on each side of the cursor.
    static let typingRadius = 750

    /// The text of a text field or area around the cursor (or its start, when there is no cursor):
    /// what the user is writing. Empty for anything that is not a text element, and never for a
    /// password field.
    private static func typedText(in element: AXUIElement, role: String) -> String {
        guard ["AXTextArea", "AXTextField", "AXComboBox"].contains(role),
            let value = text(element, kAXValueAttribute), !value.isEmpty
        else { return "" }
        let units = Array(value.utf16)
        guard let range = selectedRange(element), range.location >= 0, range.location <= units.count else {
            return String(value.prefix(typingRadius * 2))
        }
        let start = max(0, range.location - typingRadius)
        let end = min(units.count, range.location + range.length + typingRadius)
        return String(decoding: units[start..<end], as: UTF16.self)
    }

    // MARK: - The text visible in the window

    /// Roles whose contents are chrome, not what the user is reading.
    private static let skippedRoles: Set<String> = [
        "AXToolbar", "AXMenuBar", "AXMenu", "AXMenuItem", "AXMenuBarItem", "AXScrollBar",
        "AXSecureTextField", "AXSegmentedControl", "AXPopUpButton", "AXButton", "AXImage",
    ]
    /// Roles that hold text a person reads.
    private static let textRoles: Set<String> = [
        "AXStaticText", "AXTextArea", "AXTextField", "AXHeading", "AXLink", "AXCell",
    ]
    /// Lists and tables: a few rows are enough, and the selected one matters most.
    private static let listRoles: Set<String> = ["AXTable", "AXOutline", "AXList"]

    static let visibleNodes = 1500
    static let visibleSeconds = 0.4
    static let listRows = 12
    static let textPerElement = 200

    /// The text visible in the window of `app`, in reading order, for a message about it. Walks
    /// the window's accessibility tree, leaving out toolbars and menus, keeping a few rows of
    /// each list (the selected row first), and stopping at a size and a time limit, because some
    /// apps (Outlook) list thousands of rows. Apps that show nothing to Accessibility (Teams)
    /// give nothing.
    func readVisible(app: NSRunningApplication?) -> String {
        guard let app, Self.isAllowed, Self.readable(app) else { return "" }
        let root = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(root, Self.answerTimeout)
        guard let window = Self.element(root, kAXFocusedWindowAttribute) ?? Self.element(root, kAXMainWindowAttribute)
        else { return "" }
        var collector = VisibleText(limit: ScreenContext.maximumText)
        Self.collect(window, into: &collector, started: Date())
        return collector.text
    }

    /// Collects text depth first, which follows the order a person reads in.
    private static func collect(_ element: AXUIElement, into collector: inout VisibleText, started: Date) {
        guard collector.visited < visibleNodes, !collector.isFull,
            Date().timeIntervalSince(started) < visibleSeconds
        else { return }
        collector.visited += 1
        let role = text(element, kAXRoleAttribute) ?? ""
        if skippedRoles.contains(role) { return }
        if textRoles.contains(role) {
            let found = text(element, kAXValueAttribute) ?? text(element, kAXTitleAttribute)
                ?? text(element, kAXDescriptionAttribute) ?? ""
            collector.add(found, cut: textPerElement)
            if role != "AXCell" { return }  // a cell can hold more text
        }
        var children = children(of: element)
        if listRoles.contains(role) {
            let (chosen, others) = (children.filter(isSelected), children.filter { !isSelected($0) })
            children = chosen + others.prefix(max(0, listRows - chosen.count))
        }
        for child in children { collect(child, into: &collector, started: started) }
    }

    private static func isSelected(_ element: AXUIElement) -> Bool {
        (value(element, kAXSelectedAttribute) as? Bool) == true
    }

    /// Everything a read of the window gives: what `read` gives, plus the window's text.
    func readFull(copiedSince: Int? = nil) -> ScreenContext? {
        let app = NSWorkspace.shared.frontmostApplication
        guard var context = inspect(app: app, copiedSince: copiedSince)?.context else { return nil }
        context.visible = String(readVisible(app: app).prefix(ScreenContext.maximumText))
        return context
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
            "focus: \(context.focus.isEmpty ? "none" : context.focus), typed text \(context.typing.count) chars",
            "visible window text: \(readVisible(app: app).count) chars",
        ].joined(separator: "\n")
    }

    // MARK: - The field to write in

    /// The text field in `app` that has the keyboard, for writing into it.
    struct Field: @unchecked Sendable {
        let element: AXUIElement
        let role: String
        /// All the text in the field, and the part of it that is selected.
        let value: String
        let selection: String
        /// Where it is on the screen (top-left origin), if the app says.
        let frame: CGRect?
    }

    /// Roles that are text fields.
    private static let fieldRoles: Set<String> = ["AXTextArea", "AXTextField", "AXComboBox"]
    private static let containerRoles: Set<String> = ["AXGroup", "AXWebArea", "AXStaticText", "AXUnknown"]

    private static func isSettable(_ element: AXUIElement, _ attribute: String) -> Bool {
        var settable: DarwinBoolean = false
        return AXUIElementIsAttributeSettable(element, attribute as CFString, &settable) == .success
            && settable.boolValue
    }

    /// The focused element of `app` if it is a text field that may be written in: not a password
    /// field, not in a password manager, and one that takes text (a role of a text field, or an
    /// element with a text cursor, which is what web and Electron composers expose).
    func focusedField(app: NSRunningApplication?) -> Field? {
        guard let app, Self.isAllowed, Self.readable(app) else { return nil }
        let root = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(root, Self.answerTimeout)
        AXUIElementSetAttributeValue(root, "AXManualAccessibility" as CFString, kCFBooleanTrue)
        guard let focused = Self.element(root, kAXFocusedUIElementAttribute) else { return nil }
        let role = Self.text(focused, kAXRoleAttribute) ?? ""
        guard role != "AXSecureTextField", Self.text(focused, kAXSubroleAttribute) != "AXSecureTextField" else {
            return nil
        }
        // A text field, or the container a web or Electron composer shows as (it has a text cursor and
        // takes a new value). Never a checkbox, button or the like: pasting there does nothing useful.
        let takesText = Self.selectedRange(focused) != nil && Self.isSettable(focused, kAXValueAttribute)
        guard Self.fieldRoles.contains(role) || (Self.containerRoles.contains(role) && takesText) else {
            return nil
        }
        let value = Self.text(focused, kAXValueAttribute) ?? ""
        let selected = Self.selection(of: focused)?.text ?? ""
        return Field(
            element: focused, role: role, value: value,
            selection: String(selected.prefix(ScreenContext.maximumText)), frame: Self.frame(of: focused))
    }

    /// Whether `field` is still the element that has the keyboard in `app`.
    func isStillFocused(_ field: Field, in app: NSRunningApplication?) -> Bool {
        guard let app, app.isActive else { return false }
        let root = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(root, Self.answerTimeout)
        guard let now = Self.element(root, kAXFocusedUIElementAttribute) else { return false }
        return CFEqual(now, field.element)
    }

    /// Puts the user back in `field`: brings its app and window forward and gives it the keyboard.
    /// For a write that finishes after the user clicked elsewhere, which must still land in the
    /// window it was asked for. Returns whether a text field of that window has the keyboard again.
    func bringBack(_ field: Field, in app: NSRunningApplication) async -> Bool {
        let root = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(root, Self.answerTimeout)
        let window = Self.element(field.element, kAXWindowAttribute)
        for attempt in 0..<12 {  // about 1.5 seconds
            app.activate(options: [.activateIgnoringOtherApps])
            AXUIElementSetAttributeValue(root, kAXFrontmostAttribute as CFString, kCFBooleanTrue)
            if let window {
                AXUIElementSetAttributeValue(window, kAXMainAttribute as CFString, kCFBooleanTrue)
                AXUIElementPerformAction(window, kAXRaiseAction as CFString)
            }
            AXUIElementSetAttributeValue(field.element, kAXFocusedAttribute as CFString, kCFBooleanTrue)
            try? await Task.sleep(nanoseconds: 125_000_000)
            if isUsable(field, in: app, window: window) { return true }
            if attempt == 11 { FileLog.shared.info("write: bring back failed: \(describeFocus(in: app))") }
        }
        return false
    }

    /// The same field has the keyboard, or another text field of the same window (a web view makes a
    /// new element each time it is looked at): either way the paste goes where the user was writing.
    private func isUsable(_ field: Field, in app: NSRunningApplication, window: AXUIElement?) -> Bool {
        guard app.isActive else { return false }
        if isStillFocused(field, in: app) { return true }
        let root = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(root, Self.answerTimeout)
        guard let now = Self.element(root, kAXFocusedUIElementAttribute),
            let role = Self.text(now, kAXRoleAttribute),
            Self.fieldRoles.contains(role) || Self.containerRoles.contains(role),
            let window, let nowWindow = Self.element(now, kAXWindowAttribute)
        else { return false }
        return CFEqual(window, nowWindow)
    }

    /// What has the keyboard in `app`, in words with no text, for the log.
    private func describeFocus(in app: NSRunningApplication) -> String {
        let root = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(root, Self.answerTimeout)
        let role = Self.element(root, kAXFocusedUIElementAttribute).flatMap { Self.text($0, kAXRoleAttribute) } ?? "nothing"
        return "app active \(app.isActive), focused element \(role)"
    }

    /// The text of `field` now (for checking what was written), or empty.
    func currentValue(of field: Field) -> String {
        Self.text(field.element, kAXValueAttribute) ?? ""
    }

    private static func frame(of element: AXUIElement) -> CGRect? {
        guard let position = value(element, kAXPositionAttribute), let size = value(element, kAXSizeAttribute),
            CFGetTypeID(position) == AXValueGetTypeID(), CFGetTypeID(size) == AXValueGetTypeID()
        else { return nil }
        var point = CGPoint.zero
        var extent = CGSize.zero
        guard AXValueGetValue(position as! AXValue, .cgPoint, &point),  // swiftlint:disable:this force_cast
            AXValueGetValue(size as! AXValue, .cgSize, &extent)  // swiftlint:disable:this force_cast
        else { return nil }
        return CGRect(origin: point, size: extent)
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
