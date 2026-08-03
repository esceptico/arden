---
name: loop
description: Use when the user types /loop or asks to repeat/poll/babysit something on a cadence inside THIS chat. Examples — "/loop 5m check the deploy", "watch CI until green", "every 30 minutes summarize my inbox". Sets up a recurring prompt that fires into this session via the loop_create tool.
---

# /loop — schedule a recurring prompt in this chat

The user wants a repeating task scoped to THIS conversation. Parse their input and call `loop_create` once. It attaches the current active chat automatically; it cannot target another chat.

## Parsing (in priority order)

1. **Leading token interval**: if the first whitespace-separated token is a positive `d`/`h`/`m` duration (e.g. `5m`, `2h`, `1h30m`, `2d12h`), that's the interval; the remainder is the prompt.
2. **Trailing "every" clause**: otherwise, if the input ends with `every <N><unit>` or `every <N> <unit-word>` (e.g. `every 20m`, `every 5 minutes`, `every 2 hours`), extract that as the interval and strip it from the prompt. Only match when what follows "every" is a time expression — `check every PR` has no interval.
3. **Default**: if neither matches, default the interval to `10m` and use the whole input as the prompt.

Examples:
- `5m /babysit-prs` → `every=5m`, `prompt=/babysit-prs`
- `check the deploy every 20m` → `every=20m`, `prompt=check the deploy`
- `run tests every 5 minutes` → `every=5m`, `prompt=run tests`
- `watch CI until green` → `every=10m`, `prompt=watch CI until green`
- `check every PR` → `every=10m`, `prompt=check every PR` ("every" isn't followed by time)
- empty input → don't call anything; ask the user what to loop on

Intervals support days, hours, and minutes only; the minimum is `1m`. If the user asks for seconds or another shorter interval, explain that `1m` is the minimum and use it only when the requested task is otherwise unambiguous.

## Self-paced loops

When the user says "until X" or describes a stop condition (CI green, deploy done, file appears), this is a **self-paced** loop. Include the wake-up/done logic inside the prompt:

> "Check if GitHub Actions run for branch `feature/foo` is green. If yes, call `loop_done` with the reason. If still running, call `loop_schedule_wakeup` with `delay_seconds=120`. If failed, summarise the failure briefly."

Self-paced is the default for any "until X" framing. Fixed intervals are right for "every N just do Y forever" tasks.

## Calling loop_create

Call `loop_create` once with:

- `prompt`: the parsed prompt verbatim. Slash commands pass through unchanged. This must be standalone — future iterations don't see THIS conversation.
- `every`: interval string from parsing.
- `max_iterations` (optional): if the user implied a bounded duration ("for the next hour", "10 times").
- `max_age_days` (optional): if the user implied a deadline ("for the next week", "today only").
- `stop_when` (optional): natural-language predicate when there's a clear stop condition.

`loop_create` always attaches this current chat. Do not create a separate channel or use `automation_create` when the user asked for a loop here.

## After loop_create succeeds — confirm and stop

The scheduler will fire the first iteration as soon as this turn ends — it renders as a fresh chat turn so the user can see the agent's work clearly instead of buried inside this turn's tool-activity collapse.

**Do not execute the prompt inline.** Confirm briefly in one line ("Loop set · every X · first fire in a few seconds.") and stop. The user will see the iteration appear as a new turn momentarily.

## Rules

- **Single call.** One `loop_create` per user request.
- **Prompt is standalone.** Future iterations have no memory of this chat.
- **Default to self-paced** when there's any "until X" framing. Use fixed-interval only for clear `every N forever` tasks.
- **Don't second-guess the cadence the user gave.** If they said `5m`, use `5m`. Suggest a different cadence only when their interval is clearly broken (e.g. `1s` for a CI check that takes minutes).
- **One-line confirmation.** After `loop_create` succeeds, end with one short line like "Loop set · every X · first fire in a few seconds." Don't restate the prompt — they wrote it.

## When loop is the wrong tool

- If the user wants a standalone task that runs in its own session (cron-like, no chat continuation): use `automation_create` instead.
- If the user wants something done once now, not recurring: just do it, don't loop.
