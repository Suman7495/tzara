import Foundation
import Testing

@testable import TzaraCore

@Suite("Markdown blocks")
struct MarkdownBlockTests {
    @Test func plainTextIsOneParagraph() {
        #expect(Markdown.blocks(from: "Hello there") == [.paragraph("Hello there")])
    }

    @Test func aBlankLineSeparatesParagraphsAndASingleBreakStays() {
        #expect(Markdown.blocks(from: "One\ntwo\n\nThree") == [.paragraph("One\ntwo"), .paragraph("Three")])
    }

    @Test func nothingIsNothing() {
        #expect(Markdown.blocks(from: "").isEmpty)
        #expect(Markdown.blocks(from: "\n\n  \n").isEmpty)
    }

    @Test func headings() {
        #expect(Markdown.blocks(from: "# Big\n### Small") == [.heading(level: 1, text: "Big"), .heading(level: 3, text: "Small")])
    }

    @Test func aHashWithoutASpaceOrBeyondSixIsText() {
        #expect(Markdown.blocks(from: "#hashtag") == [.paragraph("#hashtag")])
        #expect(Markdown.blocks(from: "####### seven") == [.paragraph("####### seven")])
    }

    @Test func bulletsInAnyOfTheCommonMarkers() {
        let text = "- one\n* two\n+ three\n\u{2022} four"
        #expect(
            Markdown.blocks(from: text) == [
                .bullet(depth: 0, text: "one"), .bullet(depth: 0, text: "two"),
                .bullet(depth: 0, text: "three"), .bullet(depth: 0, text: "four"),
            ])
    }

    @Test func nestedBulletsGetADepth() {
        #expect(
            Markdown.blocks(from: "- a\n  - b\n    - c\n\t- d") == [
                .bullet(depth: 0, text: "a"), .bullet(depth: 1, text: "b"),
                .bullet(depth: 2, text: "c"), .bullet(depth: 2, text: "d"),
            ])
    }

    @Test func numberedItemsKeepTheirNumbers() {
        #expect(
            Markdown.blocks(from: "1. first\n2) second\n10. tenth") == [
                .numbered(depth: 0, number: 1, text: "first"), .numbered(depth: 0, number: 2, text: "second"),
                .numbered(depth: 0, number: 10, text: "tenth"),
            ])
    }

    @Test func aNumberThatIsNotAListItemIsText() {
        #expect(Markdown.blocks(from: "2026 was a year") == [.paragraph("2026 was a year")])
        #expect(Markdown.blocks(from: "3.14 is pi") == [.paragraph("3.14 is pi")])
        #expect(Markdown.blocks(from: "1234. no") == [.paragraph("1234. no")])
    }

    @Test func aMinusWithoutASpaceIsText() {
        #expect(Markdown.blocks(from: "-5 degrees") == [.paragraph("-5 degrees")])
    }

    @Test func listsEndParagraphsAndAreEndedByThem() {
        #expect(
            Markdown.blocks(from: "Intro:\n- a\n- b\nOutro") == [
                .paragraph("Intro:"), .bullet(depth: 0, text: "a"), .bullet(depth: 0, text: "b"), .paragraph("Outro"),
            ])
    }

    @Test func quotes() {
        #expect(Markdown.blocks(from: "> wise words") == [.quote("wise words")])
    }

    @Test func rules() {
        #expect(Markdown.blocks(from: "a\n---\nb") == [.paragraph("a"), .rule, .paragraph("b")])
        #expect(Markdown.blocks(from: "***") == [.rule])
        #expect(Markdown.blocks(from: "--") == [.paragraph("--")])
    }

    @Test func codeBlocksKeepTheirLinesAndSpacing() {
        let text = "Try:\n```swift\nlet x = 1\n\n    print(x)\n```\nDone"
        #expect(
            Markdown.blocks(from: text) == [
                .paragraph("Try:"), .code("let x = 1\n\n    print(x)"), .paragraph("Done"),
            ])
    }

    @Test func markdownInsideCodeIsNotInterpreted() {
        #expect(Markdown.blocks(from: "```\n# not a heading\n- not a bullet\n```") == [.code("# not a heading\n- not a bullet")])
    }

    @Test func aCodeBlockStillBeingWrittenRunsToTheEnd() {
        #expect(Markdown.blocks(from: "```\nlet x") == [.code("let x")])
        #expect(Markdown.blocks(from: "```") == [.code("")])
    }

    @Test func windowsLineEndingsAreFine() {
        #expect(Markdown.blocks(from: "- a\r\n- b\r\n") == [.bullet(depth: 0, text: "a"), .bullet(depth: 0, text: "b")])
    }

    @Test func tablesStayReadableAsText() {
        #expect(Markdown.blocks(from: "| a | b |\n|---|---|") == [.paragraph("| a | b |\n|---|---|")])
    }
}

