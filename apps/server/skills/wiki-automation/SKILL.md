---
name: wiki-automation
description: Use when the user wants a scheduled automation to create, update, retain, or archive results in the managed wiki.
---

# Wiki automation

Create a normal automation whose results live under `automations/`. The automation gets its own dedicated channel automatically. Do not attach the current chat; `create_loop` is the separate current-chat workflow.

## Choose the page layout

- Default: `automations/<stable-slug>.md`.
- Repeated dated output: `automations/<stable-slug>/<YYYY-MM-DD>.md`.
- Use another descendant of `automations/` only when the user chooses it.
- Use a concise stable page ID derived from the automation name.

These are common wiki pages. Do not invent a page type or producer metadata.

## Build a standalone prompt

The automation prompt must:

1. Gather its source data.
2. Call `list_wiki_pages` with the root directory (`directory=""`) for a fresh
   repository head. A missing target directory is normal before its first page.
3. Read the exact target page when it exists.
4. Call `create_wiki_page` when absent, or `edit_wiki_page` with the returned page version and head when present.
5. Retry a revision conflict only after another fresh list/read.
6. Archive old pages only when the user requested a retention policy; never hard-delete.
7. Report the page path and whether it created, changed, or archived anything.

Do not use filesystem tools for managed wiki pages.

## Create the automation

Call `create_automation` once. Set:

- `auto_approve=true`;
- `tool_scope` to only the write tools the prompt needs: `create_wiki_page`, `edit_wiki_page`, and optionally `archive_wiki_page`;
- the requested trigger and source tools;
- no result-channel argument—the dedicated channel is automatic.

The normal read-only wiki tools remain available without adding them to `tool_scope`.
