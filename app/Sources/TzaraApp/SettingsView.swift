import AppKit
import SwiftUI
import TzaraCore

/// The settings pages of the dashboard that are not their own page: General (name and model),
/// Context & writing, and About.
enum SettingsPage: String, CaseIterable, Identifiable {
    case general, context, about
    var id: String { rawValue }

    var title: String {
        switch self {
        case .general: "General"
        case .context: "Context & writing"
        case .about: "About"
        }
    }

    var symbol: String {
        switch self {
        case .general: "slider.horizontal.3"
        case .context: "text.viewfinder"
        case .about: "info.circle"
        }
    }
}

struct SettingsView: View {
    let page: SettingsPage
    @ObservedObject var settings: SettingsModel
    let assistantName: String
    let isRenaming: Bool
    let onRename: (String) -> Void
    let hotkeyIsActive: () -> Bool

    @State private var nameDraft = ""
    @FocusState private var nameFocused: Bool
    @State private var hotkeyActive = false

    var body: some View {
        Page(title: page.title) {
            switch page {
            case .general: general
            case .context: context
            case .about: about
            }
        }
        .onAppear {
            settings.load()
            settings.refreshScreenPermission()
            nameDraft = assistantName
            hotkeyActive = hotkeyIsActive()
        }
        .onChange(of: assistantName) { nameDraft = $0 }
    }

    // MARK: - General

