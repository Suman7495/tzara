import Foundation
import Testing

@testable import TzaraCore

@Suite("AppConnections")
struct AppConnectionsTests {
    @Test func readsTheAppsPresetsAndState() throws {
        let json = #"""
            {"apps": [{"name": "demo", "connected": true, "error": null, "offered": ["demo__add"],
                       "hidden": [{"tool": "delete", "reason": "not marked read-only"}]},
                      {"name": "broken", "connected": false, "error": "not found", "offered": [], "hidden": []}],
             "presets": [{"id": "microsoft-365-outlook", "title": "Microsoft 365: Outlook and Calendar",
                          "description": "Read your mail.", "needs": "Node.js", "first_use": "Sign in.", "added": false}],
             "reloading": true, "problems": ["x: bad"], "editable": true}
            """#
        let overview = try JSONDecoder().decode(AppsOverview.self, from: Data(json.utf8))
        #expect(overview.apps.map(\.name) == ["demo", "broken"])
        #expect(overview.apps[0].offered == ["demo__add"] && overview.apps[0].hidden.first?.tool == "delete")
        #expect(overview.apps[1].connected == false && overview.apps[1].error == "not found")
        #expect(overview.presets.first?.firstUse == "Sign in." && overview.presets.first?.added == false)
        #expect(overview.reloading && overview.editable && overview.problems == ["x: bad"])
    }

    @Test func splitsArgumentsKeepingQuotedWordsTogether() {
        #expect(AppDraft.splitArguments("-y some-package --flag") == ["-y", "some-package", "--flag"])
        #expect(AppDraft.splitArguments(#"--name "two words" 'and these'"#) == ["--name", "two words", "and these"])
        #expect(AppDraft.splitArguments("   ") == [])
        #expect(AppDraft.splitArguments(#"a "" b"#) == ["a", "", "b"])
    }

    @Test func readsEnvironmentLines() {
        #expect(AppDraft.environmentPairs("TOKEN=abc\n\n  KEY = value with = sign \n") == ["TOKEN": "abc", "KEY": "value with = sign"])
        #expect(AppDraft.environmentPairs("") == [:])
        #expect(AppDraft.environmentPairs("no equals sign") == nil)
        #expect(AppDraft.environmentPairs("=value") == nil)
    }

    @Test func saysWhatIsMissingBeforeAnAppCanBeAdded() {
        var draft = AppDraft()
        #expect(draft.problem == "Paste the app's start command.")
        draft.name = "my app"
        #expect(draft.problem?.contains("letters, digits") == true)
        draft.name = "files"
        #expect(draft.problem == "Say which program starts the app.")
        draft.command = "npx"
        #expect(draft.problem == nil)
        draft.environment = "oops"
        #expect(draft.problem?.contains("NAME=value") == true)
    }

    @Test func readsAPastedStartCommandAndNamesTheAppFromIt() {
        var draft = AppDraft()
        draft.commandLine = #"npx -y @acme/some-mcp-server@1.2 --dir "two words""#
        #expect(draft.command == "npx")
        #expect(AppDraft.splitArguments(draft.arguments) == ["-y", "@acme/some-mcp-server@1.2", "--dir", "two words"])
        #expect(draft.suggestedName == "some-mcp-server")
        #expect(draft.problem == nil)
        #expect(draft.request.name == "some-mcp-server")
        draft.name = "mine"
        #expect(draft.request.name == "mine")
    }

    @Test func turnsADraftIntoTheRequestTheServerExpects() throws {
        var draft = AppDraft()
        draft.name = " files "
        draft.command = " npx "
        draft.arguments = "-y pkg"
        draft.environment = "TOKEN=t"
        draft.allowActions = true
        let json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(draft.request)) as? [String: Any]
        #expect(json?["name"] as? String == "files" && json?["command"] as? String == "npx")
        #expect(json?["args"] as? [String] == ["-y", "pkg"])
        #expect(json?["env"] as? [String: String] == ["TOKEN": "t"])
        #expect(json?["allow_actions"] as? Bool == true)
    }

    @Test func aReadyMadeAppIsRequestedByItsId() throws {
        let json = try JSONSerialization.jsonObject(
            with: JSONEncoder().encode(NewApp(preset: "microsoft-365-outlook"))) as? [String: Any]
        #expect(json?["preset"] as? String == "microsoft-365-outlook")
    }
}

@Suite("AppToolList")
struct AppToolListTests {
    private let list = AppToolList(
        tools: [
            AppTool(name: "notion-search", description: "Search the workspace", enabled: true, chars: 400),
            AppTool(name: "notion-create-pages", description: "Create pages", enabled: true, changesThings: true, chars: 900),
            AppTool(name: "notion-fetch", description: "Read a page", enabled: false, chars: 300),
        ],
        limit: 1_000)

    @Test func countsOnlyTheTickedTools() {
        #expect(list.used == 1_300)
    }

    @Test func filtersByEveryWordInTheNameOrDescription() {
        #expect(list.matching("").count == 3)
        #expect(list.matching("page").map(\.name) == ["notion-create-pages", "notion-fetch"])
        #expect(list.matching("read page").map(\.name) == ["notion-fetch"])
        #expect(list.matching("zzz").isEmpty)
    }

    @Test func readsTheServersAnswer() throws {
        let json = #"{"tools": [{"name": "a", "description": "d", "enabled": true, "changes_things": true, "chars": 12}], "limit": 5000}"#
        let decoded = try JSONDecoder().decode(AppToolList.self, from: Data(json.utf8))
        #expect(decoded.tools.first == AppTool(name: "a", description: "d", enabled: true, changesThings: true, chars: 12))
        #expect(decoded.limit == 5000)
    }
}
