# Model Registry Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hardcoded Codex availability and runtime capability heuristics with an account-aware Codex catalog and an owned model registry.

**Architecture:** A process-local `ModelRegistry` owns immutable snapshots for standard, Codex, custom, and embedding models while preserving existing module-level lookup functions. An authenticated Codex catalog loader refreshes only the Codex provider before runtime construction and before config reload; failures retain the existing fallback snapshot.

**Tech Stack:** Python 3.13, dataclasses, httpx, FastAPI lifespan, pytest, Ruff

## Global Constraints

- Keep `models.dev` as the non-Codex provider metadata source.
- Filter live Codex models to visible entries with API support.
- Do not persist the live Codex catalog.
- Preserve existing model lookup function signatures and custom-model behavior.
- Never erase working Codex models on a failed or empty refresh.
- Keep changes inside the server model/provider/runtime boundary.

---

### Task 1: Owned Registry and Explicit Capability

**Files:**
- Modify: `apps/server/ntrp/llm/models.py`
- Test: `apps/server/tests/test_codex_models.py`

**Interfaces:**
- Produces: `Model.native_deferred_tools: bool`
- Produces: `ModelRegistry.replace_provider_models(provider: Provider, models: list[Model]) -> bool`
- Preserves: `get_model`, `get_models`, `get_models_by_provider`, `list_models`, custom-model functions

- [ ] **Step 1: Write failing registry tests**

```python
def test_registry_replaces_only_one_provider():
    registry = ModelRegistry([...])
    changed = registry.replace_provider_models(Provider.OPENAI_CODEX, [codex_model])
    assert changed is True
    assert registry.get_model("custom/model").provider == Provider.CUSTOM

def test_deferred_tools_use_explicit_capability():
    model = Model("not-name-derived", Provider.OPENAI, 128_000, native_deferred_tools=True)
    registry = ModelRegistry([model])
    assert registry.supports_native_deferred_tools(model.id)
```

- [ ] **Step 2: Run tests and confirm behavior is missing**

Run: `.venv/bin/pytest tests/test_codex_models.py -q`

Expected: FAIL because `ModelRegistry` and `native_deferred_tools` do not exist.

- [ ] **Step 3: Implement the registry**

Add an owned dictionary with atomic provider replacement, snapshot-returning getters, and explicit capability lookup. Move custom model mutation behind the registry while keeping module-level compatibility wrappers.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest tests/test_codex_models.py tests/test_config_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit registry ownership**

Run: `git add apps/server/ntrp/llm/models.py apps/server/tests/test_codex_models.py && git commit -m "refactor(llm): own model registry state"`

### Task 2: Authenticated Codex Catalog

**Files:**
- Create: `apps/server/ntrp/llm/openai_codex_catalog.py`
- Modify: `apps/server/ntrp/llm/openai_codex_auth.py`
- Modify: `apps/server/ntrp/llm/openai_codex.py`
- Test: `apps/server/tests/test_openai_codex_catalog.py`
- Test: `apps/server/tests/test_openai_codex_client.py`

**Interfaces:**
- Produces: `codex_request_headers(tokens: OpenAICodexTokens) -> dict[str, str]`
- Produces: `parse_codex_catalog(payload: object) -> list[Model]`
- Produces: `refresh_codex_models() -> bool`
- Consumes: `replace_provider_models(Provider.OPENAI_CODEX, models)`

- [ ] **Step 1: Write failing parser and refresh tests**

```python
def test_parse_codex_catalog_filters_visibility_and_api_support():
    models = parse_codex_catalog({"models": [visible, hidden, unsupported]})
    assert [model.id for model in models] == ["openai-codex/gpt-live"]
    assert models[0].native_deferred_tools is True

async def test_refresh_failure_retains_registry(monkeypatch):
    before = get_models_by_provider(Provider.OPENAI_CODEX)
    monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(side_effect=httpx.ConnectError("down")))
    assert await refresh_codex_models() is False
    assert get_models_by_provider(Provider.OPENAI_CODEX) == before
```

- [ ] **Step 2: Run tests and confirm imports fail**

Run: `.venv/bin/pytest tests/test_openai_codex_catalog.py -q`

Expected: FAIL because the catalog module does not exist.

- [ ] **Step 3: Centralize Codex client identity**

Move the compatible client version and header construction into `openai_codex_auth.py`; use the helper from both completion and catalog requests.

- [ ] **Step 4: Implement catalog parsing and refresh**

Parse `slug`, `visibility`, `supported_in_api`, `context_window`, `supported_reasoning_levels`, and `supports_search_tool`. Skip malformed entries; return `False` without replacement for request errors or an empty valid set.

