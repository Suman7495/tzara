import AppKit
import SwiftUI
import TzaraCore

/// What Tzara remembers, readable in full: the facts it has learned, what it has done for you, how you
/// write, and every stored conversation. All of it lives in a file on this Mac.
struct MemoryPage: View {
    @ObservedObject var settings: SettingsModel

    enum Tab: String, CaseIterable, Identifiable {
        case facts = "Facts", activity = "Activity", writing = "Writing", chats = "Conversations"
        var id: String { rawValue }
    }

    @State private var tab = Tab.facts
    @State private var search = ""
    @State private var chosenChat: Int?
    @State private var lines: [StoredLine] = []

    var body: some View {
        Page(title: "Memory", subtitle: "What Tzara has learned about you and done for you. It stays on this Mac.") {
            if let memory = settings.memory, memory.enabled, !memory.facts.isEmpty || memory.conversations > 0 {
                Button("Erase all\u{2026}", role: .destructive) { confirmErase(memory) }
            }
        } content: {
            if let memory = settings.memory {
                if !memory.enabled {
                    Text("Memory is turned off. Nothing is kept between conversations.").foregroundStyle(.secondary)
                } else {
                    stats(memory)
                    toolbar
                    switch tab {
                    case .facts: facts(memory)
                    case .activity: activity(memory)
                    case .writing: writing(memory)
                    case .chats: chats(memory)
                    }
                }
            } else {
                ProgressView().frame(maxWidth: .infinity, minHeight: 200)
            }
            if let message = settings.memoryMessage { Text(message).font(.caption).foregroundStyle(.secondary) }
        }
        .onAppear { settings.loadMemory() }
    }

    // MARK: - Overview

    private func stats(_ memory: MemoryOverview) -> some View {
        HStack(spacing: 14) {
            tile("Facts", memory.facts.count, "person.text.rectangle", .blue, .facts)
            tile("Things done", memory.activity.count, "checkmark.circle", .green, .activity)
            tile("Writing samples", memory.writingSamples.count, "pencil.line", .purple, .writing)
            tile("Conversations", memory.conversations, "bubble.left.and.bubble.right", .orange, .chats)
        }
    }

