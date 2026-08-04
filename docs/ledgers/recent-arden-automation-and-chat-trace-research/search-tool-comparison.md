# Search-tool comparison

## Scope and revisions

Read-only source comparison, one subagent per harness, locally verified at:

| Harness | Revision | Agent-facing surface |
| --- | --- | --- |
| Letta Code | `bd06074da707` | dedicated `Grep` |
| Letta | `b76da9092518` | `grep_files`, `semantic_search_files`, `open_files` |
| Hermes Agent | `cb06017b1d6e` | dedicated `search_files` |
| Codex | `95637f705683` | shell `rg`; no agent-facing `search_text` |
| Claude Code leaked/reconstructed snapshot | `4b9d30f79532` | dedicated `Grep`; not evidence of current Claude Code |

## Comparative matrix

| Harness | Search/prompt contract | Execution bound | Model/context bound | Continuation | Retention |
| --- | --- | --- | --- | --- | --- |
| **Arden** | literal `file_search_text`; prompt only says search literal text | match/JSON-line count; no byte or per-line cap | payload over 50k is offloaded; recent tool results aggregate to 80k | `has_more`, but no cursor/offset | durable session blob; current rows have no expiry |
| **Letta Code** | always use `Grep`, not shell; narrow scope/limits | whole `rg` stdout buffered up to 10 MiB | Grep 10k chars; global 32k chars | modes + offset/head limit | overflow artifact; >24h cleanup only on interactive start |
| **Letta** | search only when open files are insufficient | 50 MB/file, 200 MB/call, 30s, 1,000 matches | 20 matches/page; builtin response cap 50k chars | exact `offset`; semantic default 5/max 50 | tool result persists in messages; no automatic TTL found |
| **Hermes** | use `search_files`, not shell; batch independent lookups | `limit+offset`; 500 chars/match line; timeout partials | >100k becomes 1.5k preview; 200k aggregate/turn | modes + offset + `limit_reason` | `/tmp/hermes-results`; no repository TTL found |
| **Codex** | prompt says use `rg`/`rg --files` | shell capture hard-capped at 1 MiB | requested/model policy; current default 10k tokens; history re-clamped | compose shell limits/paths; no tool paging | only bounded captured/projection output retained |
| **Claude snapshot** | always use `Grep`, not shell; Agent for multi-round search | full `rg` output buffered up to 20 MB; `--max-columns 500` | Grep 20k chars; global 50k; 200k aggregate/turn | default 250 + offset | per-session overflow; configurable cleanup, default 30 days |

## Harness details

### Letta Code

- Schema supports regex, path/glob/type, content/files/count modes, context, offset, head limit, and multiline: `/Users/escept1co/src/letta-code/src/tools/schemas/Grep.json:1-74`.
- Tool prompt mandates `Grep` instead of shell and discloses the 10k-character cap: `/Users/escept1co/src/letta-code/src/tools/descriptions/Grep.md:3-13`.
- Gemini guidance explicitly weighs repeated-turn cost, narrow scope, conservative limits, and parallel scoped searches: `/Users/escept1co/src/letta-code/src/agent/prompts/source_gemini.md:9-35`.
- Grep clamps at 10k, then all tool paths have a 32k backstop: `/Users/escept1co/src/letta-code/src/tools/impl/truncation.ts:10-39`; `/Users/escept1co/src/letta-code/src/tools/impl/tool-return-clamp.ts:1-44`.
- Overflow is secret-scrubbed and written as an artifact pointer; interactive startup removes artifacts older than 24 hours: `/Users/escept1co/src/letta-code/src/tools/impl/overflow.ts:17-60,103-142`; `/Users/escept1co/src/letta-code/src/index.ts:934-939`.
- Weakness: subprocess stdout is buffered before the 10k clamp with a 10 MiB maximum: `/Users/escept1co/src/letta-code/src/tools/impl/grep.ts:95-109`.

### Letta

- `grep_files` advertises 20-result pages, total/file summaries, and exact next offsets in its generated tool description: `/Users/escept1co/src/letta/letta/functions/function_sets/files.py:42-77`.
- Exact scan guards cover regex length, individual/aggregate file bytes, timeout, and collected matches: `/Users/escept1co/src/letta/letta/services/tool_executor/files_tool_executor.py:36-45,311-457`.
- `open_files` is a distinct controlled promotion step into persistent context, with ranged views and an LRU/open-file limit: `/Users/escept1co/src/letta/letta/functions/function_sets/files.py:10-38`; `/Users/escept1co/src/letta/letta/services/tool_executor/files_tool_executor.py:110-253`.
- System prompt says never search information already present in open files/core context and retain only relevant files: `/Users/escept1co/src/letta/letta/prompts/system_prompts/memgpt_v2_chat.py:32-35,65-70`.
- Builtin results are bounded before message persistence, but no automatic result TTL was found: `/Users/escept1co/src/letta/letta/services/tool_manager.py:1157-1199`; `/Users/escept1co/src/letta/letta/agents/letta_agent_v3.py:1868-1935`.

### Hermes Agent

- Schema combines content/file search, content/files/count modes, offset, limit, and context; it instructs the model to use this instead of shell commands: `/Users/escept1co/src/hermes-agent/tools/file_tools.py:2030-2047`.
- `rg` output is sliced by offset/limit, each match line is clipped to 500 characters, and the result carries `truncated`/`limit_reason`: `/Users/escept1co/src/hermes-agent/tools/file_operations.py:2267-2425`.
- Third consecutive identical search warns; fourth blocks, while changed pagination remains allowed: `/Users/escept1co/src/hermes-agent/tools/file_tools.py:1857-1904`.
- Oversized output becomes a preview/ref; one model turn has an aggregate result budget: `/Users/escept1co/src/hermes-agent/tools/file_tools.py:2104-2138`; `/Users/escept1co/src/hermes-agent/tools/budget_config.py:10-26`.
- Large raw outputs use `/tmp/hermes-results`; no explicit cleanup was found: `/Users/escept1co/src/hermes-agent/tools/tool_result_storage.py:1-69`.

