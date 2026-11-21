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

/// One model the server can use, written `provider:model-name`.
public struct ModelChoice: Decodable, Equatable, Identifiable, Sendable {
    public let id: String
    public let description: String

    public init(id: String, description: String) {
        self.id = id
        self.description = description
    }

    /// The name without its provider, for a menu that is already grouped by provider:
    /// `qwen3:8b` for `ollama:qwen3:8b`.
    public var shortName: String {
        id.split(separator: ":", maxSplits: 1).last.map(String.init) ?? id
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
