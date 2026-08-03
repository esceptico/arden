---
name: propose-skill
description: Use this skill when the user wants to capture the current conversation as a reusable skill. Analyze the session and call skill_create to propose one — the user reviews the full body in the approval card before it's written to disk.
---

# Propose Skill

The user wants to capture the work in this conversation as a reusable skill — a procedure the agent can re-run on demand via `/<skill-name>`. Your job: analyze the session, then call `skill_create` once with the proposed parameters. The user will see your proposal in the approval card and accept or reject.

## When a skill (vs. an automation)

- **Skill**: a *procedure* the user invokes by name. No schedule. Captures know-how (steps, gotchas, references).
- **Automation**: a scheduled run with a fixed prompt. If the work has a clear cadence, use `/propose-automation` instead.

## What to look at

- The actual steps you took in this session (tool calls, files touched, decisions made)
- What was tricky or non-obvious — the value of a skill is in the gotchas
- Whether the work has a clear sequence of steps worth codifying
- A skill name that's lowercase, hyphenated, and reads like an imperative (`refactor-component`, `audit-secrets`)

## What to call

Call `skill_create` exactly once with:

- **`name`**: lowercase, hyphens only, must start with a letter, max 48 chars. Example: `"refactor-component"`.
- **`description`**: one line — what the skill does AND when to use it. This is what the agent reads to decide whether to activate the skill, so include keywords that match user intent.
- **`body`**: the full SKILL.md body, after the frontmatter (the system adds frontmatter from `name` + `description`). Markdown. Start with a `# Title` heading. Include step-by-step instructions, gotchas, examples. Write it for the *next* agent invocation — that agent has no memory of this conversation.

## Before calling — say what and why

Before the tool call, write 2-3 sentences in plain prose explaining:
1. **What** skill you're proposing (the gist, not the full body — that's in the args).
2. **Why** — cite specific evidence from THIS session: which steps, what tools, why this is worth codifying. Be concrete, not generic.

The user reads your prose first, then sees the structured args in the approval card.

## Rules

- **One proposal only.** Pick the highest-value one if there are multiple candidates.
- **Grounded.** Your prose rationale MUST reference what actually happened in this session — specific tools, files, decisions.
- **`body` is for the next invocation.** That agent has no memory of this conversation. Be explicit about steps, expected inputs/outputs, and known gotchas.
- **No frontmatter in `body`.** The system adds `name:` and `description:` from the other args.

## If there's nothing reusable

If the conversation is a one-off Q&A or exploration with no procedure worth codifying, say so plainly in 1-2 sentences and DO NOT call `skill_create`. Don't manufacture a proposal.
