---
name: investigate
description: Answer a question about a target by fanning out parallel readers over derived angles, then synthesizing a cited answer. Use to understand a subsystem, research a question, or trace how/where something works. Run via the workflow tool by name.
kind: workflow
source: builtin
---

# investigate — parallel readers, cited synthesis

A workflow preset. Run it with the `workflow` tool:

```
workflow(name="investigate", args={"target": "apps/server/arden", "question": "How does the event/SSE pipeline work end to end?", "breadth": "normal"})
```

It derives N distinct investigation angles, sends a reader at each in parallel
(each citing sources, not dumping files), then synthesizes one direct, cited
answer to your question.

**args**
- `target` (str, default ".") — the dir/repo/area to examine (can also be a topic for web research).
- `question` (str) — what you want answered.
- `breadth` ("focused" | "normal" | "wide", default "normal") — number of parallel readers.
- `reader_model` / `synth_model` (str, optional) — model overrides; omit to inherit the run's model.
