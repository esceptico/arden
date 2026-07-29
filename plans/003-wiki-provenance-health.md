# Plan 003: Emit stale-page and dangling-citation health issues

## Outcome

Make the declared `stale_page` and `dangling_citation` health codes real,
deterministic evidence instead of dead enum values.

## Current state

- `WikiHealthIssueCode` declares both codes.
- `_mechanical_issues()` emits link, validation, integrity, and index issues,
  but not these two.
- Runtime adds only `fact_review_due`.
- Wiki retrieval already defines a fact-backed page as stale when it has
  `fact_citations` and its valid `generated_from_revision` differs from the
  current fact revision.
- Citation parsing validates shape but not whether an exact fact version exists.

## Implementation

### Stale pages

In `apps/server/arden/wiki/health.py`, derive `STALE_PAGE` from
`WikiChangesReport.current_records` using the same semantics as
`wiki/context.py`:

- the page carries `fact_citations`;
- `generated_from_revision` is syntactically valid;
- the current fact revision is non-null;
- the two revisions differ.

Evidence must include both revisions. Skip malformed provenance already emitted
as `validation_error`, and never mark ordinary feed/navigation pages stale only
because their producer hash differs from the fact head.

Move the shared predicate into one named function used by context and health;
do not duplicate subtly different rules. Tighten the current retrieval behavior
at the same time: a cited page with a non-digest `generated_from_revision`
reports freshness `invalid`, not `stale` or `current`; health continues to emit
its existing `validation_error`.

### Dangling citations

During `Runtime.project_wiki_health()`:

1. Pin the fact revision and wiki report as it already does.
2. Collect each syntactically valid `(fact_id, version)` from current pages.
3. Resolve each exact version through `FactLedger.get_version()`.
4. Emit one `DANGLING_CITATION` per missing exact fact version.
5. Recheck the fact revision and retry through the existing bounded projection
   loop if it changed.

A superseded or retracted historical version is still valid provenance and is
not dangling. Its page can still be stale and republished. Invalid citation
shape remains one `validation_error`, not a duplicate dangling issue.

Owner selection:

- Dream-owned cited pages: add/use `Memory Dream`;
- other fact-backed pages: `Synthesis`.

Keep health projection read-only with respect to facts. Do not repair, remove,
or rewrite citations in the projector.

### Structure

Put cross-ledger assembly in a focused runtime helper rather than adding more
branches to `Runtime.project_wiki_health()`. `arden.wiki` must not import the
fact ledger.

## Tests

Extend `test_wiki_health.py`:

- old generated revision emits `stale_page`;
- matching revision does not;
- producer page without fact citations does not;
- malformed provenance emits only `validation_error`;
- context reports malformed cited provenance as `freshness: invalid`;
- rendered issue includes target, evidence, and owner.

Extend `test_fact_runtime.py`:

- missing fact ID/version emits `dangling_citation`;
- exact active version is valid;
- exact superseded/retracted historical version is valid;
- Dream issue ownership is correct;
- fact-head movement triggers retry and never mixes snapshots;
- repairing/re-synthesizing clears the issue.

## Acceptance

- Every declared health code has a production emitter or is removed.
- `health.md` cannot report healthy when a cited page is stale or references a
  nonexistent fact version.
- Retrieval and health use one freshness rule.
- No false stale issue is added to ordinary producer pages.
