---
name: propose-automation
description: Use this skill when the user wants to turn the current conversation into a scheduled automation. Analyze the session and call create_automation to propose one — the user reviews the full prompt + schedule in the approval card before it's saved.
---

# Propose Automation

The user wants to capture the work in this conversation as a reusable scheduled automation. Your job: analyze the session, then call `create_automation` once with the proposed parameters. The user will see your proposal in the approval card and accept or reject.

## What to look at

- The user's intent across the conversation (what were they trying to accomplish?)
- The actual tools you ran and what they returned
- Any recurring or time-based intent ("every morning", "weekly", "after deploys")
- The single most reusable shape of the task — not a literal replay

## What to call

Call `create_automation` exactly once with:

- **`name`**: a short imperative phrase, max 60 chars. Example: `"Morning standup digest"`.
- **`prompt`**: the full instructions the automation should run on each tick. Stand-alone — the future agent has no memory of this conversation. Be explicit about what to do, which sources to check, and what output to produce.
- **`description`** (optional): a concise display summary, at most 220 characters. Omit it to generate one from the prompt.
- **`trigger_type`**: `"time"` for scheduled/recurring work, `"event"` for a clear event hook (e.g. calendar approaching), or `"message"` to react to new Slack messages. If the user wants to **catch / watch / triage Slack messages**, the trigger is `"message"` — never model a Slack watcher as a recurring time scan.
- **For schedule-style time triggers** (specific clock time):
  - `at`: `"HH:MM"` (24h)
  - `days`: `"daily"`, `"weekdays"`, or comma-separated like `"mon,wed,fri"`
- **For interval-style time triggers** (every N units):
  - `every`: combinations of `d`/`h`/`m`, e.g. `"30m"`, `"2h"`, `"1d"`, `"2d12h"`, `"1h30m"`
  - optional `days` to limit which weekdays it runs on
  - optional `start` / `end` (`"HH:MM"`) to restrict to a daily window
- **For Slack message triggers** (`trigger_type="message"` — reacting to new messages):
  - `channels`: list of Slack channel names to watch. **Required.** If the user didn't name the channel(s), ASK which channel(s) — do NOT fall back to a scheduled/interval scan.
  - `from_user` (optional): only react to messages from this sender.
  - `contains` (optional): keyword filters, matched any-of and case-insensitive, e.g. `["bug", "error", "broken"]`.
  - Detection is near-real-time (~1 min) and reads via the bot token. A time/interval "scan Slack" is the wrong shape and may not even work without a user token.
- **`tool_scope`** (optional): list only the write/action tools the standalone prompt needs. Read-only tools are always available; `auto_approve` does not grant tools by itself.
- **`auto_approve`** (default false): set true ONLY if the automation must run autonomously. It skips per-run approvals for tools already in `tool_scope`. For a Slack watcher acting on untrusted messages, also set `from_user` as a sender gate.

## Before calling — say what and why

Before the tool call, write 2-3 sentences in plain prose explaining:
1. **What** automation you're proposing (the gist, not the full prompt — that's in the args).
2. **Why** — cite specific evidence from THIS session: which tools you ran, which files, what pattern made you suggest this. Be concrete, not generic.

The user reads your prose first, then sees the structured args in the approval card.

## Rules

- **One proposal only.** Pick the highest-value one if there are multiple candidates.
- **Grounded.** Your prose rationale MUST reference what actually happened in this session — specific tool calls, results, decisions. No generic "users often want to…" boilerplate.
- **`prompt` is what runs without you.** It must stand alone. Be explicit.
- **Schedule must be specific.** Don't propose `"every hour"` if the work is clearly daily; don't propose `"daily"` if it's clearly a one-off.
- **Slack watchers use message triggers, not scans.** "Catch / watch / triage Slack messages" → `trigger_type="message"` with `channels` (+ optional `from_user` / `contains`). Never substitute a recurring time scan. If no channel was named, ask which channel(s) before proposing — don't guess a cadence.

## If there's nothing reusable

If the conversation isn't a good automation candidate (one-off Q&A, exploratory chat with no shape), say so plainly in 1-2 sentences and DO NOT call `create_automation`. Don't manufacture a proposal.
