import Foundation
import Testing

@testable import TzaraCore

private func makeDirectory() throws -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("tzara-tests-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

private func write(_ text: String, to name: String, in directory: URL) throws {
    try text.write(to: directory.appendingPathComponent(name), atomically: true, encoding: .utf8)
}

@Suite("ServerLocator")
struct ServerLocatorTests {
    @Test func readsTheAddressAndTheToken() throws {
        let directory = try makeDirectory()
        try write(#"{"host": "127.0.0.1", "port": 8927, "pid": 42}"#, to: "server.json", in: directory)
        try write("  secret-token\n", to: "token", in: directory)

        let credentials = try ServerLocator(dataDirectory: directory).credentials()
        #expect(credentials.baseURL.absoluteString == "http://127.0.0.1:8927")
        #expect(credentials.token == "secret-token")
    }

    @Test func noRecordMeansNotRunning() throws {
        let directory = try makeDirectory()
        #expect(throws: ServerLocatorError.notRunning) { try ServerLocator(dataDirectory: directory).credentials() }
    }

    @Test(arguments: ["not json", "{}", #"{"host": "h"}"#, #"{"host": "h", "port": "x", "pid": 1}"#])
    func anUnreadableRecordMeansNotRunning(content: String) throws {
        let directory = try makeDirectory()
        try write(content, to: "server.json", in: directory)
        try write("token", to: "token", in: directory)
        #expect(throws: ServerLocatorError.notRunning) { try ServerLocator(dataDirectory: directory).credentials() }
    }

    @Test func aMissingTokenIsReported() throws {
        let directory = try makeDirectory()
        try write(#"{"host": "127.0.0.1", "port": 8927, "pid": 1}"#, to: "server.json", in: directory)
        #expect(throws: ServerLocatorError.tokenUnreadable) { try ServerLocator(dataDirectory: directory).credentials() }
    }

    @Test func anEmptyTokenIsReported() throws {
        let directory = try makeDirectory()
        try write(#"{"host": "127.0.0.1", "port": 8927, "pid": 1}"#, to: "server.json", in: directory)
        try write(" \n", to: "token", in: directory)
        #expect(throws: ServerLocatorError.tokenUnreadable) { try ServerLocator(dataDirectory: directory).credentials() }
    }

    @Test func defaultsToTheMacOSApplicationSupportDirectory() {
        let home = URL(fileURLWithPath: "/Users/someone")
        let locator = ServerLocator(environment: [:], homeDirectory: home)
        #expect(locator.dataDirectory.path == "/Users/someone/Library/Application Support/tzara")
    }

    @Test func theEnvironmentCanOverrideTheDirectory() {
        let locator = ServerLocator(environment: ["TZARA_DATA_DIR": "/tmp/elsewhere"])
        #expect(locator.dataDirectory.path == "/tmp/elsewhere")
    }

    @Test func aBlankOverrideIsIgnored() {
        let home = URL(fileURLWithPath: "/Users/someone")
        let locator = ServerLocator(environment: ["TZARA_DATA_DIR": ""], homeDirectory: home)
        #expect(locator.dataDirectory.path.hasSuffix("Application Support/tzara"))
    }

    @Test func anExplicitDirectoryWinsOverTheEnvironment() {
        let explicit = URL(fileURLWithPath: "/tmp/explicit")
        let locator = ServerLocator(dataDirectory: explicit, environment: ["TZARA_DATA_DIR": "/tmp/other"])
        #expect(locator.dataDirectory == explicit)
    }

    @Test func expandsATildeInTheOverride() {
        let locator = ServerLocator(environment: ["TZARA_DATA_DIR": "~/tzara-data"])
        #expect(locator.dataDirectory.path == NSHomeDirectory() + "/tzara-data")
    }
}
