import Combine
import Foundation
import TzaraCore

/// What the settings screen shows and does: the same things the command-line chat offers as
/// `/list-models`, `/current-model` and `/set-model`.
@MainActor
final class SettingsModel: ObservableObject {
    @Published private(set) var providers: [ModelProvider] = []
    @Published private(set) var currentModel = ""
    @Published private(set) var serverState = ""
    /// The model the user picked that still needs setting up (a download or a key).
    @Published private(set) var needsSetup: ModelChoice?
    /// The API key being typed for that model.
    @Published var keyDraft = ""
    /// How far the downloads have got.
    @Published private(set) var downloads: [ModelDownload] = []
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

    /// Whether tapping the write key makes Tzara write in the field of the app in front, and which key.
    @Published var writeInOtherApps: Bool {
        didSet {
            writePreferences.enabled = writeInOtherApps
            NotificationCenter.default.post(name: .writeSettingsChanged, object: nil)
        }
    }

    @Published var writeKey: WriteKey {
        didSet {
            writePreferences.key = writeKey
            NotificationCenter.default.post(name: .writeSettingsChanged, object: nil)
        }
    }

    private let writePreferences = WritePreferences()

    /// Whether Tzara may look at the app in front when the box opens. Changing it is saved at once;
    /// turning it on asks macOS for the Accessibility permission.
    @Published var useScreen: Bool {
        didSet {
            screenPreferences.enabled = useScreen
            if useScreen { ScreenReader.askForPermission() }
            screenAllowed = ScreenReader.isAllowed
        }
    }
    /// The language the Translate button translates into; empty means the language of the Mac.
    /// Whether macOS has given the permission that lets Tzara read selected text and window titles.
    @Published private(set) var screenAllowed = ScreenReader.isAllowed

    /// What Tzara remembers about the user, or `nil` until the server has said.
    @Published private(set) var memory: MemoryOverview?
    /// What went wrong the last time memory was changed.
    @Published private(set) var memoryMessage: String?
    /// Called after everything has been forgotten, so the chat can drop what it shows.
    var onMemoryErased: (() -> Void)?

    /// Set once the user has confirmed the uninstall; the app disappears a few seconds later.
    @Published private(set) var isUninstalling = false

    /// The apps Tzara is connected to through MCP.
    let apps: AppsModel
    /// Passes the apps' changes on: SwiftUI only watches this object, not the one inside it, so
    /// without this the list would not redraw when an app is added, removed or signed in to.
    private var appsChanges: AnyCancellable?

    private let client: ChatClient
    private let updater: Updater
    private let uninstaller: UpdateInstaller
    private let preferences: UpdatePreferences
    private let screenPreferences: ScreenPreferences

    /// The version of the running app. A copy run straight from the build folder has none.
    let currentVersion: AppVersion

    init(
        client: ChatClient,
        updater: Updater = Updater(),
        uninstaller: UpdateInstaller = UpdateInstaller(),
        preferences: UpdatePreferences = UpdatePreferences(),
        screenPreferences: ScreenPreferences = ScreenPreferences(),
        currentVersion: AppVersion? = AppVersion(
            Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")
    ) {
        self.client = client
        apps = AppsModel(client: client)
        self.updater = updater
        self.uninstaller = uninstaller
        self.preferences = preferences
        autoUpdate = preferences.autoUpdate
        self.screenPreferences = screenPreferences
        useScreen = screenPreferences.enabled
        writeInOtherApps = writePreferences.enabled
        writeKey = writePreferences.key
        self.currentVersion = currentVersion ?? AppVersion("0")!
        appsChanges = apps.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }
    }

    /// Looks again at whether macOS has given the permissions, which the user may just have done.
    func refreshScreenPermission() {
        screenAllowed = ScreenReader.isAllowed
    }

    var isSwitching: Bool { switchingTo != nil }

    /// The model in use if it is not one of the listed ones (it was typed in by hand), so the
    /// dropdown still has something valid to show.
    var unlistedCurrentModel: String? {
        guard !currentModel.isEmpty, !providers.contains(where: { $0.models.contains { $0.id == currentModel } })
        else { return nil }
        return currentModel
    }

    /// Fetches the model list and the current model. Called when the settings screen opens.
    func load() {
        loadMemory()
        apps.load()
        Task {
            do {
                let overview = try await client.modelOverview()
                providers = overview.providers
                downloads = overview.downloads
                let status = try await client.status()
                currentModel = status.model
                serverState = status.state
                FileLog.shared.info("settings: \(providers.count) providers, model \(status.model)")
            } catch {
                message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
            }
        }
    }

