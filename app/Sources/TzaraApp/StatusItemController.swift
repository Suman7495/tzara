import AppKit

/// The menu-bar icon. A menu-bar app has no Dock icon or window, so this is how you quit it,
/// open the chat without the hotkey, and see whether the hotkey has the permission it needs.
@MainActor
final class StatusItemController: NSObject, NSMenuDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private let hotkey: HotkeyMonitor
    private let chatBox: ChatBoxController
    private let permissionItem = NSMenuItem()
    private let openItem = NSMenuItem(title: "Open Tzara", action: #selector(openChat), keyEquivalent: "")

    init(hotkey: HotkeyMonitor, chatBox: ChatBoxController) {
        self.hotkey = hotkey
        self.chatBox = chatBox
        super.init()

        show(.idle)
        chatBox.onIndicator = { [weak self] indicator in self?.show(indicator) }
        let menu = NSMenu()
        menu.delegate = self

        openItem.target = self
        menu.addItem(openItem)
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

    /// Changes the icon: busy while a reply is written, marked when one is waiting to be read.
    private func show(_ indicator: ChatBoxController.Indicator) {
        let (symbol, description, title) =
            switch indicator {
            case .idle: ("bubble.left.and.bubble.right", "Tzara", "Open Tzara")
            case .working: ("ellipsis.bubble", "Tzara is working", "Open Tzara (working\u{2026})")
            case .ready: ("bubble.left.and.bubble.right.fill", "Tzara has a reply", "Open Tzara \u{2013} reply ready")
            }
        statusItem.button?.image = NSImage(systemSymbolName: symbol, accessibilityDescription: description)
        openItem.title = title
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        permissionItem.title = hotkey.isActive
            ? "Hotkey: tap Control+Option"
            : "Hotkey off \u{2013} needs Input Monitoring"
    }

    @objc private func openChat() { chatBox.show() }

    @objc private func openPrivacySettings() { SystemSettings.openInputMonitoring() }
}
