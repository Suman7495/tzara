import Foundation

/// The server's own account of how it is doing.
public struct ServerStatus: Decodable, Equatable, Sendable {
    /// `loading`, `ready` or `failed`.
    public let state: String
    public let model: String
    /// What the assistant calls itself.
    public let name: String
    public let busy: Bool
    public let error: String?

    public init(state: String, model: String, name: String = "Tzara", busy: Bool, error: String?) {
        self.state = state
        self.model = model
        self.name = name
        self.busy = busy
        self.error = error
    }

    private enum CodingKeys: String, CodingKey { case state, model, name, busy, error }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        state = try values.decode(String.self, forKey: .state)
        model = try values.decode(String.self, forKey: .model)
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? "Tzara"
        busy = try values.decode(Bool.self, forKey: .busy)
        error = try values.decodeIfPresent(String.self, forKey: .error)
    }
}

/// What a model still needs before it can be used.
public enum ModelNeed: String, Decodable, Sendable {
    /// Nothing: it can be chosen now.
    case ready
    /// A local model that has to be downloaded first.
    case download
    /// A cloud model that needs the user's API key.
    case key
    /// A local model, but Ollama itself is not running.
    case ollama
}

/// One model the server can use, written `provider:model-name`.
public struct ModelChoice: Decodable, Equatable, Identifiable, Sendable {
    public let id: String
    public let description: String
    public let need: ModelNeed

    public init(id: String, description: String, need: ModelNeed = .ready) {
        self.id = id
        self.description = description
        self.need = need
    }

    private enum CodingKeys: String, CodingKey { case id, description, need }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        description = try values.decode(String.self, forKey: .description)
        need = try values.decodeIfPresent(ModelNeed.self, forKey: .need) ?? .ready
    }

    /// The name without its provider, for a menu that is already grouped by provider:
    /// `qwen3:8b` for `ollama:qwen3:8b`.
    public var shortName: String {
        id.split(separator: ":", maxSplits: 1).last.map(String.init) ?? id
    }

    /// The provider part: `ollama` for `ollama:qwen3:8b`.
    public var provider: String {
        id.split(separator: ":", maxSplits: 1).first.map(String.init) ?? ""
    }
}

/// How far the download of a local model has got.
public struct ModelDownload: Decodable, Equatable, Sendable {
    public let model: String
    /// 0 to 1, or `nil` until Ollama has said.
    public let fraction: Double?
    public let error: String?
    public let finished: Bool

    public init(model: String, fraction: Double? = nil, error: String? = nil, finished: Bool = false) {
        self.model = model
        self.fraction = fraction
        self.error = error
        self.finished = finished
    }
}

/// The models, and the downloads under way.
public struct ModelOverview: Decodable, Equatable, Sendable {
    public let providers: [ModelProvider]
    public let downloads: [ModelDownload]

    public init(providers: [ModelProvider], downloads: [ModelDownload] = []) {
        self.providers = providers
        self.downloads = downloads
    }

    private enum CodingKeys: String, CodingKey { case providers, downloads }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        providers = try values.decode([ModelProvider].self, forKey: .providers)
        downloads = try values.decodeIfPresent([ModelDownload].self, forKey: .downloads) ?? []
    }
}

/// The models of one provider (Ollama, Anthropic, ...), in the order the server lists them.
public struct ModelProvider: Decodable, Equatable, Identifiable, Sendable {
    public let name: String
    public let models: [ModelChoice]
    public var id: String { name }

    public init(name: String, models: [ModelChoice]) {
        self.name = name
        self.models = models
    }
}

/// One thing said in the conversation so far.
public struct HistoryItem: Decodable, Equatable, Sendable {
    public enum Role: String, Decodable, Sendable { case user, assistant }
    public let role: Role
    public let text: String

    public init(role: Role, text: String) {
        self.role = role
        self.text = text
    }
}

/// A conversation as the server holds it: its id in the stored log, and what was said.
public struct ConversationSnapshot: Decodable, Equatable, Sendable {
    public let id: Int?
    public let messages: [HistoryItem]

    public init(id: Int?, messages: [HistoryItem]) {
        self.id = id
        self.messages = messages
    }
}

/// One thing Tzara remembers about the user.
public struct MemoryFact: Decodable, Equatable, Identifiable, Sendable {
    public let id: Int
    public let text: String
    /// When it was first remembered, as seconds since 1970.
    public let created: Double

    public init(id: Int, text: String, created: Double = 0) {
        self.id = id
        self.text = text
        self.created = created
    }
}

/// Something Tzara did for the user: a page it changed, text it wrote in another app.
public struct MemoryActivity: Decodable, Equatable, Sendable, Identifiable {
    public var id: String { "\(at)-\(summary)" }
    public let at: Double
    public let kind: String
    public let summary: String
}

/// One of the user's own texts, kept to write in their voice.
public struct WritingSample: Decodable, Equatable, Sendable, Identifiable {
    public var id: String { "\(at)-\(place)" }
    public let place: String
    public let text: String
    public let at: Double
}

/// A stored conversation, for listing.
public struct StoredChat: Decodable, Equatable, Sendable, Identifiable {
    public let id: Int
    public let started: Double
    public let title: String
    public let messages: Int
}

/// One message of a stored conversation.
public struct StoredLine: Decodable, Equatable, Sendable, Identifiable {
    public var id: String { "\(at)-\(role)-\(text.prefix(20))" }
    public let role: String
    public let text: String
    public let at: Double
}

/// Everything Tzara remembers, as the server reports it.
public struct MemoryOverview: Decodable, Equatable, Sendable {
    /// Whether memory is on at all (`TZARA_MEMORY=false` turns it off).
    public let enabled: Bool
    public let facts: [MemoryFact]
    /// How many past conversations are stored.
    public let conversations: Int
    /// What Tzara did for the user, newest first.
    public let activity: [MemoryActivity]
    /// The user's own texts, kept to match their tone.
    public let writingSamples: [WritingSample]
    /// The latest stored conversations, newest first.
    public let chats: [StoredChat]

    private enum CodingKeys: String, CodingKey {
        case enabled, facts, conversations, activity, chats
        case writingSamples = "writing_samples"
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        enabled = try values.decode(Bool.self, forKey: .enabled)
        facts = try values.decode([MemoryFact].self, forKey: .facts)
        conversations = try values.decode(Int.self, forKey: .conversations)
        activity = (try? values.decode([MemoryActivity].self, forKey: .activity)) ?? []
        writingSamples = (try? values.decode([WritingSample].self, forKey: .writingSamples)) ?? []
        chats = (try? values.decode([StoredChat].self, forKey: .chats)) ?? []
    }

    public init(
        enabled: Bool, facts: [MemoryFact], conversations: Int, activity: [MemoryActivity] = [],
        writingSamples: [WritingSample] = [], chats: [StoredChat] = []
    ) {
        self.enabled = enabled
        self.facts = facts
        self.conversations = conversations
        self.activity = activity
        self.writingSamples = writingSamples
        self.chats = chats
    }
}

/// The rules for what the assistant may be called, the same ones the server enforces.
public enum AssistantName {
    public static let maximumLength = 40

    /// The name to use for what someone typed, or `nil` if it cannot be a name: empty, too
    /// long, or more than one line.
    public static func clean(_ proposed: String) -> String? {
        let name = proposed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name.count <= maximumLength, !name.contains(where: \.isNewline) else { return nil }
        return name
    }
}