@Suite("Markdown inline")
struct MarkdownInlineTests {
    private func links(in text: AttributedString) -> [URL] {
        text.runs.compactMap(\.link)
    }

    @Test func boldItalicAndCode() {
        let text = Markdown.inline("**bold** and *italic* and `code`")
        #expect(String(text.characters) == "bold and italic and code")
        let intents = text.runs.compactMap(\.inlinePresentationIntent)
        #expect(intents.contains(.stronglyEmphasized))
        #expect(intents.contains(.emphasized))
        #expect(intents.contains(.code))
    }

    @Test func aMarkdownLinkShowsItsTitleAndOpensItsAddress() {
        let text = Markdown.inline("See [the BBC](https://www.bbc.com/news) now")
        #expect(String(text.characters) == "See the BBC now")
        #expect(links(in: text) == [URL(string: "https://www.bbc.com/news")!])
    }

    @Test func aPastedAddressBecomesALink() {
        let text = Markdown.inline("Read https://example.com/page for more")
        #expect(links(in: text) == [URL(string: "https://example.com/page")!])
    }

    @Test func aSentenceEndingAddressDoesNotTakeTheFullStop() {
        let text = Markdown.inline("It is at https://example.com/a.")
        #expect(links(in: text) == [URL(string: "https://example.com/a")!])
    }

    @Test func anAddressAlreadyInALinkIsLinkedOnce() {
        let text = Markdown.inline("[https://example.com](https://example.com)")
        #expect(links(in: text).count == 1)
    }

    @Test func fileNamesAndBareDomainsAreNotTurnedIntoLinks() {
        #expect(links(in: Markdown.inline("edit main.py and notes.md, see example.com")).isEmpty)
    }

    @Test(arguments: [
        "[x](file:///etc/passwd)", "[x](javascript:alert(1))", "[x](tel:123)", "[x](mailto:a@b.c)",
        "[x](tzara://thing)", "[x](ftp://example.com/f)", "[x](https://)",
    ])
    func linksThatAreNotWebAddressesAreNotClickable(source: String) {
        let text = Markdown.inline(source)
        #expect(links(in: text).isEmpty)
        #expect(String(text.characters) == "x")
    }

    @Test func webLinksStayClickableNextToUnsafeOnes() {
        let text = Markdown.inline("[bad](file:///x) and [good](http://example.com/y)")
        #expect(links(in: text) == [URL(string: "http://example.com/y")!])
    }

    @Test func halfWrittenFormattingWhileStreamingIsJustText() {
        #expect(String(Markdown.inline("**bold").characters).contains("bold"))
        #expect(String(Markdown.inline("[title](https://exa").characters).contains("title"))
        #expect(String(Markdown.inline("").characters).isEmpty)
    }

    @Test func lineBreaksAreKept() {
        #expect(String(Markdown.inline("one\ntwo").characters) == "one\ntwo")
    }

    @Test func onlyWebAddressesAreSafe() {
        #expect(Markdown.isSafe(URL(string: "https://example.com")!))
        #expect(Markdown.isSafe(URL(string: "HTTP://example.com")!))
        #expect(!Markdown.isSafe(URL(string: "file:///x")!))
        #expect(!Markdown.isSafe(URL(string: "https://")!))
        #expect(!Markdown.isSafe(URL(string: "relative/path")!))
    }
}