    /// Fetches what Tzara remembers. Called when the settings screen opens.
    func loadMemory() {
        Task {
            do {
                memory = try await client.memory()
                memoryMessage = nil
            } catch {
                memoryMessage = Self.describe(error)
            }
        }
    }

    /// Makes Tzara forget one thing it knows about the user.
    func forget(_ fact: MemoryFact) {
        Task {
            do {
                try await client.forgetFact(fact.id)
                memory = try await client.memory()
                memoryMessage = nil
            } catch {
                memoryMessage = Self.describe(error)
            }
        }
    }

    /// Makes Tzara forget everything it knows and every conversation it has stored. Call this
    /// only after the user has confirmed.
    /// Everything said in a stored conversation, to read it.
    func conversation(_ id: Int) async -> [StoredLine] {
        (try? await client.memoryConversation(id)) ?? []
    }

    func eraseMemory() {
        Task {
            do {
                try await client.eraseMemory()
                memory = try await client.memory()
                memoryMessage = "Forgot everything."
                onMemoryErased?()
            } catch {
                memoryMessage = Self.describe(error)
            }
        }
    }

    private static func describe(_ error: Error) -> String {
        (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
    }

    /// Picks a model. One that is ready is loaded; one that is not shows what it needs.
    func choose(_ model: String) {
        guard !isSwitching else { return }
        let choice = providers.lazy.flatMap(\.models).first { $0.id == model }
        if let choice, choice.need != .ready {
            needsSetup = choice
            keyDraft = ""
            message = nil
            return
        }
        needsSetup = nil
        guard model != currentModel else { return }
        switchingTo = model
        message = nil
        Task {
            do {
                currentModel = try await client.setModel(model)
                message = "Now using \(currentModel)."
            } catch {
                message = Self.describe(error)
            }
            switchingTo = nil
        }
    }

    /// The download of the model that needs setting up, if one is under way or has failed.
    var activeDownload: ModelDownload? {
        guard let model = needsSetup?.id else { return nil }
        return downloads.last { $0.model == model }
    }

    /// Downloads the model that was picked, then switches to it.
    func downloadPicked() {
        guard let choice = needsSetup, choice.need == .download else { return }
        Task {
            do {
                try await client.downloadModel(choice.id)
                await watchDownload(choice.id)
            } catch {
                message = Self.describe(error)
            }
        }
    }

    private func watchDownload(_ model: String) async {
        while !Task.isCancelled {
            if let overview = try? await client.modelOverview() {
                providers = overview.providers
                downloads = overview.downloads
                if let download = downloads.last(where: { $0.model == model }), download.finished {
                    if download.error == nil { choose(model) }
                    return
                }
            }
            try? await Task.sleep(nanoseconds: 1_000_000_000)
        }
    }

    /// Saves the API key typed for the model that was picked, then switches to it.
    func saveKeyForPicked() {
        guard let choice = needsSetup, choice.need == .key else { return }
        let key = keyDraft
        Task {
            do {
                try await client.saveKey(provider: choice.provider, key: key)
                keyDraft = ""
                let overview = try await client.modelOverview()
                providers = overview.providers
                choose(choice.id)
            } catch {
                message = Self.describe(error)
            }
        }
    }

    /// Where the user gets a key for a provider.
    static func keyPage(for provider: String) -> URL? {
        switch provider {
        case "anthropic": return URL(string: "https://console.anthropic.com/settings/keys")
        case "openai": return URL(string: "https://platform.openai.com/api-keys")
        case "google_genai": return URL(string: "https://aistudio.google.com/apikey")
        default: return nil
        }
    }

    /// A short name for a provider, for people.
    static func providerTitle(_ provider: String) -> String {
        switch provider {
        case "ollama": return "On this Mac (Ollama)"
        case "anthropic": return "Anthropic (Claude)"
        case "openai": return "OpenAI"
        case "google_genai": return "Google (Gemini)"
        default: return provider
        }
    }

    /// Looks for the latest release and, if it is newer than this app, installs it.
    ///
    /// The installer replaces the server and this app and opens the new one, so Tzara quits and
    /// comes back by itself after about a minute.
    func update() {
        guard !isUpdating else { return }
        guard InstalledCopy.isInstalled else {
            updateMessage = InstalledCopy.developmentNotice
            return
        }
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
        guard InstalledCopy.isInstalled else {
            updateMessage = InstalledCopy.developmentNotice
            return
        }
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
