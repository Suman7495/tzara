import Foundation

/// Where a tool the assistant is using has got to.
public enum ToolState: String, Equatable, Sendable {
    case started, completed, failed
}

/// What the server sends while it writes a reply.
public enum ChatEvent: Equatable, Sendable {
    /// A piece of the reply.
    case token(String)
    /// What the agent is doing right now, such as "Searching the web for ...". It is shown
    /// while waiting, so a long search does not look like a hang.
    case status(String)
    /// A tool the assistant uses started, finished, or failed. Unlike ``status``, which is a
    /// sentence for the person, this names the tool so the app can tell when it is all done.
    case tool(name: String, state: ToolState)
    /// The reply is complete.
    case done(firstTokenSeconds: Double?, characters: Int)
    /// The reply failed part-way.
    case error(String)
}

/// Splits a stream of bytes into lines, keeping blank ones.
///
/// Server-sent events end each event with a blank line, so the blank lines matter.
/// `URLSession.AsyncBytes.lines` drops them, which is why this exists.
public struct LineAssembler {
    private var buffer: [UInt8] = []

    public init() {}

    /// Adds one byte.
    ///
    /// - Returns: A finished line (without its line ending), or `nil` if more bytes are needed.
    public mutating func push(_ byte: UInt8) -> String? {
        guard byte == 0x0A else {
            buffer.append(byte)
            return nil
        }
        if buffer.last == 0x0D { buffer.removeLast() }
        let line = String(decoding: buffer, as: UTF8.self)  // whole line, so multi-byte text is intact
        buffer.removeAll(keepingCapacity: true)
        return line
    }
}

/// Turns the lines of a server-sent-event stream into ``ChatEvent``s.
///
/// Comment lines (starting with `:`), which the server uses as keep-alives, and events
/// with names it does not know are ignored, as the SSE specification says clients should.
public struct EventStreamParser {
    private var eventName: String?
    private var dataLines: [String] = []

    public init() {}

    /// Feeds one line.
    ///
    /// - Returns: An event when the line completes one.
    public mutating func consume(line: String) -> ChatEvent? {
        if line.isEmpty { return dispatch() }
        if line.hasPrefix(":") { return nil }

        let field: Substring
        var value: Substring
        if let colon = line.firstIndex(of: ":") {
            field = line[..<colon]
            value = line[line.index(after: colon)...]
            if value.hasPrefix(" ") { value = value.dropFirst() }
        } else {
            field = Substring(line)
            value = ""
        }

        switch field {
        case "event": eventName = String(value)
        case "data": dataLines.append(String(value))
        default: break
        }
        return nil
    }

    private mutating func dispatch() -> ChatEvent? {
        defer {
            eventName = nil
            dataLines.removeAll()
        }
        guard let name = eventName else { return nil }
        return Self.decode(name: name, data: dataLines.joined(separator: "\n"))
    }

    private static let unreadable = ChatEvent.error("Tzara sent something this app could not read.")

    private static func decode(name: String, data: String) -> ChatEvent? {
        let payload = Data(data.utf8)
        let decoder = JSONDecoder()
        switch name {
        case "token":
            guard let token = try? decoder.decode(TokenPayload.self, from: payload) else { return unreadable }
            return .token(token.text)
        case "status":
            guard let status = try? decoder.decode(TokenPayload.self, from: payload) else { return unreadable }
            return .status(status.text)
        case "tool":
            // A state this app does not know is skipped, like an event it does not know.
            guard let tool = try? decoder.decode(ToolPayload.self, from: payload) else { return unreadable }
            guard let state = ToolState(rawValue: tool.state) else { return nil }
            return .tool(name: tool.name, state: state)
        case "done":
            guard let done = try? decoder.decode(DonePayload.self, from: payload) else { return unreadable }
            return .done(firstTokenSeconds: done.firstTokenSeconds, characters: done.characters)
        case "error":
            guard let failure = try? decoder.decode(ErrorPayload.self, from: payload) else { return unreadable }
            return .error(failure.message)
        default:
            return nil
        }
    }

    private struct TokenPayload: Decodable { let text: String }
    private struct ErrorPayload: Decodable { let message: String }
    private struct ToolPayload: Decodable {
        let name: String
        let state: String
    }
    private struct DonePayload: Decodable {
        let firstTokenSeconds: Double?
        let characters: Int
        enum CodingKeys: String, CodingKey {
            case firstTokenSeconds = "first_token_seconds"
            case characters
        }
    }
}
