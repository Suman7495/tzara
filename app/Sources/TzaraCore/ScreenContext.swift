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

    private enum CodingKeys: String, CodingKey { case app, window, address, selection }

    /// Most selected characters sent; the server cuts it again to what the model can take.
    public static let maximumSelection = 20_000

    public init(
        app: String = "", window: String = "", address: String = "", selection: String = "",
        copied: Bool = false
    ) {
        self.app = app
        self.window = window
        self.address = address
        self.selection = String(selection.prefix(Self.maximumSelection))
        self.copied = copied
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            app: try values.decodeIfPresent(String.self, forKey: .app) ?? "",
            window: try values.decodeIfPresent(String.self, forKey: .window) ?? "",
            address: try values.decodeIfPresent(String.self, forKey: .address) ?? "",
            selection: try values.decodeIfPresent(String.self, forKey: .selection) ?? "")
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

/// One-word actions on what the user has in front of them. Each is sent as an ordinary message.
public enum QuickAction: CaseIterable, Sendable {
    case explain, translate

    /// The single word on the button.
    public var label: String {
        switch self {
        case .explain: "Explain"
        case .translate: "Translate"
        }
    }

    /// What an action works on.
    public enum Target: Sendable {
        /// What the user has in front of them: the selection, page or document.
        case screen
        /// The assistant's last answer.
        case reply

        fileprivate var words: String { self == .screen ? "this" : "your last answer" }
    }

    /// The message the button sends. `language` is the one to translate into.
    public func prompt(translateInto language: String, on target: Target = .screen) -> String {
        let what = target.words
        switch self {
        case .explain: return target == .screen ? "Explain this." : "Explain your last answer in simpler terms."
        case .translate:
            return "Translate \(what) into \(language). If it is already in \(language), translate it into English instead."
        }
    }
}

/// Whether Tzara may look at what the user is doing when they open the box. Off until the user
/// turns it on in Settings.
public final class ScreenPreferences: @unchecked Sendable {
    static let enabledKey = "useScreenContext"
    static let translateKey = "translateInto"

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public var enabled: Bool {
        get { defaults.object(forKey: Self.enabledKey) as? Bool ?? false }
        set { defaults.set(newValue, forKey: Self.enabledKey) }
    }

    /// The language the Translate button translates into: what the user typed in Settings, or
    /// the language of the Mac.
    public var translateInto: String {
        get {
            let chosen = (defaults.string(forKey: Self.translateKey) ?? "").trimmingCharacters(in: .whitespaces)
            return chosen.isEmpty ? Self.systemLanguage : chosen
        }
        set { defaults.set(newValue, forKey: Self.translateKey) }
    }

    /// The Mac's language by its English name, such as "German".
    public static var systemLanguage: String {
        let code = Locale.current.language.languageCode?.identifier ?? "en"
        return Locale(identifier: "en").localizedString(forLanguageCode: code) ?? "English"
    }
}
