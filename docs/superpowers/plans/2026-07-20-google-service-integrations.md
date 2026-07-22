# Separate Google Service Integrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship independently manageable Gmail, Google Calendar, and Google Drive integrations, with Docs/Sheets search, read, create, and bounded editing.

**Architecture:** A canonical Google account store owns combined OAuth credentials and explicit per-service bindings. Existing native integration descriptors remain the only recovery/discovery catalog; Gmail, Calendar, and the new Drive integration consume service-bound token paths. Drive exposes typed, approval-gated tools over Drive v3, Docs v1, and Sheets v4.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, google-auth-oauthlib, google-api-python-client, pytest, React 19, TypeScript, Zustand, Bun test.

## Global Constraints

- Preserve unrelated dirty-tree changes; stage only files named in each task.
- Do not alter generic connection states, `request_connection`, durable suspensions, or retry policy.
- Do not add keyword/regex integration discovery.
- Per-service disconnect removes the local binding; only account removal calls Google's project-wide revoke endpoint.
- All writes require approval and are never automatically retried after connection recovery.
- Drive v1 covers Docs and Sheets only; no Slides editing, Picker, uploads, permissions, comments, shared-drive administration, or memory ingestion.
- Use bounded tool inputs/outputs and account-qualified references (`account_id:file_id`).

---

### Task 1: Canonical Google account store and legacy migration

**Files:**
- Create: `apps/server/arden/integrations/google_auth/accounts.py`
- Modify: `apps/server/arden/integrations/google_auth/__init__.py`
- Test: `apps/server/tests/test_google_accounts.py`

**Interfaces:**
- Produces: `GoogleService = Literal["gmail", "calendar", "google_drive"]`.
- Produces: immutable `GoogleAccount(id, email, token_file, scopes, services)`.
- Produces: `GoogleAccountStore(root: Path)`, with `list_accounts()`, `accounts_for(service)`, `is_bound(service)`, `token_path(account)`, `upsert_authorization(...)`, `disconnect_service(...)`, `remove_account(...)`, and `migrate_legacy()`.
- Storage: `google_accounts.json` plus `google_tokens/<account_id>.json`, written atomically with mode `0600`.

- [ ] **Step 1: Write failing account-store tests**

```python
def test_store_binds_services_without_duplicating_credential(tmp_path):
    store = GoogleAccountStore(tmp_path)
    first = store.upsert_authorization(
        email="user@example.com",
        credential_json='{"token":"one"}',
        scopes=("gmail.readonly",),
        service="gmail",
    )
    second = store.upsert_authorization(
        account_id=first.id,
        email="user@example.com",
        credential_json='{"token":"two"}',
        scopes=("gmail.readonly", "calendar"),
        service="calendar",
    )
    assert second.services == frozenset({"gmail", "calendar"})
    assert len(list((tmp_path / "google_tokens").glob("*.json"))) == 1


def test_disconnect_removes_only_local_service_binding(tmp_path):
    store = seeded_store(tmp_path, services={"gmail", "calendar"})
    account = store.list_accounts()[0]
    store.disconnect_service(account.id, "gmail")
    assert store.accounts_for("gmail") == []
    assert [a.id for a in store.accounts_for("calendar")] == [account.id]
    assert store.token_path(account).exists()


def test_legacy_migration_is_idempotent_and_classifies_scopes(tmp_path):
    write_legacy_token(tmp_path / "gmail_token_user@example.com.json", GMAIL_AND_CALENDAR_SCOPES)
    store = GoogleAccountStore(tmp_path)
    store.migrate_legacy()
    store.migrate_legacy()
    [account] = store.list_accounts()
    assert account.email == "user@example.com"
    assert account.services == frozenset({"gmail", "calendar"})
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd apps/server && uv run pytest tests/test_google_accounts.py -q`
Expected: FAIL because `arden.integrations.google_auth.accounts` does not exist.

- [ ] **Step 3: Implement models, locked mutations, atomic JSON writes, and migration**

