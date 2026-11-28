import CoreGraphics
import Foundation

/// What a line typed in the bar is for: written into the field in the other app, or a question
/// to answer in the bar. Decided from the words and the field's state, with no model request.
public enum BarIntent: Equatable, Sendable {
    case write
    case chat

    /// Words a line starts with when it asks for text to be written or changed.
    static let writeStarts: [String] = [
        "write", "reply", "respond", "draft", "compose", "rewrite", "rephrase", "shorten", "lengthen",
        "translate", "fix", "polish", "proofread", "tweak", "make it", "make this", "make the", "add ",
        "say ", "tell ", "ask ", "decline", "accept", "thank", "congratulate", "invite", "politely",
        "more ", "less ", "shorter", "longer", "simpler", "formal", "casual", "friendlier", "expand",
        "email ", "message ", "dm ", "answer with", "continue", "finish", "change ", "remove ", "replace ",
    ]

    /// Clear requests for text: written (or put on the clipboard) even where no field is found.
    static let strongStarts: [String] = ["write", "reply", "respond", "draft", "compose", "rewrite", "rephrase", "email ", "dm "]

    /// Words a line starts with when it is a question or asks about text (explain it, summarise it)
    /// rather than for text to be written. Checked first: "explain this" must never replace the
    /// selection with an explanation.
    static let questionStarts: [String] = [
        "what", "why", "how", "who", "when", "where", "which", "is ", "are ", "do ", "does ", "did ",
        "can ", "could ", "should ", "would ", "will ", "tell me", "show me", "explain", "summar",
        "define", "describe", "analy", "meaning", "mean ", "tldr", "tl;dr", "eli5", "review", "check ",
        "search", "find", "look up", "open ", "remember", "forget", "list ", "compare",
    ]

    /// Polite words in front of a request, ignored when deciding.
    static let politeStarts: [String] = ["please ", "can you ", "could you ", "would you ", "will you ", "hey ", "hi ", "ok ", "so "]

    /// Words that ask for text, anywhere in the line ("help me write a reply", "I want to respond to him").
    static let writeWords: [String] = [
        "write", "reply", "respond", "draft", "compose", "rewrite", "rephrase", "reword", "shorten", "lengthen",
        "polish", "proofread", "tweak", "improve", "translate", "email", "message", "response", "answer them",
    ]

    /// Decides what `text` is for.
    ///
    /// Questions and requests to explain or summarise are chat, always, checked first: "explain this"
    /// never replaces the selection. In a text field everything else is written: the user opened the
    /// bar there to put something in it ("friendlier", "thank her for the gift", "help me reply").
    /// Where no field is found it is written only if it clearly asks for text, and goes to the
    /// clipboard.
    public static func decide(_ text: String, hasField: Bool, hasSelection: Bool, hasDraft: Bool) -> BarIntent {
        var line = text.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if line.hasPrefix("/w") { return .write }
        while let polite = politeStarts.first(where: line.hasPrefix) { line.removeFirst(polite.count) }
        if questionStarts.contains(where: line.hasPrefix) { return .chat }
        let clearlyText = strongStarts.contains(where: line.hasPrefix) || writeWords.contains { line.contains($0) }
        if !hasField { return clearlyText ? .write : .chat }
        let asksForText = clearlyText || writeStarts.contains(where: line.hasPrefix)
        // A question mark in a field is a question, unless it plainly asks for text.
        return line.hasSuffix("?") && !asksForText ? .chat : .write
    }
}

/// How tall the bar is.
public enum BarLayout {
    public static let width: CGFloat = 420
    public static let inputHeight: CGFloat = 48
    public static let chipHeight: CGFloat = 30
    public static let minimumAnswer: CGFloat = 70
    public static let maximumAnswer: CGFloat = 300

    /// The height of the answer area for `characters` of text and `steps` lines of what was done:
    /// as much as it needs, within limits, past which it scrolls.
    public static func answerHeight(characters: Int, steps: Int, hasButtons: Bool) -> CGFloat {
        let lines = max(1, (characters + 49) / 50)
        let needed = 28 + CGFloat(lines) * 19 + CGFloat(steps) * 20 + (hasButtons ? 34 : 0)
        return min(maximumAnswer, max(minimumAnswer, needed))
    }

    /// The whole bar. It is one of three things: the prompt (input line and the chip for what Tzara can
    /// see), only the response (while Tzara is answering), or the response with the prompt below it
    /// (once it has finished).
    public static func height(hasChip: Bool, answer: CGFloat?, showsInput: Bool = true) -> CGFloat {
        (showsInput ? inputHeight + (hasChip ? chipHeight : 0) : 0) + (answer ?? 0)
    }
}
