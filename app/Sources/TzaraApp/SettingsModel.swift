import Foundation
import TzaraCore

/// What the settings screen shows and does: the same things the command-line chat offers as
/// `/list-models`, `/current-model` and `/set-model`.
@MainActor
final class SettingsModel: ObservableObject {
    @Published private(set) var providers: [ModelProvider] = []
    @Published private(set) var currentModel = ""
    @Published private(set) var serverState = ""
    /// What is typed in the "other model" field.
    @Published var customModel = ""
    /// The model being loaded right now, if a switch is in progress.
    @Published private(set) var switchingTo: String?
    /// The outcome of the last switch: what went wrong, or what is now in use.
    @Published private(set) var message: String?

    /// What the Update button is doing or found: checking, installing, up to date, or what went wrong.
    @Published private(set) var updateMessage: String?
    /// True from the click on Update until the app restarts (or the check says there is nothing to do).
    @Published private(set) var isUpdating = false

    /// Whether Tzara updates itself. Changing it is saved at once.
    @Published var autoUpdate: Bool {
        didSet { preferences.autoUpdate = autoUpdate }
    }

    /// Set once the user has confirmed the uninstall; the app disappears a few seconds later.
    @Published private(set) var isUninstalling = false

    private let client: ChatClient
    private let updater: Updater
    private let uninstaller: UpdateInstaller
    private let preferences: UpdatePreferences

    /// The version of the running app. A copy run straight from the build folder has none.
    let currentVersion: AppVersion

    init(
        client: ChatClient,
        updater: Updater = Updater(),
        uninstaller: UpdateInstaller = UpdateInstaller(),
        preferences: UpdatePreferences = UpdatePreferences(),
        currentVersion: AppVersion? = AppVersion(
            Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")
    ) {
        self.client = client
        self.updater = updater
        self.uninstaller = uninstaller
        self.preferences = preferences
        autoUpdate = preferences.autoUpdate
        self.currentVersion = currentVersion ?? AppVersion("0")!
    }

    var isSwitching: Bool { switchingTo != nil }

    /// The explanation of the model being used or loaded, if it is a listed one.
    var currentDescription: String? {
        let target = switchingTo ?? currentModel
        return providers.lazy.flatMap(\.models).first { $0.id == target }?.description
    }

    /// The model in use if it is not one of the listed ones (it was typed in by hand), so the
    /// dropdown still has something valid to show.
    var unlistedCurrentModel: String? {
        guard !currentModel.isEmpty, !providers.contains(where: { $0.models.contains { $0.id == currentModel } })
        else { return nil }
        return currentModel
    }

    /// Fetches the model list and the current model. Called when the settings screen opens.
    func load() {
        Task {
            do {
                providers = try await client.models()
                let status = try await client.status()
                currentModel = status.model
                serverState = status.state
                FileLog.shared.info("settings: \(providers.count) providers, model \(status.model)")
            } catch {
                message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
            }
        }
    }

    func choose(_ model: String) {
        guard !isSwitching, model != currentModel else { return }
        switchingTo = model
        message = nil
        Task {
            do {
                currentModel = try await client.setModel(model)
                message = "Now using \(currentModel)."
            } catch {
                message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
            }
            switchingTo = nil
        }
    }

    func chooseCustom() {
        let model = customModel.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !model.isEmpty else { return }
        choose(model)
    }

    /// Looks for the latest release and, if it is newer than this app, installs it.
    ///
    /// The installer replaces the server and this app and opens the new one, so Tzara quits and
    /// comes back by itself after about a minute.
    func update() {
        guard !isUpdating else { return }
        isUpdating = true
        updateMessage = "Checking for updates\u{2026}"
        Task {
            do {
                preferences.lastCheck = Date()
                let outcome = try await updater.run(current: currentVersion) { version in
                    FileLog.shared.info("updating from \(currentVersion) to \(version)")
                    updateMessage = "Installing \(version). Tzara will restart by itself in about a minute."
                }
                if case let .upToDate(version) = outcome {
                    updateMessage = "You have the latest version (\(version))."
                    isUpdating = false
                }
            } catch {
                FileLog.shared.info("update failed: \(error.localizedDescription)")
                updateMessage = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
                isUpdating = false
            }
        }
    }

    /// Removes Tzara: the app, the login items, the server and the command. Ollama, its models
    /// and the logs stay. Call this only after the user has confirmed.
    func uninstall() {
        guard !isUninstalling else { return }
        isUninstalling = true
        updateMessage = "Uninstalling\u{2026} Tzara will close in a few seconds."
        FileLog.shared.info("uninstalling")
        do {
            try uninstaller.uninstall(currentVersionIfKnown)
        } catch {
            isUninstalling = false
            updateMessage = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }

    private var currentVersionIfKnown: AppVersion? {
        currentVersion == AppVersion("0") ? nil : currentVersion
    }
}
