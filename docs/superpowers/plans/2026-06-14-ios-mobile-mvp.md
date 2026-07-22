# iOS Mobile MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a native iOS app scaffold that can connect to the existing Arden server, list sessions, open chat history, send messages, stream SSE events, approve/reject tools, and cancel runs.

**Architecture:** Put the app in `apps/ios/arden`. Keep pure networking/models/SSE code under `arden/Core` and expose it to a local Swift package for fast `swift test`; the Xcode iOS app target includes the same files through a filesystem-synchronized source group. The first UI is SwiftUI-native and standard-control-first, with no desktop relay or local tools.

**Tech Stack:** Swift 6/Xcode 26 project, SwiftUI, URLSession, Keychain Services, Swift Testing/XCTest via SwiftPM for core code.

**Explicit v1 non-goal:** no long-term context screens, endpoints, search, or editing.

---

### Task 1: Core package and tests

**Files:**
- Create: `apps/ios/arden/Package.swift`
- Create: `apps/ios/arden/Arden/Core/ArdenModels.swift`
- Create: `apps/ios/arden/Arden/Core/ArdenAPIClient.swift`
- Create: `apps/ios/arden/Arden/Core/SSEParser.swift`
- Create: `apps/ios/arden/Tests/ArdenCoreTests/SSEParserTests.swift`
- Create: `apps/ios/arden/Tests/ArdenCoreTests/ArdenAPIClientTests.swift`

- [x] Write failing tests for SSE parsing and request construction.
- [x] Run `swift test --package-path apps/ios/arden` and verify failures.
- [x] Implement core models, API client, and parser.
- [x] Run `swift test --package-path apps/ios/arden` and verify pass.

### Task 2: iOS app target

**Files:**
- Create: `apps/ios/arden/Arden.xcodeproj/project.pbxproj`
- Create: `apps/ios/arden/Arden.xcodeproj/project.xcworkspace/contents.xcworkspacedata`
- Create: `apps/ios/arden/Arden/App/ArdenMobileApp.swift`
- Create: `apps/ios/arden/Arden/Assets.xcassets/...`
- Create: `apps/ios/arden/Arden/Info.plist`

- [x] Create a minimal iOS app target using filesystem-synchronized sources.
- [x] Add app entrypoint and generated Info.plist settings.
- [x] Build with `xcodebuild -project apps/ios/arden/Arden.xcodeproj -scheme arden -destination generic/platform=iOS -derivedDataPath apps/ios/arden/build CODE_SIGNING_ALLOWED=NO build`.

### Task 3: Store and UI

**Files:**
- Create: `apps/ios/arden/Arden/Core/ArdenMobileStore.swift`
- Create: `apps/ios/arden/Arden/Views/SettingsView.swift`
- Create: `apps/ios/arden/Arden/Views/SessionListView.swift`
- Create: `apps/ios/arden/Arden/Views/ChatView.swift`
- Create: `apps/ios/arden/Arden/Views/ApprovalCard.swift`
- Create: `apps/ios/arden/Arden/Support/KeychainConfigStore.swift`

- [x] Add a small observable store for config, sessions, history, send, stream, approve, cancel.
- [x] Add settings, session list, chat, and approval views.
- [x] Keep visuals standard SwiftUI first: `NavigationSplitView`/`NavigationStack`, `List`, `Form`, `ToolbarItem`, standard buttons, semantic tint only.
- [x] Rebuild the app.
