# Questions

## Open questions

| ID | Question | Why it matters | Owner | Status |
| --- | --- | --- | --- | --- |
| — | None. | — | — | — |

## Resolved questions

| ID | Resolution | Evidence or decision | Resolved at |
| --- | --- | --- | --- |
| Q-00 | Use all executable/runtime roots, not UI alone, then verify static orphans individually. | UI-only traversal would falsely flag CLI, scheduler, MCP, Electron preload, and protocol callbacks. | 2026-08-03 |
| Q-01 | Preserve undocumented external Python/HTTP compatibility unless deletion is independently proven. | Public-looking test-only helpers and server routes were retained; only dead desktop wrappers were removed. | 2026-08-03 |
| Q-02 | Include routes, CSS, database schema, and dependencies. | User amendment in `01-intent.md`. | 2026-08-03 |