```python
GoogleService = Literal["gmail", "calendar", "google_drive"]


@dataclass(frozen=True)
class GoogleAccount:
    id: str
    email: str | None
    token_file: str
    scopes: tuple[str, ...]
    services: frozenset[GoogleService]


class GoogleAccountStore:
    def __init__(self, root: Path):
        self.root = root
        self.index_path = root / "google_accounts.json"
        self.token_dir = root / "google_tokens"
        self._lock = RLock()

    def accounts_for(self, service: GoogleService) -> list[GoogleAccount]:
        return [account for account in self.list_accounts() if service in account.services]

    def is_bound(self, service: GoogleService) -> bool:
        return bool(self.accounts_for(service))
```

Implement `upsert_authorization` as one locked mutation: resolve account by explicit ID or case-folded email, atomically replace the credential, merge actual scopes and service binding, then atomically replace the index. `disconnect_service` removes only the binding; `remove_account` deletes index entry and token. `migrate_legacy` imports each legacy path once, derives bindings strictly from known scope URIs, and retains the legacy file.

- [ ] **Step 4: Run focused tests**

Run: `cd apps/server && uv run pytest tests/test_google_accounts.py -q`
Expected: PASS.

- [ ] **Step 5: Commit account store**

```bash
git add apps/server/arden/integrations/google_auth/accounts.py apps/server/arden/integrations/google_auth/__init__.py apps/server/tests/test_google_accounts.py
git commit -m "feat(server): add canonical Google account store"
```

### Task 2: Service-scoped OAuth and per-integration enabled state

**Files:**
- Modify: `apps/server/arden/integrations/google_auth/auth.py`
- Modify: `apps/server/arden/config.py`
- Modify: `apps/server/arden/server/schemas.py`
- Modify: `apps/server/arden/server/routers/settings.py`
- Test: `apps/server/tests/test_setup_routes.py`
- Test: `apps/server/tests/test_config_service.py`

**Interfaces:**
- Produces: `GOOGLE_SERVICE_SCOPES: dict[GoogleService, tuple[str, ...]]`.
- Produces: `authorize_google_service(service, *, account_id=None, store=None) -> GoogleAccount`.
- Produces: `Config.integration_enabled(integration_id: str) -> bool` backed by persisted `integration_states`.
- Maintains: `scopes_for_google_choice` and legacy `google` as compatibility adapters only.

- [ ] **Step 1: Add failing scope and configuration tests**

```python
def test_drive_scopes_cover_metadata_docs_and_sheets():
    assert scopes_for_google_service("google_drive") == [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/drive.metadata.readonly",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/spreadsheets",
    ]


def test_integration_enabled_prefers_service_state_over_legacy_google():
    config = Config(_env_file=None, google=True, integration_states={"gmail": False, "calendar": True})
    assert config.integration_enabled("gmail") is False
    assert config.integration_enabled("calendar") is True
    assert config.integration_enabled("google_drive") is False
```

- [ ] **Step 2: Verify focused failures**

Run: `cd apps/server && uv run pytest tests/test_setup_routes.py tests/test_config_service.py -q`
Expected: FAIL for missing service scope and config APIs.

- [ ] **Step 3: Implement exact scopes and OAuth persistence**

```python
SCOPES_IDENTITY = ("openid", "https://www.googleapis.com/auth/userinfo.email")
GOOGLE_SERVICE_SCOPES = {
    "gmail": (*SCOPES_IDENTITY, *SCOPES_GMAIL_READ, *SCOPES_GMAIL_SEND),
    "calendar": (*SCOPES_IDENTITY, *SCOPES_CALENDAR),
    "google_drive": (
        *SCOPES_IDENTITY,
        "https://www.googleapis.com/auth/drive.metadata.readonly",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/spreadsheets",
    ),
}


def scopes_for_google_service(service: GoogleService) -> list[str]:
    return list(GOOGLE_SERVICE_SCOPES[service])
```

`authorize_google_service` requests the selected service plus scopes already stored for that account, verifies the granted set, resolves email through OAuth2 userinfo, and calls `store.upsert_authorization`. Do not revoke inside service disconnect.

- [ ] **Step 4: Implement per-integration state with legacy fallback**

```python
integration_states: dict[str, bool] = Field(default_factory=dict)


def integration_enabled(self, integration_id: str) -> bool:
    if integration_id in self.integration_states:
        return self.integration_states[integration_id]
    if integration_id in {"gmail", "calendar"}:
        return self.google
    return False
```

