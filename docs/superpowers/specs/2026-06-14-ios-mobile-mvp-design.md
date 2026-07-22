# iOS Mobile MVP and Future Local Tools - Design

**Date:** 2026-06-14
**Status:** Draft
**Scope:** Build a simple native iOS client for the existing Arden server. Keep
the design compatible with future desktop-hosted local tools, but do not build
desktop pairing, relay, or local tools in v1.

---

## 1. Goal

Use arden from an iPhone with the current server as the only backend:

```text
iOS app <-> Arden server
```

The iOS app is a thin client. The server remains the source of truth for agent
logic, sessions, history, approvals, run state, and server-side tools.

---

## 2. v1 Product Scope

### Must have

- Settings screen for `serverUrl` and API key.
- Store API key in iOS Keychain.
- List sessions.
- Create a new session.
- Open session history.
- Send a chat message.
- Stream run events with SSE.
- Show pending approval cards.
- Approve or reject tool calls.
- Cancel the active run.
- Show basic connection/error states.

### Nice later, not v1

- Push notifications.
- Desktop pairing.
- Desktop local tools.
- Relay service.
- Background streaming while app is closed.
- File/image attachments.
- Session/project management beyond the basics.

---

## 3. Existing Server API

Use the current bearer-token API. The app sends:

```http
Authorization: Bearer <api_key>
```

Endpoints:

| Feature | Endpoint |
|---|---|
| Health/auth check | `GET /health` |
| List sessions | `GET /sessions` |
| Create session | `POST /sessions` |
| Load history | `GET /session/history?session_id=...` |
| Send message | `POST /chat/message` |
| Stream events | `GET /chat/events/{session_id}?stream=true&after_seq=...` |
| Resolve approval | `POST /tools/result` |
| Cancel run | `POST /cancel` |

SSE resume uses `after_seq` / `Last-Event-ID` semantics. The app should keep
the latest sequence for each open session while the app is running and reconnect
from it.

---

## 4. iOS App Structure

Recommended stack:

- SwiftUI.
- `URLSession` for JSON requests.
- `URLSession.bytes` for SSE streaming.
- Small local SSE parser.
- Keychain wrapper for API key.
- Volatile app state first; no local database in v1.

Core modules:

```text
ArdenApiClient
  - request JSON endpoints
  - attach bearer token
  - normalize errors

SseClient
  - connect to /chat/events/{session_id}
  - parse event/data/id frames
  - reconnect with after_seq

SessionStore
  - sessions
  - selected session
  - history
  - active run snapshot
  - pending approvals

Views
  - SettingsView
  - SessionListView
  - ChatView
  - ApprovalCard
```

---

## 5. UX

Keep it simple:

- First launch opens Settings if server/API key are missing.
- Main screen lists sessions.
- Tapping a session opens chat.
- Chat view has transcript, composer, active-run status, cancel button.
- Approval card appears inline or pinned above composer when needed.
- Reconnect banner appears when SSE is disconnected.

No mobile-specific agent behavior. A message from iOS is the same as a message
from desktop.

---

## 6. Connectivity

Development:

- Simulator: `http://localhost:6877` may work depending on simulator network
  routing; otherwise use the Mac LAN IP.
- Physical iPhone: use Mac LAN IP, for example `http://192.168.x.x:6877`.

Production later:

- Prefer a cloud-hosted Arden server for simple mobile access.
- If execution must happen on a private desktop, add a relay/host model later
  instead of exposing the desktop server directly.

---

## 7. Future Architecture: Local Tools

Future target:

```text
mobile <-> Arden server <-> desktop host
             |
             +-> agent loop
             +-> DB/session state
             +-> server tools
```

The mobile app always talks to the server. The desktop host also talks to the
server. Mobile should never call desktop tools directly.

### Tool authority rule

Tools run where their authority lives:

| Tool kind | Runtime |
|---|---|
| Server DB/search/API tools | Server |
| Cloud OAuth token stored on server | Server |
| Local files/repo/shell/browser | Desktop host |
| Local app automation/OS credentials | Desktop host |

### Tool runtime metadata

Every tool should eventually declare a runtime:

```ts
runtime = "server" | { type: "desktop", hostId: string }
```

Agent calls stay normal. A router decides where execution happens:

- server tool: execute inline
- desktop tool: send request to host and await result
- host offline: return structured tool error
- timeout/cancel: return structured tool error

---

## 8. Future Architecture: Desktop Capability Host

Desktop host responsibilities:

- Keep an outbound websocket connection to the server.
- Register host presence.
- Register a tool manifest.
- Receive `tool_request`.
- Execute local tool.
- Return `tool_result` or `tool_error`.
- Enforce local safety policy even if server makes a mistake.

Initial harmless tool:

```text
desktop.ping -> { hostId, appVersion, cwd?, online: true }
```

Do not start with raw `bash` or unrestricted `read_file`.

---

## 9. Future Architecture: Relay

A relay is only needed when the server is not the same process/control plane
that mobile and desktop can both reach.

Relay shape:

```text
desktop host -> relay <- mobile/server
```

Both sides open outbound connections. This avoids opening inbound ports on the
desktop.

In Arden's preferred architecture, the cloud Arden server can be the relay/control
plane. It should forward typed protocol messages, not arbitrary HTTP:

- `host_online`
- `tool_manifest`
- `tool_request`
- `tool_result`
- `approval_needed`
- `cancel_tool_request`
- `heartbeat`

---

## 10. Pairing/Auth Later

Pair where dangerous capability lives.

If execution is fully cloud-hosted:

```text
mobile <-> cloud Arden server
```

No desktop pairing needed.

If local files/shell/browser are needed:

```text
mobile <-> server <-> paired desktop host
```

Pair the desktop host.

Pairing flow:

1. Desktop shows QR or short code.
2. User confirms in authenticated mobile/web session.
3. Server issues host token.
4. Desktop stores host token locally.
5. Server can revoke host token.

Separate identities:

- user token: mobile access
- host token: desktop connection
- tool request id: per-call routing/audit

---

## 11. Future Safety Model

Before real local tools:

- workspace roots
- per-tool permissions
- per-call approval for write/execute
- timeout limits
- output size limits
- audit log
- host-side enforcement
- explicit host offline/error states

Audit row should capture:

- user id
- session id
- run id
- host id
- tool name
- args hash or redacted args
- approval status
- result status
- timestamps

---

## 12. Phases

1. Native iOS app against existing server.
2. Better mobile polish: reconnects, approval UX, active-run indicators.
3. Desktop host connection and presence.
4. `desktop.ping`.
5. Tool runtime router.
6. Typed local file tools.
7. Shell/browser tools after safety model is proven.

---

## 13. Testing

v1 tests:

- API client attaches bearer token.
- Session list decodes current `/sessions` payload.
- History decodes current `/session/history` payload.
- SSE parser handles `id`, `event`, multiline `data`, keepalive/blank frames.
- SSE reconnect resumes from latest seq.
- Approval action posts correct `/tools/result` payload.
- Cancel action posts correct `/cancel` payload.

Manual checks:

- simulator can connect to local server.
- physical phone can connect to LAN server.
- invalid API key shows clear error.
- stream resumes after app foregrounds.
