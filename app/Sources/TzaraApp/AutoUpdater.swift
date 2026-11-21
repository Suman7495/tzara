import Foundation
import TzaraCore

/// Keeps Tzara up to date in the background when the user has left that on.
///
/// A timer asks, every few minutes, whether a check is due. A check happens at most every six
/// hours, never while the chat box is open (an update restarts the app), and never when the user
/// has turned automatic updates off.
@MainActor
final class AutoUpdater {
    private let preferences: UpdatePreferences
    private let policy: AutoUpdatePolicy
    private let updater: Updater
    private let currentVersion: AppVersion
    private let isBusy: () -> Bool
    private var timer: Timer?
    private var checking = false

    init(
        currentVersion: AppVersion,
        isBusy: @escaping () -> Bool,
        preferences: UpdatePreferences = UpdatePreferences(),
        policy: AutoUpdatePolicy = AutoUpdatePolicy(),
        updater: Updater = Updater()
    ) {
        self.currentVersion = currentVersion
        self.isBusy = isBusy
        self.preferences = preferences
        self.policy = policy
        self.updater = updater
    }

    func start() {
        guard timer == nil else { return }
        // First look a minute after launch, so starting up stays quick; then every ten minutes
        // the policy decides whether six hours have passed.
        DispatchQueue.main.asyncAfter(deadline: .now() + 60) { [weak self] in self?.tick() }
        timer = Timer.scheduledTimer(withTimeInterval: 600, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
    }

    private func tick() {
        guard !checking,
            policy.isDue(
                enabled: preferences.autoUpdate, busy: isBusy(), lastCheck: preferences.lastCheck, now: Date())
        else { return }
        checking = true
        // Recorded first, so a failing update is retried in hours, not minutes.
        preferences.lastCheck = Date()
        Task {
            defer { checking = false }
            do {
                let outcome = try await updater.run(current: currentVersion) { version in
                    FileLog.shared.info("automatic update from \(currentVersion) to \(version)")
                }
                if case let .upToDate(version) = outcome { FileLog.shared.info("automatic check: \(version) is current") }
            } catch {
                FileLog.shared.info("automatic update failed: \(error.localizedDescription)")
            }
        }
    }
}
