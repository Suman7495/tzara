import Foundation
import Testing

@testable import TzaraCore

private func response(_ status: Int) -> URLResponse {
    HTTPURLResponse(url: URL(string: "https://api.github.com")!, statusCode: status, httpVersion: nil, headerFields: nil)!
}

private func checker(status: Int = 200, body: String = #"{"tag_name": "v0.2.0"}"#) -> UpdateChecker {
    UpdateChecker { _ in (Data(body.utf8), response(status)) }
}

@Suite("AppVersion")
struct AppVersionTests {
    @Test(arguments: [("0.1.1", "0.1.1"), ("v0.1.1", "0.1.1"), (" v2.0 \n", "2.0"), ("10", "10")])
    func readsVersions(text: String, shown: String) throws {
        #expect(try #require(AppVersion(text)).description == shown)
    }

    @Test(arguments: ["", "main", "v", "1.x", "1..2", "-1.0", "1.0-beta", "latest"])
    func rejectsWhatIsNotAVersion(text: String) {
        #expect(AppVersion(text) == nil)
    }

    @Test func numbersCompareAsNumbersNotText() throws {
        #expect(try #require(AppVersion("0.9.0")) < #require(AppVersion("0.10.0")))
        #expect(try #require(AppVersion("0.1.1")) < #require(AppVersion("0.1.2")))
        #expect(try #require(AppVersion("0.1.9")) < #require(AppVersion("1.0.0")))
    }

    @Test func missingPartsCountAsZero() throws {
        #expect(try #require(AppVersion("1.0")) == #require(AppVersion("1.0.0")))
        #expect(try #require(AppVersion("1")) < #require(AppVersion("1.0.1")))
    }

    @Test func theTagHasAV() throws {
        #expect(try #require(AppVersion("0.1.1")).tag == "v0.1.1")
    }
}

@Suite("UpdateChecker")
struct UpdateCheckerTests {
    private let current = AppVersion("0.1.1")!

    @Test func aNewerReleaseIsAvailable() async throws {
        #expect(try await checker().check(current: current) == .available(AppVersion("0.2.0")!))
    }

