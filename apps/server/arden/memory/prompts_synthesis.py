"""LLM synthesis prompts for memory pages.

Five prose-page synthesizers that replace the bullet-dump projection. Each
receives a list of atomic RECORDS (id, text, kind, labels, pinned,
last_confirmed_at) and synthesizes a grounded markdown page FROM them.

Modeled on dex's context-wiki: durable, texture-rich, no slop. Input is atomic
records with stable ids, so provenance in the MODEL's output is an inline
`(record:XXXXXXXX)` tag carrying the 8-char id prefix of the supporting record.
The model may cite ONLY the ids it was given, and may assert ONLY what those
records support — that contract is what the provenance check verifies. The
STORED page never shows ids: synthesize._humanize_cites translates each cite
group into a readable `(from chat)` / `(from gmail)` / `(inferred)` source tag
and keeps the verified id list in the page's raw sidecar (prose_cites).

  - PROFILE_SYSTEM     -> me.md
  - DOSSIER_SYSTEM     -> topics/<subject>.md  (one page per subject)
  - ACTIVE_WORK_SYSTEM -> active-work.md
  - DAILY_SYSTEM       -> daily/<date>.md  (per-day activity log)

Record kinds (arden.memory.models.Kind): directive | fact | source | changelog |
lesson. Scopes are visibility metadata, not shown to the model.
"""

from __future__ import annotations

import re

from arden.memory.models import TRUST_DEFAULT, Record, source_trust

# Sentinel strings the synthesizers emit when their content gate fails. The
# caller matches these exactly and skips the file rather than persisting a stub.
INSUFFICIENT_DOSSIER = "_Insufficient records to synthesize a brief._"
NO_ACTIVE_WORK = "_No active threads in the recent window._"
NO_DAILY = "_No notable activity this day._"

# A citation is only counted inside a well-formed parenthetical group —
# `(record:3f2a1b9c)` or `(record:3f2a1b9c, record:7d1e0a44)` — the exact form the
# prompt mandates. This ignores an id-shaped token a record's own text happens to
# contain (e.g. "reverted commit record:65c334bf"), which would otherwise scan as
# a fabricated cite and wrongly reject a valid page. The `(?![0-9a-fA-F])` boundary
# also rejects an over-long (hallucinated 32-char) id instead of silently keeping
# its first 8 chars.
_CITE_ID_RE = re.compile(r"record:([0-9a-fA-F]{8})(?![0-9a-fA-F])")
_CITATION_GROUP_RE = re.compile(
    r"\(\s*record:[0-9a-fA-F]{8}(?![0-9a-fA-F])(?:\s*,\s*record:[0-9a-fA-F]{8}(?![0-9a-fA-F]))*\s*\)"
)


# ---------------------------------------------------------------------------
# Shared discipline blocks (composed into each system prompt)
# ---------------------------------------------------------------------------

_GROUNDING = """\
<grounding>
You are given a fixed set of atomic RECORDS. Each line begins with an 8-char id
in brackets, e.g. `[3f2a1b9c]`. That id is your only currency.

- Every non-obvious claim ends with an inline provenance tag naming the
  record(s) that support it: `(record:3f2a1b9c)`. Two or more supports combine:
  `(record:3f2a1b9c, record:7d1e0a44)`.
- Use ONLY ids that appear in the records below. Never invent, guess, or
  reformat an id. If you cannot point to a real id, do not make the claim.
- Assert ONLY what the records support. Do not import outside knowledge, fill
  gaps with plausible detail, or infer facts the records don't state. If the
  records don't say it, it isn't on the page.
- A `directive` record is a standing instruction from the user — it always wins
  over an inferred pattern. A `fact` is a stable truth about the user or their
  world. A `source` is a receipt/pointer; cite it for the fact it evidences, not
  as a fact of its own. Ignore `changelog` records — they are housekeeping.
- A `pinned` record is user-blessed; never omit or contradict it.
- An `integration-sourced` record was machine-extracted from an external system
  (calendar/email/slack), not stated by the user. Cite it, but phrase the claim
  tentatively ("appears to", "as of last sync") — it is evidence, not a confirmed
  user fact.
- When two records conflict, prefer the one confirmed more recently and the
  higher-authority kind (directive > fact > source). State the current truth;
  do not narrate the contradiction.
</grounding>"""

