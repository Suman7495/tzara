import AppKit
import TzaraCore

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let hotkey = HotkeyMonitor()
    private var chatBox: ChatBoxController?
    private var statusItem: StatusItemController?
    private var autoUpdater: AutoUpdater?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let chatBox = ChatBoxController(client: ChatClient(), hotkeyIsActive: { [hotkey] in hotkey.isActive })
        self.chatBox = chatBox
        statusItem = StatusItemController(hotkey: hotkey, chatBox: chatBox)

        hotkey.onChordTapped = {
            FileLog.shared.info("Control+Option tapped")
            chatBox.toggle()
        }
        hotkey.start()
        FileLog.shared.info("started; log at \(FileLog.shared.path)")

        let version = AppVersion(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")
        // Only an installed copy updates itself: one run from the build folder is a developer's.
        let installed = Bundle.main.bundlePath.contains("/Applications/")
        if let version, installed, !CommandLine.arguments.contains("--snapshot") {
            autoUpdater = AutoUpdater(currentVersion: version, isBusy: { chatBox.isOpen })
            autoUpdater?.start()
        }

        // Opening the app by hand shows the box, so there is something to click. The login job
        // passes `--background` to start quietly instead. `--settings` opens the settings screen.
        let arguments = CommandLine.arguments
        if !arguments.contains("--background") {
            let settings = arguments.contains("--settings")
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { chatBox.show(settings: settings) }
        }
        // `--snapshot FILE` draws the box into a PNG after it has loaded, then quits.
        if let flag = arguments.firstIndex(of: "--snapshot"), flag + 1 < arguments.count {
            let path = arguments[flag + 1]
            DispatchQueue.main.asyncAfter(deadline: .now() + 3.5) {
                chatBox.snapshot(to: path)
                NSApplication.shared.terminate(nil)
            }
        }
    }

    /// Opening the app again while it runs (Finder, Spotlight, `open`) shows the box.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        chatBox?.show(settings: false)
        return false
    }

    func applicationWillTerminate(_ notification: Notification) {
        hotkey.stop()
    }
}
