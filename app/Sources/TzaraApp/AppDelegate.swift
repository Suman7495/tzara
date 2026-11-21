import AppKit
import TzaraCore

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let hotkey = HotkeyMonitor()
    private var chatBox: ChatBoxController?
    private var statusItem: StatusItemController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let chatBox = ChatBoxController(client: ChatClient(), hotkeyIsActive: { [hotkey] in hotkey.isActive })
        self.chatBox = chatBox
        statusItem = StatusItemController(hotkey: hotkey, chatBox: chatBox)

        hotkey.onChordTapped = { chatBox.toggle() }
        hotkey.start()
        FileLog.shared.info("started; log at \(FileLog.shared.path)")

        // `--show-now` opens the box straight away, for trying it without the hotkey;
        // `--settings` opens it on the settings screen.
        let arguments = CommandLine.arguments
        if arguments.contains("--show-now") || arguments.contains("--settings") {
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

    func applicationWillTerminate(_ notification: Notification) {
        hotkey.stop()
    }
}
