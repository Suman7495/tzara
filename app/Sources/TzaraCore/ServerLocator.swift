import Foundation

/// What a client needs to talk to the server.
public struct ServerCredentials: Equatable, Sendable {
    public let baseURL: URL
    public let token: String

    public init(baseURL: URL, token: String) {
        self.baseURL = baseURL
        self.token = token
    }
}

public enum ServerLocatorError: Error, Equatable, Sendable {
    /// There is no record of a running server.
    case notRunning
    /// The server's token file is missing or empty.
    case tokenUnreadable
}

/// Finds the Tzara server, and the token that unlocks it, from the files it leaves behind.
///
/// The server writes `server.json` (its address) while it runs, and `token`, into its data
/// directory: `~/Library/Application Support/tzara` unless `TZARA_DATA_DIR` says otherwise.
public struct ServerLocator: Sendable {
    public let dataDirectory: URL

    public init(
        dataDirectory: URL? = nil,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser
    ) {
        if let dataDirectory {
            self.dataDirectory = dataDirectory
        } else if let override = environment["TZARA_DATA_DIR"], !override.isEmpty {
            self.dataDirectory = URL(fileURLWithPath: (override as NSString).expandingTildeInPath)
        } else {
            self.dataDirectory = homeDirectory
                .appendingPathComponent("Library/Application Support/tzara", isDirectory: true)
        }
    }

    /// Reads the address and token.
    ///
    /// The address file can outlive a crashed server, so this proves only that a server was
    /// started, not that it is still there.
    public func credentials() throws -> ServerCredentials {
        guard
            let data = try? Data(contentsOf: dataDirectory.appendingPathComponent("server.json")),
            let record = try? JSONDecoder().decode(Record.self, from: data),
            let url = URL(string: "http://\(record.host):\(record.port)")
        else { throw ServerLocatorError.notRunning }

        guard
            let raw = try? String(contentsOf: dataDirectory.appendingPathComponent("token"), encoding: .utf8)
        else { throw ServerLocatorError.tokenUnreadable }
        let token = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty else { throw ServerLocatorError.tokenUnreadable }

        return ServerCredentials(baseURL: url, token: token)
    }

    private struct Record: Decodable {
        let host: String
        let port: Int
    }
}
