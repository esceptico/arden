import XCTest
@testable import ArdenCore

final class SSEParserTests: XCTestCase {
    func testParsesEventFramesAcrossChunks() throws {
        var parser = SSEParser()

        let first = parser.feed("id: 41\nevent: message\ndata: {\"type\":\"TEXT_MESSAGE_CONTENT\"")
        XCTAssertTrue(first.isEmpty)

        let second = parser.feed(",\"delta\":\"hi\"}\n\n")

        XCTAssertEqual(second.count, 1)
        XCTAssertEqual(second[0].id, "41")
        XCTAssertEqual(second[0].event, "message")
        XCTAssertEqual(second[0].data, "{\"type\":\"TEXT_MESSAGE_CONTENT\",\"delta\":\"hi\"}")
    }

    func testJoinsMultilineDataFields() throws {
        var parser = SSEParser()

        let messages = parser.feed("id: 7\ndata: first\ndata: second\n\n")

        XCTAssertEqual(messages.count, 1)
        XCTAssertEqual(messages[0].id, "7")
        XCTAssertEqual(messages[0].data, "first\nsecond")
    }
}