    @ViewBuilder private var general: some View {
        Card(title: "Assistant") {
            Line(title: "Name", detail: "What Tzara calls itself.", divider: false) {
                HStack {
                    TextField("", text: $nameDraft, prompt: Text("Tzara"))
                        .labelsHidden()
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 240)
                        .focused($nameFocused)
                        .onSubmit { onRename(nameDraft) }
                        .onChange(of: nameFocused) { focused in if !focused { onRename(nameDraft) } }
                        .disabled(isRenaming)
                    if isRenaming { ProgressView().controlSize(.small) }
                }
            }
        }
        Card(title: "Model", note: "Models marked download or key need one more step before you can use them.") {
            if settings.providers.isEmpty {
                Line(title: "Model", divider: false) { ProgressView().controlSize(.small) }
            } else {
                Line(title: "Language model", detail: "Runs on your Mac through Ollama unless you pick a cloud one.", divider: false) {
                    Picker("Model", selection: selection) {
                        if let unlisted = settings.unlistedCurrentModel { Text(unlisted).tag(unlisted) }
                        ForEach(settings.providers) { provider in
                            Section(SettingsModel.providerTitle(provider.name)) {
                                ForEach(provider.models) { model in Text(modelLabel(model)).tag(model.id) }
                            }
                        }
                    }
                    .pickerStyle(.menu)
                    .labelsHidden()
                    .frame(width: 260)
                    .disabled(settings.isSwitching)
                }
                if settings.needsSetup != nil || settings.switchingTo != nil || settings.message != nil {
                    Divider().padding(.leading, 16)
                    VStack(alignment: .leading, spacing: 8) {
                        setupPanel
                        switchStatus
                    }
                    .padding(16)
                }
            }
        }
    }

    // MARK: - Context and writing

    @ViewBuilder private var context: some View {
        Card(title: "What Tzara can see", note: "The bar shows what it will send, and you can remove it. Nothing is read from password managers or password fields, and no screenshots are taken.") {
            Line(title: "Use the window in front", detail: "Note the app, window and selected text you were in when you open the bar, so you can say \u{201C}explain this\u{201D}.", divider: needsAccessibility) {
                Toggle("", isOn: $settings.useScreen).labelsHidden().toggleStyle(.switch)
            }
            if needsAccessibility {
                Line(title: "Accessibility permission needed", detail: "To read selected text and window titles.", divider: false) {
                    Button("Open Settings", action: SystemSettings.openAccessibility)
                }
            }
        }
        Card(title: "Writing in other apps", note: "Tap the key in a text field and say what you want. Nothing is written until you ask, and nothing is ever sent. Enter applies, Esc closes, \u{2318}Z undoes.") {
            Line(title: "Write with a key", detail: "Open the bar in any text field: LinkedIn, Slack, Discord, Teams, Outlook\u{2026}", divider: settings.writeInOtherApps) {
                Toggle("", isOn: $settings.writeInOtherApps).labelsHidden().toggleStyle(.switch)
            }
            if settings.writeInOtherApps {
                Line(title: "Key", divider: !settings.screenAllowed) {
                    Picker("Key", selection: $settings.writeKey) {
                        ForEach(WriteKey.allCases) { key in Text(key.title).tag(key) }
                    }
                    .labelsHidden()
                    .frame(width: 180)
                }
                if !settings.screenAllowed {
                    Line(title: "Accessibility permission needed", detail: "To read the field and put text in it.", divider: false) {
                        Button("Allow\u{2026}") { ScreenReader.askForPermission() }
                    }
                }
            }
        }
    }

    private var needsAccessibility: Bool { settings.useScreen && !settings.screenAllowed }

    // MARK: - About

    @ViewBuilder private var about: some View {
        Card(title: "This Mac") {
            Line(title: "Hotkey", detail: settings.writeInOtherApps ? "Tap \(settings.writeKey.title)" : "Off") {
                if hotkeyActive {
                    Label("Active", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                } else {
                    Button("Allow\u{2026}", action: SystemSettings.openInputMonitoring)
                }
            }
            Line(title: "Server", divider: false) {
                Text(settings.serverState.isEmpty ? "\u{2026}" : settings.serverState)
                    .foregroundStyle(settings.serverState == "ready" ? Color.green : Color.secondary)
            }
        }
        Card(title: "Updates", note: settings.updateMessage) {
            Line(title: "Version \(settings.currentVersion.description)", detail: "Installs the latest release from GitHub.") {
                HStack(spacing: 8) {
                    if settings.isUpdating { ProgressView().controlSize(.small) }
                    Button("Update", action: settings.update).disabled(settings.isUpdating)
                }
            }
            Line(title: "Update automatically", detail: "Checks every few hours and installs new releases when nothing is open. Tzara restarts.", divider: false) {
                Toggle("", isOn: $settings.autoUpdate).labelsHidden().toggleStyle(.switch)
            }
        }
        Card(title: "Uninstall") {
            Line(title: "Uninstall Tzara", detail: "Removes the app, the background server and the tzara command. Ollama and its models are left alone.", divider: false) {
                Button("Uninstall\u{2026}", role: .destructive, action: confirmUninstall).disabled(settings.isUninstalling)
            }
        }
    }

    // MARK: - Model

    private func modelLabel(_ model: ModelChoice) -> String {
        switch model.need {
        case .ready: return model.shortName
        case .download: return "\(model.shortName)  \u{2013} download"
        case .key: return "\(model.shortName)  \u{2013} needs a key"
        case .ollama: return "\(model.shortName)  \u{2013} needs Ollama"
        }
    }

    /// What the picked model still needs: a download, a key, or Ollama running.
    @ViewBuilder private var setupPanel: some View {
        if let choice = settings.needsSetup {
            VStack(alignment: .leading, spacing: 8) {
                switch choice.need {
                case .download:
                    downloadPanel(choice)
                case .key:
                    keyPanel(choice)
                case .ollama:
                    Text("This model runs on your Mac through Ollama, and Ollama is not running.")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("Open Ollama") {
                        NSWorkspace.shared.open(URL(fileURLWithPath: "/Applications/Ollama.app"))
                    }
                case .ready:
                    EmptyView()
                }
            }
        }
    }

    @ViewBuilder private func downloadPanel(_ choice: ModelChoice) -> some View {
        let download = settings.activeDownload
        if let download, !download.finished {
            HStack(spacing: 8) {
                if let fraction = download.fraction {
                    ProgressView(value: fraction)
                } else {
                    ProgressView().controlSize(.small)
                }
                Text("Downloading \(choice.shortName)\u{2026}").font(.caption)
            }
        } else {
            Text("\(choice.shortName) is not on this Mac yet. It is a large download, and it runs without internet afterwards.")
                .font(.caption).foregroundStyle(.secondary)
            if let error = download?.error { Text(error).font(.caption).foregroundStyle(.orange) }
            Button("Download \(choice.shortName)", action: settings.downloadPicked)
        }
    }

    @ViewBuilder private func keyPanel(_ choice: ModelChoice) -> some View {
        Text("\(SettingsModel.providerTitle(choice.provider)) needs your API key. It is kept on this Mac only.")
            .font(.caption).foregroundStyle(.secondary)
        HStack {
            SecureField("API key", text: $settings.keyDraft)
                .onSubmit(settings.saveKeyForPicked)
            Button("Save and use", action: settings.saveKeyForPicked)
                .disabled(settings.keyDraft.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        if let page = SettingsModel.keyPage(for: choice.provider) {
            Link("Get a key", destination: page).font(.caption)
        }
    }

    /// While a switch is under way the menu shows the model being loaded, then snaps back to
    /// the old one if loading fails.
    private var selection: Binding<String> {
        Binding(
            get: { settings.needsSetup?.id ?? settings.switchingTo ?? settings.currentModel },
            set: { settings.choose($0) })
    }

    @ViewBuilder private var switchStatus: some View {
        if let target = settings.switchingTo {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Loading \(target)\u{2026} about 15 s").font(.caption)
            }
        } else if let message = settings.message {
            Text(message).font(.caption).foregroundStyle(.secondary)
        }
    }

    // MARK: - Uninstall

    private func confirmUninstall() {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Uninstall Tzara?"
        alert.informativeText = "This removes the app, the background server and the tzara command. "
            + "Ollama, its models and the logs are left alone."
        alert.addButton(withTitle: "Uninstall").hasDestructiveAction = true
        alert.addButton(withTitle: "Cancel")
        NSApplication.shared.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertFirstButtonReturn { settings.uninstall() }
    }

}
