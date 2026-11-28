import Foundation

/// What the user was looking at when they opened the chat box: the app, the window, the page or
/// document, and the text they had selected. It is sent with the next message, so that "translate
/// this" has something to translate.
public struct ScreenContext: Codable, Equatable, Sendable {
    public var app: String
    public var window: String
    /// A page address (without its query and fragment) or a document's path.
    public var address: String
    public var selection: String
    /// The text was copied by the user in that app, because the app did not show its selection.
    /// Not sent: it only changes how the box describes it.
    public var copied: Bool
    /// The application's identifier, such as `com.apple.Safari`.
    public var bundle: String
    /// What the user is working in inside the window, such as `text area "Message body"`.
    public var focus: String
    /// The text of that field around the cursor: a draft, a message, a line of code. Read when a
    /// message is sent; the server gives it to the model only if the message is about it.
    public var typing: String
    /// The text visible in the window, in reading order. Read when a message is sent.
    public var visible: String

    private enum CodingKeys: String, CodingKey {
        case app, window, address, selection, bundle, focus, typing, visible
    }

    /// Two contexts are the same subject when they are in the same place with the same selection.
    /// What is typed or visible changes all the time and must not make the box flicker.
    public static func == (left: ScreenContext, right: ScreenContext) -> Bool {
        left.app == right.app && left.window == right.window && left.address == right.address
            && left.selection == right.selection && left.copied == right.copied
            && left.bundle == right.bundle && left.focus == right.focus
    }

    /// Most selected characters sent; the server cuts it again to what the model can take.
    public static let maximumSelection = 20_000

    /// Most characters of typed or visible text sent; the server cuts it again.
    public static let maximumText = 8_000

    public init(
        app: String = "", window: String = "", address: String = "", selection: String = "",
        copied: Bool = false, bundle: String = "", focus: String = "", typing: String = "",
        visible: String = ""
    ) {
        self.app = app
        self.window = window
        self.address = address
        self.selection = String(selection.prefix(Self.maximumSelection))
        self.copied = copied
        self.bundle = bundle
        self.focus = focus
        self.typing = String(typing.prefix(Self.maximumText))
        self.visible = String(visible.prefix(Self.maximumText))
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            app: try values.decodeIfPresent(String.self, forKey: .app) ?? "",
            window: try values.decodeIfPresent(String.self, forKey: .window) ?? "",
            address: try values.decodeIfPresent(String.self, forKey: .address) ?? "",
            selection: try values.decodeIfPresent(String.self, forKey: .selection) ?? "",
            bundle: try values.decodeIfPresent(String.self, forKey: .bundle) ?? "",
            focus: try values.decodeIfPresent(String.self, forKey: .focus) ?? "",
            typing: try values.decodeIfPresent(String.self, forKey: .typing) ?? "",
            visible: try values.decodeIfPresent(String.self, forKey: .visible) ?? "")
    }

    public var isEmpty: Bool {
        app.isEmpty && window.isEmpty && address.isEmpty && selection.isEmpty
    }

    /// Whether there is something for an action to work on: a selection, or a page or document.
    public var actionable: Bool { !selection.isEmpty || !address.isEmpty }

    /// The line shown above the input, so the user can see exactly what is being sent.
    public var summary: String {
        var parts: [String] = []
        if !app.isEmpty { parts.append(app) }
        if !window.isEmpty { parts.append(Self.cut(window, to: 40)) }
        if !selection.isEmpty {
            let count = selection.count
            parts.append("\(count) character\(count == 1 ? "" : "s") \(copied ? "copied" : "selected")")
        }
        return parts.joined(separator: " \u{00B7} ")
    }

    /// An address as the model should see it: no query or fragment (they often hold tokens and
    /// tracking), and a file's path rather than its `file://` form.
    public static func cleanAddress(_ raw: String) -> String {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: trimmed), let scheme = url.scheme?.lowercased() else { return trimmed }
        if scheme == "file" { return url.path }
        guard scheme == "http" || scheme == "https", let host = url.host else { return trimmed }
        return "\(scheme)://\(host)\(url.path)"
    }

    private static func cut(_ text: String, to length: Int) -> String {
        text.count > length ? String(text.prefix(length)) + "\u{2026}" : text
    }
}

/// Keeps the chip in step with the app in front while the box is open.
///
/// What the user removes is not put back until it changes. Sending a message does not remove
/// it: the chip always shows what Tzara is aware of, and it goes with every message.
public struct ScreenFollow: Equatable, Sendable {
    /// What would be sent with the next message.
    public private(set) var current: ScreenContext?
    private var dismissed: ScreenContext?

    public init() {}

    /// The box has just opened: start again from what was read.
    public mutating func start(with context: ScreenContext?) {
        dismissed = nil
        current = context.flatMap { $0.isEmpty ? nil : $0 }
    }

    /// A newer reading of the app in front. Nothing changes when nothing could be read or it is
    /// what the user has already removed or sent. Returns whether the chip changed.
    @discardableResult
    public mutating func follow(_ context: ScreenContext?) -> Bool {
        guard let context, !context.isEmpty, context != dismissed, context != current else { return false }
        current = context
        return true
    }

    /// The user removed it.
    public mutating func drop() {
        dismissed = current
        current = nil
    }

}

/// Whether Tzara may look at what the user is doing when they open the box. Off until the user
/// turns it on in Settings.
public final class ScreenPreferences: @unchecked Sendable {
    static let enabledKey = "useScreenContext"

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public var enabled: Bool {
        get { defaults.object(forKey: Self.enabledKey) as? Bool ?? false }
        set { defaults.set(newValue, forKey: Self.enabledKey) }
    }
}
