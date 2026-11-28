import Testing
@testable import TzaraCore

@Suite("The bar")
struct BarBehaviorTests {
    @Test func withoutAFieldOnlyAClearRequestForTextIsWritten() {
        #expect(BarIntent.decide("write an email to Anna", hasField: false, hasSelection: false, hasDraft: false) == .write)
        #expect(BarIntent.decide("help me reply to him", hasField: false, hasSelection: false, hasDraft: false) == .write)
        #expect(BarIntent.decide("friendlier", hasField: false, hasSelection: false, hasDraft: false) == .chat)
        #expect(BarIntent.decide("what is 2+2", hasField: false, hasSelection: false, hasDraft: false) == .chat)
    }

    @Test func writingRequestsAreWrittenInAField() {
        for line in ["write an email to Anna about Friday", "Reply that I agree", "draft a polite no", "email Anna about the review"] {
            #expect(BarIntent.decide(line, hasField: true, hasSelection: false, hasDraft: false) == .write, "\(line)")
        }
    }

    @Test func questionsAreChatEvenInAField() {
        for line in ["what is the capital of Australia", "how do I reset my router?", "search the news"] {
            #expect(BarIntent.decide(line, hasField: true, hasSelection: false, hasDraft: false) == .chat, "\(line)")
        }
    }

    @Test func withATextSelectedAChangeIsAWrite() {
        #expect(BarIntent.decide("friendlier", hasField: true, hasSelection: true, hasDraft: false) == .write)
        #expect(BarIntent.decide("is this correct?", hasField: true, hasSelection: true, hasDraft: false) == .chat)
    }

    @Test func aDraftAndAShortInstructionIsAWrite() {
        #expect(BarIntent.decide("with more energy", hasField: true, hasSelection: false, hasDraft: true) == .write)
    }

    @Test func inAFieldEverythingThatIsNotAQuestionIsWritten() {
        for line in ["help me write a reply", "thank her for the gift", "I want to respond to this saying yes", "pizza"] {
            #expect(BarIntent.decide(line, hasField: true, hasSelection: false, hasDraft: false) == .write, "\(line)")
        }
        #expect(BarIntent.decide("is this correct?", hasField: true, hasSelection: false, hasDraft: true) == .chat)
        #expect(BarIntent.decide("can you reply with yes?", hasField: true, hasSelection: false, hasDraft: false) == .write)
    }

    @Test func slashWAlwaysWrites() {
        #expect(BarIntent.decide("/w anything", hasField: true, hasSelection: false, hasDraft: false) == .write)
    }

    @Test func theAnswerAreaGrowsWithinLimits() {
        #expect(BarLayout.answerHeight(characters: 10, steps: 0, hasButtons: false) == BarLayout.minimumAnswer)
        #expect(BarLayout.answerHeight(characters: 100_000, steps: 3, hasButtons: true) == BarLayout.maximumAnswer)
        #expect(BarLayout.answerHeight(characters: 300, steps: 0, hasButtons: false) > BarLayout.minimumAnswer)
    }

    @Test func theBarIsABubbleAPillOfSuggestionsAndAPromptWithGapsBetween() {
        let gap = BarLayout.gap
        #expect(BarLayout.height(hasChip: true, answer: nil) == BarLayout.inputHeight + BarLayout.chipHeight)
        // While Tzara answers, only the response is shown.
        #expect(BarLayout.height(hasChip: true, answer: 100, showsInput: false) == 100)
        // Afterwards: the bubble, the suggestions and the prompt, apart.
        #expect(
            BarLayout.height(hasChip: false, answer: 100, showsInput: true, suggestions: true)
                == 100 + BarLayout.suggestionHeight + BarLayout.inputHeight + 2 * gap)
        #expect(BarLayout.height(hasChip: false, answer: 100, showsInput: true) == 100 + BarLayout.inputHeight + gap)
    }
}

@Suite("Explaining is not writing")
struct ExplainTests {
    @Test func askingAboutSelectedTextIsChat() {
        for line in ["remember this", "memorize this", "forget the dentist", "what do you remember about me", "note that I like tea"] {
            #expect(BarIntent.decide(line, hasField: true, hasSelection: true, hasDraft: true) == .chat, "\(line)")
        }
        for line in ["explain", "Explain this", "explain it to me", "summarize this", "what does this mean", "define", "why", "tldr"] {
            #expect(BarIntent.decide(line, hasField: true, hasSelection: true, hasDraft: true) == .chat, "\(line)")
            #expect(BarIntent.decide(line, hasField: false, hasSelection: false, hasDraft: false) == .chat, "\(line)")
        }
    }
}

@Suite("Suggestions")
struct SuggestionsTests {
    @Test func withTextOnTheScreenAndNoAnswerTheyWorkOnTheScreen() {
        let found = Suggestions.make(hasScreenText: true, lastAnswer: nil, language: "German")
        #expect(found.map(\.label) == ["Explain", "Summarize", "Translate"])
        #expect(found.last?.message == "Translate this into German.")
    }

    @Test func afterAnAnswerTheyFollowItUp() {
        let short = Suggestions.make(hasScreenText: true, lastAnswer: "Canberra.", language: "French")
        #expect(short.map(\.label) == ["Tell me more", "Simpler", "Translate"])
        let long = Suggestions.make(hasScreenText: false, lastAnswer: String(repeating: "word ", count: 80))
        #expect(long.map(\.label).contains("Shorter"))
    }

    @Test func nothingToWorkOnNothingOffered() {
        #expect(Suggestions.make(hasScreenText: false, lastAnswer: nil).isEmpty)
        #expect(Suggestions.make(hasScreenText: false, lastAnswer: "  ").isEmpty)
    }
}