Add `integration_states` to `PERSIST_KEYS`. Extend `IntegrationToggles` with `gmail`, `calendar`, and `google_drive`; merge these into the existing state map in `PATCH /config`. A legacy `google` patch sets Gmail and Calendar together but never enables Drive.

- [ ] **Step 5: Run focused tests**

Run: `cd apps/server && uv run pytest tests/test_setup_routes.py tests/test_config_service.py -q`
Expected: PASS.

- [ ] **Step 6: Commit auth and config**

```bash
git add apps/server/arden/integrations/google_auth/auth.py apps/server/arden/config.py apps/server/arden/server/schemas.py apps/server/arden/server/routers/settings.py apps/server/tests/test_setup_routes.py apps/server/tests/test_config_service.py
git commit -m "feat(server): scope Google connections by service"
```

### Task 3: Decouple Gmail and Calendar registrations

**Files:**
- Modify: `apps/server/arden/integrations/gmail/__init__.py`
- Modify: `apps/server/arden/integrations/calendar/__init__.py`
- Modify: `apps/server/arden/integrations/calendar/client.py`
- Test: `apps/server/tests/test_integration_connections.py`
- Create: `apps/server/tests/test_calendar_multi_account.py`

**Interfaces:**
- Consumes: `GoogleAccountStore.accounts_for(service)` and `Config.integration_enabled(id)`.
- Produces: Gmail connection ID `gmail`; Calendar connection ID `calendar`.
- Constraint: Calendar constructors use only explicit token paths.

- [ ] **Step 1: Write failing independent-state tests**

```python
def test_registered_google_integrations_have_independent_connections():
    assert GMAIL.connection.connection_id == "gmail"
    assert CALENDAR.connection.connection_id == "calendar"


def test_calendar_never_falls_back_to_gmail_token(monkeypatch, tmp_path):
    monkeypatch.setattr(calendar_client, "ARDEN_DIR", tmp_path)
    (tmp_path / "gmail_token_user@example.com.json").write_text("{}")
    assert GoogleCalendar().token_path == tmp_path / "calendar_token.json"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd apps/server && uv run pytest tests/test_integration_connections.py tests/test_calendar_multi_account.py -q`
Expected: FAIL because registrations still share `google` and Calendar scans Gmail files.

- [ ] **Step 3: Build clients only from explicit service bindings**

```python
def _build(config: Config) -> MultiCalendarSource | None:
    if not config.integration_enabled("calendar"):
        return None
    store = google_account_store()
    token_paths = [store.token_path(account) for account in store.accounts_for("calendar")]
    source = MultiCalendarSource(token_paths=token_paths, days_back=7, days_ahead=30)
    return source if source.sources else None
```

Apply the same shape to Gmail. Set each connection's `enabled`, `configured`, `connection_id`, and `required_scopes` to its own service contract. Remove Gmail token fallback from `GoogleCalendar.__init__`.

- [ ] **Step 4: Run focused and recovery tests**

Run: `cd apps/server && uv run pytest tests/test_integration_connections.py tests/test_calendar_multi_account.py tests/test_connection_recovery.py -q`
Expected: PASS.

- [ ] **Step 5: Commit decoupling**

```bash
git add apps/server/arden/integrations/gmail/__init__.py apps/server/arden/integrations/calendar/__init__.py apps/server/arden/integrations/calendar/client.py apps/server/tests/test_integration_connections.py apps/server/tests/test_calendar_multi_account.py
git commit -m "refactor(server): separate Gmail and Calendar connections"
```

### Task 4: Google Drive Docs/Sheets client

**Files:**
- Create: `apps/server/arden/integrations/google_drive/client.py`
- Create: `apps/server/arden/integrations/google_drive/render.py`
- Test: `apps/server/tests/test_google_drive_client.py`

**Interfaces:**
- Produces: `DriveFile(ref, account_id, file_id, name, kind, modified_time, url)`.
- Produces: `GoogleDriveClient(token_path, account_id, email, build_service=googleapiclient.discovery.build)` and `MultiGoogleDriveClient(accounts)`.
- Methods: `search`, `read_doc`, `create_doc`, `edit_doc`, `read_sheet`, `create_sheet`, `update_sheet`, `append_sheet_rows`, `verify_connection`.
- Produces: `flatten_google_doc(payload) -> str` preserving paragraphs, lists, and tables.

