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
        let suite = "tzara-screen-test-\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let preferences = ScreenPreferences(defaults: defaults)
        #expect(preferences.enabled == false)
        preferences.enabled = true
        #expect(ScreenPreferences(defaults: defaults).enabled)
    }

    @Test func hasSomethingToActOnWhenThereIsASelectionOrAnAddress() {
        #expect(!ScreenContext(app: "Notes", window: "Plans").actionable)
        #expect(ScreenContext(app: "Safari", address: "https://example.com").actionable)
        #expect(ScreenContext(app: "Notes", selection: "hola").actionable)
    }

    @Test func textTheUserCopiedIsSaidToBeCopiedAndIsNotSent() throws {
        let copied = ScreenContext(app: "Teams", selection: "hello", copied: true)
        #expect(copied.summary == "Teams \u{00B7} 5 characters copied")
        let sent = try #require(String(data: JSONEncoder().encode(copied), encoding: .utf8))
        #expect(!sent.contains("copied"))
        let back = try JSONDecoder().decode(ScreenContext.self, from: Data(sent.utf8))
        #expect(back.selection == "hello" && back.copied == false)
    }

    // MARK: - Following the app in front

    private let notes = ScreenContext(app: "Notes", window: "Plans")
    private let selected = ScreenContext(app: "Code", window: "agent.py", selection: "max_input_tokens")

    @Test func showsWhatWasReadWhenTheBoxOpens() {
        var follow = ScreenFollow()
        follow.start(with: notes)
        #expect(follow.current == notes)
        follow.start(with: ScreenContext())
        #expect(follow.current == nil)
    }

    @Test func updatesWhenTheUserSelectsSomethingNewWhileTheBoxIsOpen() {
        var follow = ScreenFollow()
        follow.start(with: notes)
        let changed = follow.follow(selected)
        let again = follow.follow(selected)
        #expect(changed)
        #expect(follow.current == selected)
        #expect(!again)  // nothing new
    }

    @Test func keepsWhatItHasWhenNothingCouldBeRead() {
        var follow = ScreenFollow()
        follow.start(with: selected)
        let none = follow.follow(nil)
        let empty = follow.follow(ScreenContext())
        #expect(!none)
        #expect(!empty)
        #expect(follow.current == selected)
    }

    @Test func doesNotPutBackWhatTheUserRemoved() {
        var follow = ScreenFollow()
        follow.start(with: selected)
        follow.drop()
        #expect(follow.current == nil)
        let sameAgain = follow.follow(selected)  // still selected over there, but removed here
        let different = follow.follow(notes)
        #expect(!sameAgain)
        #expect(different)
    }

    @Test func keepsWhatIsAttachedWhenAMessageIsSent() {
        var follow = ScreenFollow()
        follow.start(with: selected)
        // Sending does not touch it: the box always shows what Tzara is aware of.
        #expect(follow.current == selected)
        let same = follow.follow(selected)
        #expect(!same)
        #expect(follow.current == selected)
    }

    @Test func startsAgainWhenTheBoxOpensAgain() {
        var follow = ScreenFollow()
        follow.start(with: selected)
        follow.drop()
        follow.start(with: selected)
        #expect(follow.current == selected)
    }

    // MARK: - Focus, typed text, visible text

    @Test func aContextCarriesTheFocusAndTheTexts() throws {
        let context = ScreenContext(
            app: "Outlook", bundle: "com.microsoft.Outlook", focus: "text area \"Message body\"",
            typing: "Hi Anna", visible: "From: Anna")
        let json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(context)) as? [String: String]
        #expect(json?["bundle"] == "com.microsoft.Outlook")
        #expect(json?["focus"] == "text area \"Message body\"")
        #expect(json?["typing"] == "Hi Anna" && json?["visible"] == "From: Anna")
    }

    @Test func typingAndVisibleTextDoNotMakeTwoContextsDifferent() {
        let before = ScreenContext(app: "Outlook", window: "Re", focus: "text area", typing: "Hi", visible: "a")
        let after = ScreenContext(app: "Outlook", window: "Re", focus: "text area", typing: "Hi Anna", visible: "b")
        #expect(before == after)
        #expect(before != ScreenContext(app: "Outlook", window: "Re", focus: "text field"))
    }

    @Test func aLongTypedOrVisibleTextIsCut() {
        let long = String(repeating: "a", count: ScreenContext.maximumText + 100)
        let context = ScreenContext(typing: long, visible: long)
        #expect(context.typing.count == ScreenContext.maximumText)
        #expect(context.visible.count == ScreenContext.maximumText)
    }

    @Test func anOlderServerResponseWithoutTheNewFieldsStillReads() throws {
        let json = Data(#"{"app":"Notes","window":"Plans","address":"","selection":""}"#.utf8)
        let context = try JSONDecoder().decode(ScreenContext.self, from: json)
        #expect(context.app == "Notes" && context.focus == "" && context.visible == "")
    }
}
