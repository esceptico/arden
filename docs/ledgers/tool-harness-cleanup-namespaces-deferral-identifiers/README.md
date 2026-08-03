<!-- development-ledger:v2 -->

# Tool harness cleanup: namespaces, deferral, identifiers

## Status

| Field | Value |
| --- | --- |
| State | verifying |
| Active phase | verification |
| Created | 2026-08-03T18:18:25+04:00 |
| Last updated | 2026-08-03T18:18:25+04:00 |
| Last consolidated | 2026-08-03 (e5b33cc7) |
| Codebase branch | main |
| Codebase revision | e5b33cc746f05043cb227678745a9b96ea9608b3 |
| Sources checked through | code: e5b33cc746f05043cb227678745a9b96ea9608b3; web: not checked |

## Original task — verbatim

let's verify things with /development-ledger please

(Scope of the arc being verified, from earlier in the session:
 "we need to review which tools are not deferred now – probably a lot of them might be";
 "i like idea of clean namespaces, and i wanted to do some cleanup there as well (and refine some prompts)";
 "so it would be pretty long work but we must check all of it";
 rename rule: "put namespace to the start (as a prefix) and keep other part looks fine as a prose, don't need to force other rules imo";
 "let's work on internal identifiers. be careful here, check all the stuff";
 followed by a live failure report: "`OperationalError: table session_messages_fts has no column named file_search_text` a lot of errors, app is not working")

## Amendments — verbatim

None.

## Current synthesis

The arc shipped in five commits (`08b37ac8`, `3268ee05`, `bea89e0b`, `2c6f2822`,
`e5b33cc7`) and is verified statically, at the registry level, and against the
real database schema. It is **not** verified at runtime.

What changed, in one line each:

- **Names**: all 96 tools carry their owning surface as a prefix, remainder in
  natural prose (`session_create`, `wiki_create_page`, `file_read`).
- **Deferral**: a per-tool `ToolPolicy.deferred` flag replaced the central
  source set; groups derive from the name prefix, so the tool name is the one
  source of truth. 67 deferred, 29 always-on (≈20 visible in a normal chat).
- **Incident fix**: every `session_*` tool is deferred, so `create_session`
  no longer sits in the schema of an agent doing wiki or file work.
- **Prompts**: the native tool-search variant is built from `(classic, native)`
  pairs defined once and asserted, replacing a verbatim-copy dict that had
  already drifted on 6 of 11 keys.
- **Identifiers**: implementation functions, approval handlers, `*_tool` vars
  and Input classes follow the tool names; genuinely different same-named
  symbols (routes, store/service methods, gmail client) deliberately did not
  move.

The arc also caused a live outage: `search_text` was not only a tool name but
the `session_messages` column, and the codemod rewrote it inside the store's
SQL. Tests could not catch it — fresh databases are self-consistent under either
spelling — so it surfaced only when the app stopped writing messages. It is
fixed, and the migration now heals databases the broken build touched; that heal
was executed against a reconstruction of the user's real schema and rows, not
just a synthetic fixture.

## Decisions

- **D1** — Namespace prefix first, remainder natural prose; no forced
  `<resource>_<verb>` reordering. (User, this session.)
- **D2** — Deferral is declared at the tool, and grouping derives from the name
  prefix; hand-maintained membership maps are deleted.
- **D3** — Do not rename anything that persists or is externally owned: scope
  keys, integration ids (`gmail`, `google_drive`), provider wire identifiers.
- **D4** — No regression eval for the original mis-call incident; judged too
  context-conditional to reproduce. (User, this session.)
- **D5** — User-authored automation prompts naming old tools are left alone; a
  stale name fails loudly with recovery text instead of being silently rewritten.
- **D6** — Internal identifier renaming stops at symbols that *are* the tools;
  same-named route handlers and service methods keep their names.

## Open questions

- Does the model, at runtime, load the `session` group only when the user really
  asks for chat management — and leave it alone during content work? (G1)
- Do desktop labels and icons render correctly for renamed tools in a live
  transcript, given `PREFIX_ICON` now anchors on real prefixes? (G1)

## Next action

Restart the server, then close G1: confirm the database heals on boot (the app
writes messages again), ask for a wiki/file task and confirm `session_create` is
never offered, and ask for chat management and confirm the `session` group loads
on demand. Desktop check: open a transcript and confirm renamed tools render
with correct labels and icons.

## Details

- [Research](research.md)
- [Implementation](implementation.md)
- [Verification](verification.md)
