import AppKit

// A menu-bar app: no Dock icon, no main window. The hotkey opens the chat box.
// Top-level code is not on the main actor, but this is the main thread.
MainActor.assumeIsolated {
    let application = NSApplication.shared
    let delegate = AppDelegate()  // held here: NSApplication only keeps a weak reference
    application.delegate = delegate
    application.setActivationPolicy(.accessory)
    application.run()
}
