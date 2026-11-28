import SwiftUI

/// The pieces every dashboard page is made of, so the pages look like one app: a page with a title
/// in a centred column, cards that group related rows, and rows with a label on the left and the
/// control on the right.
enum DashboardStyle {
    static let columnWidth: CGFloat = 960
    static let cardRadius: CGFloat = 14
}

/// A page: a big title, an optional line under it, and content, in a scrolling centred column.
struct Page<Content: View, Trailing: View>: View {
    let title: String
    var subtitle: String?
    @ViewBuilder var trailing: Trailing
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(title).font(.system(size: 28, weight: .bold))
                        if let subtitle { Text(subtitle).font(.body).foregroundStyle(.secondary) }
                    }
                    Spacer()
                    trailing
                }
                content
            }
            .frame(maxWidth: DashboardStyle.columnWidth, alignment: .leading)
            .padding(.horizontal, 36)
            .padding(.top, 56)
            .padding(.bottom, 48)
            .frame(maxWidth: .infinity)
        }
    }
}

extension Page where Trailing == EmptyView {
    init(title: String, subtitle: String? = nil, @ViewBuilder content: () -> Content) {
        self.title = title
        self.subtitle = subtitle
        self.trailing = EmptyView()
        self.content = content()
    }
}

/// A rounded group of rows, with a small heading above it and an optional note below.
struct Card<Content: View>: View {
    var title: String?
    var note: String?
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let title {
                Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(.secondary).padding(.leading, 4)
            }
            VStack(alignment: .leading, spacing: 0) { content }
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: DashboardStyle.cardRadius))
                .overlay(
                    RoundedRectangle(cornerRadius: DashboardStyle.cardRadius).strokeBorder(Color.primary.opacity(0.08)))
            if let note {
                Text(note).font(.caption).foregroundStyle(.secondary).padding(.leading, 4)
            }
        }
    }
}

/// One row of a card: what it is (and a line about it) on the left, the control on the right.
struct Line<Control: View>: View {
    let title: String
    var detail: String?
    var divider = true
    @ViewBuilder var control: Control

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 16) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                    if let detail { Text(detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
                }
                Spacer(minLength: 16)
                control
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            if divider { Divider().padding(.leading, 16) }
        }
    }
}

/// A small coloured label: a status, a place, a kind.
struct Chip: View {
    let text: String
    var color: Color = .secondary

    var body: some View {
        Text(text)
            .font(.caption.weight(.medium))
            .foregroundStyle(color)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color.opacity(0.14), in: Capsule())
    }
}

/// A search box in the dashboard's style.
struct SearchField: View {
    @Binding var text: String
    var prompt = "Search"

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
            TextField(prompt, text: $text).textFieldStyle(.plain)
            if !text.isEmpty {
                Button { text = "" } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.plain).foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 9))
        .frame(maxWidth: 360)
    }
}

/// "3 days ago", "just now": how long ago something was.
enum Ago {
    static func text(_ seconds: Double) -> String {
        guard seconds > 0 else { return "" }
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .full
        return formatter.localizedString(for: Date(timeIntervalSince1970: seconds), relativeTo: Date())
    }

    static func date(_ seconds: Double) -> String {
        Date(timeIntervalSince1970: seconds).formatted(date: .abbreviated, time: .shortened)
    }
}