    private func tile(_ title: String, _ count: Int, _ symbol: String, _ color: Color, _ target: Tab) -> some View {
        Button { tab = target } label: {
            HStack(spacing: 12) {
                Image(systemName: symbol).font(.title3).foregroundStyle(color).frame(width: 36, height: 36)
                    .background(color.opacity(0.14), in: RoundedRectangle(cornerRadius: 9))
                VStack(alignment: .leading, spacing: 0) {
                    Text("\(count)").font(.title2.bold())
                    Text(title).font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
            }
            .padding(14)
            .frame(maxWidth: .infinity)
            .background(Color.primary.opacity(tab == target ? 0.09 : 0.045), in: RoundedRectangle(cornerRadius: DashboardStyle.cardRadius))
            .overlay(
                RoundedRectangle(cornerRadius: DashboardStyle.cardRadius)
                    .strokeBorder(tab == target ? color.opacity(0.5) : Color.primary.opacity(0.08)))
        }
        .buttonStyle(.plain)
    }

    private var toolbar: some View {
        HStack(spacing: 12) {
            Picker("", selection: $tab) {
                ForEach(Tab.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: 420)
            SearchField(text: $search, prompt: "Search \(tab.rawValue.lowercased())")
            Spacer()
        }
    }

    private func matches(_ text: String) -> Bool {
        let words = search.lowercased().split(separator: " ")
        return words.allSatisfy { text.lowercased().contains($0) }
    }

    private func empty(_ text: String) -> some View {
        Text(text).foregroundStyle(.secondary).frame(maxWidth: .infinity, minHeight: 160)
    }

    // MARK: - Facts

    @ViewBuilder private func facts(_ memory: MemoryOverview) -> some View {
        let shown = memory.facts.filter { matches($0.text) }
        if shown.isEmpty {
            empty(memory.facts.isEmpty ? "Nothing yet. Tzara learns about you as you chat." : "No facts match.")
        } else {
            Card(note: "Learned from what you said. Delete any you don't want kept.") {
                ForEach(Array(shown.enumerated()), id: \.element.id) { index, fact in
                    Line(title: fact.text, detail: "Learned " + Ago.text(fact.created), divider: index < shown.count - 1) {
                        Button { settings.forget(fact) } label: { Image(systemName: "trash") }
                            .buttonStyle(.plain)
                            .foregroundStyle(.secondary)
                            .help("Forget this")
                            .accessibilityLabel("Forget: \(fact.text)")
                    }
                }
            }
        }
    }

    // MARK: - Activity

    @ViewBuilder private func activity(_ memory: MemoryOverview) -> some View {
        let shown = memory.activity.filter { matches($0.summary) }
        if shown.isEmpty {
            empty(memory.activity.isEmpty ? "Nothing yet. Pages changed and text written for you show here." : "Nothing matches.")
        } else {
            Card(note: "What Tzara did, newest first: a page it changed in an app, text it wrote for you.") {
                ForEach(Array(shown.enumerated()), id: \.element.id) { index, item in
                    Line(title: item.summary, detail: Ago.date(item.at), divider: index < shown.count - 1) {
                        Chip(text: item.kind == "write" ? "Wrote" : "App", color: item.kind == "write" ? .purple : .green)
                    }
                }
            }
        }
    }

    // MARK: - Writing samples

    @ViewBuilder private func writing(_ memory: MemoryOverview) -> some View {
        let shown = memory.writingSamples.filter { matches($0.text) }
        if shown.isEmpty {
            empty(memory.writingSamples.isEmpty
                ? "Nothing yet. When you polish a draft with the bar, Tzara keeps it to learn your tone."
                : "Nothing matches.")
        } else {
            Card(note: "Your own drafts, used as examples so what Tzara writes sounds like you.") {
                ForEach(Array(shown.enumerated()), id: \.element.id) { index, sample in
                    Line(title: sample.text, detail: Ago.date(sample.at), divider: index < shown.count - 1) {
                        Chip(text: sample.place.capitalized, color: .purple)
                    }
                }
            }
        }
    }

    // MARK: - Conversations

    @ViewBuilder private func chats(_ memory: MemoryOverview) -> some View {
        let shown = memory.chats.filter { matches($0.title) }
        if shown.isEmpty {
            empty(memory.chats.isEmpty ? "No stored conversations." : "No conversation matches.")
        } else {
            HStack(alignment: .top, spacing: 16) {
                Card {
                    ForEach(Array(shown.enumerated()), id: \.element.id) { index, chat in
                        Button { open(chat.id) } label: {
                            VStack(spacing: 0) {
                                HStack(spacing: 10) {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(chat.title.isEmpty ? "Untitled" : chat.title).lineLimit(2)
                                        Text("\(Ago.date(chat.started)) \u{00B7} \(chat.messages) messages")
                                            .font(.caption).foregroundStyle(.secondary)
                                    }
                                    Spacer(minLength: 0)
                                }
                                .padding(.horizontal, 16)
                                .padding(.vertical, 11)
                                .background(chosenChat == chat.id ? Color.accentColor.opacity(0.14) : .clear)
                                if index < shown.count - 1 { Divider().padding(.leading, 16) }
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
                .frame(width: 340)
                transcript
            }
        }
    }

    private var transcript: some View {
        Card {
            if chosenChat == nil {
                Text("Choose a conversation to read it.").foregroundStyle(.secondary).padding(24)
            } else if lines.isEmpty {
                ProgressView().padding(24)
            } else {
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(lines) { line in
                        VStack(alignment: .leading, spacing: 3) {
                            Text(line.role == "user" ? "You" : "Tzara").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                            Text(line.text).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
                .padding(18)
            }
        }
        .frame(maxWidth: .infinity)
    }

    private func open(_ id: Int) {
        chosenChat = id
        lines = []
        Task { lines = await settings.conversation(id) }
    }

    private func confirmErase(_ memory: MemoryOverview) {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Erase everything Tzara remembers?"
        alert.informativeText = "This forgets \(memory.facts.count) facts, what it did for you, how you write and "
            + "\(memory.conversations) stored conversations, including the current one. It cannot be undone."
        alert.addButton(withTitle: "Erase").hasDestructiveAction = true
        alert.addButton(withTitle: "Cancel")
        NSApplication.shared.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertFirstButtonReturn { settings.eraseMemory() }
    }
}
