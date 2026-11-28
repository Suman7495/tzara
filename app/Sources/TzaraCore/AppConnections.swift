import Foundation

/// An app Tzara is connected to (or tried to connect to) through MCP, as the server reports it.
public struct ConnectedApp: Decodable, Equatable, Sendable, Identifiable {
    public var id: String { name }
    public let name: String
    public let connected: Bool
    public let error: String?
    /// The tools Tzara can use.
    public let offered: [String]
    /// Tools it holds back, each with why (usually: it might change something).
    public let hidden: [HiddenTool]

    public struct HiddenTool: Decodable, Equatable, Sendable {
        public let tool: String
        public let reason: String
    }

    /// Whether it has a Sign in button: its own login, run from Settings.
    public let signIn: Bool

    private enum CodingKeys: String, CodingKey {
        case name, connected, error, offered, hidden
        case signIn = "sign_in"
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        name = try values.decode(String.self, forKey: .name)
        connected = try values.decode(Bool.self, forKey: .connected)
        error = try values.decodeIfPresent(String.self, forKey: .error)
        offered = try values.decodeIfPresent([String].self, forKey: .offered) ?? []
        hidden = try values.decodeIfPresent([HiddenTool].self, forKey: .hidden) ?? []
        signIn = try values.decodeIfPresent(Bool.self, forKey: .signIn) ?? false
    }

    public init(
        name: String, connected: Bool, error: String? = nil, offered: [String] = [],
        hidden: [HiddenTool] = [], signIn: Bool = false
    ) {
        self.signIn = signIn
        self.name = name
        self.connected = connected
        self.error = error
        self.offered = offered
        self.hidden = hidden
    }
}

/// How signing in to an app is going: the link and code the user needs, and how it ended.
public struct SignInState: Decodable, Equatable, Sendable {
    public let running: Bool
    public let url: String
    public let code: String
    public let message: String
    public let done: Bool
    public let error: String

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        running = try values.decodeIfPresent(Bool.self, forKey: .running) ?? false
        url = try values.decodeIfPresent(String.self, forKey: .url) ?? ""
        code = try values.decodeIfPresent(String.self, forKey: .code) ?? ""
        message = try values.decodeIfPresent(String.self, forKey: .message) ?? ""
        done = try values.decodeIfPresent(Bool.self, forKey: .done) ?? false
        error = try values.decodeIfPresent(String.self, forKey: .error) ?? ""
    }

    private enum CodingKeys: String, CodingKey { case running, url, code, message, done, error }
}

/// A ready-made app that can be added with one press.
public struct AppPreset: Decodable, Equatable, Sendable, Identifiable {
    public let id: String
    /// The name the app has once added: how its row is matched with the connected app.
    public let name: String
    public let title: String
    public let description: String
    /// What to do the first time, such as signing in.
    public let firstUse: String
    /// Whether it is already connected.
    public let added: Bool
    /// What to call the access token it needs, or empty if it signs in by itself.
    public let tokenLabel: String
    /// Where the user gets that token.
    public let tokenHelp: String

    public var needsToken: Bool { !tokenLabel.isEmpty }

    private enum CodingKeys: String, CodingKey {
        case id, name, title, description, added
        case firstUse = "first_use"
        case tokenLabel = "token_label"
        case tokenHelp = "token_help"
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? id
        title = try values.decode(String.self, forKey: .title)
        description = try values.decode(String.self, forKey: .description)
        firstUse = try values.decode(String.self, forKey: .firstUse)
        added = try values.decode(Bool.self, forKey: .added)
        tokenLabel = try values.decodeIfPresent(String.self, forKey: .tokenLabel) ?? ""
        tokenHelp = try values.decodeIfPresent(String.self, forKey: .tokenHelp) ?? ""
    }

    public init(
        id: String, name: String? = nil, title: String, description: String, firstUse: String,
        added: Bool, tokenLabel: String = "", tokenHelp: String = ""
    ) {
        self.name = name ?? id
        self.tokenLabel = tokenLabel
        self.tokenHelp = tokenHelp
        self.id = id
        self.title = title
        self.description = description
        self.firstUse = firstUse
        self.added = added
    }
}

/// Everything the Apps part of the settings shows.
public struct AppsOverview: Decodable, Equatable, Sendable {
    public let apps: [ConnectedApp]
    public let presets: [AppPreset]
    /// A change is being applied: connecting to an app can take a while.
    public let reloading: Bool
    /// Problems with the list of apps itself.
    public let problems: [String]
    /// Whether apps can be added and removed here.
    public let editable: Bool

    public init(apps: [ConnectedApp], presets: [AppPreset], reloading: Bool, problems: [String], editable: Bool) {
        self.apps = apps
        self.presets = presets
        self.reloading = reloading
        self.problems = problems
        self.editable = editable
    }
}

/// What the user types to add an app of their own, and what it becomes.
public struct AppDraft: Equatable, Sendable {
    public var name = ""
    public var command = ""
    /// Arguments on one line, as typed: `-y some-package --flag "two words"`.
    public var arguments = ""
    /// One `NAME=value` per line.
    public var environment = ""
    /// Offer tools that may change or send things. Off: only tools that say they only read.
    public var allowActions = false

    public init() {}

    /// The whole start command as the app's own instructions give it: `npx -y some-package`.
    /// Reading it fills in the program and the arguments.
    public var commandLine: String {
        get { [command, arguments].filter { !$0.isEmpty }.joined(separator: " ") }
        set {
            let parts = Self.splitArguments(newValue)
            command = parts.first ?? ""
            arguments = parts.dropFirst().map { $0.contains(" ") ? "\"\($0)\"" : $0 }.joined(separator: " ")
        }
    }

