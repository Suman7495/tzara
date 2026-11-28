import Foundation
import TzaraCore

/// What the Apps part of the settings shows and does: the apps Tzara is connected to (through
/// MCP), the ready-made ones to add, and a form for one of the user's own.
@MainActor
final class AppsModel: ObservableObject {
    @Published private(set) var overview: AppsOverview?
    /// What to tell the user: what went wrong, or what to do first with an app just added.
    @Published private(set) var message: String?
    @Published var draft = AppDraft()
    /// How signing in goes, by app name.
    @Published private(set) var signIns: [String: SignInState] = [:]
    /// The app whose tools are open for choosing, with its list as the user is editing it.
    @Published private(set) var toolsOf: String?
    @Published var toolList: AppToolList?
    /// The tools as the server has them, to tell whether the user changed anything.
    private var savedTools: Set<String> = []
    @Published var toolsMessage: String?
    @Published var toolFilter = ""
    /// Apps the user pressed Remove on, until the server has dropped them.
    @Published private(set) var removing: Set<String> = []
    /// Apps signed in to during this run.
    @Published private(set) var signedIn: Set<String> = []

    private let client: ChatClient
    private var watching: Task<Void, Never>?

    /// How often, and for how long, to look again while an app is connecting.
    private static let pollSeconds: UInt64 = 1
    private static let patience = 120

    init(client: ChatClient) {
        self.client = client
    }

    var isConnecting: Bool { overview?.reloading == true }

    func load() {
        Task {
            do {
                overview = try await client.apps()
                if isConnecting { watch() }
            } catch {
                message = Self.describe(error)
            }
        }
    }

    /// Adds a ready-made app, and says what to do first with it.
    func add(_ preset: AppPreset, token: String = "") {
        message = "\(preset.title) is connecting\u{2026} \(preset.firstUse)"
        namesBefore = Set(overview?.apps.map(\.name) ?? [])
        guide = true
        send(NewApp(preset: preset.id, token: token))
    }

    /// The apps there were when the user pressed Add, so the one that appears can be told apart.
    private var namesBefore: Set<String> = []
    /// Whether to start signing in to the app just added, as soon as it is there.
    private var guide = false

    /// Right after an app that needs signing in is added, starts the sign-in: the user pressed Add,
    /// so the next step is theirs to finish, not to look for.
    private func startGuidedSignIn() {
        guard guide, let apps = overview?.apps, overview?.reloading == false else { return }
        guard let fresh = apps.first(where: { !namesBefore.contains($0.name) }) else { return }
        guide = false
        if fresh.signIn { signIn(fresh) }
    }

    /// Adds the app typed in the form, if it is complete.
    func addCustom() {
        guard draft.problem == nil else { return }
        send(draft.request)
        draft = AppDraft()
    }

    /// Runs the app's own login. The user gets a link and a code, and the app reconnects by itself.
    func signIn(_ app: ConnectedApp) {
        message = nil
        Task {
            do {
                signIns[app.name] = try await client.startSignIn(app.name)
                for _ in 0..<600 {
                    try? await Task.sleep(nanoseconds: 1_000_000_000)
                    let state = try await client.signInState(app.name)
                    signIns[app.name] = state
                    if !state.running { break }
                }
                if signIns[app.name]?.done == true {
                    signedIn.insert(app.name)
                    watch()
                }
            } catch {
                message = Self.describe(error)
            }
        }
    }

    /// Opens (or closes) the list of an app's tools, to tick the ones the model may use.
    func toggleTools(_ app: ConnectedApp) {
        if toolsOf == app.name {
            toolsOf = nil
            return
        }
        toolsOf = app.name
        toolList = nil
        toolsMessage = nil
        toolFilter = ""
        Task {
            do {
                let list = try await client.appTools(app.name)
                toolList = list
                savedTools = Set(list.tools.filter(\.enabled).map(\.name))
            } catch {
                toolsMessage = Self.describe(error)
            }
        }
    }

    func setTool(_ name: String, enabled: Bool) {
        guard var list = toolList, let index = list.tools.firstIndex(where: { $0.name == name }) else { return }
        var tools = list.tools
        tools[index].enabled = enabled
        list = AppToolList(tools: tools, limit: list.limit)
        toolList = list
        toolsMessage = list.used > list.limit
            ? "Too many: these use \(list.used) of the \(list.limit) that fit. Untick some." : nil
    }

    var toolsChanged: Bool {
        guard let list = toolList else { return false }
        return Set(list.tools.filter(\.enabled).map(\.name)) != savedTools
    }

    /// Applies the ticks. Tzara rebuilds what it can do, which takes a few seconds.
    func applyTools() {
        guard let app = toolsOf, let list = toolList, list.used <= list.limit else { return }
        let chosen = list.tools.filter(\.enabled).map(\.name)
        toolsMessage = "Applying\u{2026}"
        Task {
            do {
                try await client.setAppTools(app, tools: chosen)
                savedTools = Set(chosen)
                toolsMessage = nil
                watch()
            } catch {
                toolsMessage = Self.describe(error)
            }
        }
    }

    func remove(_ app: ConnectedApp) {
        message = nil
        removing.insert(app.name)
        Task {
            do {
                overview = try await client.removeApp(app.name)
                watch()
            } catch {
                removing.remove(app.name)
                message = Self.describe(error)
            }
        }
    }

    private func send(_ app: NewApp) {
        Task {
            do {
                overview = try await client.addApp(app)
                watch()
            } catch {
                message = Self.describe(error)
            }
        }
    }

    /// Looks again every second until the change has been applied: connecting to an app can
    /// mean downloading it, which takes a while.
    private func watch() {
        watching?.cancel()
        watching = Task {
            for _ in 0..<Self.patience {
                try? await Task.sleep(nanoseconds: Self.pollSeconds * 1_000_000_000)
                if Task.isCancelled { return }
                guard let latest = try? await client.apps() else { continue }
                overview = latest
                if !latest.reloading {
                    removing.formIntersection(Set(latest.apps.map(\.name)))
                    startGuidedSignIn()
                    return
                }
            }
        }
    }

    private static func describe(_ error: Error) -> String {
        (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
    }
}
