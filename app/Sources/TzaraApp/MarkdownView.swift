import SwiftUI
import TzaraCore

/// A chat message drawn with its formatting: bold and italic text, links, lists, headings and
/// code. See ``Markdown`` for what is understood. Text can be selected, and links open in the
/// browser.
struct MarkdownView: View {
    let text: String

    var body: some View {
        let blocks = Markdown.blocks(from: text)
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                row(for: block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .textSelection(.enabled)
    }

    @ViewBuilder private func row(for block: MarkdownBlock) -> some View {
        switch block {
        case let .paragraph(text):
            Text(Markdown.inline(text))
        case let .heading(level, text):
            Text(Markdown.inline(text))
                .font(level == 1 ? .title3 : .headline)
                .fontWeight(.semibold)
                .padding(.top, 2)
        case let .bullet(depth, text):
            item(marker: "\u{2022}", width: 12, text: text, depth: depth)
        case let .numbered(depth, number, text):
            item(marker: "\(number).", width: 26, text: text, depth: depth)
        case let .quote(text):
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 1.5).fill(.tertiary).frame(width: 3)
                Text(Markdown.inline(text)).foregroundStyle(.secondary)
            }
            .fixedSize(horizontal: false, vertical: true)
        case let .code(code):
            Text(code)
                .font(.system(.callout, design: .monospaced))
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.primary.opacity(0.08), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
        case .rule:
            Divider()
        }
    }

    /// A list item. The marker has a fixed width, so the text of "9." and "10." lines up.
    private func item(marker: String, width: CGFloat, text: String, depth: Int) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(marker).monospacedDigit().frame(minWidth: width, alignment: .trailing)
            Text(Markdown.inline(text))
        }
        .padding(.leading, CGFloat(depth) * 14)
    }
}
