import Foundation
import TzaraCore

// Talks to a running Tzara server from the terminal, to check the client against the real
// thing without opening the app:
//
//     swift run TzaraChatProbe "What is 6 * 7?"     send a message, stream the reply
//     swift run TzaraChatProbe status               state, model and name
//     swift run TzaraChatProbe models               the models, by provider
//     swift run TzaraChatProbe history              the conversation so far
//     swift run TzaraChatProbe model ollama:mistral switch model
//     swift run TzaraChatProbe rename Jarvis        rename the assistant

let standardError = FileHandle.standardError

/// Writes straight to stdout, so lines and streamed pieces keep their order.
func show(_ text: String) {
    FileHandle.standardOutput.write(Data(text.utf8))
}

func fail(_ message: String, code: Int32 = 1) -> Never {
    standardError.write(Data((message + "\n").utf8))
    exit(code)
}

let arguments = Array(CommandLine.arguments.dropFirst())
guard let command = arguments.first else { fail("usage: TzaraChatProbe \"message\" | status | models | history | model ID | rename NAME", code: 2) }
let argument = arguments.dropFirst().joined(separator: " ")
let client = ChatClient()

do {
    switch command {
    case "status":
        let status = try await client.status()
        show("state=\(status.state) model=\(status.model) name=\(status.name) busy=\(status.busy)\n")
    case "models":
        for provider in try await client.models() {
            show("\(provider.name)\n")
            for model in provider.models { show("  \(model.id)  \u{2014} \(model.description)\n") }
        }
    case "history":
        for item in try await client.history() { show("\(item.role.rawValue): \(item.text)\n") }
    case "model":
        show("now using \(try await client.setModel(argument))\n")
    case "rename":
        show("now called \(try await client.rename(argument))\n")
    default:
        let message = arguments.joined(separator: " ")
        let status = try await client.status()
        show("server: \(status.state), model \(status.model), name \(status.name)\n")
        for try await event in client.stream(message) {
            switch event {
            case let .token(text):
                show(text)
            case let .tool(name, state):
                show("\n[tool \(name): \(state.rawValue)]")
            case let .status(text):
                show("\n[\(text)]\n")
            case let .choices(options):
                show("\n[buttons: \(options.map(\.label).joined(separator: " | "))]")
            case let .done(firstTokenSeconds, characters):
                let first = firstTokenSeconds.map { String(format: "%.2fs", $0) } ?? "n/a"
                show("\n[done: \(characters) characters, first token \(first)]\n")
            case let .error(text):
                fail("\n[error: \(text)]")
            }
        }
    }
} catch {
    fail((error as? LocalizedError)?.errorDescription ?? "\(error)")
}
