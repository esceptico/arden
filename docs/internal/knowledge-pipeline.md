# Knowledge Pipeline Concept

This is the proposed abstraction for unifying memory, reflection, artifacts, and external sinks.

## Core Model

```text
Objects -> Processors -> Objects -> Activation
```

Everything durable is an object. Any object can become input to a processor. Processors create new objects. Activation decides whether an object is recalled, injected, published, reviewed, or ignored.

## Object Types

- `Source`: an external or internal container, such as chat, file, Slack, Gmail, Obsidian, browser page, calendar event, or tool run.
- `EvidenceRef`: a precise pointer into a source, such as message id, file range, note block, URL citation, or tool output id.
- `Fact`: an atomic remembered claim with evidence.
- `Pattern`: consolidated understanding derived from facts or other objects.
- `Procedure`: behavior or workflow guidance for the agent.
- `Artifact`: human-facing output, such as note, report, task list, brief, or proposal.
- `Reflection`: higher-level interpretation over any object set.
- `SinkReceipt`: proof that something was published or written somewhere.

## Processor Types

- `extract`: turn source/evidence into facts or candidates.
- `consolidate`: merge facts into patterns.
- `reflect`: reason over any object set and produce higher-level understanding.
- `render`: turn knowledge into a human-facing artifact.
- `publish`: send an artifact to a sink.
- `verify`: check whether an object is stale, contradicted, or still valid.

Reflection is universal. It can run over raw evidence, procedures, artifacts, prior reflections, or any mix of them.

## Activation

Storage is not usage. Activation is a separate decision:

- inject into prompt
- retrieve by search
- show in UI
- publish to Obsidian or another sink
- notify the user
- propose a task
- update behavior after approval

## Policy

Different outputs need different trust levels:

- Working notes can be temporary and automatic.
- Facts should be evidence-backed.
- Patterns should keep lineage and be regenerable.
- Procedures should require stronger approval because they change behavior.
- External publishing should require review unless explicitly automated.
- No processor should silently rewrite source objects or procedures in place.

## Current arden Mapping

- Saved sessions and outbox events are the start of the source/event layer.
- Chat extraction is an `extract` processor.
- Memory consolidation is a `consolidate` processor.
- Facts and observations are durable knowledge objects.
- Skills/directives are close to procedural memory.
- File writes and Obsidian MCP writes should become artifact sink operations, not special memory paths.

## Capability View

This abstraction is mainly useful because it supports higher-level agent capabilities:

- Self-improvement: reflect over failures, corrections, and repeated workflows, then propose procedure or skill changes.
- Continual learning: keep facts, patterns, project context, and procedures fresh without treating all chat history as memory.
- Contextual proactive action: use evidence, scope, and policy to decide when to suggest a note, task, reminder, follow-up, or automation.
- Project continuity: carry useful context across sessions without leaking unrelated global memory.
- External knowledge work: turn chat and evidence into notes, reports, briefs, or tasks in systems like Obsidian.

The system should not silently self-modify. It should produce reviewable candidates for behavior-changing outputs, especially procedures, skills, automations, and external writes.

## Research Anchors

- LangGraph separates thread memory from long-term memory stores. That supports keeping session replay separate from durable knowledge.
- Mem0 scopes memory by user, agent, app, run, and metadata. That supports making scope explicit on every durable object.
- Letta separates core memory blocks, archival memory, and recall/message history. That supports separating always-visible profile/procedure from searchable archive.
- ChatGPT Projects keep chats, files, instructions, and memory inside project boundaries. That supports a first-class project/workspace scope.
- Claude Artifacts treat reusable outputs as standalone objects beside the chat. That supports artifacts as durable objects, not transcript chunks.
- MCP separates resources, prompts, and tools. That supports treating sources, workflows, and mutating sinks as separate capabilities.

## Guiding Rule

Memory is for future behavior. Artifacts are for human reuse. Evidence is the source of truth. Processors move information between these layers.
