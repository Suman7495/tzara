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
