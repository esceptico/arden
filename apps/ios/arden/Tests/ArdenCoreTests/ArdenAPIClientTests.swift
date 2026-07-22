import XCTest
@testable import ArdenCore

final class ArdenAPIClientTests: XCTestCase {
    func testBuildsAuthorizedJSONRequest() throws {
        let config = AppConfig(serverURL: "http://127.0.0.1:6877/", apiKey: "secret-token")
        let body = ChatMessageRequest(message: "hello", sessionID: "session one", clientID: "client-1")

        let request = try ArdenAPIClient.makeRequest(
            config: config,
            path: "/chat/message",
            method: "POST",
            body: body
        )

        XCTAssertEqual(request.url?.absoluteString, "http://127.0.0.1:6877/chat/message")
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer secret-token")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Accept"), "application/json")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")

        let decoded = try JSONDecoder().decode(ChatMessageRequest.self, from: try XCTUnwrap(request.httpBody))
        XCTAssertEqual(decoded.message, "hello")
        XCTAssertEqual(decoded.sessionID, "session one")
        XCTAssertEqual(decoded.clientID, "client-1")
        XCTAssertFalse(decoded.skipApprovals)
    }

    func testBuildsSessionHistoryURLWithQueryEscaping() throws {
        let config = AppConfig(serverURL: "http://localhost:6877", apiKey: "")

        let request = try ArdenAPIClient.makeRequest(
            config: config,
            path: "/session/history?session_id=session%20one&limit=100",
            method: "GET",
            body: Optional<EmptyRequest>.none
        )

        XCTAssertEqual(request.url?.absoluteString, "http://localhost:6877/session/history?session_id=session%20one&limit=100")
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
    }
}

private struct EmptyRequest: Encodable {}