### Codex

- The agent prompt recommends `rg` for text and `rg --files` for filenames; there is no dedicated agent grep schema: `/Users/escept1co/src/codex/codex-rs/core/gpt_5_2_prompt.md:244-252`.
- `exec_command.max_output_tokens` defaults to 10k and may be further policy-capped: `/Users/escept1co/src/codex/codex-rs/core/src/tools/handlers/shell_spec.rs:55-58`.
- Raw stdout/stderr capture is independently hard-capped at 1 MiB: `/Users/escept1co/src/codex/codex-rs/core/src/exec.rs:68-80`; `/Users/escept1co/src/codex/codex-rs/utils/pty/src/lib.rs:12-20`.
- Model output reports original token count and omitted bytes, then history clamps function output again: `/Users/escept1co/src/codex/codex-rs/core/src/tools/context.rs:408-440`; `/Users/escept1co/src/codex/codex-rs/core/src/context_manager/history.rs:123-146,344-369`.
- The TUI `@` fuzzy file search is a composer UI, not an agent search tool: `/Users/escept1co/src/codex/codex-rs/file-search/README.md:1-5`.

### Claude Code leaked/reconstructed snapshot

This repository is useful comparative source, not proof of the current Claude product.

- `Grep` schema supports modes, glob/type/context, default 250 entries, offset, and multiline. It warns that unlimited output wastes context: `/Users/escept1co/src/claude-code-leaked/src/tools/GrepTool/GrepTool.ts:35-88`.
- Prompt mandates dedicated Grep, defaults to filename mode, and delegates multi-round work to Agent: `/Users/escept1co/src/claude-code-leaked/src/tools/GrepTool/prompt.ts:7-16`.
- It limits columns to 500 but buffers raw `rg` output before head/offset, up to 20 MB: `/Users/escept1co/src/claude-code-leaked/src/tools/GrepTool/GrepTool.ts:329-475`; `/Users/escept1co/src/claude-code-leaked/src/utils/ripgrep.ts:80,345-418`.
- Grep declares 20k chars; global cap is 50k and aggregate per-turn cap is 200k: `/Users/escept1co/src/claude-code-leaked/src/tools/GrepTool/GrepTool.ts:160-187`; `/Users/escept1co/src/claude-code-leaked/src/constants/toolLimits.ts:6-49`.
- Overflow output is replaced with a stable preview/ref and cleaned under configurable retention, default 30 days: `/Users/escept1co/src/claude-code-leaked/src/utils/toolResultStorage.ts:137-198,739-895`; `/Users/escept1co/src/claude-code-leaked/src/utils/cleanup.ts:23-30,575-594`.

## Synthesis for Arden

### What Arden already gets right

- Dedicated typed file search, default cwd, glob filter, explicit match cap.
- Server-side 50k serialized-payload offload protects the next model context: `apps/server/arden/core/tool_executor.py:395-431`.
- Model requests keep only an 80k-character aggregate of the newest full tool results and stub older results: `apps/server/arden/core/model_context_budget.py:12-69`.
- Full results are content-addressed/compressed rather than left only in an ephemeral temp file.

### Confirmed gaps

1. **Producer work is unbounded by bytes.** Match-count/JSON-line limits do not constrain a minified line; `content` and `data.matches[].text` duplicate it: `apps/desktop/electron/executor-tools.cjs:521-618`.
2. **The schema lacks cheap discovery modes and real continuation.** It supports only literal content results plus `has_more`: `apps/server/arden/tools/files.py:159-166`.
3. **The prompt is underspecified.** It does not say scope first, request files/count before content, page instead of repeat, or read narrow file windows: `apps/server/arden/tools/files.py:36-39`.
4. **Offload is post-construction and implies indefinite retention.** Current persistence uses `retention_class='session'`, `expires_at=NULL`: `apps/server/arden/context/store.py:3466-3487`.

### Recommended contract

1. Stream/parse `rg`; stop when any hard producer budget is hit: results, total bytes, per-line bytes, elapsed time, or cancellation.
2. Add `output_mode = content | files_only | count`, `cursor/offset`, conservative defaults, hard maxima, and `{has_more,next_cursor,limit_reason}`.
3. Cap each snippet and avoid duplicating snippet text in both model content and structured data.
4. Keep the existing 50k per-result and 80k aggregate model-request backstops; expose their applied bounds in telemetry.
5. Do not persist unlimited raw search output by default. A partial bounded search result is valid. If full capture is explicitly requested, store an artifact with TTL/quota/promotion—not an immortal session result.
6. Prompt sequence: broad `files_only`/`count` with narrow path/glob → paged content snippets → `file_read` exact windows. Repeating the same page without an intervening write should return the cached result or a no-progress warning.

## Rejected copy-paste choices

- Do not copy post-hoc truncation alone; Letta Code and the Claude snapshot still buffer 10–20 MB first.
- Do not copy Letta's 50/200 MB scan ceilings; they do not solve giant single-line output.
- Do not rely on `/tmp` cleanup as Hermes does.
- Do not replace Arden's typed tool with Codex shell-only search; borrow Codex's independent capture/history caps.