_NO_SLOP = """\
<output_rules>
Write tight, concrete prose. No preamble, no filler, no hedging on cited facts.

Banned:
- "It's worth noting that...", "Interestingly...", "Notably...", "Overall..."
- "Based on the available records...", "From what I can see...", "It appears..."
- "I've synthesized...", "Here is the...", "Let me...", restating the task
- Generic padding: "is involved in various initiatives", "plays a key role"
- Any claim with no `(record:...)` tag that isn't trivially obvious (section
  headers, the user's own name)

Output ONLY the markdown page. No code fences around it, no commentary before
or after. Short paragraphs over walls of text. If a section has nothing
record-backed to say, omit the section entirely — an absent section beats a
padded one.

Preserve the user's own phrasing when a record quotes them directly; don't
paraphrase a stated preference into blandness.
</output_rules>"""

_SYNTHESIS_QUALITY = """\
<synthesis_quality>
SYNTHESIZE, don't enumerate. The failure mode you are replacing is a bullet
dump: "The user prefers tea. The user imports Apple Health into Obsidian." Your
job is the opposite — fold related records into coherent prose that reads like
an operational briefing.

- Group records by what they're ABOUT, then write a paragraph per theme that
  integrates them. Several records about the same thing become one sentence with
  several citations, not several sentences.
- Keep it durable and timeless-but-current: write what is TRUE NOW, not a diary
  of what happened. "Owns the memory subsystem (record:...)" not "On Tuesday
  added a record about memory."
- Texture is the value. Prefer the specific ("reviews PRs before merge, no
  auto-commit") over the generic ("cares about quality").
- Don't repeat the same fact in two sections. State it once, in the section
  where it belongs.
- A single uncorroborated inference is weak evidence — phrase it tentatively or
  leave it out. A directive or pinned record is strong — state it plainly.
- BRIEFING REGISTER, not essay: short sentences (never chain more than two
  clauses), bold lead-ins on bullets (`- **Kevin Gu** — engineering lead; …`),
  bullets over paragraphs whenever the content is list-shaped, and a small
  markdown table when facts are enumerable (accounts, contacts, PR status).
  The reader scans this page; write for the scan.
</synthesis_quality>"""


# ---------------------------------------------------------------------------
# Record formatting for the user message
# ---------------------------------------------------------------------------


def format_record_line(record: Record, labels: list[str] | None = None) -> str:
    """One record -> one line: `[8charid] (kind, pinned, confirmed YYYY-MM-DD) text  «labels»`.

    The 8-char prefix shown here is EXACTLY what the model must echo as
    `(record:XXXXXXXX)`. last_confirmed_at is truncated to the date.
    """
    meta = [record.kind]
    if record.pinned:
        meta.append("pinned")
    if record.source_ref and source_trust(record.source_ref.kind) <= TRUST_DEFAULT:
        meta.append("integration-sourced")
    if record.last_confirmed_at:
        meta.append(f"confirmed {record.last_confirmed_at[:10]}")
    line = f"[{record.id[:8]}] ({', '.join(meta)}) {record.text.strip()}"
    if labels:
        line += f"  «{', '.join(labels)}»"
    return line


def format_records_block(
    records: list[Record],
    labels_by_id: dict[str, list[str]] | None = None,
) -> str:
    labels_by_id = labels_by_id or {}
    return "\n".join(format_record_line(r, labels_by_id.get(r.id)) for r in records)


def cited_ids(text: str) -> set[str]:
    """The set of 8-char id prefixes cited inside `(record:…)` citation groups —
    not every `record:HEX` substring anywhere in the prose."""
    ids: set[str] = set()
    for group in _CITATION_GROUP_RE.finditer(text):
        ids.update(m.group(1).lower() for m in _CITE_ID_RE.finditer(group.group(0)))
    return ids


# ===========================================================================
# 1) PROFILE  ->  me.md
# ===========================================================================

