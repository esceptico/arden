# Memory Notebook Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the separate raw editor with an in-place WYSIWYG/source note editor and simplify the surrounding notebook UI.

**Architecture:** A focused `MemoryEditor` owns Milkdown and source-mode rendering while `ArtifactMemoryView` retains draft, review, conflict, and save-event state. Presentation-only changes simplify `MemoryNote`, `NotebookRail`, and `MemoryInspector` without changing server contracts.

**Tech Stack:** React 19, TypeScript, Milkdown Crepe, Tailwind CSS, Bun tests.

## Global Constraints

- Preserve the existing preview/apply/revision-conflict pipeline.
- Preserve frontmatter and wikilink Markdown.
- No worktree, subagents, or commits without explicit user approval.

---

### Task 1: In-place Markdown editor

**Files:**
- Modify: `apps/desktop/package.json`
- Modify: `apps/desktop/src/features/memory/components/MemoryEditor.tsx`
- Modify: `apps/desktop/src/features/memory/components/ArtifactMemoryView.tsx`
- Test: `apps/desktop/tests/memoryEditing.test.tsx`

- [ ] Add failing coverage for focus-independent `Cmd/Ctrl+E`, WYSIWYG/source toggling, exact preview payload, and review entry.
- [ ] Add Milkdown and implement the smallest editor wrapper that reports Markdown changes.
- [ ] Keep the note layout in place while editing and retain source mode as an alternate view.
- [ ] Run `bun test apps/desktop/tests/memoryEditing.test.tsx`.

### Task 2: Quiet notebook presentation

**Files:**
- Modify: `apps/desktop/src/features/memory/components/MemoryNote.tsx`
- Modify: `apps/desktop/src/features/memory/components/shared.tsx`
- Modify: `apps/desktop/src/features/memory/components/NotebookRail.tsx`
- Modify: `apps/desktop/src/features/memory/components/MemoryInspector.tsx`
- Test: `apps/desktop/tests/memoryNotebook.test.tsx`
- Test: `apps/desktop/tests/memoryInspector.test.tsx`

- [ ] Add failing assertions for compact properties, placeholder-description suppression, and concise link context.
- [ ] Remove nested metadata chrome and reduce rail/inspector density.
- [ ] Run the three focused memory presentation tests.

### Task 3: Verification

**Files:**
- Modify only files required by failures found during verification.

- [ ] Run all desktop memory tests.
- [ ] Run desktop typecheck and lint on changed files.
- [ ] Run the desktop production build.
- [ ] Inspect the final diff and report remaining limitations without committing.
