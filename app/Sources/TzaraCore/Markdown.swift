import Foundation

/// One block of a chat message, after splitting its Markdown into paragraphs, lists and so on.
public enum MarkdownBlock: Equatable, Sendable {
    case paragraph(String)
    case heading(level: Int, text: String)
    case bullet(depth: Int, text: String)
    case numbered(depth: Int, number: Int, text: String)
    case quote(String)
    case code(String)
    case rule
}

/// Turns the Markdown that models write into something the chat can draw.
///
/// SwiftUI's `Text` understands bold, italic, code and links inside a line, but not lists,
/// headings or code blocks, so the message is first split into ``MarkdownBlock``s, and each
/// block's text is then read with ``inline(_:)``.
///
/// Only `http` and `https` links are ever clickable. A model (or a web page it read) could
/// otherwise write `[click](file:///...)` or a link to some app's custom address.
public enum Markdown {
    /// Splits a message into blocks. Never fails: what it does not know stays plain text, and
    /// a code block that has not been closed yet (the reply is still streaming) runs to the end.
    public static func blocks(from text: String) -> [MarkdownBlock] {
        var blocks: [MarkdownBlock] = []
        var paragraph: [String] = []
        var code: [String]?

        func endParagraph() {
            if !paragraph.isEmpty { blocks.append(.paragraph(paragraph.joined(separator: "\n"))) }
            paragraph = []
        }

        // Swift counts "\r\n" as one character, so splitting on "\n" alone would miss it.
        let normalized = text.replacingOccurrences(of: "\r\n", with: "\n")
        for rawLine in normalized.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(rawLine)
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if let open = code {
                if trimmed.hasPrefix("```") {
                    blocks.append(.code(open.joined(separator: "\n")))
                    code = nil
                } else {
                    code = open + [line]
                }
                continue
            }
            if trimmed.hasPrefix("```") {
                endParagraph()
                code = []
            } else if trimmed.isEmpty {
                endParagraph()
            } else if isRule(trimmed) {
                endParagraph()
                blocks.append(.rule)
            } else if let (level, heading) = heading(trimmed) {
                endParagraph()
                blocks.append(.heading(level: level, text: heading))
            } else if let item = listItem(line) {
                endParagraph()
                blocks.append(item)
            } else if trimmed.hasPrefix(">") {
                endParagraph()
                blocks.append(.quote(String(trimmed.dropFirst()).trimmingCharacters(in: .whitespaces)))
            } else {
                paragraph.append(trimmed)
            }
        }
        if let open = code { blocks.append(.code(open.joined(separator: "\n"))) }
        endParagraph()
        return blocks
    }

    /// Reads the formatting inside one line or paragraph: **bold**, *italic*, `code`,
    /// ~~strikethrough~~ and links, written as `[title](https://...)` or just pasted.
    public static func inline(_ text: String) -> AttributedString {
        var result =
            (try? AttributedString(
                markdown: text,
                options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(text)
        removeUnsafeLinks(from: &result)
        linkBareAddresses(in: &result)
        return result
    }

    /// Whether a link may be opened from the chat: a web address and nothing else.
    public static func isSafe(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased() else { return false }
        return (scheme == "http" || scheme == "https") && url.host?.isEmpty == false
    }

    // MARK: - Blocks

    private static func isRule(_ trimmed: String) -> Bool {
        guard trimmed.count >= 3, let first = trimmed.first, "-*_".contains(first) else { return false }
        return trimmed.allSatisfy { $0 == first }
    }

    private static func heading(_ trimmed: String) -> (Int, String)? {
        let level = trimmed.prefix(while: { $0 == "#" }).count
        guard (1...6).contains(level) else { return nil }
        let rest = trimmed.dropFirst(level)
        guard rest.first == " " else { return nil }
        return (level, rest.trimmingCharacters(in: .whitespaces))
    }

    private static func listItem(_ line: String) -> MarkdownBlock? {
        var indent = 0
        for character in line {
            if character == " " { indent += 1 } else if character == "\t" { indent += 4 } else { break }
        }
        let depth = min(indent / 2, 3)
        let rest = line.trimmingCharacters(in: .whitespaces)

        for marker in ["- ", "* ", "+ ", "\u{2022} "] where rest.hasPrefix(marker) {
            return .bullet(depth: depth, text: String(rest.dropFirst(marker.count)).trimmingCharacters(in: .whitespaces))
        }
        let digits = rest.prefix(while: \.isNumber)
        let after = rest.dropFirst(digits.count)
        if (1...3).contains(digits.count), let number = Int(digits),
            let separator = after.first, separator == "." || separator == ")",
            after.dropFirst().first == " "
        {
            return .numbered(
                depth: depth, number: number,
                text: String(after.dropFirst(2)).trimmingCharacters(in: .whitespaces))
        }
        return nil
    }

    // MARK: - Links

    private static func removeUnsafeLinks(from text: inout AttributedString) {
        let unsafe = text.runs.compactMap { run -> Range<AttributedString.Index>? in
            guard let url = run.link, !isSafe(url) else { return nil }
            return run.range
        }
        for range in unsafe { text[range].link = nil }
    }

    /// Makes addresses that were written out in full clickable, as models often do not use
    /// Markdown links. Only text that starts with `http://` or `https://` counts: a detector
    /// that also links `main.py` or `notes.md` (both are real top-level domains) would be wrong
    /// more often than right.
    private static func linkBareAddresses(in text: inout AttributedString) {
        let plain = String(text.characters)
        guard plain.localizedCaseInsensitiveContains("http"),
            let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue)
        else { return }

        let found = detector.matches(in: plain, range: NSRange(plain.startIndex..., in: plain))
        for match in found {
            guard let url = match.url, isSafe(url),
                let written = Range(match.range, in: plain),
                plain[written].lowercased().hasPrefix("http"),
                let range = Range(match.range, in: text),
                !text[range].runs.contains(where: { $0.link != nil })
            else { continue }
            text[range].link = url
        }
    }
}
