import SwiftUI

@main
struct ArdenMobileApp: App {
    @StateObject private var store = ArdenMobileStore()
    @AppStorage("arden.appearance") private var appearance: AppAppearance = .system

    var body: some Scene {
        WindowGroup {
            RootView(store: store)
                .tint(Theme.accent)
                .preferredColorScheme(appearance.colorScheme)
                .task {
                    await store.bootstrap()
                }
        }
    }
}
