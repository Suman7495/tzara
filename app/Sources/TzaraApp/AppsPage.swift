import AppKit
import SwiftUI
import TzaraCore

/// A logo for an app: its colour and a letter or symbol, drawn here. The brands' own artwork is not
/// bundled; a coloured tile with the initial reads as the same thing at card size.
struct AppLogo: View {
    let id: String
    let name: String
    var size: CGFloat = 46

    private var style: (color: Color, symbol: String?, letter: String) {
        switch id {
        case "notion": (Color(red: 0.1, green: 0.1, blue: 0.1), nil, "N")
        case "hubspot": (Color(red: 1.0, green: 0.48, blue: 0.35), "circle.hexagongrid.fill", "H")
        case "microsoft-365-outlook": (Color(red: 0.04, green: 0.39, blue: 0.68), "envelope.fill", "O")
        case "microsoft-365-files": (Color(red: 0.0, green: 0.47, blue: 0.83), "cloud.fill", "D")
        case "microsoft-365-teams": (Color(red: 0.38, green: 0.39, blue: 0.65), "person.2.fill", "T")
        case "microsoft-365-excel": (Color(red: 0.11, green: 0.44, blue: 0.26), "tablecells.fill", "X")
        default: (Color.accentColor, nil, String(name.first.map(String.init)?.uppercased() ?? "?"))
        }
    }

    var body: some View {
        let style = style
        RoundedRectangle(cornerRadius: size * 0.24, style: .continuous)
            .fill(LinearGradient(colors: [style.color, style.color.opacity(0.78)], startPoint: .top, endPoint: .bottom))
            .frame(width: size, height: size)
            .overlay {
                if let symbol = style.symbol {
                    Image(systemName: symbol).font(.system(size: size * 0.42, weight: .semibold)).foregroundStyle(.white)
                } else {
                    Text(style.letter).font(.system(size: size * 0.5, weight: .bold, design: .rounded)).foregroundStyle(.white)
                }
            }
            .shadow(color: style.color.opacity(0.25), radius: 4, y: 2)
    }
}

/// The apps page: every app Tzara can connect to as a card, with a search box and filters. Nothing is
/// connected until Add is pressed, and Remove disconnects it again.
struct AppsPage: View {
    @ObservedObject var settings: SettingsModel

    enum Filter: String, CaseIterable, Identifiable {
        case all = "All", connected = "Connected", available = "Available", attention = "Needs attention"
        var id: String { rawValue }
    }

    @State private var search = ""
    @State private var filter = Filter.all
    @State private var tokens: [String: String] = [:]
    @State private var addingToken: String?
    @State private var addingCustom = false

    private var apps: AppsModel { settings.apps }

    /// What a card shows for an app.
    private enum CardState {
        case notAdded, connecting, removing, ready(String), warning(String)
    }

    private struct Item: Identifiable {
        let id: String
        let title: String
        let detail: String
        let preset: AppPreset?
        let app: ConnectedApp?
    }

