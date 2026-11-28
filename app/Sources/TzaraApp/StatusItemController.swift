import AppKit
import TzaraCore

/// The menu-bar icon. A menu-bar app has no Dock icon or window, so this is how you open the
/// dashboard, the bar without the hotkey, quit it, and see whether the hotkey has the permission it needs.
@MainActor
final class StatusItemController: NSObject, NSMenuDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private let hotkey: HotkeyMonitor
    private let bar: BarController
    private let dashboard: DashboardController
    private let permissionItem = NSMenuItem()
    private let askItem = NSMenuItem(title: "Ask Tzara", action: #selector(openBar), keyEquivalent: "")
    private let dashboardItem = NSMenuItem(title: "Open Dashboard\u{2026}", action: #selector(openDashboard), keyEquivalent: "")

    init(hotkey: HotkeyMonitor, bar: BarController, dashboard: DashboardController) {
        self.hotkey = hotkey
        self.bar = bar
        self.dashboard = dashboard
        super.init()

        show(.idle)
        bar.onIndicator = { [weak self] indicator in self?.show(indicator) }
        let menu = NSMenu()
        menu.delegate = self

        askItem.target = self
        dashboardItem.target = self
        menu.addItem(askItem)
        menu.addItem(dashboardItem)
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
    private func show(_ indicator: BarController.Indicator) {
        let (symbol, description, title) =
            switch indicator {
            case .idle: ("bubble.left.and.bubble.right", "Tzara", "Ask Tzara")
            case .working: ("ellipsis.bubble", "Tzara is working", "Ask Tzara (working\u{2026})")
            case .ready: ("bubble.left.and.bubble.right.fill", "Tzara has a reply", "Ask Tzara \u{2013} reply ready")
            }
        statusItem.button?.image = NSImage(systemSymbolName: symbol, accessibilityDescription: description)
        askItem.title = title
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        permissionItem.title = hotkey.isActive
            ? "Hotkey: tap \(WritePreferences().key.title)"
            : "Hotkey off \u{2013} needs Input Monitoring"
    }

    @objc private func openBar() { bar.show() }

    @objc private func openDashboard() { dashboard.show() }

    @objc private func openPrivacySettings() { SystemSettings.openInputMonitoring() }
}
