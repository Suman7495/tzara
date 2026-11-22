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

    /// Most selected characters sent; the server cuts it again to what the model can take.
    public static let maximumSelection = 20_000

    public init(app: String = "", window: String = "", address: String = "", selection: String = "") {
        self.app = app
        self.window = window
        self.address = address
        self.selection = String(selection.prefix(Self.maximumSelection))
    }

    public var isEmpty: Bool {
        app.isEmpty && window.isEmpty && address.isEmpty && selection.isEmpty
    }

    /// The line shown above the input, so the user can see exactly what is being sent.
    public var summary: String {
        var parts: [String] = []
        if !app.isEmpty { parts.append(app) }
        if !window.isEmpty { parts.append(Self.cut(window, to: 40)) }
        if !selection.isEmpty {
            let count = selection.count
            parts.append("\(count) character\(count == 1 ? "" : "s") selected")
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
