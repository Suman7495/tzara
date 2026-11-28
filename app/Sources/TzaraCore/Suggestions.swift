import Foundation

/// A button offered under an answer, or above the input when something is selected: one tap sends
/// its message. Made in code, from what is there: no model request, so no seconds of waiting.
public struct Suggestion: Equatable, Sendable, Identifiable {
    public var id: String { label }
    public let label: String
    public let message: String
}

public enum Suggestions {
    /// What to offer.
    ///
    /// - Parameters:
    ///   - hasScreenText: The user has text selected or a page open: offer to explain, summarise or
    ///     translate it.
    ///   - lastAnswer: Tzara's last answer, if there is one to follow up: shorten it, make it simpler,
    ///     go on, translate it.
    ///   - language: The language "Translate" translates into: the Mac's own.
    public static func make(hasScreenText: Bool, lastAnswer: String?, language: String = systemLanguage) -> [Suggestion] {
        if let answer = lastAnswer?.trimmingCharacters(in: .whitespacesAndNewlines), !answer.isEmpty {
            var offered = [Suggestion(label: "Tell me more", message: "Tell me more.")]
            if answer.count > 280 { offered.append(Suggestion(label: "Shorter", message: "Make that shorter.")) }
            offered.append(Suggestion(label: "Simpler", message: "Explain that in simpler terms."))
            offered.append(Suggestion(label: "Translate", message: "Translate that into \(language)."))
            return offered
        }
        guard hasScreenText else { return [] }
        return [
            Suggestion(label: "Explain", message: "Explain this."),
            Suggestion(label: "Summarize", message: "Summarize this."),
            Suggestion(label: "Translate", message: "Translate this into \(language)."),
        ]
    }

    /// The Mac's language by its English name, such as "German".
    public static var systemLanguage: String {
        let code = Locale.current.language.languageCode?.identifier ?? "en"
        return Locale(identifier: "en").localizedString(forLanguageCode: code) ?? "English"
    }
}
