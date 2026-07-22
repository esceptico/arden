# iOS Visual Redesign — Direction B (Neutral / Codex-leaning)

**Date:** 2026-06-14
**Status:** Approved (direction chosen from 3 rendered mockups)
**Scope:** Pure visual/UX redesign of the existing iOS app (`apps/ios/arden`). No
API, networking, store logic, or model changes. The fake/mock data path is what
renders during this work, so mock content is updated to match the design.

Reference mockup: `designs/ios/direction-b-neutral.html` (the binding visual contract).

---

## 1. Why

The current app applies iOS-26 Liquid Glass (`glassEffect`/`ardenGlass`) to every
surface — top bar, every message bubble, tool rows, activity pills, composer,
send button, model pill, session cards, settings groups, approval card. The
reference Claude/Codex iOS apps use **zero glass on content**; the only
translucent surface is the system nav bar. Glass on stacked content reads as
noisy and un-Apple. Type is also oversized (21pt assistant, 20pt composer) and
the accent is tripled (blue + tinted-black + orange).

Direction B fixes this with Codex/developer-tool precision: pure white on a cool
grouped canvas, near-black cool text, hairline borders, **SF Mono for engineered
metadata**, and exactly **one accent** (precise blue) plus one bold near-black
primary pill.

---

## 2. Design tokens (the contract)

Implemented in a new `Support/ArdenTheme.swift` as semantic `Color`s that adapt
automatically light/dark via a dynamic `UIColor` provider (no asset catalog
needed). Hex values are taken verbatim from the mockup `:root` / `.screen.dark`.

| Token | Light | Dark |
|---|---|---|
| `canvas` (grouped bg) | `#F2F2F7` | `#1C1C1E` |
| `doc` (chat transcript bg) | `#FFFFFF` | `#000000` |
| `surface` (cards/rows/composer) | `#FFFFFF` | `#1C1C1E` |
| `raised` (selected/pressed) | `#EAEAEF` | `#2C2C2E` |
| `bubble` (user bubble) | `#F1F2F4` | `#2A2A2C` |
| `sep` (hairline) | `rgba(60,60,67,0.20)` | `rgba(84,84,88,0.55)` |
| `groupOutline` | `#E3E3E8` | `#2C2C2E` |
| `composerBorder` | `#D6D8DD` | `#3A3A3C` |
| `textPrimary` | `#0E1216` | `#F2F2F3` |
| `textSecondary` | `#62676D` | `#9BA0A6` |
| `textTertiary` | `#9AA0A6` | `#6C7176` |
| `accent` | `#2B6CF0` | `#4C8DFF` |
| `accentTint` | `rgba(43,108,240,0.10)` | `rgba(76,141,255,0.16)` |
| `pill` (primary action) | `#131417` | `#FFFFFF` |
| `pillText` | `#FFFFFF` | `#0A0A0A` |
| `sendDisabled` | `#C4C8CE` | `#4A4D52` |
| `destructive` | `#D7373B` | `#FF6166` |
| `errorFill` | `#FDECEC` | `#3A1D1E` |
| `success` | `#1F9D57` | `#34C77B` |

**Type scale** (SF Pro / `.system`; SF Mono via `.system(..., design: .monospaced)`):

- Transcript body / user bubble / list item: **17pt regular**, line spacing ~5.
- Nav title: **16pt semibold**. Nav subtitle: **12pt mono** secondary.
- Assistant label (e.g. `opus 4.8 · high`): **12pt mono**, tertiary, lowercase.
- Tool name: **13pt semibold mono**; tool path: **13pt mono** secondary.
- Tool status / streaming / timestamps / message counts: **12–13pt mono**.
- Section headers (Starred/Recents): **13pt** secondary.
- Composer placeholder/text: **17pt**; model inline **14pt** (name medium).

**Geometry:** user bubble radius 19; composer radius 22 with 1pt `composerBorder`;
cards/rows radius 10–12; FAB pill height 48 / radius 24; send circle 32. Screen
horizontal margin 16–20. One accent only; the dark pill is the single bold element.

**Motion:** keep restrained — `PressScaleButtonStyle` (0.96), spring scroll-to-bottom,
3-dot blinking "thinking" indicator. Remove the composer focus scale bounce.

---

## 3. Per-screen specs

### ChatView
- Native `NavigationStack` + system nav bar (the only translucent surface):
  `.navigationBarTitleDisplayMode(.inline)`, a `.principal` two-line title
  (16pt semibold session title + 12pt mono subtitle `live · <host>` with a 6pt
  accent live-dot when a run is active), leading `line.3.horizontal` (open
  sessions), trailing `square.and.pencil` (new chat) + `ellipsis` (settings).
