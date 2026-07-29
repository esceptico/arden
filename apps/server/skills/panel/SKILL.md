---
name: panel
description: Decide between approaches — generate N diverse proposals from different lenses, score each against criteria with independent judges, then synthesize a decisive recommendation. Use for design choices, tradeoff calls, "which approach should we take". Run via the workflow tool by name.
kind: workflow
source: builtin
---

# panel — diverge, judge, synthesize

A workflow preset. Run it with the `workflow` tool:

```
workflow(name="panel", args={"question": "How should we shard the event bus per tenant?", "n": 3, "criteria": ["correctness", "simplicity", "blast radius"]})
```

It proposes N approaches from deliberately different lenses (simplest, most
robust, unconventional, lowest-risk), scores each on your criteria with
independent judges, then recommends one — grafting the best ideas from the
runners-up. Beats one-shot reasoning when the solution space is wide.

**args**
- `question` (str) — the decision to make.
- `n` (int, 1-5, default 3) — number of proposals.
- `criteria` (list[str], optional) — scoring axes; defaults to correctness/simplicity/risk.
- `gen_model` / `synth_model` (str, optional) — model overrides; omit to inherit the run's model.
