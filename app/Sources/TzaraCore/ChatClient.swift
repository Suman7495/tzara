import Foundation

/// Why a request to the server did not work, in terms a person can act on.
public enum ChatClientError: Error, Equatable, LocalizedError, Sendable {
    case serverNotRunning
    case unauthorized
    case busy
    case notReady(String)
    case messageRejected(String)
    case failed(String)
    case unexpectedStatus(Int)
    case network(String)

    public var errorDescription: String? {
        switch self {
        case .serverNotRunning:
            return "Tzara's server isn't running. Start it with \u{201C}tzara start\u{201D} in a terminal."
        case .unauthorized:
            return "The server rejected this app's token. Restart the server with \u{201C}tzara stop\u{201D} then \u{201C}tzara start\u{201D}."
        case .busy:
            return "Tzara is still answering the last message."
        case let .notReady(detail):
            return detail.isEmpty ? "Tzara's model isn't ready yet." : detail
        case let .messageRejected(detail):
            return detail
        case let .failed(detail):
            return detail.isEmpty ? "The server could not do that." : detail
        case let .unexpectedStatus(code):
            return "The server answered with an unexpected status (\(code))."
        case let .network(detail):
            return "Could not reach the server: \(detail)"
        }
    }
}

/// Talks to the Tzara server over HTTP. The endpoints are listed in the repository README.
public final class ChatClient: @unchecked Sendable {
    private let locator: ServerLocator
    private let session: URLSession

    /// Seconds a reply may go without a byte before it is abandoned. The server sends a
    /// keep-alive every ten seconds, so a longer silence means something is wrong.
    private static let replyTimeout: TimeInterval = 60
    private static let quickTimeout: TimeInterval = 5
    /// Switching model or renaming reloads the model, which takes about fifteen seconds.
    private static let reloadTimeout: TimeInterval = 180

    public init(locator: ServerLocator = ServerLocator(), session: URLSession = .shared) {
        self.locator = locator
        self.session = session
    }

    /// Asks the server how it is doing.
    public func status() async throws -> ServerStatus {
        try await decoded(ServerStatus.self, path: "/v1/status")
    }

    /// The models the server knows about, grouped by provider.
    public func models() async throws -> [ModelProvider] {
        try await decoded(ModelCatalog.self, path: "/v1/models").providers
    }

    /// The conversation so far, so a freshly opened chat can show it.
    public func history() async throws -> [HistoryItem] {
        try await decoded(Conversation.self, path: "/v1/conversation").messages
    }

    /// What Tzara remembers about the user, for the settings screen.
    public func memory() async throws -> MemoryOverview {
        try await decoded(MemoryOverview.self, path: "/v1/memory")
    }

    /// Makes Tzara forget one remembered fact.
    public func forgetFact(_ id: Int) async throws {
        _ = try await send(path: "/v1/memory/facts/\(id)", method: "DELETE")
    }

    /// Makes Tzara forget everything: every fact, every stored conversation, and this one.
    public func eraseMemory() async throws {
        _ = try await send(path: "/v1/memory", method: "DELETE")
    }

    /// Gets the model ready if it has been idle, so that it is loaded by the time the user has
    /// typed. Returns at once, and failing is harmless: it is only a head start.
    public func wake() async {
        _ = try? await send(path: "/v1/wake", method: "POST")
    }

    /// Switches to another model. The conversation carries over. Takes about fifteen seconds.
    ///
    /// - Returns: The model now in use.
    @discardableResult
    public func setModel(_ model: String) async throws -> String {
        try await decoded(ModelAnswer.self, path: "/v1/model", method: "PUT", body: ["model": model],
                          timeout: Self.reloadTimeout).model
    }

    /// Renames the assistant, which it then uses to describe itself. Takes a few seconds.
    ///
    /// - Returns: The name now in use.
    @discardableResult
    public func rename(_ name: String) async throws -> String {
        try await decoded(NameAnswer.self, path: "/v1/name", method: "PUT", body: ["name": name],
                          timeout: Self.reloadTimeout).name
    }

    /// Sends a message and streams the reply.
    ///
    /// Stop consuming the stream (or cancel the task reading it) to cancel the reply: the
    /// connection closes and the server stops writing.
    public func stream(_ message: String) -> AsyncThrowingStream<ChatEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let request = try makeRequest(
                        path: "/v1/chat", method: "POST", body: ["message": message], timeout: Self.replyTimeout)
                    let (bytes, response) = try await session.bytes(for: request)
                    if let http = response as? HTTPURLResponse, http.statusCode != 200 {
                        var body = Data()
                        for try await byte in bytes { body.append(byte) }
                        try Self.check(http, body: body)
                    }

