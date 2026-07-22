---
name: audit
description: Find -> adversarially verify -> rank issues over a target (a diff, dir, file, or glob). Use for code review, a scoped bug hunt, a security or perf pass. Run via the workflow tool by name.
kind: workflow
source: builtin
---

# audit — find, verify, rank

A workflow preset. Run it with the `workflow` tool:

```
workflow(name="audit", args={"target": "apps/server/arden/server", "dimensions": ["bugs", "security"], "depth": "normal"})
```

Parallel finders surface issues along each dimension, every fresh finding is
adversarially verified by independent skeptics, and only the survivors are
ranked into a report. Loops until a round finds nothing new (capped by `depth`).

**args**
- `target` (str, default ".") — what to examine: a dir, file, glob, or "the diff".
- `dimensions` (list[str], optional) — e.g. `["bugs", "security", "performance"]`; defaults to a correctness/security/perf/edge-cases set.
- `depth` ("quick" | "normal" | "deep", default "normal") — scales skeptics-per-finding and max rounds.
- `finder_model` / `synth_model` (str, optional) — override the model for finders / the final synthesis; omit to inherit the run's model.