- [ ] **Step 1: Write failing mocked-client tests**

```python
def test_search_filters_docs_and_sheets_and_qualifies_refs(fake_services):
    client = GoogleDriveClient(Path("token.json"), "acct-1", "user@example.com", build_service=fake_services)
    results = client.search("roadmap", kind="all", limit=10)
    assert [item.ref for item in results] == ["acct-1:doc-1", "acct-1:sheet-1"]


def test_edit_doc_uses_revision_control(fake_services):
    client = seeded_drive_client(fake_services, revision_id="rev-7")
    client.edit_doc("doc-1", operation="append", text="Next", match=None)
    assert fake_services.docs.batch_body["writeControl"] == {"requiredRevisionId": "rev-7"}


def test_sheet_update_writes_only_requested_range(fake_services):
    client = seeded_drive_client(fake_services)
    client.update_sheet("sheet-1", "Data!A2:B2", [["a", 2]], "USER_ENTERED")
    assert fake_services.sheets.updated_range == "Data!A2:B2"
```

- [ ] **Step 2: Verify failure**

Run: `cd apps/server && uv run pytest tests/test_google_drive_client.py -q`
Expected: FAIL because the Drive client module does not exist.

- [ ] **Step 3: Implement bounded read/search methods**

Use Drive `files.list` with escaped `name/fullText` query, MIME allowlist, explicit fields, and page-size cap. Use Docs `documents.get(includeTabsContent=True)` and Sheets `spreadsheets.values.get`. `flatten_google_doc` walks structural elements recursively and emits tables as tab-separated rows. Reject unqualified references when multiple accounts are connected.

- [ ] **Step 4: Implement create/edit methods**

Docs creation calls `documents.create`, then inserts initial content. Docs edits support only `append` and `replace_all`, fetch the current revision immediately before `batchUpdate`, and include `requiredRevisionId`. Sheets creation uses `spreadsheets.create`; updates and appends use the values API with `RAW | USER_ENTERED` allowlisting.

- [ ] **Step 5: Run focused tests**

Run: `cd apps/server && uv run pytest tests/test_google_drive_client.py -q`
Expected: PASS.

- [ ] **Step 6: Commit Drive client**

```bash
git add apps/server/arden/integrations/google_drive/client.py apps/server/arden/integrations/google_drive/render.py apps/server/tests/test_google_drive_client.py
git commit -m "feat(server): add Google Docs and Sheets client"
```

### Task 5: Drive tools, approvals, provenance, and registration

**Files:**
- Create: `apps/server/arden/integrations/google_drive/tools.py`
- Create: `apps/server/arden/integrations/google_drive/__init__.py`
- Modify: `apps/server/arden/integrations/__init__.py`
- Modify: `apps/server/arden/tools/deferred.py`
- Modify: `apps/server/arden/agent/types/tool_presentation.py`
- Test: `apps/server/tests/test_google_drive_tools.py`
- Test: `apps/server/tests/test_deferred_tools.py`
- Test: `apps/server/tests/test_tool_presentation.py`

**Interfaces:**
- Produces exact tool names: `search_google_drive`, `read_google_doc`, `read_google_sheet`, `create_google_doc`, `edit_google_doc`, `create_google_sheet`, `update_google_sheet`, `append_google_sheet_rows`.
- All tools use permission `google_drive`; writes require approval.
- Read results emit account-qualified `ToolSourceRef` values.

- [ ] **Step 1: Write failing tool metadata and behavior tests**

```python
def test_drive_write_tools_require_approval():
    for tool in (create_google_doc_tool, edit_google_doc_tool, create_google_sheet_tool, update_google_sheet_tool, append_google_sheet_rows_tool):
        assert tool.policy.action == ToolAction.WRITE
        assert tool.policy.requires_approval is True
        assert tool.policy.permissions == frozenset({"google_drive"})


async def test_read_doc_returns_source_reference():
    result = await read_google_doc(execution_with_drive(), ReadGoogleDocInput(document_ref="acct:doc-1"))
    assert result.source_refs[0].provider == "google_drive"
    assert result.source_refs[0].ref == "acct:doc-1"
```

- [ ] **Step 2: Verify failure**

