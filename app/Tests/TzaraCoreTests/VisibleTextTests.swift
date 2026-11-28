import Foundation
import Testing

@testable import TzaraCore

@Suite("VisibleText")
struct VisibleTextTests {
    @Test func collectsLinesInTheOrderTheyAreAdded() {
        var text = VisibleText(limit: 1000)
        text.add("From: Anna", cut: 200)
        text.add("Subject: Invoice", cut: 200)
        #expect(text.text == "From: Anna\nSubject: Invoice")
    }

    @Test func leavesOutBlanksAndRepeatsOfTheLineBefore() {
        var text = VisibleText(limit: 1000)
        text.add("   ", cut: 200)
        text.add("Reply", cut: 200)
        text.add("Reply", cut: 200)
        text.add("Forward", cut: 200)
        #expect(text.text == "Reply\nForward")
    }

    @Test func joinsAnElementsLinesIntoOne() {
        var text = VisibleText(limit: 1000)
        text.add("one\ntwo\r\nthree", cut: 200)
        #expect(text.text == "one two three")
    }

    @Test func cutsALongLine() {
        var text = VisibleText(limit: 1000)
        text.add(String(repeating: "a", count: 300), cut: 50)
        #expect(text.text.count == 51 && text.text.hasSuffix("\u{2026}"))
    }

    @Test func saysWhenItIsFull() {
        var text = VisibleText(limit: 30)
        #expect(!text.isFull)
        text.add(String(repeating: "x", count: 40), cut: 200)
        #expect(text.isFull)
    }
}
