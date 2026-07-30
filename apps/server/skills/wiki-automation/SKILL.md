---
name: wiki-automation
description: Use when the user wants a scheduled automation to create, update, retain, or archive results in the managed wiki.
---

# Wiki automation

Create a normal automation whose results live under `automations/`. The automation gets its own dedicated channel automatically. Do not attach the current chat; `create_loop` is the separate current-chat workflow.

## Choose the page layout

- Default: `automations/<stable-slug>.md`.
- Repeated dated output, only when history is needed: `automations/<stable-slug>/<YYYY-MM-DD>.md`.
- Use another descendant of `automations/` only when the user chooses it.

These are common wiki pages. Do not invent a page type or producer metadata.

## Create directories deliberately

Every directory has a semantic `README.md`, not a generated file list. The wiki creates any missing ancestor README atomically with the first page. Automations must read a directory README before processing files in that directory. A single file directly in the existing `automations/` directory needs no new subdirectory README.

- Do not create boilerplate README pages manually. Creating `automations/<slug>.md` also creates `automations/README.md` when needed.
- Creating dated output also creates `automations/<slug>/README.md` when needed.
- When a create or move reports a created or restored README, read it immediately. If it contains bootstrap text, edit it in the same run with the directory's exact purpose, producer and owned paths, consumers and read order, source/privacy/trust boundaries, and retention policy.
- On later runs, read the directory README before its pages and follow that contract.
- Any automation that consumes another automation's output must explicitly list/read its named input directory, or read its named file, in its prompt. Raw output is readable/searchable by any agent, but it never becomes a fact or canonical daily page without an explicit curation step.

## Build a standalone prompt

The automation prompt must:

1. Gather its source data.
2. Call `list_wiki_pages` with the root directory (`directory=""`). A `not_found`
   result while probing a missing target directory is normal before its first page.
3. For each existing named directory, read its README and then list/read the exact input pages before using its output.
4. Read the exact target path when it exists.
5. Call `create_wiki_page` when absent with its exact managed path, title,
   aliases, and body; otherwise call `edit_wiki_page` with the exact path and body.
   The backend handles write safety.
6. Read every created or restored README reported by that write and specialize any bootstrap contract before completing the run.
7. Retry a write conflict only after another fresh list/read.
8. Archive old pages only when the user requested a retention policy; never hard-delete.
9. Report the page path and whether it created, changed, or archived anything.

Do not use filesystem tools for managed wiki pages.

## Create the automation

Call `create_automation` once. Set:

- `auto_approve=true`;
- `tool_scope` to only the write tools the prompt needs: `create_wiki_page`, `edit_wiki_page`, and optionally `move_wiki_page` or `archive_wiki_page`;
- the requested trigger and source tools;
- `idempotency_key` to `wiki-automation:<stable-slug>` and `idempotency_scope="global"`. The slug names this producer's enduring job, not one run or page version;
- no result-channel argument—the dedicated channel is automatic.

The normal read-only wiki tools remain available without adding them to `tool_scope`.

If creation has an ambiguous result, first call `list_automations`. Retry with the exact same key only when that producer is absent. Never make a `-2`, date, or versioned key to work around a retry. When the producer already exists, call `update_automation` instead of creating another one. An explicit deletion releases the global key, so recreating the same producer uses the same key.