    @Test func theSameReleaseIsUpToDate() async throws {
        let same = checker(body: #"{"tag_name": "v0.1.1"}"#)
        #expect(try await same.check(current: current) == .upToDate(current))
    }

    @Test func anOlderReleaseIsUpToDateToo() async throws {
        let older = checker(body: #"{"tag_name": "v0.1.0"}"#)
        #expect(try await older.check(current: current) == .upToDate(current))
    }

    @Test func asksGitHubForTheLatestReleaseOfTheRepository() async throws {
        let seen = Box<URLRequest>()
        let checker = UpdateChecker(settings: UpdateSettings(repository: "me/thing")) { request in
            seen.value = request
            return (Data(#"{"tag_name": "v1.0.0"}"#.utf8), response(200))
        }
        _ = try await checker.latest()
        #expect(seen.value?.url?.absoluteString == "https://api.github.com/repos/me/thing/releases/latest")
        #expect(seen.value?.value(forHTTPHeaderField: "Accept") == "application/vnd.github+json")
    }

    @Test(arguments: [
        (404, UpdateError.noReleases), (403, .rateLimited), (429, .rateLimited), (500, .unexpectedStatus(500)),
    ])
    func statusCodesBecomeErrors(status: Int, expected: UpdateError) async {
        await #expect(throws: expected) { try await checker(status: status).latest() }
    }

    @Test(arguments: ["not json", "[]", "{}", #"{"tag_name": 3}"#, #"{"tag_name": "nightly"}"#])
    func anUnreadableAnswerIsReported(body: String) async {
        await #expect(throws: UpdateError.unreadable) { try await checker(body: body).latest() }
    }

    @Test func aNetworkFailureSaysWhy() async {
        let offline = UpdateChecker { _ in throw URLError(.notConnectedToInternet) }
        await #expect(throws: (any Error).self) { try await offline.latest() }
        do { _ = try await offline.latest() } catch let error as UpdateError {
            guard case .network = error else { Issue.record("expected .network, got \(error)"); return }
            #expect(error.errorDescription?.hasPrefix("Could not reach GitHub") == true)
        } catch { Issue.record("unexpected \(error)") }
    }
}

@Suite("UpdateInstaller")
struct UpdateInstallerTests {
    private let settings = UpdateSettings(
        installerURL: "https://example.test/{tag}/install.sh",
        logFile: URL(fileURLWithPath: "/tmp/tzara logs/update.log"))
    private let version = AppVersion("0.2.0")!

    @Test func runsTheInstallerOfThatReleaseDetached() throws {
        let launched = Box<(String, [String])>()
        try UpdateInstaller(settings: settings) { launched.value = ($0, $1) }.start(version)

        let (executable, arguments) = try #require(launched.value)
        #expect(executable == "/usr/bin/nohup")
        #expect(arguments.prefix(2) == ["/bin/sh", "-c"])
        let script = arguments[2]
        #expect(script.contains("curl -fsSL 'https://example.test/v0.2.0/install.sh' | TZARA_REF=v0.2.0 sh"))
        #expect(script.hasSuffix("&"))
    }

    @Test func uninstallingRunsTheInstallerWithUninstallDetached() throws {
        let launched = Box<(String, [String])>()
        try UpdateInstaller(settings: settings) { launched.value = ($0, $1) }.uninstall(version)

        let (executable, arguments) = try #require(launched.value)
        #expect(executable == "/usr/bin/nohup")
        #expect(arguments[2].contains("curl -fsSL 'https://example.test/v0.2.0/install.sh' | sh -s -- --uninstall"))
        #expect(!arguments[2].contains("TZARA_REF"))
        #expect(arguments[2].hasSuffix("&"))
    }

    @Test func aCopyWithoutAVersionUninstallsWithTheNewestInstaller() {
        let script = UpdateInstaller(settings: settings) { _, _ in }.uninstallScript(for: nil)
        #expect(script.contains("https://example.test/main/install.sh"))
    }

    @Test func theUninstallScriptReallyPassesTheFlagToTheInstaller() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("tzara gone \(UUID().uuidString)")
        let release = root.appendingPathComponent("v0.2.0")
        try FileManager.default.createDirectory(at: release, withIntermediateDirectories: true)
        let marker = root.appendingPathComponent("ran.txt")
        try "echo \"args=$*\" > '\(marker.path)'\n".write(
            to: release.appendingPathComponent("install.sh"), atomically: true, encoding: .utf8)
        let settings = UpdateSettings(
            installerURL: "file://\(root.path.replacingOccurrences(of: " ", with: "%20"))/{tag}/install.sh",
            logFile: root.appendingPathComponent("logs/update.log"))

        try UpdateInstaller(settings: settings).uninstall(version)

        for _ in 0..<100 where ((try? String(contentsOf: marker, encoding: .utf8)) ?? "").isEmpty {
            Thread.sleep(forTimeInterval: 0.05)
        }
        #expect((try? String(contentsOf: marker, encoding: .utf8)) == "args=--uninstall\n")
        try? FileManager.default.removeItem(at: root)
    }

    @Test func theOutputGoesToAQuotedLogFile() throws {
        let script = UpdateInstaller(settings: settings) { _, _ in }.script(for: version)
        #expect(script.contains("> '/tmp/tzara logs/update.log' 2>&1"))
        #expect(script.contains("mkdir -p '/tmp/tzara logs'"))
    }

    @Test func quotesAreEscapedSoTheyCannotEndTheQuoting() {
        #expect(UpdateInstaller.quoted("it's") == #"'it'\''s'"#)
    }

    @Test func aLaunchFailureIsReported() {
        struct Boom: Error {}
        #expect(throws: UpdateError.self) {
            try UpdateInstaller(settings: settings) { _, _ in throw Boom() }.start(version)
        }
    }

    @Test func theScriptReallyRunsTheDownloadedInstallerWithTheTag() throws {
        // A real shell, a real `curl` (of a file:// URL) and a stand-in installer, in a path with a space.
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("tzara update \(UUID().uuidString)")
        let release = root.appendingPathComponent("v0.2.0")
        try FileManager.default.createDirectory(at: release, withIntermediateDirectories: true)
        let marker = root.appendingPathComponent("ran.txt")
        try "echo \"ref=$TZARA_REF\" > '\(marker.path)'\n".write(
            to: release.appendingPathComponent("install.sh"), atomically: true, encoding: .utf8)
        let settings = UpdateSettings(
            installerURL: "file://\(root.path.replacingOccurrences(of: " ", with: "%20"))/{tag}/install.sh",
            logFile: root.appendingPathComponent("logs/update.log"))

        try UpdateInstaller(settings: settings).start(version)

        for _ in 0..<100 where ((try? String(contentsOf: marker, encoding: .utf8)) ?? "").isEmpty {
            Thread.sleep(forTimeInterval: 0.05)
        }
        let got = (try? String(contentsOf: marker, encoding: .utf8)) ?? "nil"
        #expect(got == "ref=v0.2.0\n", "got: \(got)")
        try? FileManager.default.removeItem(at: root)
    }

    @Test func theRealLauncherStartsAProcess() throws {
        let marker = FileManager.default.temporaryDirectory.appendingPathComponent("tzara-launch-\(UUID().uuidString)")
        try UpdateInstaller.launchProcess("/usr/bin/touch", [marker.path])
        for _ in 0..<50 where !FileManager.default.fileExists(atPath: marker.path) { Thread.sleep(forTimeInterval: 0.05) }
        #expect(FileManager.default.fileExists(atPath: marker.path))
        try? FileManager.default.removeItem(at: marker)
    }
}

/// Lets a test closure hand a value back.
private final class Box<Value>: @unchecked Sendable {
    var value: Value?
}
