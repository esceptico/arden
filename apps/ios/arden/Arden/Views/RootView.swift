import SwiftUI

struct RootView: View {
    @ObservedObject var store: ArdenMobileStore
    @State private var showingSettings = false
    @State private var showingSessions = false

    var body: some View {
        ChatView(
            store: store,
            showingSettings: $showingSettings,
            showingSessions: $showingSessions
        )
        .sheet(isPresented: $showingSettings) {
            SettingsView(store: store)
                .presentationDetents([.large])
                .presentationCornerRadius(44)
        }
        .sheet(isPresented: $showingSessions) {
            SessionListView(
                store: store,
                showingSettings: $showingSettings,
                dismissOnSelect: true
            )
            .presentationDetents([.medium, .large])
            .presentationCornerRadius(44)
        }
        .onAppear {
            showingSettings = store.needsConfiguration
        }
        .alert("Error", isPresented: errorPresented) {
            Button("OK", role: .cancel) {
                store.errorMessage = nil
            }
        } message: {
            Text(store.errorMessage ?? "")
        }
    }

    private var errorPresented: Binding<Bool> {
        Binding(
            get: { store.errorMessage != nil },
            set: { isPresented in
                if !isPresented {
                    store.errorMessage = nil
                }
            }
        )
    }
}
