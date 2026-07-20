# Separate Google Service Integrations Design

## Goal

Expose Gmail, Google Calendar, and Google Drive as independent native integrations. Google Drive v1 covers Google Docs and Google Sheets, including search, read, create, and bounded edits.

Preserve the existing chat connection recovery and constrained discovery contract. This work supplies better registered integrations and setup actions; it does not add another discovery or auto-fix path.

## Product model

Settings shows three integrations:

- **Gmail** — search, read, and send email.
- **Google Calendar** — search and manage events.
- **Google Drive** — search Google Docs and Sheets; read, create, and edit them.

Each service can be connected or disconnected for each Google account. The shared Google OAuth client credentials remain a one-time setup prerequisite.

Google combines OAuth grants for the same user and API project, and revocation invalidates all scopes granted to that project. Therefore:

- **Disconnect service** removes ntrp's local service binding and immediately removes that integration's tools. It does not call Google's project-wide revoke endpoint.
- **Remove Google account** revokes the Google grant and deletes every local binding/token for that account.
- The UI explains this distinction instead of implying that one Google scope can be revoked independently.

This follows Google's incremental-authorization and revocation model: <https://developers.google.com/identity/protocols/oauth2/web-server>.

## Connection and credential architecture

### Canonical account store

Replace filename discovery as the source of truth with a `GoogleAccountStore` under `ntrp/integrations/google_auth/`. It owns:

- stable account ID and display email;
- one credential file per account, protected by user-only filesystem permissions;
- scopes actually granted by Google;
- explicit local bindings for `gmail`, `calendar`, and `google_drive`;
- passive health inspection that never launches OAuth.

Token files are written atomically with user-only permissions. Integration clients receive token paths only from this store. Calendar no longer scans Gmail token filenames.

### OAuth flow

All service flows request identity scopes plus only the selected service's missing scopes. Reconnecting an existing account uses incremental authorization and replaces the stored combined credential after verifying the granted scopes.

Service scopes:

- Gmail: existing Gmail read/send scopes and Pub/Sub only when notifier setup needs it.
- Calendar: existing Calendar scope.
- Drive: `drive.metadata.readonly` for discovery, `documents` for Docs, and `spreadsheets` for Sheets. Use `drive.file` for files created or explicitly selected through ntrp where supported.

The Drive setup assistant warns that broad metadata access is a restricted Drive scope and identifies the Drive, Docs, and Sheets APIs that must be enabled. Google recommends narrow `drive.file` access, but it cannot provide autonomous search across all existing files: <https://developers.google.com/workspace/drive/api/guides/api-specific-auth>.

After OAuth, ntrp checks the scopes Google actually granted. Missing scopes produce the existing typed `scope_required` state.

### Server API

Add a provider-neutral Google router used by Settings and chat actions:

- `GET /google/accounts` — accounts, granted scopes, and service bindings.
- `POST /google/{integration_id}/connect` — connect or add scopes for one service.
- `DELETE /google/{integration_id}/accounts/{account_id}` — remove one local service binding.
- `DELETE /google/accounts/{account_id}` — revoke and remove the whole account.
- `POST /google/{integration_id}/verify` — passive service-specific verification.

`integration_id` is allowlisted to the registered Google integrations. Existing `/gmail/*` calls become thin compatibility wrappers during migration; all logic lives in the account store/service.

## Configuration and registry

Replace the single `config.google` runtime gate with persisted per-integration enabled state. Keep a legacy read fallback so existing users do not lose working tools.

Each registered integration owns a distinct connection:

- `GMAIL`: `connection_id="gmail"`
- `CALENDAR`: `connection_id="calendar"`
- `GOOGLE_DRIVE`: `connection_id="google_drive"`

Their `configured` callbacks query service bindings from `GoogleAccountStore`; their `enabled` callbacks query per-integration state. `IntegrationRegistry`, `ConnectionService`, `request_connection`, typed recovery, durable suspension, and retry policy remain canonical and unchanged.

`GOOGLE_DRIVE` is registered in `ALL_INTEGRATIONS` and deferred as `google_drive`. Its tools appear in discovery only through the existing connection descriptor catalog.

## Google Drive client

Add `ntrp/integrations/google_drive/` with a multi-account client. It builds Drive v3, Docs v1, and Sheets v4 clients from explicitly bound accounts.

All file identities are account-qualified (`account_id:file_id`) so duplicate IDs or multiple Google accounts cannot resolve ambiguously. Search results include stable source references, title, MIME kind, modified time, account, and `webViewLink`.

Reads are bounded:

- Docs are flattened to readable text while preserving paragraphs, lists, and tables.
- Sheets return a bounded A1 range with sheet names and tabular values.
- Oversized content is truncated with an explicit continuation/range hint.

