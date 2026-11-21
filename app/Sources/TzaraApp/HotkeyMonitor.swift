import CoreGraphics
import Foundation
import TzaraCore

/// Watches the keyboard, system-wide, for a tap of the Control+Option chord.
///
/// This is a listen-only event tap: it sees keystrokes but never changes or swallows them, so
/// every other shortcut keeps working. macOS only allows it once the user has granted the
/// app **Input Monitoring**, so until then the monitor asks once and then checks every couple
/// of seconds, and starts as soon as permission appears.
final class HotkeyMonitor {
    /// Called on the main thread when the chord is tapped.
    var onChordTapped: (() -> Void)?

    private var detector = ChordDetector()
    private var tap: CFMachPort?
    private var source: CFRunLoopSource?
    private var pollTimer: Timer?
    private var askedForPermission = false
    private var loggedMissingTap = false
    private var receivedModifierEvent = false

    /// Whether the keyboard is being watched right now.
    var isActive: Bool { tap != nil && hasPermission }

    /// Whether the user has granted Input Monitoring.
    var hasPermission: Bool { CGPreflightListenEventAccess() }

    func start() {
        guard tap == nil else { return }
        // Try the tap itself: macOS refuses it without permission, and that is a better test
        // than the permission query, which can answer "no" for a grant it does not yet honour.
        if installTap() { return }

        if !askedForPermission {
            askedForPermission = true
            _ = CGRequestListenEventAccess()
            FileLog.shared.info("asked for Input Monitoring permission")
        }
        schedulePolling()
    }

    func stop() {
        pollTimer?.invalidate()
        pollTimer = nil
        if let source { CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes) }
        if let tap { CFMachPortInvalidate(tap) }
        source = nil
        tap = nil
    }

    // MARK: - Permission polling

    private func schedulePolling() {
        guard pollTimer == nil else { return }
        pollTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            guard let self, self.installTap() else { return }
            self.pollTimer?.invalidate()
            self.pollTimer = nil
        }
    }

    // MARK: - The event tap

    @discardableResult
    private func installTap() -> Bool {
        guard tap == nil else { return true }

        let types: [CGEventType] = [.flagsChanged, .keyDown, .leftMouseDown, .rightMouseDown, .otherMouseDown]
        let mask = types.reduce(CGEventMask(0)) { $0 | (CGEventMask(1) << $1.rawValue) }

        let callback: CGEventTapCallBack = { _, type, event, userInfo in
            if let userInfo {
                Unmanaged<HotkeyMonitor>.fromOpaque(userInfo).takeUnretainedValue().handle(type: type, event: event)
            }
            return Unmanaged.passUnretained(event)  // listen only: never alter or swallow
        }

        guard
            let tap = CGEvent.tapCreate(
                tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly,
                eventsOfInterest: mask, callback: callback,
                userInfo: Unmanaged.passUnretained(self).toOpaque())
        else {
            if !loggedMissingTap {
                loggedMissingTap = true
                FileLog.shared.info("no keyboard event tap yet (Input Monitoring granted: \(hasPermission))")
            }
            return false
        }

        let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
        self.tap = tap
        self.source = source
        FileLog.shared.info("watching for Control+Option (Input Monitoring granted: \(hasPermission))")
        if !hasPermission { _ = CGRequestListenEventAccess() }
        return true
    }

    private func handle(type: CGEventType, event: CGEvent) {
        switch type {
        case .tapDisabledByTimeout, .tapDisabledByUserInput:
            // macOS switches a tap off if it is slow or the user intervenes; turn it back on.
            if let tap { CGEvent.tapEnable(tap: tap, enable: true) }
            FileLog.shared.info("event tap was disabled by the system; re-enabled")
        case .flagsChanged:
            if !receivedModifierEvent {
                receivedModifierEvent = true
                FileLog.shared.info("the keyboard watcher is receiving modifier keys")
            }
            let seconds = Double(event.timestamp) / 1_000_000_000
            let modifiers = ModifierSet(flags: event.flags)
            if detector.handle(.modifiersChanged(modifiers, at: seconds)) { onChordTapped?() }
        default:
            _ = detector.handle(.otherInput)
        }
    }
}
