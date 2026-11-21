import Foundation

/// What the user chose about updates, and when the app last looked. Stored in the app's defaults.
public final class UpdatePreferences: @unchecked Sendable {
    static let autoUpdateKey = "autoUpdate"
    static let lastCheckKey = "lastUpdateCheck"

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    /// Whether the app updates itself. On until the user turns it off.
    public var autoUpdate: Bool {
        get { defaults.object(forKey: Self.autoUpdateKey) as? Bool ?? true }
        set { defaults.set(newValue, forKey: Self.autoUpdateKey) }
    }

    /// When the app last asked GitHub for the newest release (automatically or by the button).
    public var lastCheck: Date? {
        get { defaults.object(forKey: Self.lastCheckKey) as? Date }
        set { defaults.set(newValue, forKey: Self.lastCheckKey) }
    }
}

/// Decides when an automatic update check is due.
public struct AutoUpdatePolicy: Sendable {
    /// How long to wait between automatic checks.
    public var interval: TimeInterval

    public init(interval: TimeInterval = 6 * 60 * 60) {
        self.interval = interval
    }

    /// True when updating is on, nothing is in the way, and the last check was long enough ago.
    ///
    /// - Parameters:
    ///   - enabled: The user's choice.
    ///   - busy: Whether the person is using Tzara right now. An update restarts the app,
    ///     so it waits for the chat box to be closed.
    public func isDue(enabled: Bool, busy: Bool, lastCheck: Date?, now: Date) -> Bool {
        guard enabled, !busy else { return false }
        guard let lastCheck else { return true }
        // A clock that moved backwards must not postpone checks for hours.
        return now < lastCheck || now.timeIntervalSince(lastCheck) >= interval
    }
}

/// Looks for a newer release and starts installing it.
public struct Updater: Sendable {
    private let checker: UpdateChecker
    private let installer: UpdateInstaller

    public init(checker: UpdateChecker = UpdateChecker(), installer: UpdateInstaller = UpdateInstaller()) {
        self.checker = checker
        self.installer = installer
    }

    /// Checks, and installs if a newer release exists.
    ///
    /// - Parameter installing: Called with the version just before the installer starts.
    /// - Returns: What was found.
    @discardableResult
    public func run(current: AppVersion, installing: (AppVersion) -> Void = { _ in }) async throws -> UpdateOutcome {
        let outcome = try await checker.check(current: current)
        if case let .available(version) = outcome {
            installing(version)
            try installer.start(version)
        }
        return outcome
    }
}
