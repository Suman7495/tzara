import AppKit
import SwiftUI
import TzaraCore

/// The settings screen. It covers what the command-line chat does with `/list-models`,
/// `/current-model` and `/set-model`, plus the assistant's name and the state of the hotkey
/// and server.
struct SettingsView: View {
    @ObservedObject var settings: SettingsModel
    let assistantName: String
    let isRenaming: Bool
    let onRename: (String) -> Void
    let hotkeyIsActive: () -> Bool

    @State private var nameDraft = ""
    @FocusState private var nameFocused: Bool
    @State private var hotkeyActive = false

    var body: some View {
        Form {
            Section {
                modelPicker
                otherModel
                switchStatus
            }
            Section {
                HStack {
                    TextField("Name", text: $nameDraft)
                        .focused($nameFocused)
                        .onSubmit { onRename(nameDraft) }
                        .onChange(of: nameFocused) { focused in if !focused { onRename(nameDraft) } }
                        .disabled(isRenaming)
                        .help("Press Return to rename. The assistant uses this name for itself.")
                    if isRenaming { ProgressView().controlSize(.small) }
                }
            }
            Section("Memory") { memorySection }
            Section("What Tzara can see") { screenSection }
            Section {
                LabeledContent("Hotkey") { hotkeyStatus }
                LabeledContent("Server") { serverStatus }
                updates
                Button("Uninstall Tzara", role: .destructive, action: confirmUninstall)
                    .buttonStyle(.borderedProminent)
                    .tint(.red)
                    .disabled(settings.isUninstalling)
            }
        }
        .formStyle(.grouped)
        .scrollContentBackground(.hidden)
        .onAppear {
            settings.load()
            settings.refreshScreenPermission()
            nameDraft = assistantName
            hotkeyActive = hotkeyIsActive()
        }
        .onChange(of: assistantName) { nameDraft = $0 }
    }

    // MARK: - Model

    @ViewBuilder private var modelPicker: some View {
        if settings.providers.isEmpty {
            LabeledContent("Model") { ProgressView().controlSize(.small) }
        } else {
            Picker("Model", selection: selection) {
                if let unlisted = settings.unlistedCurrentModel {
                    Text(unlisted).tag(unlisted)
                }
                ForEach(settings.providers) { provider in
                    Section(provider.name.uppercased()) {
                        ForEach(provider.models) { model in
                            Text(model.shortName).tag(model.id)
                        }
                    }
                }
            }
            .pickerStyle(.menu)
            .disabled(settings.isSwitching)

            if let description = settings.currentDescription {
                Text(description).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    /// While a switch is under way the menu shows the model being loaded, then snaps back to
    /// the old one if loading fails.
    private var selection: Binding<String> {
        Binding(
            get: { settings.switchingTo ?? settings.currentModel },
            set: { settings.choose($0) })
    }

    private var otherModel: some View {
        HStack {
            TextField("Other", text: $settings.customModel, prompt: Text("provider:model-name"))
                .onSubmit(settings.chooseCustom)
            Button("Use", action: settings.chooseCustom)
                .disabled(settings.customModel.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .disabled(settings.isSwitching)
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

    // MARK: - Memory

    @ViewBuilder private var memorySection: some View {
        if let memory = settings.memory {
            if !memory.enabled {
                Text("Memory is turned off (TZARA_MEMORY=false). Nothing is kept between conversations.")
                    .font(.caption).foregroundStyle(.secondary)
            } else if memory.facts.isEmpty {
                Text("Nothing yet. Tell Tzara \u{201C}remember that \u{2026}\u{201D} and it will show up here.")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                ForEach(memory.facts) { fact in
                    HStack(alignment: .firstTextBaseline) {
                        Text(fact.text).font(.callout).textSelection(.enabled)
                        Spacer(minLength: 8)
                        Button { settings.forget(fact) } label: { Image(systemName: "trash") }
                            .buttonStyle(.plain)
                            .foregroundStyle(.secondary)
                            .help("Forget this")
                            .accessibilityLabel("Forget: \(fact.text)")
                    }
                }
            }
            if memory.enabled {
                Button("Erase all memory\u{2026}", role: .destructive) { confirmErase(memory) }
                    .disabled(memory.facts.isEmpty && memory.conversations == 0)
            }
        } else {
            ProgressView().controlSize(.small)
        }
        if let message = settings.memoryMessage {
            Text(message).font(.caption).foregroundStyle(.secondary)
        }
    }

    private func confirmErase(_ memory: MemoryOverview) {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Erase everything Tzara remembers?"
        alert.informativeText = "This forgets \(memory.facts.count) things about you and "
            + "\(memory.conversations) stored conversations, including the one on screen. It cannot be undone."
        alert.addButton(withTitle: "Erase").hasDestructiveAction = true
        alert.addButton(withTitle: "Cancel")
        NSApplication.shared.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertFirstButtonReturn { settings.eraseMemory() }
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

    // MARK: - What Tzara can see

    @ViewBuilder private var screenSection: some View {
        Toggle("Use the app and selected text in front", isOn: $settings.useScreen)
            .help("When you open the box, Tzara notes which app, window and selected text you were in, so you can say \u{201C}translate this\u{201D}.")
        Text(
            "The box shows what it will send, and you can remove it. "
                + "Nothing is read from password managers or password fields."
        )
        .font(.caption).foregroundStyle(.secondary)
        TextField("Translate into", text: $settings.translateInto, prompt: Text(ScreenPreferences.systemLanguage))
            .help("The language the Translate button uses. Anything already in that language goes into English.")
        if settings.useScreen, !settings.screenAllowed {
            HStack {
                Text("Needs the Accessibility permission to read selected text and window titles.")
                    .font(.caption).foregroundStyle(.orange)
                Button("Open Settings", action: SystemSettings.openAccessibility)
            }
        }
    }

    // MARK: - Updates

    @ViewBuilder private var updates: some View {
        LabeledContent("Version \(settings.currentVersion.description)") {
            HStack(spacing: 8) {
                if settings.isUpdating { ProgressView().controlSize(.small) }
                Button("Update", action: settings.update)
                    .disabled(settings.isUpdating)
                    .help("Install the latest release from GitHub")
            }
        }
        Toggle("Update automatically", isOn: $settings.autoUpdate)
            .help("Checks every few hours and installs new releases when this box is closed. Tzara restarts.")
        if let message = settings.updateMessage {
            Text(message).font(.caption).foregroundStyle(.secondary)
        }
    }

    // MARK: - This Mac

    private var hotkeyStatus: some View {
        HStack(spacing: 8) {
            Text("Tap Control + Option")
            if hotkeyActive {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
            } else {
                Button("Allow\u{2026}", action: SystemSettings.openInputMonitoring)
            }
        }
    }

    private var serverStatus: some View {
        Text(settings.serverState.isEmpty ? "\u{2026}" : settings.serverState)
            .foregroundStyle(settings.serverState == "ready" ? Color.green : Color.secondary)
    }
}