- [ ] **Step 5: Run catalog and client tests**

Run: `.venv/bin/pytest tests/test_openai_codex_catalog.py tests/test_openai_codex_client.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Codex catalog support**

Run: `git add apps/server/ntrp/llm/openai_codex_catalog.py apps/server/ntrp/llm/openai_codex_auth.py apps/server/ntrp/llm/openai_codex.py apps/server/tests/test_openai_codex_catalog.py apps/server/tests/test_openai_codex_client.py && git commit -m "feat(llm): load live Codex model catalog"`

### Task 3: Runtime Refresh Integration

**Files:**
- Modify: `apps/server/ntrp/server/app.py`
- Modify: `apps/server/ntrp/server/runtime/config.py`
- Modify: `apps/server/ntrp/server/runtime/core.py`
- Modify: `apps/server/ntrp/server/routers/providers.py`
- Test: `apps/server/tests/test_runtime_config_status.py`
- Test: `apps/server/tests/test_openai_codex_auth.py`

**Interfaces:**
- `RuntimeConfig` consumes `refresh_models: Callable[[], Awaitable[bool]]`
- FastAPI lifespan refreshes the registry before constructing `Runtime`
- Config reload refreshes the registry before calling `get_config()`

- [ ] **Step 1: Write failing refresh-order tests**

```python
async def test_reload_refreshes_models_before_reading_config():
    events = []
    runtime = RuntimeConfig(..., refresh_models=lambda: record(events, "models"))
    await runtime.reload()
    assert events[:2] == ["models", "config"]
```

Add a provider-status test proving successful OAuth polling reloads runtime configuration once live models are available.

- [ ] **Step 2: Run tests and confirm refresh hook is missing**

Run: `.venv/bin/pytest tests/test_runtime_config_status.py tests/test_openai_codex_auth.py -q`

Expected: FAIL because `RuntimeConfig` has no model refresh callback.

- [ ] **Step 3: Wire startup and reload**

Refresh before `Runtime()` in lifespan. Inject the refresh callback into `RuntimeConfig` and await it before `get_config()` during reload. Make OAuth status trigger runtime reload only when login reports connected.

- [ ] **Step 4: Run runtime tests**

Run: `.venv/bin/pytest tests/test_runtime_config_status.py tests/test_openai_codex_auth.py -q`

Expected: PASS.

- [ ] **Step 5: Commit runtime refresh wiring**

Run: `git add apps/server/ntrp/server/app.py apps/server/ntrp/server/runtime/config.py apps/server/ntrp/server/runtime/core.py apps/server/ntrp/server/routers/providers.py apps/server/tests/test_runtime_config_status.py apps/server/tests/test_openai_codex_auth.py && git commit -m "feat(server): refresh Codex models with runtime config"`

### Task 4: Fallback Alignment and Verification

**Files:**
- Modify: `apps/server/ntrp/llm/models.py`
- Test: `apps/server/tests/test_codex_models.py`

**Interfaces:**
- Bundled Codex fallback contains only visible API-supported models known at release time.

- [ ] **Step 1: Add fallback parity test**

Assert fallback IDs match the six supported Codex API models and exclude Spark, hidden models, and retired entries.

- [ ] **Step 2: Align fallback metadata**

Add Luna and Terra, remove stale 5.2/5.3 Codex entries, and set explicit capability values.

- [ ] **Step 3: Run focused and full server verification**

Run: `.venv/bin/pytest tests/test_codex_models.py tests/test_openai_codex_catalog.py tests/test_openai_codex_auth.py tests/test_openai_codex_client.py tests/test_provider_image_formats.py tests/test_runtime_config_status.py tests/test_config_service.py -q`

Run: `.venv/bin/ruff check ntrp/llm/models.py ntrp/llm/openai_codex.py ntrp/llm/openai_codex_auth.py ntrp/llm/openai_codex_catalog.py ntrp/server/app.py ntrp/server/runtime/config.py ntrp/server/runtime/core.py ntrp/server/routers/providers.py tests/test_codex_models.py tests/test_openai_codex_catalog.py tests/test_openai_codex_client.py tests/test_runtime_config_status.py`

Expected: all tests pass and Ruff reports no errors.

- [ ] **Step 4: Run live Codex smoke checks**

Fetch the authenticated catalog and verify the runtime registry matches API-supported visible entries. Send one minimal Luna completion through `OpenAICodexClient` and expect `OK`.

- [ ] **Step 5: Commit fallback alignment**

Run: `git add apps/server/ntrp/llm/models.py apps/server/tests/test_codex_models.py && git commit -m "fix(llm): align Codex fallback catalog"`
