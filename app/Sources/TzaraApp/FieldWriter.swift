import AppKit
import Carbon.HIToolbox
import TzaraCore

/// Writes for the user, in the text field of the app that was in front when the bar opened.
///
/// The field's text and the window's text are read (Accessibility), the server writes one piece of
/// text, and it is pasted into the field: a selection is replaced, a draft or instruction is
/// replaced, an empty field is filled. Return is never pressed: nothing is sent, the user reads it
/// and sends it themselves, and one Command+Z takes it back.
///
/// Pasting goes through the clipboard (restored afterwards) because it works in every app,
/// including the web and Electron ones (LinkedIn, Slack, Discord, Teams, Outlook), and because
/// pasting a line break inserts it where typing one would send the message. Before pasting it
/// checks that the same field still has the keyboard; if not, the text is left on the clipboard.
@MainActor
final class FieldWriter {
    /// What was read in the other app when a line was sent from the bar.
    struct Target: @unchecked Sendable {
        let field: ScreenReader.Field?
        let context: ScreenContext
        let visible: String
    }

    enum Outcome { case wrote, copied, failed }

    private let client: ChatClient
    private let reader = ScreenReader()

    init(client: ChatClient) { self.client = client }

    /// Reads the focused field and the window of `app`, off the main thread.
    func read(app: NSRunningApplication) async -> Target {
        let reader = self.reader
        let name = app.localizedName ?? ""
        return await Task.detached(priority: .userInitiated) {
            let field = reader.focusedField(app: app)
            let context = reader.inspect(app: app)?.context ?? ScreenContext(app: name)
            let visible = String(reader.readVisible(app: app).suffix(ScreenContext.maximumText))
            return Target(field: field, context: context, visible: visible)
        }.value
    }

    /// Has `instruction` written and puts it in the field (or on the clipboard when there is no
    /// field, or the user moved on). `status` tells the bar what is going on; `beforePaste` hides the
    /// bar so the paste goes to the field and not to it.
    func write(
        _ instruction: String, to target: Target, in app: NSRunningApplication,
        status: (String) -> Void, beforePaste: () async -> Void
    ) async -> Outcome {
        let field = target.field
        let context = target.context
        let body = WriteBody(
            app: context.app, bundle: context.bundle, window: context.window, address: context.address,
            field: field?.value ?? "", selection: field?.selection ?? "", visible: target.visible,
            instruction: instruction)
        FileLog.shared.info(
            "write: \(context.app); field \(field?.role ?? "none") \(field?.value.count ?? 0) chars, "
                + "selection \(field?.selection.count ?? 0), window text \(target.visible.count), "
                + "guidance \(instruction.count) chars")
        status("Writing\u{2026}")
        let started = Date()
        do {
            let written = try await client.write(body)
            if Task.isCancelled { return .failed }
            FileLog.shared.info(
                "write: got \(written.text.count) chars (\(written.mode), \(written.place)) in "
                    + String(format: "%.1f", Date().timeIntervalSince(started)) + " s")
            guard written.changed else {
                status("I could not apply that edit. Try saying it another way.")
                FileLog.shared.info("write: the edit changed nothing; the field is left as it was")
                return .failed
            }
            guard let field else {
                Paster.copy(written.text)
                status("No text field found: copied. Press \u{2318}V to paste.")
                FileLog.shared.info("write: no field; text copied to the clipboard")
                return .copied
            }
            // The user may have clicked elsewhere while it was written. It still goes where it was asked
            // for: that window comes forward for the moment, and the user is put back where they were.
            var returnTo: NSRunningApplication?
            if !reader.isStillFocused(field, in: app) {
                let front = NSWorkspace.shared.frontmostApplication
                returnTo = front?.processIdentifier == app.processIdentifier ? nil : front
                guard await reader.bringBack(field, in: app) else {
                    Paster.copy(written.text)
                    status("Could not get back to the field: the text is on the clipboard.")
                    FileLog.shared.info("write: could not bring the field back; text left on the clipboard")
                    return .copied
                }
                FileLog.shared.info("write: the user had moved; the field was brought back for the paste")
            }
            await beforePaste()
            await Paster.insert(written.text, replacingAll: written.replaces == "field")
            if let returnTo, !returnTo.isTerminated {
                returnTo.activate(options: [.activateIgnoringOtherApps])  // back to what they were doing
            }
            let now = reader.currentValue(of: field)
            let sample = String(written.text.prefix(24))
            let verified = now.isEmpty ? "unreadable" : (now.contains(sample) ? "yes" : "no")
            FileLog.shared.info(
                "write: pasted \(written.text.count) chars, replaced \(written.replaces); field shows it: \(verified)")
            status(written.text.isEmpty ? "Deleted. \u{2318}Z undoes it." : "Written. \u{2318}Z undoes it.")
            return .wrote
        } catch {
            if Task.isCancelled { return .failed }
            FileLog.shared.info("write: failed: \(error.localizedDescription)")
            status(Self.short(error))
            return .failed
        }
    }