PROFILE_SYSTEM = "\n\n".join(
    [
        """\
You write `me.md` — the user's self-page, the root of a personal memory wiki.
Every other page is grounded in this one. It is an always-current briefing on
who the user is, how they work, and what they care about — not a timeline.

You are given the records that define the user: their standing `directive`s
(behavior rules they've set), their user-scoped `fact`s (identity, role,
preferences, background), and any `pinned` records (user-blessed, never drop).
Synthesize them into the page below. Write in the second person — address the
user directly ("You work on…", "You prefer…"), never third-person narrator.

Output this structure, omitting any section with no record-backed content:

```
# <user's name, or "Profile" if no record names them>

## Identity
Role, company, contact handles, location/timezone, self-description — whatever
the records establish. Tight bullets with bold lead-ins (`- **Role:** …`,
`- **Location:** …`); cite each non-obvious claim.

## What you work on
The user's actual focus areas and ownership, synthesized into a few paragraphs
or a short list of substantive threads. Group related facts; cite them.

## Preferences
Standing behavior rules and stated preferences — how you want the assistant to
act, how you like to work. `directive` records anchor this section and are
stated as rules, not observations. Cite each.

## Key relationships / tools
The people you work with most closely and the tools/systems you rely on, each
with a one-line note on the relationship. Only those the records actually
establish; cite each.
```

You are also given `known_subjects`: the exact titles of topic pages that exist
elsewhere in the wiki. In `Key relationships / tools`, link ONLY those exact
titles with `[[Title]]`. If a person/tool/system is record-backed but not in
`known_subjects`, write it as plain text. Never imply a separate page exists for
a subject that has none.

Keep the whole page tight — this is a briefing, not a biography. If two records
say the same thing, merge them into one cited statement. Lead each section with
its strongest, most durable facts.""",
        _GROUNDING,
        _SYNTHESIS_QUALITY,
        _NO_SLOP,
    ]
)


def profile_user_message(
    records: list[Record],
    labels_by_id: dict[str, list[str]] | None = None,
    known_subjects: list[str] | None = None,
) -> str:
    known = "\n".join(f"- {s}" for s in (known_subjects or [])) or "(none)"
    return (
        "Synthesize `me.md` from the records below. These are every directive, "
        "user-scoped fact, and pinned record on file about the user.\n\n"
        "<records>\n"
        f"{format_records_block(records, labels_by_id)}\n"
        "</records>\n\n"
        "<known_subjects>\n"
        f"{known}\n"
        "</known_subjects>"
    )


# ===========================================================================
# 2) DOSSIER_SYSTEM  ->  topics/<subject>.md  (one page per subject)
# ===========================================================================

DOSSIER_SYSTEM = "\n\n".join(
    [
        """\
You write a single topic page for ONE subject (a person, project, product,
company, place, or named topic) in a personal memory wiki. The page is a durable
operational briefing on that subject, written from the user's perspective: who
or what this is to the user, what's known, and what's unresolved.

You are given the subject's title, the records tagged with that subject, and a
list of OTHER known subject titles you may link to. Output this structure:

```
# <subject title>

## What we know
Synthesized prose — NOT a list of the records. Fold the records into a coherent
briefing: what this subject is, the user's relationship to it, its current
state, the substantive details that matter. Cite every non-obvious claim with
`(record:XXXXXXXX)`. Group related facts into the same sentence or paragraph.

## Open loops
ONLY if records describe something unresolved — an open question, a pending
decision, a blocker, a waiting-on. Omit this section entirely if nothing is
open. One cited bullet per loop.

## Related
ONLY subjects that appear in the known-subjects list provided AND are
meaningfully connected per the records. One bullet each:
`- [[OtherSubject]] — one-line relationship`. Use the exact title from the
known-subjects list inside the `[[...]]`. Omit the section if there are no
record-backed links. Never link a subject that isn't in the provided list.
```

HARD GATE — apply before writing anything:
Count the distinct, meaningful facts the records establish about this subject. A
restatement of the same fact counts once. Routine narration, source receipts
with no standalone content, and changelog noise do not count. If there are fewer
than 3 meaningful facts, output EXACTLY this and nothing else:

"""
        + INSUFFICIENT_DOSSIER
        + """

Do not output a title, sections, or any other text when the gate fails. A thin
stub pollutes the wiki; refusing to write it is the correct outcome.""",
        _GROUNDING,
        _SYNTHESIS_QUALITY,
        _NO_SLOP,
    ]
)


def dossier_user_message(
    title: str,
    records: list[Record],
    known_subjects: list[str],
    labels_by_id: dict[str, list[str]] | None = None,
) -> str:
    known = "\n".join(f"- {s}" for s in known_subjects) or "(none)"
    return (
        f"Subject: {title}\n\n"
        "<records>\n"
        f"{format_records_block(records, labels_by_id)}\n"
        "</records>\n\n"
        "<known_subjects>\n"
        f"{known}\n"
        "</known_subjects>\n\n"
        f'Write the topic page for "{title}". Apply the 3-fact gate first.'
    )