Run: `cd apps/server && uv run pytest tests/test_google_drive_tools.py tests/test_deferred_tools.py tests/test_tool_presentation.py -q`
Expected: FAIL because Drive tools and group are unregistered.

- [ ] **Step 3: Implement typed tools and approval previews**

Define strict Pydantic bounds for query length, result count, document text, A1 ranges, row count, column count, and total cells. Approval callbacks fetch old content/ranges and return `ApprovalInfo` with concise preview/diff. Convert provider validation/conflict errors into `ToolResult.failure` without generic string parsing.

- [ ] **Step 4: Register integration and deferred presentation**

```python
GOOGLE_DRIVE = Integration(
    id="google_drive",
    label="Google Drive",
    tools=DRIVE_TOOLS,
    build=_build,
    connection=IntegrationConnectionSpec(
        connection_id="google_drive",
        capability="Search, read, create, and edit Google Docs and Sheets",
        action="oauth",
        enabled=lambda config: config.integration_enabled("google_drive"),
        configured=lambda _config: google_account_store().is_bound("google_drive"),
        required_scopes=tuple(scopes_for_google_service("google_drive")),
    ),
)
```

Add `google_drive` to `ALL_INTEGRATIONS`, deferred source/group aliases/order/descriptions, and tool presentation using document/table icons.

- [ ] **Step 5: Run focused tests**

Run: `cd apps/server && uv run pytest tests/test_google_drive_tools.py tests/test_deferred_tools.py tests/test_tool_presentation.py tests/test_integration_connections.py -q`
Expected: PASS.

- [ ] **Step 6: Commit tools**

```bash
git add apps/server/arden/integrations/google_drive apps/server/arden/integrations/__init__.py apps/server/arden/tools/deferred.py apps/server/arden/agent/types/tool_presentation.py apps/server/tests/test_google_drive_tools.py apps/server/tests/test_deferred_tools.py apps/server/tests/test_tool_presentation.py apps/server/tests/test_integration_connections.py
git commit -m "feat(server): expose Google Drive agent tools"
```

### Task 6: Generic Google routes and chat connection actions

**Files:**
- Create: `apps/server/arden/server/routers/google.py`
- Modify: `apps/server/arden/server/app.py`
- Modify: `apps/server/arden/server/routers/gmail.py`
- Modify: `apps/server/arden/server/routers/setup.py`
- Modify: `apps/server/arden/server/runtime/core.py`
- Modify: `apps/desktop/src/api/settings.ts`
- Modify: `apps/desktop/src/actions/connections.ts`
- Test: `apps/server/tests/test_google_routes.py`
- Test: `apps/server/tests/test_provider_routes.py`
- Test: `apps/desktop/tests/connectionBanner.test.tsx`

**Interfaces:**
- Produces route contract from the design spec under `/google`.
- Produces desktop `connectGoogleServiceApi`, `disconnectGoogleServiceApi`, `removeGoogleAccountApi`, and `listGoogleAccountsApi`.
- Preserves legacy `/gmail/add`, `/gmail/accounts`, and `/gmail/{token_file}` as adapters.

- [ ] **Step 1: Add failing route tests**

```python
def test_google_connect_authorizes_exact_integration(client, monkeypatch):
    monkeypatch.setattr(google_router, "authorize_google_service", fake_authorize)
    response = client.post("/google/google_drive/connect", json={})
    assert response.status_code == 200
    assert fake_authorize.calls == ["google_drive"]


def test_disconnect_service_does_not_revoke_account(client, seeded_google_store, monkeypatch):
    revoke = Mock()
    monkeypatch.setattr(google_router, "revoke_google_account", revoke)
    response = client.delete("/google/gmail/accounts/acct-1")
    assert response.status_code == 200
    assert revoke.call_count == 0
```

- [ ] **Step 2: Verify route failures**

Run: `cd apps/server && uv run pytest tests/test_google_routes.py tests/test_provider_routes.py -q`
Expected: FAIL because the router is absent.

- [ ] **Step 3: Implement allowlisted routes and runtime refresh**

Use `GoogleService` validation, run browser OAuth through `asyncio.to_thread`, refresh native integrations once after mutations, and verify the exact registry descriptor. Account removal calls Google's revoke endpoint before deleting local state; if Google already reports an invalid token, finish local deletion and return a warning.

