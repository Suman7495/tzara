import Foundation
import Testing

@testable import TzaraCore

private func freshDefaults() -> UserDefaults {
    let suite = "tzara-tests-\(UUID().uuidString)"
    let defaults = UserDefaults(suiteName: suite)!
    defaults.removePersistentDomain(forName: suite)
    return defaults
}

@Suite("UpdatePreferences")
struct UpdatePreferencesTests {
    @Test func automaticUpdatesAreOnByDefault() {
        #expect(UpdatePreferences(defaults: freshDefaults()).autoUpdate)
    }

    @Test func turningThemOffIsRemembered() {
        let defaults = freshDefaults()
        UpdatePreferences(defaults: defaults).autoUpdate = false
        #expect(UpdatePreferences(defaults: defaults).autoUpdate == false)
    }

    @Test func turningThemBackOnIsRemembered() {
        let defaults = freshDefaults()
        let preferences = UpdatePreferences(defaults: defaults)
        preferences.autoUpdate = false
        preferences.autoUpdate = true
        #expect(UpdatePreferences(defaults: defaults).autoUpdate)
    }

    @Test func theLastCheckIsRemembered() {
        let defaults = freshDefaults()
        let preferences = UpdatePreferences(defaults: defaults)
        #expect(preferences.lastCheck == nil)
        let moment = Date(timeIntervalSince1970: 1_700_000_000)
        preferences.lastCheck = moment
        #expect(UpdatePreferences(defaults: defaults).lastCheck == moment)
    }
}

@Suite("AutoUpdatePolicy")
struct AutoUpdatePolicyTests {
    private let policy = AutoUpdatePolicy(interval: 3600)
    private let now = Date(timeIntervalSince1970: 1_700_000_000)

    @Test func neverCheckedMeansDue() {
        #expect(policy.isDue(enabled: true, busy: false, lastCheck: nil, now: now))
    }

    @Test func notDueBeforeTheIntervalHasPassed() {
        #expect(!policy.isDue(enabled: true, busy: false, lastCheck: now.addingTimeInterval(-3599), now: now))
    }

    @Test func dueOnceTheIntervalHasPassed() {
        #expect(policy.isDue(enabled: true, busy: false, lastCheck: now.addingTimeInterval(-3600), now: now))
        #expect(policy.isDue(enabled: true, busy: false, lastCheck: now.addingTimeInterval(-86_400), now: now))
    }

    @Test func offMeansNeverDue() {
        #expect(!policy.isDue(enabled: false, busy: false, lastCheck: nil, now: now))
    }

    @Test func notWhileTheBoxIsOpen() {
        #expect(!policy.isDue(enabled: true, busy: true, lastCheck: nil, now: now))
    }

    @Test func aClockSetBackwardsDoesNotPostponeChecks() {
        #expect(policy.isDue(enabled: true, busy: false, lastCheck: now.addingTimeInterval(7200), now: now))
    }

    @Test func theDefaultIntervalIsSixHours() {
        #expect(AutoUpdatePolicy().interval == 21_600)
    }
}

@Suite("Updater")
struct UpdaterTests {
    private func updater(latest tag: String, launched: Box<AppVersion>) -> Updater {
        let checker = UpdateChecker { _ in
            (
                Data(#"{"tag_name": "\#(tag)"}"#.utf8),
                HTTPURLResponse(url: URL(string: "https://x.test")!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            )
        }
        let installer = UpdateInstaller { _, arguments in
            launched.value = AppVersion(arguments.last?.components(separatedBy: "TZARA_REF=v").last?.components(separatedBy: " ").first ?? "")
        }
        return Updater(checker: checker, installer: installer)
    }

    @Test func installsANewerRelease() async throws {
        let launched = Box<AppVersion>()
        var announced: AppVersion?
        let outcome = try await updater(latest: "v0.3.0", launched: launched)
            .run(current: AppVersion("0.1.1")!) { announced = $0 }
        #expect(outcome == .available(AppVersion("0.3.0")!))
        #expect(announced == AppVersion("0.3.0")!)
        #expect(launched.value == AppVersion("0.3.0")!)
    }

    @Test func installsNothingWhenCurrent() async throws {
        let launched = Box<AppVersion>()
        var announced = false
        let outcome = try await updater(latest: "v0.1.1", launched: launched)
            .run(current: AppVersion("0.1.1")!) { _ in announced = true }
        #expect(outcome == .upToDate(AppVersion("0.1.1")!))
        #expect(!announced && launched.value == nil)
    }
}

private final class Box<Value>: @unchecked Sendable {
    var value: Value?
}
