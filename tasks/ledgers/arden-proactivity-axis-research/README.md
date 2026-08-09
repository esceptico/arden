<!-- development-ledger:v2 -->

# Arden proactivity axis research

## Status

| Field | Value |
| --- | --- |
| State | researching |
| Active phase | research |
| Created | 2026-08-09T03:18:23+04:00 |
| Last updated | 2026-08-09T03:18:23+04:00 |
| Last consolidated | not yet |
| Codebase branch | main |
| Codebase revision | ed974d85f34c5d2475b5bfbc7086bfe85d8e32e9 |
| Sources checked through | code: ed974d85f34c5d2475b5bfbc7086bfe85d8e32e9; web: not checked |

## Original task — verbatim

look, i need something stronger than your conclusion here
do a proper research on the current product, on getenergy startup moat, on asks from users, on chatgpt pulse maybe (it's closed and i need to understand why), on recent openai "personal agi" job postings, on reddit (+ adhd subreddits about tooling and stuff, because if our tool good for adhd people, it's excellent for all other people)

spawn some subagents (sonnet would be fine) and do a proper research

also /development-ledger to not lose the info

---
Prior context in the same session (amendments/background, not the verbatim ask):
- User shared https://claude.ai/share/decb8162-473e-402d-a1b7-aa0cc12f12ef — a conversation about "improving agency like Gabriel Petersson", which concluded that Arden currently has no extreme axis, floated RSI as the strongest candidate, and reframed proactivity as an interruption-cost problem rather than a prediction-accuracy problem.
- User then said: "i don't quite like the current areas in the arden, it's not quite aligned imo / and it's a lot of notifications with weird texts"

## Amendments — verbatim

None.

## Current synthesis

All six threads returned (T1–T6, findings F-01…F-31 in [research.md](research.md)).

**1. Proactivity-as-information is a graveyard, and the cause of death is consistent.** Pulse retired
2026-06-17 after ~9 months, never reaching Plus or Free (F-09). Google Now folded into an ad feed;
Arc's AI features peaked at 0.4–12% adoption; Rewind/Limitless shut down; Recall survived only after a
privacy backlash (F-12). The recurring failure is *no deliverable* — output that costs reading and
yields nothing, becoming "one more inbox" (F-11), judged by a harsher bar precisely because it was
unsolicited (F-28).

**2. Both survivors converge on the same replacement shape: user-directed and deliverable-producing.**
OpenAI replaced Pulse with Scheduled Tasks, explicitly citing "personalised, action-oriented, and
steerable by the user" (F-10). Energy ships named assistants that plan → execute → return finished
work with an audit trail (F-15). Two independent parties, same conclusion.

**3. The design I was about to propose is table stakes, not an axis.** "Agent drafts, human authorizes"
is a formalised, named pattern already shipped in Claude's Gmail integration (F-25). Silent prep /
explicit execution is convergent industry practice — correct, but it makes Arden *normal*, not extreme.

**4. Two of the four candidate axes are weaker than assumed.** Local-first: willingness to pay for AI
privacy is 3% and *falling* while concern rises (F-26) — a values statement, not a market, though
77% of ADHD tool users rank privacy critical (F-24), so it survives as a cohort differentiator.
Inspectability: real but narrow (technical users, F-27), and Energy already advertises an audit trail
(F-15).

**5. The genuinely unoccupied position is not a behaviour — it is an instrument.** Every product in the
graveyard died *without a published measurement of its own interruption cost*. Pulse was killed on a
qualitative read plus a search-interest proxy; no retention data was ever published (F-09/F-11). Arc
had telemetry and used it only to euthanise features (F-12). Meanwhile the frontier lab names the hard
problem as trust calibration — when to act vs. ask (F-14) — and staffs it as an *evals* problem (F-13).
Nobody has shipped the curve. The synthesis: **measured proactivity** — an agent that demonstrably
becomes less interruptive over time while completing more — subsumes the RSI candidate (self-improvement
becomes the thing you measure rather than announce), matches the demand evidence (54% want quiet
scheduled check-ins over push, F-19; reminders do not move completion rates at all, F-20), and is
structurally hard for a funded consumer team, which cannot publish unflattering numbers.

**6. Arden's specific defect is narrower than "notification factory".** The ask pipeline is deliberately
starved and there is no OS push at all (F-05). The real defect is a **forced-emission clause** — at ≥2
quiet runs with open work, the custodian *must* propose a review ask (F-03) — fired by elapsed time
rather than new information, compounded by an incentive that scores page-writing runs as "quiet" (F-04).
Arden also already runs a working silent-prep layer for facts/wiki (F-06), so the pattern needs
extending, not inventing. Nothing anywhere counts interruptions.

## Decisions

- None adopted. Axis selection is the user's call; this ledger is research-only.

## Open questions

- **User-blocking**: which surface does the user actually experience as "a lot of notifications"?
  No OS push exists (F-05), so it is the Home focus deck, automation toasts, or approval prompts —
  different culprits, different fixes.
- Is the graveyard evidence that proactivity fails *for a fixable reason*, or that it simply fails?
  F-28 supports the pessimistic reading. Instrumenting first is what makes this falsifiable in weeks.
- Latency as an axis was never researched — no evidence either way in this pass.
- Would the F-03/F-04 fixes alone resolve the felt problem, independent of any axis choice?

## Next action

User decides: (a) fix areas now (F-03/F-04 are defects regardless of axis), (b) design the
instrumentation layer that makes "measured proactivity" falsifiable, or (c) neither — challenge the
synthesis. No implementation without an explicit request.

## Details

- [Research](research.md)
- [Implementation](implementation.md)
- [Verification](verification.md)
