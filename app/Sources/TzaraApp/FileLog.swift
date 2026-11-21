import Foundation

/// A tiny log file, `~/Library/Logs/tzara/app.log`, next to the Python side's log.
///
/// Tzara is a menu-bar app with no terminal, so this is where to look when the hotkey
/// does nothing. It records what the app is doing, never what you type or what Tzara says.
final class FileLog: @unchecked Sendable {
    static let shared = FileLog()

    private let url: URL
    private let queue = DispatchQueue(label: "tzara.app.log")
    private let formatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss,SSS"
        return formatter
    }()

    private init() {
        let directory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/tzara", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        url = directory.appendingPathComponent("app.log")
    }

    var path: String { url.path }

    func info(_ message: String) {
        queue.async {
            let line = "\(self.formatter.string(from: Date())) INFO    app: \(message)\n"
            guard let data = line.data(using: .utf8) else { return }
            if let handle = try? FileHandle(forWritingTo: self.url) {
                handle.seekToEndOfFile()
                handle.write(data)
                try? handle.close()
            } else {
                try? data.write(to: self.url)
            }
        }
    }
}
