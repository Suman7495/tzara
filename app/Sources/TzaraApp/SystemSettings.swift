import AppKit

/// Shortcuts into macOS System Settings.
enum SystemSettings {
    /// Opens Privacy & Security → Input Monitoring, where the hotkey is allowed.
    static func openInputMonitoring() {
        let address = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
        if let url = URL(string: address) { NSWorkspace.shared.open(url) }
    }

    /// Opens Privacy & Security → Accessibility, where reading the app in front is allowed.
    static func openAccessibility() {
        let address = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        if let url = URL(string: address) { NSWorkspace.shared.open(url) }
    }
}
