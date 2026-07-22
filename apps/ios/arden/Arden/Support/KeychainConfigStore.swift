import Foundation
import Security

enum KeychainConfigError: Error, LocalizedError {
    case saveFailed(OSStatus)

    var errorDescription: String? {
        switch self {
        case .saveFailed(let status):
            "Could not save API key: \(status)"
        }
    }
}

final class KeychainConfigStore {
    private let defaults = UserDefaults.standard
    private let serverURLKey = "arden.ios.serverURL"
    private let service = "arden.ios"
    private let account = "apiKey"

    func load() -> AppConfig {
        AppConfig(
            serverURL: defaults.string(forKey: serverURLKey) ?? AppConfig.default.serverURL,
            apiKey: readAPIKey() ?? ""
        ).normalized
    }

    func save(_ config: AppConfig) throws -> AppConfig {
        let normalized = config.normalized
        defaults.set(normalized.serverURL, forKey: serverURLKey)
        try saveAPIKey(normalized.apiKey)
        return normalized
    }

    private func readAPIKey() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private func saveAPIKey(_ value: String) throws {
        let deleteQuery: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        SecItemDelete(deleteQuery as CFDictionary)

        guard !value.isEmpty else { return }
        let addQuery: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecValueData as String: Data(value.utf8)
        ]
        let status = SecItemAdd(addQuery as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw KeychainConfigError.saveFailed(status)
        }
    }
}
