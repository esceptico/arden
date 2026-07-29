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
- Use a concise stable page ID derived from the automation name.

These are common wiki pages. Do not invent a page type or producer metadata.

## Create directories deliberately

Every directory has a semantic `README.md`, not a generated file list. The wiki creates any missing ancestor README atomically with the first page. Automations must read a directory README before processing files in that directory. A single file directly in the existing `automations/` directory needs no new subdirectory README.

- Do not create boilerplate README pages manually. Creating `automations/<slug>.md` also creates `automations/README.md` when needed.
- Creating dated output also creates `automations/<slug>/README.md` when needed.
- On later runs, read the directory README before its pages. If the user supplied a more specific producer, consumer, privacy, or retention policy, edit the generated README to record it.
- Any automation that consumes another automation's output must explicitly list/read its named input directory, or read its named file, in its prompt. Raw output is readable/searchable by any agent, but it never becomes a fact or canonical daily page without an explicit curation step.

## Build a standalone prompt

The automation prompt must:

1. Gather its source data.
2. Call `list_wiki_pages` with the root directory (`directory=""`) for a fresh
   repository head. A `not_found` result while probing a missing target directory
   is normal before its first page; retain the fresh root head for creation.
3. For each existing named directory, read its README and then list/read the exact input pages before using its output.
4. Read the exact target page when it exists.
5. Call `create_wiki_page` when absent, or `edit_wiki_page` with the returned page version and head when present.
6. Retry a revision conflict only after another fresh list/read.
7. Archive old pages only when the user requested a retention policy; never hard-delete.
8. Report the page path and whether it created, changed, or archived anything.

Do not use filesystem tools for managed wiki pages.

## Create the automation

Call `create_automation` once. Set:

- `auto_approve=true`;
- `tool_scope` to only the write tools the prompt needs: `create_wiki_page`, `edit_wiki_page`, and optionally `move_wiki_page` or `archive_wiki_page`;
- the requested trigger and source tools;
- no result-channel argument—the dedicated channel is automatic.

The normal read-only wiki tools remain available without adding them to `tool_scope`.
