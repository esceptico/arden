# Animation plans

| # | Plan | Severity | Status |
| --- | --- | --- | --- |
| 001 | [Make peek tab content move horizontally](001-horizontal-peek-tabs.md) | MEDIUM | DONE |
| 002 | [Make rail mode content move horizontally](002-horizontal-rail-modes.md) | MEDIUM | DONE |
| 003 | [Replace chat controls with a supplementary inspector and shared dissolve](003-dissolve-inspector-scroll-rail.md) | HIGH | DONE |
| 004 | [Give Area Room one spatial transition and remove decorative tab motion](004-area-room-motion-boundary.md) | HIGH | DONE |
| 005 | [Add one restrained entrance system to every Board mockup](005-shared-page-entrance.md) | MEDIUM | DONE |

## Recommended execution order

1. Execute `001-horizontal-peek-tabs.md`. It records the settled axis rule in
   the consolidated language and proves the content-plane transition on the
   larger inspector surface.
2. Execute `002-horizontal-rail-modes.md`. It applies the same rule locally to
   the frequent rail-mode interaction without introducing shared machinery.
3. Execute `003-dissolve-inspector-scroll-rail.md`. It simplifies the chat
   inspector, ports the production conversation rail, and defines the shared
   dissolve contract for object and state replacement.
4. Execute `004-area-room-motion-boundary.md`. It makes Area details an explicit
   right-edge inspector and removes motion from frequent information switches.
5. Execute `005-shared-page-entrance.md`. It adds one block-level navigation
   entrance and scopes skeleton bridges to data-heavy views.

The plans have no code dependency and may be executed independently. Keep both
implementations local; do not extract a shared tab-motion abstraction.
