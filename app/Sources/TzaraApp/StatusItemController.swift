import AppKit

/// The menu-bar icon. A menu-bar app has no Dock icon or window, so this is how you quit it,
/// open the chat without the hotkey, and see whether the hotkey has the permission it needs.
@MainActor
final class StatusItemController: NSObject, NSMenuDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private let hotkey: HotkeyMonitor
    private let chatBox: ChatBoxController
    private let permissionItem = NSMenuItem()

    init(hotkey: HotkeyMonitor, chatBox: ChatBoxController) {
        self.hotkey = hotkey
        self.chatBox = chatBox
        super.init()

        statusItem.button?.image = NSImage(
            systemSymbolName: "bubble.left.and.bubble.right", accessibilityDescription: "Tzara")
        let menu = NSMenu()
        menu.delegate = self

        menu.addItem(withTitle: "Open Tzara", action: #selector(openChat), keyEquivalent: "").target = self
        menu.addItem(.separator())
        permissionItem.isEnabled = false
        menu.addItem(permissionItem)
        let grant = menu.addItem(
            withTitle: "Open Input Monitoring Settings\u{2026}", action: #selector(openPrivacySettings), keyEquivalent: "")
        grant.target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit Tzara", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        statusItem.menu = menu
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        permissionItem.title = hotkey.isActive
            ? "Hotkey: tap Control+Option"
            : "Hotkey off \u{2013} needs Input Monitoring"
    }

    @objc private func openChat() { chatBox.show() }

    @objc private func openPrivacySettings() { SystemSettings.openInputMonitoring() }
}
