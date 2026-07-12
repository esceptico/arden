# Model Registry Refactor Design

## Goal

Make model availability and capabilities come from the correct provider source, especially the account-specific OpenAI Codex catalog, while preserving synchronous model lookup for the existing runtime.

## Scope

- Keep the checked-in `models.dev` snapshot for Anthropic, OpenAI API, Google, and OpenRouter metadata.
- Fetch the authenticated Codex `/models` catalog when Codex OAuth is connected and during runtime reload.
- Filter Codex entries to `visibility == "list"` and `supported_in_api == true`.
- Replace only the Codex provider's runtime models atomically after a successful refresh.
- Keep a small bundled Codex fallback so configuration can validate before network refresh and when Codex is unavailable.
- Store provider capabilities on `Model` instead of inferring them from model-name prefixes.
- Preserve custom-model behavior and existing public lookup functions.

## Approaches Considered

1. **Runtime Codex catalog with bundled fallback — selected.** Correct for account rollouts and removals, yet resilient offline.
2. **Regenerate a checked-in Codex catalog.** Simpler runtime, but cannot reflect account-specific availability and drifts between releases.
3. **Maintain a hardcoded allowlist.** Smallest diff, but repeats the failure that exposed Luna incorrectly.

## Architecture

### Registry

Introduce a `ModelRegistry` that owns base, Codex, custom, and embedding models. Existing module-level functions delegate to one process registry, limiting call-site churn. Provider replacement builds a new dictionary and swaps it only after complete validation, so readers never observe a partial refresh.

### Codex Catalog

Add a focused catalog loader beside the Codex client. It reuses OAuth tokens and the same compatible client headers as completion requests, calls `GET /models?client_version=<version>`, validates entries, and converts API-supported visible models to `Model` values with the `openai-codex/` prefix and zero token pricing.

The catalog response supplies context limits, output limits, reasoning efforts, and capability flags. Missing optional fields use conservative defaults. HTTP, authentication, or schema failures leave the existing bundled or last-successful Codex set untouched and emit a warning.

### Runtime Flow

1. Build the registry synchronously from the checked-in provider snapshot, bundled Codex fallback, and custom models.
2. After runtime configuration loads, refresh Codex models when OAuth credentials exist.
3. Refresh again after Codex connection and `/reload`.
4. Re-resolve configured model fields after refresh so removed account models fall back through existing configuration rules.
5. Serve `/models` from the registry's current snapshot.

## Capabilities

Add an explicit `native_deferred_tools` field to `Model`. Generated first-party metadata and Codex catalog conversion populate it. `supports_native_deferred_tools()` becomes a direct registry lookup without provider or name heuristics.

No general capability framework is introduced. Additional flags remain out of scope until a consumer needs them.

## Fallback and Cache

The bundled Codex fallback contains only the currently API-supported visible models. A successful live refresh is authoritative for the process. Persistent network caching is out of scope because OAuth/account availability can change and the bundled fallback already covers offline startup.

## Error Handling

- Catalog failure: log once per refresh and retain the current Codex models.
- Malformed catalog entry: skip that entry and continue.
- Empty valid catalog: retain the current models rather than erasing Codex support.
- Removed configured model: existing configuration validation chooses the normal fallback during runtime reload.
- Custom models: remain isolated from provider replacement.

## Testing

- Parse representative Codex catalog entries and filter hidden or API-unsupported models.
- Verify malformed entries are skipped.
- Verify provider replacement is atomic and preserves custom models.
- Verify catalog failure retains fallback models.
- Verify capability lookup reads `Model.native_deferred_tools` without name heuristics.
- Keep the existing Codex completion request regression and run one authenticated Luna smoke test.

## Non-Goals

- Replacing `models.dev` for non-Codex providers.
- Fetching every provider's model list at startup.
- Persisting live Codex catalogs to disk.
- Redesigning custom model configuration or the model picker UI.
