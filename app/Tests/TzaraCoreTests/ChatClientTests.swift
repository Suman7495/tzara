import Foundation
import Testing

@testable import TzaraCore

/// A fake network: answers each request from a handler registered for its port, so tests
/// running in parallel never see each other's traffic.
final class StubProtocol: URLProtocol {
    struct Reply {
        var status = 200
        var body = Data()
        var failure: URLError?
    }

    private static let lock = NSLock()
    private static var handlers: [Int: (URLRequest) -> Reply] = [:]

    static func register(port: Int, handler: @escaping (URLRequest) -> Reply) {
        lock.lock()
        defer { lock.unlock() }
        handlers[port] = handler
    }

    private static func handler(for port: Int) -> ((URLRequest) -> Reply)? {
        lock.lock()
        defer { lock.unlock() }
        return handlers[port]
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let url = request.url, let handler = Self.handler(for: url.port ?? 0) else {
            client?.urlProtocol(self, didFailWithError: URLError(.cannotConnectToHost))
            return
        }
        let reply = handler(request)
        if let failure = reply.failure {
            client?.urlProtocol(self, didFailWithError: failure)
            return
        }
        let response = HTTPURLResponse(
            url: url, statusCode: reply.status, httpVersion: "HTTP/1.1", headerFields: [:])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: reply.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

/// Reads the body of a request that URLSession turned into a stream.
private func body(of request: URLRequest) -> Data {
    guard let stream = request.httpBodyStream else { return request.httpBody ?? Data() }
    stream.open()
    defer { stream.close() }
    var data = Data()
    var buffer = [UInt8](repeating: 0, count: 1024)
    while stream.hasBytesAvailable {
        let count = stream.read(&buffer, maxLength: buffer.count)
        if count <= 0 { break }
        data.append(buffer, count: count)
    }
    return data
}

private func sse(_ events: [(String, String)]) -> Data {
    Data(events.map { "event: \($0.0)\ndata: \($0.1)\n\n" }.joined().utf8)
}

private struct Harness {
    let client: ChatClient
    let port: Int
    let token = "test-token"
}

/// A client wired to a fake server on a port of its own. Pass `running: false` for "no server.json".
private func makeHarness(running: Bool = true, handler: @escaping (URLRequest) -> StubProtocol.Reply) throws -> Harness {
    let port = Int.random(in: 20_000..<60_000)
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent("tzara-client-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    if running {
        try #"{"host": "127.0.0.1", "port": \#(port), "pid": 1}"#.write(
            to: directory.appendingPathComponent("server.json"), atomically: true, encoding: .utf8)
        try "test-token".write(to: directory.appendingPathComponent("token"), atomically: true, encoding: .utf8)
    }
    StubProtocol.register(port: port, handler: handler)
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [StubProtocol.self]
    let client = ChatClient(
        locator: ServerLocator(dataDirectory: directory), session: URLSession(configuration: configuration))
    return Harness(client: client, port: port)
}

private func collect(_ stream: AsyncThrowingStream<ChatEvent, Error>) async throws -> [ChatEvent] {
    var events: [ChatEvent] = []
    for try await event in stream { events.append(event) }
    return events
}

private func reply(_ status: Int, detail: String) -> StubProtocol.Reply {
    StubProtocol.Reply(status: status, body: Data(#"{"detail": "\#(detail)"}"#.utf8))
}

@Suite("ChatClient streaming")
struct ChatClientStreamTests {
    @Test func yieldsTheEventsInOrder() async throws {
        let harness = try makeHarness { _ in
            .init(body: sse([
                ("token", #"{"text": "Hello"}"#), ("token", #"{"text": " there"}"#),
                ("done", #"{"first_token_seconds": 0.3, "characters": 11}"#),
            ]))
        }
        let events = try await collect(harness.client.stream("hi"))
        #expect(events == [.token("Hello"), .token(" there"), .done(firstTokenSeconds: 0.3, characters: 11)])
    }

    @Test func sendsTheMessageWithTheTokenToTheChatEndpoint() async throws {
        final class Seen: @unchecked Sendable { var request: URLRequest?; var body = Data() }
        let seen = Seen()
        let harness = try makeHarness { request in
            seen.request = request
            seen.body = body(of: request)
            return .init(body: sse([("done", #"{"first_token_seconds": null, "characters": 0}"#)]))
        }
        _ = try await collect(harness.client.stream("What is 6 * 7?"))

        #expect(seen.request?.httpMethod == "POST")
        #expect(seen.request?.url?.path == "/v1/chat")
        #expect(seen.request?.value(forHTTPHeaderField: "Authorization") == "Bearer test-token")
        #expect(seen.request?.value(forHTTPHeaderField: "Content-Type") == "application/json")
        #expect(String(data: seen.body, encoding: .utf8) == #"{"message":"What is 6 * 7?"}"#)
    }

    @Test func neverSendsAnOriginHeader() async throws {
        final class Seen: @unchecked Sendable { var origin: String? = "unset" }
        let seen = Seen()
        let harness = try makeHarness { request in
            seen.origin = request.value(forHTTPHeaderField: "Origin")
            return .init(body: sse([("done", #"{"first_token_seconds": null, "characters": 0}"#)]))
        }
        _ = try await collect(harness.client.stream("hi"))
        #expect(seen.origin == nil)  // the server refuses requests that carry one
    }

    @Test func ignoresKeepAlivesAndStopsAtTheDoneEvent() async throws {
        let body = Data(
            (": keep-alive\n\nevent: token\ndata: {\"text\": \"a\"}\n\n: keep-alive\n\n"
                + "event: done\ndata: {\"first_token_seconds\": 1.0, \"characters\": 1}\n\n"
                + "event: token\ndata: {\"text\": \"after the end\"}\n\n").utf8)
        let harness = try makeHarness { _ in .init(body: body) }
        let events = try await collect(harness.client.stream("hi"))
        #expect(events == [.token("a"), .done(firstTokenSeconds: 1.0, characters: 1)])
    }

    @Test func aReplyThatFailsPartWayEndsWithTheErrorEvent() async throws {
        let harness = try makeHarness { _ in
            .init(body: sse([("token", #"{"text": "Hel"}"#), ("error", #"{"message": "the model fell over"}"#)]))
        }
        let events = try await collect(harness.client.stream("hi"))
        #expect(events == [.token("Hel"), .error("the model fell over")])
    }

    @Test func aStreamThatEndsWithoutADoneEventJustFinishes() async throws {
        let harness = try makeHarness { _ in .init(body: sse([("token", #"{"text": "partial"}"#)])) }
        #expect(try await collect(harness.client.stream("hi")) == [.token("partial")])
    }

    @Test func busyMeansTheLastReplyIsStillBeingWritten() async throws {
        let harness = try makeHarness { _ in reply(409, detail: "another reply is still being written") }
        await #expect(throws: ChatClientError.busy) { try await collect(harness.client.stream("hi")) }
    }

    @Test func notReadyCarriesTheServersExplanation() async throws {
        let harness = try makeHarness { _ in reply(503, detail: "the model is not ready (state: loading)") }
        await #expect(throws: ChatClientError.notReady("the model is not ready (state: loading)")) {
            try await collect(harness.client.stream("hi"))
        }
    }

    @Test func aRejectedTokenIsReported() async throws {
        let harness = try makeHarness { _ in reply(401, detail: "missing or invalid token") }
        await #expect(throws: ChatClientError.unauthorized) { try await collect(harness.client.stream("hi")) }
    }

    @Test(arguments: [413, 422])
    func aRejectedMessageCarriesTheReason(status: Int) async throws {
        let harness = try makeHarness { _ in reply(status, detail: "message is too long") }
        await #expect(throws: ChatClientError.messageRejected("message is too long")) {
            try await collect(harness.client.stream("hi"))
        }
    }

    @Test func anUnexpectedStatusIsReportedWithItsCode() async throws {
        let harness = try makeHarness { _ in .init(status: 500) }
        await #expect(throws: ChatClientError.unexpectedStatus(500)) { try await collect(harness.client.stream("hi")) }
    }

    @Test func aRefusedConnectionMeansTheServerIsNotRunning() async throws {
        let harness = try makeHarness { _ in .init(failure: URLError(.cannotConnectToHost)) }
        await #expect(throws: ChatClientError.serverNotRunning) { try await collect(harness.client.stream("hi")) }
    }

    @Test func noRecordOfAServerMeansNotRunningWithoutTouchingTheNetwork() async throws {
        final class Calls: @unchecked Sendable { var count = 0 }
        let calls = Calls()
        let harness = try makeHarness(running: false) { _ in
            calls.count += 1
            return .init()
        }
        await #expect(throws: ChatClientError.serverNotRunning) { try await collect(harness.client.stream("hi")) }
        #expect(calls.count == 0)
    }

    @Test func otherNetworkFailuresAreReportedWithTheirReason() async throws {
        let harness = try makeHarness { _ in .init(failure: URLError(.timedOut)) }
        do {
            _ = try await collect(harness.client.stream("hi"))
            Issue.record("expected an error")
        } catch let error as ChatClientError {
            guard case .network = error else {
                Issue.record("expected a network error, got \(error)")
                return
            }
        }
    }
}

@Suite("ChatClient control calls")
struct ChatClientControlTests {
    @Test func statusDecodesTheServersAnswer() async throws {
        let harness = try makeHarness { request in
            #expect(request.url?.path == "/v1/status")
            #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer test-token")
            return .init(body: Data(#"{"state": "ready", "model": "ollama:qwen3:8b", "name": "Jarvis", "busy": false, "error": null}"#.utf8))
        }
        let status = try await harness.client.status()
        #expect(status == ServerStatus(state: "ready", model: "ollama:qwen3:8b", name: "Jarvis", busy: false, error: nil))
    }

    @Test func statusReportsAFailedLoad() async throws {
        let harness = try makeHarness { _ in
            .init(body: Data(#"{"state": "failed", "model": "m", "busy": false, "error": "ollama is not running"}"#.utf8))
        }
        #expect(try await harness.client.status().error == "ollama is not running")
    }

    @Test func statusWithABadTokenIsUnauthorized() async throws {
        let harness = try makeHarness { _ in reply(401, detail: "missing or invalid token") }
        await #expect(throws: ChatClientError.unauthorized) { try await harness.client.status() }
    }

    @Test func statusWithNoServerIsNotRunning() async throws {
        let harness = try makeHarness(running: false) { _ in .init() }
        await #expect(throws: ChatClientError.serverNotRunning) { try await harness.client.status() }
    }

    @Test func interruptPostsToTheInterruptEndpointAndNeverThrows() async throws {
        final class Seen: @unchecked Sendable { var path: String?; var method: String? }
        let seen = Seen()
        let harness = try makeHarness { request in
            seen.path = request.url?.path
            seen.method = request.httpMethod
            return .init(status: 204)
        }
        await harness.client.interrupt()
        #expect(seen.path == "/v1/interrupt")
        #expect(seen.method == "POST")
    }

    @Test func interruptIsHarmlessWhenThereIsNoServer() async throws {
        let harness = try makeHarness(running: false) { _ in .init() }
        await harness.client.interrupt()  // must simply return
    }

    @Test func newConversationPostsToItsEndpoint() async throws {
        final class Seen: @unchecked Sendable { var path: String? }
        let seen = Seen()
        let harness = try makeHarness { request in
            seen.path = request.url?.path
            return .init(body: Data(#"{"ok": true}"#.utf8))
        }
        try await harness.client.newConversation()
        #expect(seen.path == "/v1/conversation/new")
    }

    @Test func newConversationWhileBusyIsReported() async throws {
        let harness = try makeHarness { _ in reply(409, detail: "the agent is busy") }
        await #expect(throws: ChatClientError.busy) { try await harness.client.newConversation() }
    }
}

@Suite("ChatClient settings calls")
struct ChatClientSettingsTests {
    private final class Seen: @unchecked Sendable {
        var path: String?
        var method: String?
        var auth: String?
        var body = Data()
    }

    private func harness(seen: Seen, reply: StubProtocol.Reply) throws -> Harness {
        try makeHarness { request in
            seen.path = request.url?.path
            seen.method = request.httpMethod
            seen.auth = request.value(forHTTPHeaderField: "Authorization")
            seen.body = body(of: request)
            return reply
        }
    }

    @Test func statusWithoutANameDefaultsToTzara() async throws {
        let harness = try makeHarness { _ in
            .init(body: Data(#"{"state": "ready", "model": "m", "busy": false, "error": null}"#.utf8))
        }
        #expect(try await harness.client.status().name == "Tzara")
    }

    @Test func modelsKeepTheServersOrderOfProvidersAndModels() async throws {
        let seen = Seen()
        let json = """
            {"providers": [
              {"name": "ollama", "models": [
                {"id": "ollama:qwen3:8b", "description": "Qwen3"}, {"id": "ollama:mistral", "description": "Mistral"}]},
              {"name": "anthropic", "models": [{"id": "anthropic:claude-haiku-4-5", "description": "Haiku"}]}
            ]}
            """
        let harness = try harness(seen: seen, reply: .init(body: Data(json.utf8)))
        let providers = try await harness.client.models()

        #expect(seen.path == "/v1/models" && seen.method == "GET" && seen.auth == "Bearer test-token")
        #expect(providers.map(\.name) == ["ollama", "anthropic"])
        #expect(providers[0].models.map(\.id) == ["ollama:qwen3:8b", "ollama:mistral"])
        #expect(providers[1].models[0] == ModelChoice(id: "anthropic:claude-haiku-4-5", description: "Haiku"))
    }

    @Test func historyIsDecodedInOrder() async throws {
        let seen = Seen()
        let json = #"{"messages": [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}]}"#
        let harness = try harness(seen: seen, reply: .init(body: Data(json.utf8)))
        let history = try await harness.client.history()

        #expect(seen.path == "/v1/conversation" && seen.method == "GET")
        #expect(history == [HistoryItem(role: .user, text: "hi"), HistoryItem(role: .assistant, text: "hello")])
    }

    @Test func anEmptyHistoryIsFine() async throws {
        let harness = try makeHarness { _ in .init(body: Data(#"{"messages": []}"#.utf8)) }
        #expect(try await harness.client.history().isEmpty)
    }

    @Test func settingTheModelPutsItAndReturnsTheOneNowInUse() async throws {
        let seen = Seen()
        let harness = try harness(seen: seen, reply: .init(body: Data(#"{"model": "ollama:mistral"}"#.utf8)))
        let model = try await harness.client.setModel("ollama:mistral")

        #expect(model == "ollama:mistral")
        #expect(seen.path == "/v1/model" && seen.method == "PUT")
        #expect(seen.auth == "Bearer test-token")
        #expect(String(data: seen.body, encoding: .utf8) == #"{"model":"ollama:mistral"}"#)
    }

    @Test func renamingPutsTheNameAndReturnsTheOneNowInUse() async throws {
        let seen = Seen()
        let harness = try harness(seen: seen, reply: .init(body: Data(#"{"name": "Jarvis"}"#.utf8)))
        let name = try await harness.client.rename("Jarvis")

        #expect(name == "Jarvis")
        #expect(seen.path == "/v1/name" && seen.method == "PUT")
        #expect(String(data: seen.body, encoding: .utf8) == #"{"name":"Jarvis"}"#)
    }

    @Test func aModelThatCannotBeLoadedCarriesTheServersExplanation() async throws {
        let harness = try makeHarness { _ in reply(502, detail: "could not load ollama:nope: model not found") }
        await #expect(throws: ChatClientError.failed("could not load ollama:nope: model not found")) {
            try await harness.client.setModel("ollama:nope")
        }
    }

    @Test func aRejectedNameOrModelCarriesTheReason() async throws {
        let harness = try makeHarness { _ in reply(422, detail: "the name must be one line of 1 to 40 characters") }
        await #expect(throws: ChatClientError.messageRejected("the name must be one line of 1 to 40 characters")) {
            try await harness.client.rename("")
        }
        await #expect(throws: ChatClientError.messageRejected("the name must be one line of 1 to 40 characters")) {
            try await harness.client.setModel("nonsense")
        }
    }

    @Test func switchingWhileAReplyIsBeingWrittenIsBusy() async throws {
        let harness = try makeHarness { _ in reply(409, detail: "the agent is busy") }
        await #expect(throws: ChatClientError.busy) { try await harness.client.setModel("ollama:mistral") }
        await #expect(throws: ChatClientError.busy) { try await harness.client.rename("Jarvis") }
    }

    @Test func aGarbledAnswerIsReportedNotCrashedOn() async throws {
        let harness = try makeHarness { _ in .init(body: Data("not json".utf8)) }
        await #expect(throws: ChatClientError.unexpectedStatus(200)) { try await harness.client.models() }
        await #expect(throws: ChatClientError.unexpectedStatus(200)) { try await harness.client.history() }
    }

    @Test func everyCallNeedsARunningServer() async throws {
        let harness = try makeHarness(running: false) { _ in .init() }
        await #expect(throws: ChatClientError.serverNotRunning) { try await harness.client.models() }
        await #expect(throws: ChatClientError.serverNotRunning) { try await harness.client.history() }
        await #expect(throws: ChatClientError.serverNotRunning) { try await harness.client.setModel("a:b") }
        await #expect(throws: ChatClientError.serverNotRunning) { try await harness.client.rename("x") }
    }
}

@Suite("ModelChoice")
struct ModelChoiceTests {
    @Test func theShortNameDropsTheProvider() {
        #expect(ModelChoice(id: "ollama:qwen3:8b", description: "").shortName == "qwen3:8b")
        #expect(ModelChoice(id: "anthropic:claude-haiku-4-5", description: "").shortName == "claude-haiku-4-5")
        #expect(ModelChoice(id: "openai:gpt-4o", description: "").shortName == "gpt-4o")
    }

    @Test func aNameWithNoProviderIsLeftAlone() {
        #expect(ModelChoice(id: "mistral", description: "").shortName == "mistral")
    }

    @Test func onlyTheFirstColonSeparatesTheProvider() {
        #expect(ModelChoice(id: "ollama:llama3.1:70b", description: "").shortName == "llama3.1:70b")
    }
}

@Suite("AssistantName")
struct AssistantNameTests {
    @Test func trimsSurroundingWhitespace() {
        #expect(AssistantName.clean("  Jarvis \n") == "Jarvis")
    }

    @Test func allowsSpacesAndUnicodeInside() {
        #expect(AssistantName.clean("Dr. Tzara \u{2728}") == "Dr. Tzara \u{2728}")
    }

    @Test(arguments: ["", "   ", "\n", "two\nlines", "line one\r\nline two"])
    func refusesNamesThatAreEmptyOrSpanLines(name: String) {
        #expect(AssistantName.clean(name) == nil)
    }

    @Test func refusesNamesOverTheLimitButAllowsExactlyTheLimit() {
        #expect(AssistantName.clean(String(repeating: "x", count: 40)) != nil)
        #expect(AssistantName.clean(String(repeating: "x", count: 41)) == nil)
    }

    @Test func countsCharactersNotBytes() {
        #expect(AssistantName.clean(String(repeating: "\u{1F409}", count: 40)) != nil)
    }
}

@Suite("ChatClientError messages")
struct ChatClientErrorTests {
    @Test func everyErrorHasAHumanReadableDescription() {
        let errors: [ChatClientError] = [
            .serverNotRunning, .unauthorized, .busy, .notReady(""), .notReady("loading"),
            .messageRejected("too long"), .failed(""), .failed("could not load it"),
            .unexpectedStatus(500), .network("timed out"),
        ]
        for error in errors { #expect(!(error.errorDescription ?? "").isEmpty) }
    }

    @Test func theNotRunningMessageTellsThePersonWhatToType() {
        #expect(ChatClientError.serverNotRunning.errorDescription?.contains("tzara start") == true)
    }

    @Test func aServerExplanationIsShownVerbatim() {
        #expect(ChatClientError.notReady("the model is not ready (state: loading)").errorDescription
            == "the model is not ready (state: loading)")
    }
}