Provider exceptions map only documented authentication and scope failures to `IntegrationConnectionError`. Permission, not-found, quota, malformed-range, and edit-conflict errors remain ordinary structured tool failures.

## Tool contract

### Read tools

- `search_google_drive(query, kind, account, limit)` — searches Docs and Sheets metadata.
- `read_google_doc(document_ref)` — returns normalized text and revision metadata.
- `read_google_sheet(spreadsheet_ref, range)` — returns bounded values using A1 notation.

### Write tools

- `create_google_doc(title, content, account)`
- `edit_google_doc(document_ref, operation, text, match)` where v1 operations are `append` and `replace_all`.
- `create_google_sheet(title, headers, rows, account)`
- `update_google_sheet(spreadsheet_ref, range, values, value_input_option)`
- `append_google_sheet_rows(spreadsheet_ref, range, rows, value_input_option)`

No raw Docs `batchUpdate` or Sheets request JSON is exposed to the model. Inputs are typed and bounded. Every write uses `ToolAction.WRITE`, external scope, the `google_drive` permission, and explicit approval.

Approval previews show document text changes or the old/new sheet range. Docs edits use revision controls and fail closed on a concurrent edit. Sheet writes are restricted to the approved range and value matrix. Write operations are never automatically retried after connection recovery; the user/model must retry explicitly after re-verification.

Read results emit `ToolSourceRef(provider="google_drive", kind="document" | "spreadsheet", ...)`. Write results return the affected qualified reference and browser URL.

## Desktop behavior

Replace the umbrella Google card with a Google account section plus three service cards. Each card shows per-account state and offers Connect, Reconnect, Disable/Enable, and Disconnect as applicable. Account removal is a separate destructive menu action with copy stating that all Google services will be removed.

The setup assistant starts with the exact service, shows its requested scopes, runs service-specific preflight, and verifies only that integration afterward.

The chat connection banner routes OAuth by `integration_id` through the generic Google endpoint. It keeps compatibility for already-persisted pending events whose legacy `connection_id` is `google`. No event schema or discovery prompt changes are required.

## Migration

Migration is passive and idempotent:

1. Inspect legacy `gmail_token*.json` and `calendar_token*.json` without refreshing them.
2. Identify the account where possible and import each credential once.
3. Create service bindings based only on scopes present in the token.
4. Map legacy `google=true` to enabled bindings for the imported Gmail/Calendar services.
5. Keep legacy routes and pending `connection_id="google"` events working during one compatibility window.
6. Never delete a legacy token until the imported record is durably written and verified.

Legacy combined credentials may retain scopes for a locally disconnected service. Only account removal performs provider-wide revocation, matching Google's actual revocation semantics.

## Auto-fix and auto-discovery overlap

This feature intentionally reuses the work already committed in `71606ed2` and `7ed12cb6`:

- extend `IntegrationConnectionSpec` declarations; do not change connection states;
- register `google_drive` in the existing catalog; do not add suggestion heuristics;
- reuse `IntegrationConnectionError`; do not parse provider error strings in generic code;
- keep `request_connection`, durable suspension, verification, and safe-retry logic unchanged;
- make the desktop OAuth dispatcher generic, without changing the connection event/store contract.

No changes are expected in `integrations/base.py`, `integrations/registry.py`, `tools/connections.py`, or the durable connection lifecycle. Shared edit points are limited to Gmail/Calendar registration, `google_auth/auth.py`, and `actions/connections.ts`. Implementation should rebase on the latest auto-fix work before touching those files.

## Testing

Server tests cover:

- account-store atomic writes, multi-account bindings, disconnect, revoke, and legacy migration;
- exact per-service scopes and partially granted consent;
- independent registry states for Gmail, Calendar, and Drive;
- Drive search/source references and account-qualified IDs;
- Docs/Sheets read bounds and normalization;
- create/edit approvals, revision/range conflicts, and no write auto-retry;
- typed auth/scope recovery without changing discovery behavior;
- compatibility routes and legacy pending connection IDs.

Desktop tests cover separate service cards, account/service actions, destructive account removal, exact setup scopes, chat OAuth routing, and existing connection-banner recovery.

End-to-end verification connects one account to each service, independently disconnects one service, exercises Docs and Sheets read/write operations, and confirms auto-discovery exposes only the disconnected registered integration.

## Non-goals

- Google Slides content editing
- arbitrary Drive binary files, uploads, permissions, comments, or shared-drive administration
- Google Picker UI in v1
- background Drive ingestion or implicit memory indexing
- a second integration suggestion or recovery system
- selective provider-side scope revocation, which Google does not support per service for one API project
