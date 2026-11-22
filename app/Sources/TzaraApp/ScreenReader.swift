import AppKit
import ApplicationServices
import TzaraCore

/// Reads what the user is doing in the app in front: its name, the window's title, the page or
/// document, and the selected text. Called when the chat box opens. The box is a non-activating
/// panel, so at that moment the app the user was in is still the frontmost one.
///
/// The name of the app needs no permission. The rest comes from the Accessibility API, which the
/// user must allow (System Settings → Privacy & Security → Accessibility). Without it only the
/// name is read. Nothing is read from password managers or from password fields.
@MainActor
struct ScreenReader {
    /// Apps whose windows are never read, even with permission.
    static let neverRead: Set<String> = [
        "com.apple.keychainaccess",
        "com.apple.Passwords",
        "com.1password.1password",
        "com.agilebits.onepassword7",
        "com.bitwarden.desktop",
    ]

    /// How long to wait for an app to answer before giving up on it, in seconds.
    static let answerTimeout: Float = 0.3

    /// Whether macOS has allowed this app to use the Accessibility API.
    static var isAllowed: Bool { AXIsProcessTrusted() }

    /// Asks macOS to show its permission prompt (once; afterwards the user must use Settings).
    static func askForPermission() {
        let options = ["AXTrustedCheckOptionPrompt": true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)
    }

    /// What the user has in front of them, or `nil` if the app in front is Tzara itself or one
    /// that is never read.
    func read() -> ScreenContext? {
        guard let app = NSWorkspace.shared.frontmostApplication,
            app.bundleIdentifier != Bundle.main.bundleIdentifier,
            !Self.neverRead.contains(app.bundleIdentifier ?? "")
        else { return nil }

        var context = ScreenContext(app: app.localizedName ?? "")
        guard Self.isAllowed else { return context }

        let element = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(element, Self.answerTimeout)
        // Chrome and apps built on it only describe themselves once a client asks for it.
        AXUIElementSetAttributeValue(element, "AXManualAccessibility" as CFString, kCFBooleanTrue)

        if let window = Self.element(element, kAXFocusedWindowAttribute) {
            context.window = Self.text(window, kAXTitleAttribute) ?? ""
            context.address = ScreenContext.cleanAddress(Self.text(window, kAXDocumentAttribute) ?? "")
        }
        if let focused = Self.element(element, kAXFocusedUIElementAttribute),
            Self.text(focused, kAXRoleAttribute) != "AXSecureTextField"
        {
            context = ScreenContext(
                app: context.app, window: context.window, address: context.address,
                selection: Self.text(focused, kAXSelectedTextAttribute) ?? "")
        }
        return context
    }

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

    private static func text(_ element: AXUIElement, _ attribute: String) -> String? {
        value(element, attribute) as? String
    }
}
