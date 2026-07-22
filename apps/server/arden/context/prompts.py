from arden.core.prompts import env

SUMMARIZE_PROMPT_TEMPLATE = env.from_string("""You are continuing an active personal assistant session.
Create a state handoff for seamless continuation. Target length: ~{{ budget }} words.

## Required Sections:

### Active Objective
What is the user trying to accomplish RIGHT NOW?

### Open Loops
- Pending follow-ups with who/what/when
- Unanswered questions
- Promised actions
Format: "- [item] (source: note:path, email:id, raw_item:id, or unverified)"

### Next Actions
Ordered checklist of what should happen next (3-8 items)

### Key Facts
ONLY facts that affect next actions. For each fact:
- Include source pointer if available: (source: note:path, email:id, raw_item:id)
- If no source, mark as: (unverified)

### Pointers
List of identifiers that may need retrieval:
- note paths referenced
- raw_item IDs for full content
- email IDs

## Rules:
- If a fact cannot be traced to a source, mark (unverified)
- Do NOT restate general preferences unless relevant to current objective
- Focus on CONTINUING work, not documenting history
- Be terse. State, not story.""")

MERGE_SUMMARY_PROMPT_TEMPLATE = env.from_string("""You are continuing an active personal assistant session.
An existing state handoff exists. Merge new conversation into it. Target length: ~{{ budget }} words.

## Instructions:
Update each section by merging new information into the existing summary:
- **Active Objective**: Replace if the goal has changed, keep if unchanged.
- **Open Loops**: Add new loops, remove resolved ones, keep unresolved ones.
- **Next Actions**: Rewrite to reflect current state — drop completed items, add new ones.
- **Key Facts**: Add new facts, keep existing facts still relevant to next actions, drop stale ones.
- **Pointers**: Update with current set of identifiers needing retrieval.

## Rules:
- Preserve detail from the existing summary that is still relevant — do not re-summarize it lossy.
- If a fact cannot be traced to a source, mark (unverified).
- Focus on CONTINUING work, not documenting history.
- Be terse. State, not story.""")

RESEARCH_AGENT_COMPACTION_CONTEXT = """## Research Agent Handoff
This handoff is for a spawned research agent, not the top-level chat.
Preserve:
- research task and current answer shape
- research_outline sections, covered sections, uncovered gaps
- source-backed facts with source pointers or short quotes
- contradictions and dead ends with what was tried
- tool result pointers, file paths, URLs, message IDs, and query strings needed for retrieval

Rules:
- Do not turn weak evidence into a firm fact.
- Keep unresolved gaps explicit.
- Prefer compact evidence bullets over narrative."""
