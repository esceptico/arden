import Foundation

struct SSEMessage: Equatable {
    let id: String?
    let event: String?
    let data: String
}

struct SSEParser {
    private var buffer = ""

    mutating func feed(_ chunk: String) -> [SSEMessage] {
        buffer += chunk.replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")

        var messages: [SSEMessage] = []
        while let range = buffer.range(of: "\n\n") {
            let rawFrame = String(buffer[..<range.lowerBound])
            buffer.removeSubrange(buffer.startIndex..<range.upperBound)
            if let message = Self.parseFrame(rawFrame) {
                messages.append(message)
            }
        }
        return messages
    }

    private static func parseFrame(_ rawFrame: String) -> SSEMessage? {
        var id: String?
        var event: String?
        var dataLines: [String] = []

        for line in rawFrame.split(separator: "\n", omittingEmptySubsequences: false) {
            if line.isEmpty || line.hasPrefix(":") {
                continue
            }
            let text = String(line)
            let parts = text.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
            let field = String(parts[0])
            var value = parts.count > 1 ? String(parts[1]) : ""
            if value.hasPrefix(" ") {
                value.removeFirst()
            }
            switch field {
            case "id":
                id = value
            case "event":
                event = value
            case "data":
                dataLines.append(value)
            default:
                continue
            }
        }

        guard !dataLines.isEmpty else { return nil }
        return SSEMessage(id: id, event: event, data: dataLines.joined(separator: "\n"))
    }
}