    /// A name made from the package in the command (`@acme/some-mcp-server` becomes `some-mcp-server`),
    /// used when the user gave none.
    public var suggestedName: String {
        let candidate = Self.splitArguments(arguments).first { !$0.hasPrefix("-") } ?? command
        var base = String(candidate.split(separator: "/").last ?? "")
        if let at = base.dropFirst().firstIndex(of: "@") { base = String(base[..<at]) }
        let cleaned = base.filter { $0.isLetter || $0.isNumber || $0 == "-" }
        return String(cleaned.prefix(30))
    }

    /// The name used: the one typed, or the suggested one.
    public var effectiveName: String {
        let typed = name.trimmingCharacters(in: .whitespaces)
        return typed.isEmpty ? suggestedName : typed
    }

    private static let allowedName = try! NSRegularExpression(pattern: "^[A-Za-z0-9-]{1,30}$")

    /// Why this cannot be added yet, in words, or `nil` if it can.
    public var problem: String? {
        let trimmed = effectiveName
        if trimmed.isEmpty { return command.isEmpty ? "Paste the app's start command." : "Give the app a name." }
        let range = NSRange(trimmed.startIndex..., in: trimmed)
        if Self.allowedName.firstMatch(in: trimmed, range: range) == nil {
            return "The name may use letters, digits and - only (30 at most)."
        }
        if command.trimmingCharacters(in: .whitespaces).isEmpty { return "Say which program starts the app." }
        if Self.environmentPairs(environment) == nil { return "Environment lines must look like NAME=value." }
        return nil
    }

    /// The request that adds it.
    public var request: NewApp {
        NewApp(
            preset: nil, name: effectiveName,
            command: command.trimmingCharacters(in: .whitespaces),
            args: Self.splitArguments(arguments), env: Self.environmentPairs(environment) ?? [:],
            allowActions: allowActions)
    }

    /// Splits a line into arguments, keeping what is inside quotes together.
    public static func splitArguments(_ line: String) -> [String] {
        var arguments: [String] = []
        var current = ""
        var quote: Character?
        var started = false
        for character in line {
            if let open = quote {
                if character == open { quote = nil } else { current.append(character) }
            } else if character == "\"" || character == "'" {
                quote = character
                started = true
            } else if character.isWhitespace {
                if started || !current.isEmpty { arguments.append(current) }
                current = ""
                started = false
            } else {
                current.append(character)
            }
        }
        if started || !current.isEmpty { arguments.append(current) }
        return arguments
    }

    /// `NAME=value` lines as a dictionary (blank lines ignored), or `nil` if a line is not one.
    public static func environmentPairs(_ text: String) -> [String: String]? {
        var pairs: [String: String] = [:]
        for line in text.split(whereSeparator: \.isNewline) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty { continue }
            guard let equals = trimmed.firstIndex(of: "="), equals != trimmed.startIndex else { return nil }
            let name = trimmed[..<equals].trimmingCharacters(in: .whitespaces)
            if name.isEmpty { return nil }
            pairs[name] = trimmed[trimmed.index(after: equals)...].trimmingCharacters(in: .whitespaces)
        }
        return pairs
    }
}

/// The body of `POST /v1/apps`: a ready-made app by `preset`, or the parts of one.
public struct NewApp: Encodable, Equatable, Sendable {
    public let preset: String?
    public let name: String
    public let command: String
    public let args: [String]
    public let env: [String: String]
    public let allowActions: Bool
    public let token: String

    public init(
        preset: String?, name: String = "", command: String = "", args: [String] = [],
        env: [String: String] = [:], allowActions: Bool = false, token: String = ""
    ) {
        self.token = token
        self.preset = preset
        self.name = name
        self.command = command
        self.args = args
        self.env = env
        self.allowActions = allowActions
    }

    private enum CodingKeys: String, CodingKey {
        case preset, name, command, args, env, token
        case allowActions = "allow_actions"
    }
}

/// One tool of a connected app, as the Settings screen lists it with a checkbox.
public struct AppTool: Decodable, Equatable, Identifiable, Sendable {
    public var id: String { name }
    public let name: String
    public let description: String
    /// Whether the model may use it.
    public var enabled: Bool
    /// Whether it might create, change or delete something (it carries no read-only mark).
    public let changesThings: Bool
    /// How much of the reply's room it takes.
    public let chars: Int

    private enum CodingKeys: String, CodingKey {
        case name, description, enabled, chars
        case changesThings = "changes_things"
    }

    public init(name: String, description: String = "", enabled: Bool = false, changesThings: Bool = false, chars: Int = 0) {
        self.name = name
        self.description = description
        self.enabled = enabled
        self.changesThings = changesThings
        self.chars = chars
    }
}

/// An app's tools and how much room there is for them.
public struct AppToolList: Decodable, Equatable, Sendable {
    public let tools: [AppTool]
    /// The most characters the ticked tools may use together.
    public let limit: Int

    public init(tools: [AppTool], limit: Int) {
        self.tools = tools
        self.limit = limit
    }

    /// Characters used by the tools that are ticked.
    public var used: Int { tools.filter(\.enabled).reduce(0) { $0 + $1.chars } }

    /// The tools matching ``query`` (all of them if it is empty): every word must be in the name or description.
    public func matching(_ query: String) -> [AppTool] {
        let words = query.lowercased().split(whereSeparator: { !$0.isLetter && !$0.isNumber }).map(String.init)
        guard !words.isEmpty else { return tools }
        return tools.filter { tool in
            let text = (tool.name + " " + tool.description).lowercased()
            return words.allSatisfy { text.contains($0) }
        }
    }
}