- [ ] **Step 4: Make chat OAuth routing generic**

```typescript
if (connection.action === "oauth" && GOOGLE_INTEGRATIONS.has(connection.integrationId)) {
  await connectGoogleServiceApi(state.config, connection.integrationId);
  await setIntegrationEnabled(connection.integrationId, true);
} else if (connection.action === "enable" && GOOGLE_INTEGRATIONS.has(connection.integrationId)) {
  await setIntegrationEnabled(connection.integrationId, true);
}
```

Accept legacy `connectionId === "google"` by routing from `integrationId`; do not change pending event/store types.

- [ ] **Step 5: Run server and desktop connection tests**

Run: `cd apps/server && uv run pytest tests/test_google_routes.py tests/test_provider_routes.py tests/test_connection_requests.py tests/test_connection_recovery.py -q`
Expected: PASS.

Run: `cd apps/desktop && bun test tests/connectionBanner.test.tsx tests/connectionEvent.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit routes and dispatcher**

```bash
git add apps/server/arden/server/routers/google.py apps/server/arden/server/app.py apps/server/arden/server/routers/gmail.py apps/server/arden/server/routers/setup.py apps/server/arden/server/runtime/core.py apps/desktop/src/api/settings.ts apps/desktop/src/actions/connections.ts apps/server/tests/test_google_routes.py apps/server/tests/test_provider_routes.py apps/desktop/tests/connectionBanner.test.tsx
git commit -m "feat: connect Google services independently"
```

### Task 7: Separate desktop service cards and setup flow

**Files:**
- Create: `apps/desktop/src/features/settings/components/GoogleServiceCard.tsx`
- Modify: `apps/desktop/src/features/settings/components/GoogleCard.tsx`
- Modify: `apps/desktop/src/features/settings/components/IntegrationsTab.tsx`
- Modify: `apps/desktop/src/features/settings/components/setup/GoogleSetupAssistant.tsx`
- Modify: `apps/desktop/src/features/settings/lib/integrationConnection.ts`
- Modify: `apps/desktop/src/features/settings/lib/setupAssistant.ts`
- Modify: `apps/desktop/src/api/types.ts`
- Test: `apps/desktop/tests/integrationConnection.test.ts`
- Test: `apps/desktop/tests/googleIntegrations.test.tsx`
- Test: `apps/desktop/tests/serverContracts.test.ts`

**Interfaces:**
- Consumes: `GoogleAccountSummary { id, email, services, scopes, error }`.
- Produces: one account section and Gmail/Calendar/Drive service cards with account-level Connect, Disable/Enable, Disconnect, Reconnect, and Remove Account actions.

- [ ] **Step 1: Add failing rendering and lifecycle tests**

```tsx
test("renders independent Google services from account bindings", async () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  await act(async () => root.render(<IntegrationsTab />));
  expect(host.textContent).toContain("Gmail");
  expect(host.textContent).toContain("Google Calendar");
  expect(host.textContent).toContain("Google Drive");
  expect(host.textContent).toContain("Docs and Sheets");
});


test("disconnecting Gmail preserves Calendar binding", async () => {
  const host = await renderWithGoogleAccount({ services: ["gmail", "calendar"] });
  const disconnect = [...host.querySelectorAll("button")].find(
    (button) => button.getAttribute("aria-label") === "Disconnect Gmail for user@example.com",
  );
  await act(async () => disconnect?.click());
  expect(apiCalls()).toContainEqual({ method: "DELETE", path: "/google/gmail/accounts/acct-1" });
  expect(apiCalls()).not.toContainEqual(expect.objectContaining({ path: "/google/accounts/acct-1" }));
});
```

- [ ] **Step 2: Verify failures**

Run: `cd apps/desktop && bun test tests/integrationConnection.test.ts tests/googleIntegrations.test.tsx tests/serverContracts.test.ts`
Expected: FAIL because the UI still renders one umbrella Google switch.

- [ ] **Step 3: Implement typed account/service state helpers**

```typescript
export type GoogleIntegrationId = "gmail" | "calendar" | "google_drive";

