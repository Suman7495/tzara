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

    private let client: ChatClient

    init(client: ChatClient) {
        self.client = client
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
}
