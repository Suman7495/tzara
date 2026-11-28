import Testing
@testable import TzaraCore

@Suite("The docs")
struct DocsContentTests {
    @Test func everyTopicHasATitleAndText() {
        #expect(DocsContent.topics.count >= 8)
        for topic in DocsContent.topics {
            #expect(!topic.title.isEmpty && topic.body.count > 200, "\(topic.id)")
            #expect(topic.body.hasPrefix("# "), "\(topic.id) starts with its heading")
        }
        #expect(Set(DocsContent.topics.map(\.id)).count == DocsContent.topics.count)
    }

    @Test func theDocsSayHowToOpenTheBar() {
        #expect(DocsContent.topics.contains { $0.body.contains("Right Option") })
    }
}