export interface GoogleAccountSummary {
  id: string;
  email: string | null;
  services: GoogleIntegrationId[];
  scopes: string[];
  error?: string | null;
}
```

Derive each card solely from server bindings and `serverConfig.integrations[id]`. Do not infer connection state from labels or token filenames.

- [ ] **Step 4: Implement cards and exact setup flow**

`GoogleCard` becomes the shared account container; `GoogleServiceCard` renders one registered service. Setup accepts the target integration, shows exact scopes and required APIs, and calls the generic route. Removing an account uses a destructive confirmation distinct from service disconnect.

- [ ] **Step 5: Run desktop tests and typecheck**

Run: `cd apps/desktop && bun test tests/integrationConnection.test.ts tests/googleIntegrations.test.tsx tests/serverContracts.test.ts tests/connectionBanner.test.tsx`
Expected: PASS.

Run: `cd apps/desktop && bun run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit desktop UI**

```bash
git add apps/desktop/src/features/settings/components/GoogleServiceCard.tsx apps/desktop/src/features/settings/components/GoogleCard.tsx apps/desktop/src/features/settings/components/IntegrationsTab.tsx apps/desktop/src/features/settings/components/setup/GoogleSetupAssistant.tsx apps/desktop/src/features/settings/lib/integrationConnection.ts apps/desktop/src/features/settings/lib/setupAssistant.ts apps/desktop/src/api/types.ts apps/desktop/tests/integrationConnection.test.ts apps/desktop/tests/googleIntegrations.test.tsx apps/desktop/tests/serverContracts.test.ts
git commit -m "feat(desktop): manage Google services separately"
```

### Task 8: Migration, regression, and final verification

**Files:**
- Modify: `apps/server/arden/integrations/README.md`
- Modify: `apps/server/tests/test_setup_routes.py`
- Modify: `apps/server/tests/test_integration_connections.py`
- Modify: `apps/desktop/tests/connectionBanner.test.tsx`

**Interfaces:**
- Verifies the complete contract; produces no new runtime abstraction.

- [ ] **Step 1: Add end-to-end contract tests**

```python
def test_legacy_combined_token_yields_two_independent_descriptors(runtime_with_legacy_google):
    descriptors = {item.integration_id: item for item in runtime_with_legacy_google.integrations.list_connections()}
    assert descriptors["gmail"].state == "connected"
    assert descriptors["calendar"].state == "connected"
    assert descriptors["google_drive"].state == "not_configured"
```

Add a desktop regression asserting a persisted legacy `connection_id="google"`, `integration_id="calendar"` still calls `/google/calendar/connect` and resolves the original pending request.

- [ ] **Step 2: Run full targeted server suite**

Run: `cd apps/server && uv run pytest tests/test_google_accounts.py tests/test_google_drive_client.py tests/test_google_drive_tools.py tests/test_google_routes.py tests/test_setup_routes.py tests/test_integration_connections.py tests/test_connection_suggestions.py tests/test_connection_requests.py tests/test_connection_recovery.py tests/test_deferred_tools.py tests/test_tool_presentation.py -q`
Expected: PASS.

- [ ] **Step 3: Run server lint**

Run: `cd apps/server && uv run ruff check arden/integrations/google_auth arden/integrations/google_drive arden/integrations/gmail arden/integrations/calendar arden/server/routers/google.py tests/test_google_accounts.py tests/test_google_drive_client.py tests/test_google_drive_tools.py tests/test_google_routes.py`
Expected: PASS.

- [ ] **Step 4: Run desktop regression suite**

Run: `cd apps/desktop && bun test tests/googleIntegrations.test.tsx tests/integrationConnection.test.ts tests/connectionBanner.test.tsx tests/connectionEvent.test.ts tests/serverContracts.test.ts`
Expected: PASS.

Run: `cd apps/desktop && bun run typecheck`
Expected: PASS.

- [ ] **Step 5: Update integration documentation**

Document the account store, service-binding semantics, exact Drive tool boundary, project-wide Google revocation behavior, and how new native Google services reuse `IntegrationConnectionSpec`.

- [ ] **Step 6: Commit verification and docs**

```bash
git add apps/server/arden/integrations/README.md apps/server/tests/test_setup_routes.py apps/server/tests/test_integration_connections.py apps/desktop/tests/connectionBanner.test.tsx
git commit -m "docs: document separate Google service connections"
```
