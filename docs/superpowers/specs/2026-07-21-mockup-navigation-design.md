# Mockup navigation design

## Goal

Make the Board mockups directly navigable so visual review can move between surfaces without manually editing URLs.

## Route contract

| Destination | File |
| --- | --- |
| Home / Mission Control | `board-home.html` |
| Chat / session | `board-chat.html` |
| Automations | `board-automations.html` |
| Memory | `board-memory.html` |
| Settings | `board-settings.html` |
| Area | `board-area-room.html` |
| System overlays | `board-system-overlays.html` |

## Behavior

- Use native relative `<a href>` links for cross-page navigation.
- Preserve the existing visual classes and layout.
- Mark the current destination with `aria-current="page"` and the existing selected style.
- Session rows open Chat; Area rows open Area Room.
- Keep page-local actions, disclosure controls, theme toggles, and overlay triggers as buttons.
- Add a compact shared review switcher where a plate lacks global navigation, including overlays.

## Accessibility and resilience

Navigation works without JavaScript, supports standard browser behaviors such as opening in a new tab, and exposes link semantics to assistive technology.

## Verification

- A contract test asserts every route and current-page marker.
- Existing mockup suites remain green.
- Browser navigation is checked across all seven destinations.