    var body: some View {
        Page(title: "Apps", subtitle: "Connect the apps Tzara can read from. Nothing is connected until you add it.") {
            if let overview = apps.overview, overview.editable {
                Button { addingCustom = true } label: { Label("Add another app", systemImage: "plus") }
                    .disabled(overview.reloading)
            }
        } content: {
            if let overview = apps.overview {
                toolbar
                let items = visible(items(overview), overview: overview)
                if items.isEmpty {
                    Text("No apps match.").foregroundStyle(.secondary).frame(maxWidth: .infinity, minHeight: 160)
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 290), spacing: 16, alignment: .top)], spacing: 16) {
                        ForEach(items) { item in card(item, overview: overview) }
                    }
                }
                ForEach(overview.problems, id: \.self) { Text($0).font(.caption).foregroundStyle(.orange) }
            } else {
                ProgressView().frame(maxWidth: .infinity, minHeight: 200)
            }
            if let message = apps.message { Text(message).font(.caption).foregroundStyle(.secondary) }
        }
        .sheet(isPresented: $addingCustom) { customSheet }
        .sheet(item: toolsTarget) { target in
            toolsSheet(target.name)
        }
        .onAppear { apps.load() }
    }

    private struct ToolsTarget: Identifiable {
        let name: String
        var id: String { name }
    }

    private var toolsTarget: Binding<ToolsTarget?> {
        Binding(
            get: { apps.toolsOf.map { ToolsTarget(name: $0) } },
            set: { newValue in if newValue == nil { closeTools() } })
    }

    // MARK: - Toolbar and filtering

    private var toolbar: some View {
        HStack(spacing: 12) {
            SearchField(text: $search, prompt: "Search apps")
            Picker("", selection: $filter) {
                ForEach(Filter.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: 430)
            Spacer()
        }
    }

    private func items(_ overview: AppsOverview) -> [Item] {
        let presets = overview.presets.map { preset in
            Item(id: preset.id, title: preset.title, detail: preset.description, preset: preset,
                 app: overview.apps.first { $0.name == preset.name })
        }
        let own: [Item] = overview.apps
            .filter { added in !overview.presets.contains { $0.name == added.name } }
            .map { added in Item(id: added.name, title: added.name, detail: "Added by you", preset: nil, app: added) }
        return presets + own
    }

    private func visible(_ items: [Item], overview: AppsOverview) -> [Item] {
        items.filter { item in
            let words = search.lowercased().split(separator: " ")
            let text = (item.title + " " + item.detail).lowercased()
            guard words.allSatisfy({ text.contains($0) }) else { return false }
            let state = state(of: item.app, reloading: overview.reloading)
            switch filter {
            case .all: return true
            case .connected: if case .ready = state { return true } else { return false }
            case .available: if case .notAdded = state { return true } else { return false }
            case .attention: if case .warning = state { return true } else { return false }
            }
        }
    }

    // MARK: - A card

    private func card(_ item: Item, overview: AppsOverview) -> some View {
        let state = state(of: item.app, reloading: overview.reloading)
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                AppLogo(id: item.preset?.id ?? item.id, name: item.title)
                VStack(alignment: .leading, spacing: 4) {
                    Text(item.title).font(.headline).lineLimit(2)
                    chip(state)
                }
                Spacer(minLength: 0)
            }
            Text(statusText(state, fallback: item.detail))
                .font(.callout)
                .foregroundStyle(isWarning(state) ? Color.orange : Color.secondary)
                .lineLimit(4)
                .frame(maxWidth: .infinity, minHeight: 56, alignment: .topLeading)
            if let app = item.app, let signIn = apps.signIns[app.name] { signInDetail(signIn) }
            if let preset = item.preset, item.app == nil, addingToken == preset.id { tokenStep(preset) }
            Spacer(minLength: 0)
            actions(item, state: state, overview: overview)
        }
        .padding(16)
        .frame(maxWidth: .infinity, minHeight: 190, alignment: .topLeading)
        .background(Color.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: DashboardStyle.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: DashboardStyle.cardRadius).strokeBorder(Color.primary.opacity(0.08)))
    }

    @ViewBuilder private func actions(_ item: Item, state: CardState, overview: AppsOverview) -> some View {
        HStack(spacing: 8) {
            if let app = item.app {
                if app.signIn, !app.connected, !apps.signedIn.contains(app.name), !overview.reloading {
                    Button("Sign in") { apps.signIn(app) }
                        .buttonStyle(.borderedProminent)
                        .disabled(apps.signIns[app.name]?.running == true)
                }
                if app.connected { Button("Tools") { apps.toggleTools(app) } }
                Spacer()
                Button("Remove", role: .destructive) { apps.remove(app) }
                    .disabled(apps.removing.contains(app.name))
            } else if let preset = item.preset {
                Spacer()
                Button(addingToken == preset.id ? "Cancel" : "Add") { add(preset) }
                    .buttonStyle(.borderedProminent)
                    .disabled(overview.reloading)
            }
        }
    }

    @ViewBuilder private func chip(_ state: CardState) -> some View {
        switch state {
        case .notAdded: Chip(text: "Not added")
        case .connecting: Chip(text: "Connecting\u{2026}", color: .blue)
        case .removing: Chip(text: "Removing\u{2026}", color: .secondary)
        case .ready: Chip(text: "Connected", color: .green)
        case .warning: Chip(text: "Needs attention", color: .orange)
        }
    }

    private func isWarning(_ state: CardState) -> Bool {
        if case .warning = state { return true }
        return false
    }

    private func state(of app: ConnectedApp?, reloading: Bool) -> CardState {
        guard let app else { return .notAdded }
        if apps.removing.contains(app.name) { return .removing }
        if app.connected {
            if app.offered.isEmpty { return .warning("Connected, but Tzara has no tools from it.") }
            let more = app.hidden.isEmpty ? "" : ", \(app.hidden.count) not offered"
            return .ready("Connected: \(app.offered.count) tools\(more).")
        }
        if app.error == "sign in needed" { return .warning("Press Sign in to finish setting it up.") }
        if let error = app.error { return .warning("Could not connect: \(error)") }
        return reloading ? .connecting : .warning("Not connected.")
    }

    private func statusText(_ state: CardState, fallback: String) -> String {
        switch state {
        case .notAdded: return fallback
        case .connecting: return "Connecting\u{2026} the first time can take a minute."
        case .removing: return "Removing\u{2026}"
        case let .ready(text), let .warning(text): return text
        }
    }

    // MARK: - Adding

    /// Add: an app that needs a token asks for it first; any other connects at once.
    private func add(_ preset: AppPreset) {
        if preset.needsToken {
            addingToken = addingToken == preset.id ? nil : preset.id
            return
        }
        apps.add(preset)
    }

    private func tokenStep(_ preset: AppPreset) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                SecureField("", text: Binding(get: { tokens[preset.id] ?? "" }, set: { tokens[preset.id] = $0 }),
                            prompt: Text(preset.tokenLabel))
                    .labelsHidden()
                Button("Connect") {
                    apps.add(preset, token: tokens[preset.id] ?? "")
                    tokens[preset.id] = ""
                    addingToken = nil
                }
                .disabled((tokens[preset.id] ?? "").trimmingCharacters(in: .whitespaces).isEmpty)
            }
            Text(preset.tokenHelp).font(.caption).foregroundStyle(.secondary)
        }
    }

    /// The link and the code to type there, or why it did not work.
    @ViewBuilder private func signInDetail(_ state: SignInState) -> some View {
        if !state.error.isEmpty {
            Text(state.error).font(.caption).foregroundStyle(.orange)
        } else if state.done {
            Text("Signed in.").font(.caption).foregroundStyle(.green)
        } else if state.code.isEmpty, state.running, let url = URL(string: state.url) {
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("Finish signing in in your browser.").font(.caption)
                Link("Open page", destination: url).font(.caption)
            }
        } else if !state.code.isEmpty, let url = URL(string: state.url) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Open the page and enter this code:").font(.caption)
                HStack {
                    Text(state.code).font(.system(.title3, design: .monospaced)).textSelection(.enabled)
                    Button("Copy") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(state.code, forType: .string)
                    }
                    Link("Open page", destination: url)
                }
            }
        } else {
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("Starting the sign-in\u{2026}").font(.caption)
            }
        }
    }

    // MARK: - Another app

    private var customSheet: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Add another app").font(.title3.bold())
            Text("Any app that follows the MCP standard, with the start command from its instructions.")
                .foregroundStyle(.secondary)
            TextField("", text: Binding(get: { apps.draft.commandLine }, set: { apps.draft.commandLine = $0 }),
                      prompt: Text("npx -y some-package"))
                .labelsHidden()
                .font(.system(.body, design: .monospaced))
            TextField("", text: Binding(get: { apps.draft.environment }, set: { apps.draft.environment = $0 }),
                      prompt: Text("Token or setup, NAME=value (only if the app needs one)"))
                .labelsHidden()
            if !apps.draft.commandLine.isEmpty, let problem = apps.draft.problem {
                Text(problem).font(.caption).foregroundStyle(.orange)
            }
            HStack {
                Spacer()
                Button("Cancel") { addingCustom = false }
                Button("Connect") {
                    apps.addCustom()
                    addingCustom = false
                }
                .buttonStyle(.borderedProminent)
                .disabled(apps.draft.problem != nil)
            }
        }
        .padding(24)
        .frame(width: 520)
    }

    // MARK: - Tools

    private func closeTools() {
        if let app = apps.overview?.apps.first(where: { $0.name == apps.toolsOf }) { apps.toggleTools(app) }
    }

    /// The tools of an app, each with a checkbox: only the ticked ones are given to the model.
    private func toolsSheet(_ name: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("\(name): tools").font(.title3.bold())
                Spacer()
                Button("Done") { closeTools() }.keyboardShortcut(.defaultAction)
            }
            Text("Tick what Tzara may use. Each one adds to what it reads before every reply, so keep the list short.")
                .font(.callout).foregroundStyle(.secondary)
            if let list = apps.toolList {
                SearchField(text: Binding(get: { apps.toolFilter }, set: { apps.toolFilter = $0 }),
                            prompt: "Filter \(list.tools.count) tools")
                ScrollView {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(list.matching(apps.toolFilter)) { tool in
                            Toggle(isOn: Binding(get: { tool.enabled }, set: { apps.setTool(tool.name, enabled: $0) })) {
                                VStack(alignment: .leading, spacing: 1) {
                                    HStack(spacing: 6) {
                                        Text(tool.name)
                                        if tool.changesThings { Chip(text: "may change things", color: .orange) }
                                    }
                                    Text(tool.description).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                                }
                            }
                            .toggleStyle(.checkbox)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(height: 320)
                HStack {
                    Text("\(list.used) of \(list.limit) used")
                        .font(.caption).foregroundStyle(list.used > list.limit ? .orange : .secondary)
                    Spacer()
                    Button("Apply", action: apps.applyTools)
                        .buttonStyle(.borderedProminent)
                        .disabled(!apps.toolsChanged || list.used > list.limit)
                }
            } else if apps.toolsMessage == nil {
                ProgressView().frame(maxWidth: .infinity, minHeight: 120)
            }
            if let message = apps.toolsMessage { Text(message).font(.caption).foregroundStyle(.orange) }
        }
        .padding(24)
        .frame(width: 600)
    }
}
