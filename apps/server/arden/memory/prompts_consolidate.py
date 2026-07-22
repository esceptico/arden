"""Structured-output schemas + rubric for record CONSOLIDATE/LINT.

Adapted from the old claim-pipeline `prompts_consolidate.py` to the flat
RecordStore: no subject/provenance/corroboration/feedback/validity/edge fields —
a record is just {id, text, kind, last_confirmed_at, pinned}. The judge proposes
only three ops over a recalled neighborhood; it never invents facts, never raises
trust, and never touches a pinned record.
"""

from pydantic import BaseModel, Field

# Prompt-cacheable prefix. The bar to act is HIGH, NOOP is the safe default, and
# a pinned record is never merged or invalidated away.
LINT_RUBRIC = """You are CONSOLIDATING a small slice of a personal memory of atomic RECORDS.

Consolidation is not deduplication. Your job is to integrate records into a SMALL,
CLEAN body of coherent knowledge: fold together what is really one thing, name it
by what it IS, and clear out what's stale or never belonged.

You are shown a NEIGHBORHOOD of records (id, text, kind, last_confirmed_at, pinned).
Propose ONLY these operations:

- merge: two or more records that are observations, restatements, refinements, or
  accumulating evidence of the SAME underlying fact / preference / behavior / pattern.
  Integrate them into ONE coherent, self-contained statement (`merged_text`) and
  collapse the rest into it. List EVERY member id. Set `kind` to what the integrated
  statement IS by function:
    'directive' — a standing instruction that should steer assistant behavior,
    'fact'      — a stable standing fact about the user or their world,
    'source'    — a receipt/evidence pointer, not default recall knowledge.
- retype: a SINGLE record whose kind is wrong — a record that actually states a
  standing directive, stable fact, or source receipt. Give its id and the correct kind.
  This is how mis-typed records become real, typed knowledge.
- invalidate: retire a record that no longer earns a place in durable memory. Three
  cases: it is STALE (no longer true); it is CONTRADICTED by a newer record shown here
  (set contradicted_by); or it is low-worth NOISE that never belonged — transient
  session/tool narration, one-off debugging notes, ephemeral status updates, raw
  experiment/metric telemetry, completed one-off task scaffolding, or an engineering
  build-spec that belongs in an issue tracker rather than personal memory. Give the id.
- drop_orphan: a record that carries no standalone value and whose evidence is gone
  (no provenance source) — a stray fragment.
- noop: when records are unrelated, or you are unsure.

A neighborhood may consist entirely of `lesson` records — the agent's distilled
playbook of working rules. Treat those like any dedup pass: merge near-duplicate or
overlapping lessons into the ONE sharpest, most actionable second-person sentence,
and invalidate a lesson a newer one clearly obsoletes. Never retype a lesson (omit
`kind` on lesson merges) — an agent-inferred rule must not become a durable fact.

Worthiness bar (same one the writer uses): durable knowledge about the USER — identity,
preferences, goals, working style — standing behaviour rules, and substantive ongoing-
project facts all STAY. A record being merely old, or about engineering work, is NOT
reason enough to retire it. Retire only what is genuinely transient or never-durable.
When unsure whether something is durable, KEEP it (noop) — a wrong deletion is worse
than a kept record.

Hard rules:
- INTEGRATE aggressively within one topic, but NEVER merge records that carry
  DISTINCT facts — even on the same subject. A false merge that blends two different
  facts poisons the memory. When two records say genuinely different things, keep both.
- The integrated `merged_text` must be fully supported by its members. NEVER invent
  facts, dates, or numbers, and never lose a distinct fact a member carried.
- NEVER merge, invalidate, drop, or retype a record whose "pinned" is true.
- A merge needs at least two member ids. Use only ids that appear in the neighborhood.

Output strictly as the requested JSON."""


class MergeOp(BaseModel):
    op: str = Field(default="merge", description="literal 'merge'")
    member_ids: list[str] = Field(description="ids of the records that are one thing, to integrate")
    merged_text: str | None = Field(default=None, description="the integrated, self-contained wording for the survivor")
    kind: str | None = Field(
        default=None, description="function-type of the integrated statement: directive|fact|source"
    )
    reason: str = ""


class RetypeOp(BaseModel):
    op: str = Field(default="retype", description="literal 'retype'")
    record_id: str
    kind: str = Field(description="correct function-type: directive|fact|source")
    reason: str = ""


class InvalidateOp(BaseModel):
    op: str = Field(default="invalidate", description="literal 'invalidate'")
    record_id: str
    contradicted_by: str | None = Field(
        default=None,
        description="id of the newer record that contradicts/supersedes this one, if any",
    )
    reason: str = ""


class DropOrphanOp(BaseModel):
    op: str = Field(default="drop_orphan", description="literal 'drop_orphan'")
    record_id: str
    reason: str = ""


class LintOps(BaseModel):
    merges: list[MergeOp] = Field(default_factory=list)
    retypes: list[RetypeOp] = Field(default_factory=list)
    invalidations: list[InvalidateOp] = Field(default_factory=list)
    orphans: list[DropOrphanOp] = Field(default_factory=list)


LABEL_HYGIENE_RUBRIC = """You are curating the LABEL vocabulary of a personal memory.

Labels are short open-vocabulary names the curator attaches to records. Each line is
`label: count [kind]` where kind is `entity` or `meta`. You do two jobs.

(1) RENAMES — fold a label into another ONLY when they are clearly the SAME label
spelled differently: case variants, singular/plural, trivial rephrasings
("dex" / "Dex memory"). Prefer the more popular spelling; ties → shorter, properly-cased.
NEVER fold genuinely different things ("health" vs "medication"). When unsure, leave both.

(2) KIND — classify each label that is currently mis-kinded. Emit a reclass op only to
CHANGE a label's kind.
- entity = a concrete named SUBJECT worth its own topic page: a person, project, product,
  company, place, or named topic the user actually cares about
  (e.g. "Dex", "arden", "Health", "O-1A Visa", "Obsidian", "Memory design").
- meta = a process/status/category tag that is NOT a subject and must NOT get a topic page
  (e.g. "Bug", "Server", "Tools", "Approval required", "Aside", "Audit", "Application",
  "UI/UX", "Read-only", "Research request").
Rule of thumb: if "What do we know about <label>?" reads as a sensible question about a
real thing, it is an entity; if it reads as a category bucket, it is meta. When unsure → meta.

Output strictly as the requested JSON. Empty lists are normal answers."""


class LabelRenameOp(BaseModel):
    old: str = Field(description="the variant spelling to fold away")
    new: str = Field(description="the canonical label name to fold it into")
    reason: str = ""


class LabelKindOp(BaseModel):
    label: str = Field(description="an existing label whose kind should change")
    kind: str = Field(description="'entity' or 'meta'")
    reason: str = ""


class LabelOps(BaseModel):
    renames: list[LabelRenameOp] = Field(default_factory=list)
    reclass: list[LabelKindOp] = Field(default_factory=list)
