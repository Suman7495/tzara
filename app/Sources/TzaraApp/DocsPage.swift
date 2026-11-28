import SwiftUI
import TzaraCore

/// The documentation: topics on the left, the chosen one on the right.
struct DocsPage: View {
    @State private var topic = DocsContent.topics[0].id
    @State private var search = ""

    private var shown: [DocsTopic] {
        let words = search.lowercased().split(separator: " ")
        return DocsContent.topics.filter { topic in words.allSatisfy { (topic.title + " " + topic.body).lowercased().contains($0) } }
    }

    var body: some View {
        Page(title: "Docs", subtitle: "How Tzara works, and how to get the most from it.") {
            SearchField(text: $search, prompt: "Search the docs")
            HStack(alignment: .top, spacing: 24) {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(shown) { item in
                        Button { topic = item.id } label: {
                            HStack(spacing: 10) {
                                Image(systemName: item.symbol).frame(width: 18)
                                Text(item.title)
                                Spacer(minLength: 0)
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 7)
                            .background(topic == item.id ? Color.primary.opacity(0.1) : .clear, in: RoundedRectangle(cornerRadius: 8))
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                    if shown.isEmpty { Text("Nothing found.").foregroundStyle(.secondary).padding(10) }
                }
                .frame(width: 230)
                if let current = DocsContent.topics.first(where: { $0.id == topic }) ?? shown.first {
                    DocsBody(text: current.body)
                        .padding(28)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: DashboardStyle.cardRadius))
                        .overlay(RoundedRectangle(cornerRadius: DashboardStyle.cardRadius).strokeBorder(Color.primary.opacity(0.08)))
                }
            }
        }
    }
}

/// A topic's Markdown, with tables drawn as grids (the chat's Markdown has none).
private struct DocsBody: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
                switch segment {
                case let .text(markdown): MarkdownView(text: markdown)
                case let .table(rows): table(rows)
                }
            }
        }
    }

    private enum Segment { case text(String), table([[String]]) }

    /// Splits the text into runs of Markdown and tables.
    private var segments: [Segment] {
        var result: [Segment] = []
        var run: [String] = []
        var rows: [[String]] = []
        func flushRun() { if !run.isEmpty { result.append(.text(run.joined(separator: "\n"))); run = [] } }
        func flushTable() { if !rows.isEmpty { result.append(.table(rows)); rows = [] } }
        for line in text.split(separator: "\n", omittingEmptySubsequences: false).map(String.init) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("|") {
                flushRun()
                if trimmed.contains("---") { continue }
                rows.append(trimmed.split(separator: "|", omittingEmptySubsequences: true).map { $0.trimmingCharacters(in: .whitespaces) })
            } else {
                flushTable()
                run.append(line)
            }
        }
        flushRun()
        flushTable()
        return result
    }

    private func table(_ rows: [[String]]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                HStack(alignment: .top, spacing: 16) {
                    ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                        Text(Markdown.inline(cell))
                            .fontWeight(index == 0 ? .semibold : .regular)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding(.vertical, 8)
                .padding(.horizontal, 12)
                .background(index == 0 ? Color.primary.opacity(0.06) : .clear)
                if index < rows.count - 1 { Divider() }
            }
        }
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Color.primary.opacity(0.1)))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