    private static func short(_ error: Error) -> String {
        switch error as? ChatClientError {
        case .busy: return "Tzara is answering a message; try again."
        case .notReady: return "The model is still loading."
        case .serverNotRunning: return "Tzara's server is not running."
        default: return "Could not write that. Try again."
        }
    }
}

/// Puts text into the field that has the keyboard: through the clipboard, which is put back.
enum Paster {
    /// Puts text on the clipboard, for when it cannot be put in a field.
    @MainActor
    static func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    private static let concealed = NSPasteboard.PasteboardType("org.nspasteboard.ConcealedType")

    @MainActor
    static func insert(_ text: String, replacingAll: Bool) async {
        let board = NSPasteboard.general
        let saved = board.pasteboardItems?.map { item in
            Dictionary(uniqueKeysWithValues: item.types.compactMap { type in item.data(forType: type).map { (type, $0) } })
        } ?? []
        board.clearContents()
        board.setString(text, forType: .string)
        board.setData(Data(), forType: concealed)  // clipboard managers skip what is marked so
        let ours = board.changeCount

        if replacingAll {
            press("a")
            try? await Task.sleep(nanoseconds: 60_000_000)
        }
        // Nothing to paste (a deletion): the selection, or everything, is removed with Delete.
        if text.isEmpty { pressKey(51) } else { press("v") }
        try? await Task.sleep(nanoseconds: 350_000_000)

        guard board.changeCount == ours else { return }  // the user copied something meanwhile: leave it
        board.clearContents()
        for item in saved {
            let restored = NSPasteboardItem()
            for (type, data) in item { restored.setData(data, forType: type) }
            board.writeObjects([restored])
        }
    }

    /// Command plus a letter, to the app in front. The key is found in the current layout, so it
    /// is right on QWERTY, Dvorak and AZERTY alike.
    private static func press(_ letter: Character) {
        let code = keyCode(for: letter) ?? (letter == "v" ? 9 : 0)
        let source = CGEventSource(stateID: .hidSystemState)
        for down in [true, false] {
            let event = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: down)
            event?.flags = .maskCommand
            event?.post(tap: .cghidEventTap)
        }
    }

    private static func pressKey(_ code: CGKeyCode) {
        let source = CGEventSource(stateID: .hidSystemState)
        for down in [true, false] {
            CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: down)?.post(tap: .cghidEventTap)
        }
    }

    private static func keyCode(for letter: Character) -> CGKeyCode? {
        guard let source = TISCopyCurrentASCIICapableKeyboardLayoutInputSource()?.takeRetainedValue(),
            let raw = TISGetInputSourceProperty(source, kTISPropertyUnicodeKeyLayoutData)
        else { return nil }
        let data = unsafeBitCast(raw, to: CFData.self)
        guard let bytes = CFDataGetBytePtr(data) else { return nil }
        return bytes.withMemoryRebound(to: UCKeyboardLayout.self, capacity: 1) { layout in
            for code in 0..<128 {
                var dead: UInt32 = 0
                var length = 0
                var characters = [UniChar](repeating: 0, count: 4)
                let status = UCKeyTranslate(
                    layout, UInt16(code), UInt16(kUCKeyActionDown), 0, UInt32(LMGetKbdType()),
                    OptionBits(kUCKeyTranslateNoDeadKeysBit), &dead, 4, &length, &characters)
                if status == noErr, length > 0,
                    String(utf16CodeUnits: characters, count: length).lowercased() == String(letter)
                {
                    return CGKeyCode(code)
                }
            }
            return nil
        }
    }
}
