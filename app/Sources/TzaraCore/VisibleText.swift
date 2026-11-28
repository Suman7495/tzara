import Foundation

/// Gathers the text of a window up to a size, leaving out repeats and blanks.
public struct VisibleText: Sendable {
    public let limit: Int
    public var visited = 0
    public private(set) var pieces: [String] = []
    private var size = 0
    private var last = ""

    public init(limit: Int) { self.limit = limit }

    public var isFull: Bool { size >= limit }
    public var text: String { pieces.joined(separator: "\n") }

    public mutating func add(_ raw: String, cut: Int) {
        let line = raw.split(whereSeparator: \.isNewline).joined(separator: " ")
            .trimmingCharacters(in: .whitespaces)
        guard !line.isEmpty, line != last else { return }
        let kept = line.count > cut ? String(line.prefix(cut)) + "\u{2026}" : line
        pieces.append(kept)
        size += kept.count + 1
        last = line
    }
}
