import AppKit
import TzaraCore

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let hotkey = HotkeyMonitor()
    private var bar: BarController?
    private var dashboard: DashboardController?
    private var statusItem: StatusItemController?
    private var autoUpdater: AutoUpdater?
    private let writePreferences = WritePreferences()

    func applicationDidFinishLaunching(_ notification: Notification) {
        let bar = BarController(client: ChatClient())
        let dashboard = DashboardController(model: bar.model, hotkeyIsActive: { [hotkey] in hotkey.isActive })
        self.bar = bar
        self.dashboard = dashboard
        statusItem = StatusItemController(hotkey: hotkey, bar: bar, dashboard: dashboard)

        // Tapping or holding the key opens or closes the small bar, beside the pointer.
        hotkey.onWriteKey = { [writePreferences] gesture in
            guard writePreferences.enabled else { return }
            FileLog.shared.info("write key: \(gesture)")
            bar.toggle()
        }
        hotkey.writeKey = writePreferences.enabled ? writePreferences.key : nil
        NotificationCenter.default.addObserver(
            forName: .writeSettingsChanged, object: nil, queue: .main
        ) { [hotkey, writePreferences] _ in
            MainActor.assumeIsolated {
                hotkey.writeKey = writePreferences.enabled ? writePreferences.key : nil
            }
        }
        hotkey.start()
        FileLog.shared.info("started; log at \(FileLog.shared.path)")

        let version = AppVersion(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")
        // Only an installed copy updates itself: one run from the build folder is a developer's.
        let installed = InstalledCopy.isInstalled
        if let version, installed, !CommandLine.arguments.contains("--snapshot") {
            autoUpdater = AutoUpdater(currentVersion: version, isBusy: { bar.isOpen || bar.isWorking || dashboard.isOpen })
            autoUpdater?.start()
        }

        // The only thing a key opens is the small bar. The dashboard (conversation, settings, memory,
        // apps) opens from the menu-bar icon, or here for `--settings`; `--show` opens the bar, which
        // the demo and snapshot flags also need.
        let arguments = CommandLine.arguments
        let inBackground = arguments.contains("--background")
        let showsDashboard = arguments.contains("--settings")
        let showsBar = ["--show", "--demo-screen", "--demo-choices", "--bar-ask"].contains(where: arguments.contains)
            || (arguments.contains("--snapshot") && !showsDashboard)
        if !inBackground, showsDashboard {
            // `--page memory|apps|docs|general|context|about` opens that page, for a snapshot.
            let name = arguments.firstIndex(of: "--page").flatMap { $0 + 1 < arguments.count ? arguments[$0 + 1] : nil }
            let page: DashboardPage =
                switch name {
                case "memory": .memory
                case "apps": .apps
                case "docs": .docs
                default: name.flatMap(SettingsPage.init(rawValue:)).map { .settings($0) } ?? .chat
                }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { dashboard.show(page) }
        }
        if !inBackground, showsBar {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { bar.show() }
        }
        // `--demo-screen` shows a made-up screen context, to check how the chip and buttons look.
        if arguments.contains("--demo-screen") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { bar.attachDemoContext() }
        }
        // `--probe-screen SECONDS` waits, then says in numbers (never the text) what Tzara reads in
        // the app that is in front, and quits: switch to the app, select something, and look.
        if let flag = arguments.firstIndex(of: "--probe-screen") {
            let seconds = flag + 1 < arguments.count ? Double(arguments[flag + 1]) ?? 8 : 8
            DispatchQueue.main.asyncAfter(deadline: .now() + seconds) {
                print(ScreenReader().report(for: NSWorkspace.shared.frontmostApplication))
                NSApplication.shared.terminate(nil)
            }
        }
        // `--ask "text"` sends a message from the dashboard's chat a moment after launch, to look at how
        // the reply is laid out (use with `--settings --snapshot FILE --snapshot-after SECONDS`).
        if let flag = arguments.firstIndex(of: "--ask"), flag + 1 < arguments.count {
            let text = arguments[flag + 1]
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { bar.model.ask(text) }
        }
        // `--bar-ask "text"` sends a message from the bar after it opens (use with `--show --snapshot ...`).
        if let flag = arguments.firstIndex(of: "--bar-ask"), flag + 1 < arguments.count {
            let text = arguments[flag + 1]
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { bar.ask(text) }
        }
        // `--bar-hide-after SECONDS` puts the bar away, as a click elsewhere does, to see it pop up with the answer.
        if let flag = arguments.firstIndex(of: "--bar-hide-after"), flag + 1 < arguments.count,
            let seconds = Double(arguments[flag + 1])
        {
            DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { bar.hide() }
        }
        // `--demo-choices` shows a reply that asks "Search online?" with its buttons.
        if arguments.contains("--demo-choices") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { bar.showDemoChoices() }
        }
        // `--snapshot FILE` draws the bar (or, with --settings, the dashboard) into a PNG after it has loaded, then quits. Add
        // `--snapshot-after SECONDS` to wait longer, for things that arrive late.
        if let flag = arguments.firstIndex(of: "--snapshot"), flag + 1 < arguments.count {
            let path = arguments[flag + 1]
            var wait = 3.5
            if let after = arguments.firstIndex(of: "--snapshot-after"), after + 1 < arguments.count,
                let seconds = Double(arguments[after + 1])
            {
                wait = seconds
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + wait) {
                if showsDashboard { dashboard.snapshot(to: path) } else { bar.snapshot(to: path) }
                NSApplication.shared.terminate(nil)
            }
        }
    }

    /// Opening the app again while it runs (Finder, Spotlight, `open`) shows the dashboard.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        dashboard?.show()
        return false
    }

    func applicationWillTerminate(_ notification: Notification) {
        hotkey.stop()
    }
}