- Transcript on `doc` background. User turn = right-aligned `bubble` fill, 17pt,
  radius 19, max width ~78%. Assistant turn = plain `textPrimary` 17pt with a
  mono lowercase label; render inline markdown (`**bold**`, `` `code` ``) via
  `AttributedString(markdown:)`, leave list lines as text.
- Tool row = quiet inline row with top+bottom hairlines: small glyph + mono name
  + truncated mono path + `success` check & duration. No card, no glass.
- Activity = small centered mono caption. Error = `destructive` text on
  `errorFill`, no glass.
- Composer (bottom `safeAreaInset`): flat `surface` rounded rect, 1pt
  `composerBorder`, radius 22. Row: `plus`, model-inline (accent star glyph +
  `Opus 4.8` medium + ` · High` secondary), `mic`, send circle. Send is
  `sendDisabled` glyph on `canvas` when empty → `pill` fill with `pillText`
  arrow when text present. While streaming, send becomes a stop (square) that
  calls `cancelRun()`.

### SessionListView (drawer/sheet)
- Flat list on `canvas` (no giant glass card). Header row: `ARDEN` wordmark (24pt
  bold) + circular `pill` avatar with user initials.
- Sessions rendered as flat rows (radius 10): leading glyph (accent filled dot
  when `activeRunID != nil`, else outline bubble), title 16pt, mono meta
  (`12 messages` + relative time), accent `N to approve` badge when
  `pendingApprovalsCount > 0`. Selected row = `raised` fill, no checkmark.
  Hairline separators inset to the text column. Group under a `Recents` section
  header (and `Starred` only if a starred concept exists — it does not in v1, so
  omit rather than fake it).
- Floating dark `New chat` pill (FAB) centered above the home indicator.
- Toolbar: close (x) + settings (gear) kept functional.

### SettingsView
- iOS grouped-inset look: `canvas` background, white rounded `surface` card
  groups with hairline dividers, secondary section headers. Restyle existing
  controls (Stub-API toggle, Server/API-key fields, Appearance picker) to flat —
  remove all glass. Values that are technical (server URL) use mono. Save/close
  toolbar buttons become plain/tinted, not glass.

### ApprovalCard
- The one moment that must stand out: `surface` card, 1pt `groupOutline`, radius
  16. Tool name in mono, preview/diff in mono (diff in a horizontally scrollable
  block). `Reject` = `destructive` plain text button; `Approve` = dark `pill`
  button. No glass.

### App / Root
- `ArdenMobileApp`: `.tint(Theme.accent)` instead of `.blue`.

---

## 4. Mock data (renders during this work)

Update `Core/MockArdenData.swift` so the running app matches the mockup content:
sessions `IRF540N slayer excitor circuit` (active run + 1 to approve, 34 msgs),
`ARDEN project overview` (12), `Xcode incompatibility after macOS update` (8),
`Cold outreach message feedback` (21), `Rephrasing a startup tweet` (6),
`Job security comparison: US vs EU` (15). Transcripts use the realistic
electronics/agent content from the mockup; assistant label `opus 4.8 · high`;
subtitle host `macbook.local`; composer model `Opus 4.8 · High`.

---

## 5. Files

- **New:** `Support/ArdenTheme.swift` (tokens, `Color(hex:)`, dynamic provider,
  mono helper, `PressScaleButtonStyle`, `Hairline`, shared icon-button + send
  primitives).
- **Remove/replace:** `Support/ArdenGlass.swift` (glass helpers deleted; keep
  `PressScaleButtonStyle` by moving it into `ArdenTheme.swift`).
- **Rewrite:** `Views/ChatView.swift`, `Views/SessionListView.swift`,
  `Views/SettingsView.swift`, `Views/ApprovalCard.swift`.
- **Edit:** `App/ArdenMobileApp.swift` (tint), `Core/MockArdenData.swift` (content).
- No changes to `Store/`, `Core/Arden*Client`, `SSEParser`, `MobileTranscript`,
  or models beyond what's listed.

---

## 6. Verification

1. `xcodebuild -project apps/ios/arden/Arden.xcodeproj -scheme arden -destination
   'platform=iOS Simulator,name=iPhone 16 Pro' CODE_SIGNING_ALLOWED=NO build`
   compiles clean.
2. Boot the simulator, install, launch in mock mode.
3. Screenshot **Chat (light)**, toggle `xcrun simctl ui booted appearance dark`
   → **Chat (dark)**, then drive the sessions sheet and settings sheet for those
   screenshots. Compare against `designs/ios/direction-b-neutral.html`.
4. Confirm: zero glass on content, one accent, native nav bar, mono metadata,
   flat session list, standout approval card, both color schemes correct.
