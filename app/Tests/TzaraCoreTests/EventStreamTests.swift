import Foundation
import Testing

@testable import TzaraCore

private func events(from text: String) -> [ChatEvent] {
    var parser = EventStreamParser()
    var found: [ChatEvent] = []
    for line in text.components(separatedBy: "\n") {
        if let event = parser.consume(line: line) { found.append(event) }
    }
    return found
}

@Suite("LineAssembler")
struct LineAssemblerTests {
    private func lines(from bytes: [UInt8]) -> [String] {
        var assembler = LineAssembler()
        return bytes.compactMap { assembler.push($0) }
    }

    @Test func splitsOnNewlines() {
        #expect(lines(from: Array("one\ntwo\n".utf8)) == ["one", "two"])
    }

    @Test func keepsBlankLines() {
        #expect(lines(from: Array("a\n\nb\n".utf8)) == ["a", "", "b"])
    }

    @Test func stripsCarriageReturns() {
        #expect(lines(from: Array("a\r\n\r\nb\r\n".utf8)) == ["a", "", "b"])
    }

    @Test func waitsForTheLineToEnd() {
        var assembler = LineAssembler()
        #expect(assembler.push(0x61) == nil)
        #expect(assembler.push(0x0A) == "a")
    }

    @Test func keepsMultiByteCharactersIntactEvenWhenBytesArriveOneAtATime() {
        let text = "caf\u{00E9} \u{2192} \u{1F409}\n"
        #expect(lines(from: Array(text.utf8)) == ["caf\u{00E9} \u{2192} \u{1F409}"])
    }
}

@Suite("EventStreamParser")
struct EventStreamParserTests {
    @Test func readsTokensInOrder() {
        let stream = """
            event: token
            data: {"text": "Hello"}

            event: token
            data: {"text": " there"}


            """
        #expect(events(from: stream) == [.token("Hello"), .token(" there")])
    }

    @Test func readsTheDoneEvent() {
        let stream = "event: done\ndata: {\"first_token_seconds\": 0.29, \"characters\": 11}\n\n"
        #expect(events(from: stream) == [.done(firstTokenSeconds: 0.29, characters: 11)])
    }

    @Test func aDoneEventWithNoFirstTokenTimeIsFine() {
        let stream = "event: done\ndata: {\"first_token_seconds\": null, \"characters\": 0}\n\n"
        #expect(events(from: stream) == [.done(firstTokenSeconds: nil, characters: 0)])
    }

    @Test func readsAnErrorEvent() {
        let stream = "event: error\ndata: {\"message\": \"the model fell over\"}\n\n"
        #expect(events(from: stream) == [.error("the model fell over")])
    }

    @Test func ignoresKeepAliveComments() {
        let stream = ": keep-alive\n\nevent: token\ndata: {\"text\": \"hi\"}\n\n: keep-alive\n\n"
        #expect(events(from: stream) == [.token("hi")])
    }

    @Test func aKeepAliveBetweenAnEventNameAndItsDataDoesNotBreakIt() {
        let stream = "event: token\n: keep-alive\ndata: {\"text\": \"hi\"}\n\n"
        #expect(events(from: stream) == [.token("hi")])
    }

    @Test func ignoresEventsItDoesNotKnow() {
        #expect(events(from: "event: future\ndata: {}\n\nevent: token\ndata: {\"text\": \"x\"}\n\n") == [.token("x")])
    }

    @Test func textWithNewlinesAndUnicodeSurvivesTheJSONEncoding() {
        let stream = "event: token\ndata: {\"text\": \"line one\\nline two \\u2192 \\u201cq\\u201d\"}\n\n"
        #expect(events(from: stream) == [.token("line one\nline two \u{2192} \u{201C}q\u{201D}")])
    }

    @Test func aMalformedEventBecomesAnErrorInsteadOfBeingLost() {
        let result = events(from: "event: token\ndata: not json\n\n")
        #expect(result.count == 1)
        guard case .error = result[0] else {
            Issue.record("expected an error event, got \(result[0])")
            return
        }
    }

    @Test func aBlankLineWithNothingPendingDoesNothing() {
        #expect(events(from: "\n\n\n").isEmpty)
    }

    @Test func fieldNamesWithoutAColonAreIgnored() {
        #expect(events(from: "garbage\nevent: token\ndata: {\"text\": \"ok\"}\n\n") == [.token("ok")])
    }

    @Test func oneSpaceAfterTheColonIsOptional() {
        #expect(events(from: "event:token\ndata:{\"text\": \"ok\"}\n\n") == [.token("ok")])
    }

    @Test func eventsDoNotLeakIntoEachOther() {
        // The second event has a name but no data, so it must not inherit the first one's data.
        let stream = "event: token\ndata: {\"text\": \"a\"}\n\nevent: token\n\n"
        let result = events(from: stream)
        #expect(result.first == .token("a"))
        #expect(result.count == 2)
        guard case .error = result[1] else {
            Issue.record("a data-less event should be reported, got \(result[1])")
            return
        }
    }
}
