# Implementation

> A checked item means implemented, not verified. Verification evidence is recorded separately.

## Intended outcome

A model has exactly one unambiguous way to discover a deferred tool; discovery survives valid continuation/compaction, but execution is always authorized against the current registry.

## Checklist

- [x] **I-01 — Remove duplicate native loaders**
  - On OpenAI/Anthropic native paths, omit Arden's function-form `tool_search`; send only provider-native search plus deferred definitions.
  - Stop prompting `tool_search(query="select:...")` on native paths. Keep `load_tools` only for non-native models.
  - Required verification: request-shape tests assert exactly one search mechanism.
- [x] **I-02 — Preserve typed discovery state**
  - Replay complete provider-native search call/output/reference items, or use provider conversation/response IDs where adopted.
  - Reconstruct discovered tools from structured history; never parse prose such as “Loaded tools.”
  - Required verification: a later turn can call a previously discovered tool without an unrelated model action.
- [x] **I-03 — Unify visibility and execution authority**
  - Intersect reconstructed discovery with the current allowed deferred-tool catalog before visibility or dispatch.
  - Return typed stale-call recovery instructing the model to use provider-native search again.
- [x] **I-04 — Make compaction/resume explicit**
  - Preserve the structured loaded-tool baseline in rehydration metadata.
  - Remove silent `loaded_tools.clear` behavior.
- [x] **I-05 — Contain malformed workflow fallback**
  - Make `investigate` require an explicit non-empty question and meaningful target before spawning.
  - Settle cancelled workflow completion without automatically starting a fresh chat run solely to narrate cancellation.
- [x] **I-06 — Preserve wiki evidence across turns**
  - Share resource observations across runs in one session.
  - On compaction, revoke content-read authority while preserving version/head CAS evidence.
- [x] **I-07 — Expose typed failures to models**
  - Persist tool outcomes and append compact status/error/recovery metadata to provider-visible results without changing UI content.
- [x] **I-08 — Bound workflow execution**
  - Defer workflow exposure, require approval, validate typed preset inputs, and cap panel fan-out before spawning.
- [x] **I-09 — Run mutation preconditions before approval**
  - Add a reusable tool preflight stage after argument/capability validation and before approval.
  - Keep the same checks at execution time to close approval-time races.
- [x] **I-10 — Add exact-text wiki patching**
  - Add `wiki_patch_page(path, old_text, new_text)` with a unique exact-match requirement and compare-and-swap commit.
  - Preserve unrelated current content; return typed missing/ambiguous/conflict recovery.
- [x] **I-11 — Expose read dependencies with mutations**
  - On native deferred paths, loading a wiki mutation also exposes `wiki_read_page` when allowed.
- [x] **I-12 — Align model guidance with enforcement**
  - Put the full-replacement read prerequisite, unchanged-version validity, and patch/full-replacement distinction in prompt and tool descriptions.

## Notes

- Avoid keyword/regex detection of `noop`; enforce typed contracts and state invariants.
- Do not deduplicate identical stateful calls generically; repair the proven state-transition loop.
