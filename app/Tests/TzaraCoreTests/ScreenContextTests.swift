import Foundation
import Testing

@testable import TzaraCore

@Suite("ScreenContext")
struct ScreenContextTests {
    @Test func saysWhatIsBeingSentInOneLine() {
        let context = ScreenContext(app: "Safari", window: "CORS guide", selection: "Browsers block it.")
        #expect(context.summary == "Safari \u{00B7} CORS guide \u{00B7} 18 characters selected")
        #expect(ScreenContext(app: "Notes").summary == "Notes")
        #expect(ScreenContext(app: "Notes", selection: "a").summary == "Notes \u{00B7} 1 character selected")
    }

    @Test func cutsALongWindowTitleInTheSummary() {
        let summary = ScreenContext(app: "Safari", window: String(repeating: "w", count: 100)).summary
        #expect(summary.hasSuffix("\u{2026}") && summary.count < 60)
    }

    @Test func isEmptyOnlyWhenEverythingIs() {
        #expect(ScreenContext().isEmpty)
        #expect(!ScreenContext(selection: "x").isEmpty)
    }

    @Test func keepsAtMostTheMaximumSelection() {
        let long = String(repeating: "a", count: ScreenContext.maximumSelection + 50)
        #expect(ScreenContext(selection: long).selection.count == ScreenContext.maximumSelection)
    }

    @Test func dropsTheQueryAndFragmentOfAWebAddress() {
        #expect(
            ScreenContext.cleanAddress("https://example.com/a/b?token=secret&utm=1#top")
                == "https://example.com/a/b")
    }

    @Test func givesAFilesPathNotItsURL() {
        #expect(ScreenContext.cleanAddress("file:///Users/me/Notes/plan.md") == "/Users/me/Notes/plan.md")
    }

    @Test func leavesOtherAddressesAlone() {
        #expect(ScreenContext.cleanAddress("  about:blank ") == "about:blank")
        #expect(ScreenContext.cleanAddress("not a url") == "not a url")
    }

    @Test func isOffUntilTheUserTurnsItOn() throws {
        let defaults = try #require(UserDefaults(suiteName: "tzara-screen-test-\(UUID().uuidString)"))
        let preferences = ScreenPreferences(defaults: defaults)
        #expect(preferences.enabled == false)
        preferences.enabled = true
        #expect(ScreenPreferences(defaults: defaults).enabled)
    }
}