                    var lines = LineAssembler()
                    var parser = EventStreamParser()
                    for try await byte in bytes {
                        guard let line = lines.push(byte), let event = parser.consume(line: line) else { continue }
                        continuation.yield(event)
                        switch event {
                        case .done, .error: continuation.finish(); return
                        case .token, .status, .tool: break
                        }
                    }
                    continuation.finish()
                } catch {
                    if Self.isCancellation(error) {
                        continuation.finish()
                    } else {
                        continuation.finish(throwing: Self.clientError(from: error))
                    }
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// Cancels the reply being written. Harmless if there is none.
    public func interrupt() async {
        guard let request = try? makeRequest(path: "/v1/interrupt", method: "POST", timeout: Self.quickTimeout)
        else { return }
        _ = try? await session.data(for: request)
    }

    /// Makes the server forget the conversation so far.
    public func newConversation() async throws {
        _ = try await send(path: "/v1/conversation/new", method: "POST")
    }

    // MARK: - Plumbing

    private func makeRequest(
        path: String, method: String, body: (any Encodable)? = nil, timeout: TimeInterval
    ) throws -> URLRequest {
        let credentials: ServerCredentials
        do {
            credentials = try locator.credentials()
        } catch {
            throw ChatClientError.serverNotRunning
        }
        var request = URLRequest(url: credentials.baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = timeout
        request.setValue("Bearer \(credentials.token)", forHTTPHeaderField: "Authorization")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONEncoder().encode(AnyEncodable(body))
        }
        return request
    }

    private func send(
        path: String, method: String, body: (any Encodable)? = nil, timeout: TimeInterval = ChatClient.quickTimeout
    ) async throws -> Data {
        let request = try makeRequest(path: path, method: method, body: body, timeout: timeout)
        let (data, response) = try await perform(request)
        try Self.check(response, body: data)
        return data
    }

    private func decoded<Value: Decodable>(
        _ type: Value.Type, path: String, method: String = "GET", body: (any Encodable)? = nil,
        timeout: TimeInterval = ChatClient.quickTimeout
    ) async throws -> Value {
        let data = try await send(path: path, method: method, body: body, timeout: timeout)
        guard let value = try? JSONDecoder().decode(type, from: data) else {
            throw ChatClientError.unexpectedStatus(200)
        }
        return value
    }

    private func perform(_ request: URLRequest) async throws -> (Data, URLResponse) {
        do {
            return try await session.data(for: request)
        } catch {
            throw Self.clientError(from: error)
        }
    }

    private static func check(_ response: URLResponse, body: Data) throws {
        guard let http = response as? HTTPURLResponse else { throw ChatClientError.unexpectedStatus(0) }
        try check(http, body: body)
    }

    private static func check(_ http: HTTPURLResponse, body: Data) throws {
        let detail = (try? JSONDecoder().decode(ErrorBody.self, from: body))?.detail ?? ""
        switch http.statusCode {
        case 200..<300: return
        case 401: throw ChatClientError.unauthorized
        case 409: throw ChatClientError.busy
        case 503: throw ChatClientError.notReady(detail)
        case 413, 422: throw ChatClientError.messageRejected(detail.isEmpty ? "The server rejected that." : detail)
        case 502: throw ChatClientError.failed(detail)
        default: throw ChatClientError.unexpectedStatus(http.statusCode)
        }
    }

    private static func isCancellation(_ error: Error) -> Bool {
        error is CancellationError || (error as? URLError)?.code == .cancelled
    }

    private static func clientError(from error: Error) -> ChatClientError {
        if let known = error as? ChatClientError { return known }
        if let urlError = error as? URLError {
            switch urlError.code {
            case .cannotConnectToHost, .networkConnectionLost, .cannotFindHost: return .serverNotRunning
            default: return .network(urlError.localizedDescription)
            }
        }
        return .network(error.localizedDescription)
    }

    private struct ErrorBody: Decodable { let detail: String }
    private struct ModelCatalog: Decodable { let providers: [ModelProvider] }
    private struct Conversation: Decodable { let messages: [HistoryItem] }
    private struct ModelAnswer: Decodable { let model: String }
    private struct NameAnswer: Decodable { let name: String }
    /// Lets a request body be any `Encodable` value, not only a dictionary of strings.
    private struct AnyEncodable: Encodable {
        let value: any Encodable
        init(_ value: any Encodable) { self.value = value }
        func encode(to encoder: Encoder) throws { try value.encode(to: encoder) }
    }
}
