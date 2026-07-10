# Areas and Custodians Rebuild

Date: 2026-07-10  
Status: approved for implementation by the user on `feat/area-custodians`

## Goal

Make Areas a trustworthy durable domain boundary, then make Custodians a
bounded delegate that operates inside that boundary. The rebuild replaces the
current rename-level unification and optimistic permission model; compatibility
with buggy behavior is not a requirement.

## Areas: canonical contract

An Area is one durable user-owned domain. Every Area has one identity, name,
instructions, optional default cwd, chats, and a room. A topic page and a
Custodian are capabilities of that same entity, not separate kinds of Area.

Every active Area appears in navigation and can open a room. A room without a
page or Custodian shows explicit capability setup rather than disappearing.

### Invariants

- New IDs use `area_<12 hex>`; migrated `proj_*` IDs remain valid.
- `page_path` is either null or a normalized vault-relative Markdown path.
- A page can belong to at most one active or archived Area.
- Path resolution must prove containment inside the memory vault before read or
  write.
- Area names are case-insensitively unique among active Areas.
- Archiving is reversible and preserves session membership.
- Archived Areas do not appear in normal lists, asks, or automation runs.
- Restore returns the Area and its chats to their prior state.
- Creating, promoting, delegating, pausing, archiving, and restoring go through
  one lifecycle service which updates storage and runtime consistently.

### Capabilities

The API returns one canonical Area record containing identity, instructions,
page attachment, delegation settings, and archive state. Runtime status and
derived room data remain separate projections, keyed by `area_id`.

Page setup supports:

- creating a safe `topics/<slug>.md` page for an Area;
- attaching an existing unowned topic page;
- detaching a page only when the Custodian is disabled.

## Desktop state and UX

The desktop has one normalized Area domain. Lists, Home summaries, room detail,
and settings all reconcile into records keyed by `area_id`; the separate
`areaRecords` source of truth is removed.

Every room exposes:

- identity and instructions;
- chats filed into the Area;
- page capability state and open loops when attached;
- Custodian capability, permissions, status, and asks when delegated.

Triage remains suggestion-first. Accepting `create` creates a real Area room;
it never silently attaches a page or delegates a Custodian.

## Custodian: bounded delegate

A Custodian is the curator for exactly one Area. It owns its channel, its Area
page, its watch state, and its asks. It does not own global memory or arbitrary
external tools.

### Permission contracts

`observe` receives:

- dedicated read/patch/write tools locked to the current Area page;
- read-only recall and Area-scoped transcript tools;
- read-only web and filesystem research tools;
- no global memory mutation, session mutation, arbitrary automation mutation,
  notification, or external side-effect tools.

`act` adds an explicit allowlist of internal workflow/automation execution
tools. It does not receive the full tool registry. Consequential or external
tools retain their normal approval policy; the Custodian never globally skips
approvals.

Changing autonomy synchronizes the live automation immediately. Downgrading to
observe revokes acting tools before the API returns.

### Provisioning and lifecycle

- Delegating provisions the channel and automation immediately; no restart.
- Disabling or archiving disables the automation immediately.
- Pause is honest persisted state and disables autonomous dispatch.
- Resume schedules a prompt check without exceeding the autonomous budget.
- Renaming updates the channel/automation display name.

### Scheduling

Events are explicit domain events: Area chat activity, externally-authored Area
page changes, ask resolution/reply, and supported connector events. Explicit
user-domain events wake after debounce; future ambiguous connector events must
use a schema-backed triage decision, never keyword/regex routing.

Attention clamps self-paced heartbeat timing and sets an autonomous runs/day
cap. Every autonomous dispatch path checks the cap. Manual user runs bypass the
cap and are reported separately.

Quiet runs decay cadence. Ignored asks reduce attention without re-notifying the
same decision each run.

### Page-write provenance

Custodian page tools record the exact post-write content digest. The memory
watcher suppresses only the matching recorded self-write. Time-window
suppression is removed so real edits are never discarded merely because they
occurred near a run.

### Asks

`notify`, `question`, and `review` are durable records.

- `notify` can expire after 72 hours.
- `question` persists until answered or explicitly dismissed.
- `review` persists until approved, rejected, or withdrawn.
- Quiet or malformed later runs never retire unresolved decisions.
- Re-nominating the same decision updates one stable ask rather than generating
  a new notification.
- A reply is written to the Custodian channel with ask identity and then wakes
  it.
- Approval/rejection is stored as an explicit resolution event. The next run
  re-verifies world state before acting.
- Mechanical approval/failure asks are projections of canonical runtime state;
  they disappear when the underlying condition disappears.

### Intake and liveness

The first run can read the page and recent Area chats through explicitly scoped
tools. It records what it is watching and asks at most one calibration question.

The room shows persisted operational state: running, last checked, next check
and reason, last trigger, autonomous budget, and pause/error state. It exposes
the channel transcript as the audit trail; page-diff UI is not required in this
rebuild.

## Error handling

- Lifecycle operations fail without advertising state that was not applied.
- Runtime side effects use compensation when a storage write fails.
- Corrupt JSON state fails closed with an actionable log and an empty recoverable
  store; writes are atomic replace operations.
- Notification failures are isolated and recorded without failing the run.

## Verification

The rebuild is complete only when tests prove path containment and uniqueness,
reversible archive/restore, canonical client reconciliation, immediate
provisioning and permission revocation, cap enforcement, exact self-write
suppression, durable ask identity/resolution, correct reply routing, and
mechanical ask retirement. Full server and desktop gates must pass.

