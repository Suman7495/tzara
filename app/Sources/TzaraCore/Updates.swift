import Foundation

/// A release number such as `0.1.1`, comparable the way people expect (`0.10.0` is newer than `0.9.0`).
public struct AppVersion: Comparable, Equatable, CustomStringConvertible, Sendable {
    public let components: [Int]

    /// Reads `0.1.1` or `v0.1.1`. Anything else (`main`, `1.x`, an empty string) is not a version.
    public init?(_ text: String) {
        var text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("v") { text.removeFirst() }
        let parts = text.split(separator: ".", omittingEmptySubsequences: false)
        let numbers = parts.compactMap { Int($0) }
        guard !parts.isEmpty, numbers.count == parts.count, numbers.allSatisfy({ $0 >= 0 }) else { return nil }
        components = numbers
    }

    public var description: String { components.map(String.init).joined(separator: ".") }

    /// The tag a release of this version carries on GitHub.
    public var tag: String { "v\(description)" }

    public static func < (left: AppVersion, right: AppVersion) -> Bool {
        let length = max(left.components.count, right.components.count)
        for index in 0..<length {
            let a = index < left.components.count ? left.components[index] : 0
            let b = index < right.components.count ? right.components[index] : 0
            if a != b { return a < b }
        }
        return false
    }

    public static func == (left: AppVersion, right: AppVersion) -> Bool { !(left < right) && !(right < left) }
}

/// The result of asking GitHub whether a newer release exists.
public enum UpdateOutcome: Equatable, Sendable {
    case upToDate(AppVersion)
    case available(AppVersion)
}

public enum UpdateError: Error, Equatable, LocalizedError, Sendable {
    case noReleases
    case unreadable
    case rateLimited
    case unexpectedStatus(Int)
    case network(String)
    case couldNotStart(String)

    public var errorDescription: String? {
        switch self {
        case .noReleases:
            return "No release has been published yet."
        case .unreadable:
            return "GitHub's answer about the latest release was not understood."
        case .rateLimited:
            return "GitHub is limiting requests right now. Try again in a few minutes."
        case let .unexpectedStatus(code):
            return "GitHub answered with an unexpected status (\(code))."
        case let .network(detail):
            return "Could not reach GitHub: \(detail)"
        case let .couldNotStart(detail):
            return "Could not start the update: \(detail)"
        }
    }
}

/// Where releases live and how they are installed. Kept apart so tests can change them.
public struct UpdateSettings: Sendable {
    public var repository: String
    /// The installer to run, with `{tag}` standing for the release tag.
    public var installerURL: String
    /// Where the installer's output goes, so a failed update can be diagnosed.
    public var logFile: URL

    public init(
        repository: String = "Suman7495/tzara",
        installerURL: String = "https://raw.githubusercontent.com/Suman7495/tzara/{tag}/install.sh",
        logFile: URL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/tzara/update.log")
    ) {
        self.repository = repository
        self.installerURL = installerURL
        self.logFile = logFile
    }

    var latestReleaseURL: URL {
        URL(string: "https://api.github.com/repos/\(repository)/releases/latest")!
    }
}

/// Asks GitHub for the latest release.
public struct UpdateChecker: Sendable {
    public typealias Fetch = @Sendable (URLRequest) async throws -> (Data, URLResponse)

    private let settings: UpdateSettings
    private let fetch: Fetch

    public init(
        settings: UpdateSettings = UpdateSettings(),
        fetch: @escaping Fetch = { try await URLSession.shared.data(for: $0) }
    ) {
        self.settings = settings
        self.fetch = fetch
    }

    /// The newest published release.
    public func latest() async throws -> AppVersion {
        var request = URLRequest(url: settings.latestReleaseURL)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 15

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await fetch(request)
        } catch {
            throw UpdateError.network((error as? LocalizedError)?.errorDescription ?? error.localizedDescription)
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        switch status {
        case 200: break
        case 404: throw UpdateError.noReleases
        case 403, 429: throw UpdateError.rateLimited
        default: throw UpdateError.unexpectedStatus(status)
        }
        guard
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let tag = object["tag_name"] as? String,
            let version = AppVersion(tag)
        else { throw UpdateError.unreadable }
        return version
    }

    /// Compares the newest release with the version running now.
    public func check(current: AppVersion) async throws -> UpdateOutcome {
        let newest = try await latest()
        return newest > current ? .available(newest) : .upToDate(current)
    }
}

/// Installs a release, or removes Tzara, by running the project's own installer, detached from the app.
///
/// The installer replaces the Python package and the server, then quits and replaces this app
/// and opens the new one, so it must keep running after the app is gone.
public struct UpdateInstaller: Sendable {
    public typealias Launch = @Sendable (_ executable: String, _ arguments: [String]) throws -> Void

    private let settings: UpdateSettings
    private let launch: Launch

    public init(
        settings: UpdateSettings = UpdateSettings(),
        launch: @escaping Launch = UpdateInstaller.launchProcess
    ) {
        self.settings = settings
        self.launch = launch
    }

    /// The shell script that installs `version`.
    func script(for version: AppVersion) -> String {
        // The variable belongs to `sh`, the installer; set before `curl` it would never reach it.
        detached(ref: version.tag, shell: "TZARA_REF=\(version.tag) sh")
    }

    /// The shell script that removes Tzara, using the installer of `version` (or the newest
    /// installer when this app has no version, as a copy run from the build folder has none).
    func uninstallScript(for version: AppVersion?) -> String {
        detached(ref: version?.tag ?? "main", shell: "sh -s -- --uninstall")
    }

    private func detached(ref: String, shell: String) -> String {
        let url = settings.installerURL.replacingOccurrences(of: "{tag}", with: ref)
        let log = Self.quoted(settings.logFile.path)
        // The background job keeps running when the installer quits this app.
        return "mkdir -p \(Self.quoted(settings.logFile.deletingLastPathComponent().path)); "
            + "(curl -fsSL \(Self.quoted(url)) | \(shell)) > \(log) 2>&1 &"
    }

    /// Starts the update and returns at once; it finishes after this app has quit and restarted.
    public func start(_ version: AppVersion) throws {
        try run(script(for: version))
    }

    /// Starts removing Tzara (the app, the login items, the server and the command) and returns
    /// at once; the app is gone a few seconds later. Ollama, its models and the logs stay.
    public func uninstall(_ version: AppVersion?) throws {
        try run(uninstallScript(for: version))
    }

    private func run(_ script: String) throws {
        do {
            try launch("/usr/bin/nohup", ["/bin/sh", "-c", script])
        } catch {
            throw UpdateError.couldNotStart(error.localizedDescription)
        }
    }

    static func quoted(_ text: String) -> String {
        "'" + text.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    public static let launchProcess: Launch = { executable, arguments in
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
    }
}