# ===========================================================================
# 3) ACTIVE-WORK  ->  active-work.md
# ===========================================================================

ACTIVE_WORK_SYSTEM = "\n\n".join(
    [
        """\
You write `active-work.md` — a single page capturing what the user is working on
RIGHT NOW. It is the briefing an agent reads to pick up the user's current
threads cold.

You are given two pools of records: records confirmed or updated in the recent
window (roughly the last 7 days), and project-scoped records. Together these
describe live work. Synthesize them into one page:

```
# Active work

<intro: one sentence framing the current focus, if the records support it>

## <Thread name>
A paragraph per running thread: what it is, where it stands, what's in progress.
Cite every claim. Use the recency of records to judge what's still live.

## <Another thread>
...

## Open next-steps & blockers
The unresolved items across threads — pending decisions, blockers, things
waiting on someone. One cited bullet each. Omit if nothing is open.
```

Framing rules:
- Timeless-but-CURRENT: write the present state of each thread ("Building the
  per-chat model picker behind a feature flag (record:...)"), not a dated log of
  what happened. The reader wants the standing situation, not history.
- Group records into coherent threads by what they're about. A thread needs
  real substance — don't promote a single stray record to its own heading; fold
  minor items into a related thread or the next-steps list.
- A `directive` or `pinned` record about current priorities outranks an inferred
  one. If a record says a thread is deprioritized or done, reflect that — don't
  list stale work as active.
- Cite every claim with `(record:XXXXXXXX)`.

If the records contain nothing that reads as recent, live work, output EXACTLY:

"""
        + NO_ACTIVE_WORK
        + """

and nothing else.""",
        _GROUNDING,
        _SYNTHESIS_QUALITY,
        _NO_SLOP,
    ]
)


def active_work_user_message(
    recent_records: list[Record],
    project_records: list[Record],
    labels_by_id: dict[str, list[str]] | None = None,
) -> str:
    # De-dupe: a record can be both recent and project-scoped; show it once.
    seen: set[str] = set()
    merged: list[Record] = []
    for r in [*recent_records, *project_records]:
        if r.id not in seen:
            seen.add(r.id)
            merged.append(r)
    return (
        "Synthesize `active-work.md` from the records below — these are records "
        "confirmed/updated in the recent window plus project-scoped records.\n\n"
        "<records>\n"
        f"{format_records_block(merged, labels_by_id)}\n"
        "</records>"
    )


# ===========================================================================
# 5) DAILY  ->  daily/<YYYY-MM-DD>.md (a dated activity log)
# ===========================================================================

DAILY_SYSTEM = "\n\n".join(
    [
        """\
You write one day's page in a personal memory wiki: `daily/<date>.md`, a concise
log of what the user actually did, decided, or learned on that date. Unlike the
timeless pages, this one IS dated — it captures that day's activity so the user
can later recall "what was I doing then".

You are given the date and the records that entered memory on that date. Merge
them into a tight narrative — combine related records into a single line, drop
trivia, lead with what mattered. Output:

```
# <date>

- One merged event per meaningful thread that day, past tense, citing the
  record(s): `Shipped the entity-promotion fix and reconciled 19 pages to 7
  (record:XXXXXXXX).` Two or three closely-related records become ONE line.
```

Rules:
- This is a LOG of that day, so past tense and event-shaped ("Decided…",
  "Shipped…", "Met with…", "Learned…") — not the timeless present the other
  pages use.
- Aggressively merge. A day is a handful of lines, not one per record. If five
  records describe one work session, that's one line.
- Cite every line with `(record:XXXXXXXX)`. Skip pure housekeeping.

If the records describe nothing worth logging, output EXACTLY:

"""
        + NO_DAILY
        + """

and nothing else.""",
        _GROUNDING,
        _NO_SLOP,
    ]
)


def daily_user_message(
    day: str,
    records: list[Record],
    labels_by_id: dict[str, list[str]] | None = None,
) -> str:
    return (
        f"Date: {day}\n\n"
        "<records>\n"
        f"{format_records_block(records, labels_by_id)}\n"
        "</records>\n\n"
        f"Write the log for {day}. Merge aggressively."
    )
