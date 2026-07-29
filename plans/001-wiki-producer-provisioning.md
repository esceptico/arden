# Plan 001: Provision a wiki producer as one recoverable operation

## Outcome

Add one approved, idempotent agent operation that creates:

1. an empty producer-owned page under `feeds/` or `insights/`; and
2. the scheduled automation that reads and updates that page.

This closes the product capability gap. It is not a guide for configuring any
specific Coast automation.

## Current state

- `WikiService.publish_generated()` can create or update generated pages
  atomically inside the wiki repository.
- `publish_wiki_generated` deliberately accepts only an existing page, exact
  page/repository versions, and the page's owning automation ID.
- `AutomationService.create()` provisions a channel and persists an automation
  in separate SQLite-backed stores.
- Runtime completion proof correctly requires a producer run to read one of its
  owned pages; a publish is optional for a valid no-change run.
- There is no transaction shared by the wiki repository, channel store, and
  automation store.

## Design

### Application coordinator

Add `apps/server/arden/services/wiki_producers.py`. This is a cross-domain
application service; do not make `arden.wiki` depend on `arden.automation`.

Define explicit input/result value objects and a `WikiProducerProvisioner` with
these dependencies:

- `WikiService`
- `AutomationService`
- `SessionService`
- the registered tool-name set for scope validation

The request contains:

- stable `page_id`, Markdown `path`, title, and aliases;
- automation name, prompt, optional model, and the existing trigger fields;
- the external source-tool scope needed by the producer;
- the caller's exact expected wiki head.

The provisioner must:

1. Accept only new `feeds/*.md` or `insights/*.md` pages.
2. Derive a stable reserved automation ID and channel session ID from
   `page_id`; callers cannot supply either.
3. Add exactly `read_wiki_page` and `publish_wiki_generated` to the validated
   source-tool scope.
4. Set `auto_approve=True` because the whole producer contract is reviewed in
   the provisioning approval.
5. Canonically hash the complete normalized request. Set page metadata itself:
   `producer_automation_id` plus a versioned producer-contract marker carrying
   that fingerprint. Do not accept arbitrary metadata or `fact_citations`.
6. Create the empty generated page first with `WikiService.publish_generated()`
   and exact head CAS.
7. Reuse or provision the deterministic channel, then call
   `AutomationService.create(..., task_id=...)`.
8. Return the exact page ID/version/head, automation ID, channel ID, and whether
   each side was created or replayed.

### Forward recovery

This is a saga, not an atomic cross-store transaction.

- No enabled automation may exist before its page.
- If the wiki commit succeeds and automation creation fails, return a typed,
  retryable partial-provision error containing the stable page and automation
  IDs.
- An exact retry inspects both sides and creates only the missing half.
- A retry after complete success returns the same result without new history,
  channel, or automation rows.
- A changed retry has a different contract fingerprint and fails closed.
  Foreign existing pages, mismatched owners, conflicting tasks, and stale wiki
  heads also fail closed.
- Do not archive/delete the page as compensation: a user may have edited the
  visible partial page before retry.

Make deterministic channel provisioning reuse an existing channel with the
same session ID and `origin_automation_id`; do not create orphan channels on
retry.

### Public tool

Add `provision_wiki_producer` in a focused tool module, or in
`apps/server/arden/tools/wiki.py` if it remains readable.

- Pydantic input with `extra="forbid"`.
- `ToolAction.WRITE`, approval required, idempotent.
- Permissions include both `wiki` and `automation`.
- Approval preview shows both durable artifacts: page identity, trigger,
  model, auto-approval, exact tools, and full prompt.
- Result returns structured page, automation, and recovery state.

Register it in:

- `apps/server/arden/integrations/core.py` under `_wiki`;
- `apps/server/arden/tools/deferred.py` discovery text.

Extract the exact tool-scope matching currently private to
`tools/automation.py` into one small shared validation function. Both ordinary
automation creation and producer provisioning must reject unmatched scope
entries.

Do not add an HTTP/Desktop surface in this change. The missing ledger
capability is the public agent tool; a future UI can call the same provisioner.

### Preserve existing boundaries

Do not change:

- `publish_wiki_generated` update-only behavior;
- page/head compare-and-swap;
- generated-region user-edit protection;
- producer completion proof;
- Synthesis, Dream, fact-backed pages, health, or generic page creation;
- the existing automation creation tool.

## Tests

Add `apps/server/tests/test_wiki_producers.py`:

- happy path creates exactly one page, channel, and automation;
- only `feeds/` and `insights/` are accepted;
- required wiki tools are added literally and source scopes are validated;
- metadata and stable IDs are coordinator-owned;
- exact retry after success is a no-op;
- exact retry resumes after a page-only partial result;
- changed retry and foreign page/task conflicts fail closed;
- stale wiki head and concurrent creation conflict;
- deterministic channel reuse after a partial failure.

Extend:

- `test_wiki_tools.py`: approval and structured result;
- `test_deferred_tools.py`: searchable `_wiki` discovery;
- `test_fact_runtime.py`: provisioned producer passes owned-page read proof and
  can finish without publishing;
- tool registry/catalog assertions.

## Acceptance

- One approved tool call can provision both artifacts.
- No first-run page claiming is added.
- No second producer registry/table is introduced.
- Partial failure is visible and exact retry converges.
- Existing Email feed and Dream ownership tests remain unchanged and pass.
