# Memory Notebook and Diff UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the memory database inspector with a note-first notebook that supports semantic navigation, trust inspection, revision-safe editing, and clear page/memory-effect review.

**Architecture:** `ArtifactMemoryView` remains the orchestration boundary but renders an index rail, note workspace, and optional trust inspector. Server revisions drive cached reads, drafts, conflicts, links, and history. A shared `DiffReview` owns review behavior while a lazy `RawDiffRenderer` adapter isolates `@pierre/diffs`.

**Tech Stack:** React 19, TypeScript, Bun, Vite, Electron, `@pierre/diffs`, existing arden UI primitives, Bun test runner

## Global Constraints

- This plan starts after page-edit, link-index, and event-history APIs pass their completion gate.
- Build a notebook for arden; do not clone Obsidian chrome or interaction density.
- Remove the top-level Files/Records mode toggle.
- Keep raw records and filesystem browsing as secondary diagnostics.
- Do not load full artifact bodies in list/navigation responses.
- Preserve drafts across SSE refresh and reject stale saves visibly.
- The server owns revisions and memory operations; the diff renderer is presentation only.
- No local graph in v1.
- Preserve keyboard access, screen-reader labels, selection, and reduced-motion behavior.
- Keep package-specific types and CSS inside `RawDiffRenderer.tsx`.

---

### Task 1: Revision-aware Desktop API Contract

**Files:**
- Modify: `apps/desktop/src/api/memoryArtifacts.ts`
- Create: `apps/desktop/src/features/memory/lib/notebookTypes.ts`
- Create: `apps/desktop/tests/memoryApi.test.ts`

**Interfaces:**
- Extends: `MemoryArtifact` with `revision`, `summary`, and `editable`
- Produces: `previewPageEdit`, `applyPageEdit`, `getPageHistory`, and `getPageLinks`
- Produces: UI-owned `PageEditPreview`, `PageEditEvent`, `MemoryOperation`, and `MemoryLink`

- [ ] **Step 1: Write failing API serialization tests**

```ts
test("preview sends the exact base revision and candidate", async () => {
  await previewPageEdit(config, {
    path: "topics/a.md",
    baseRevision: "sha256:base",
    content: "# A\nchanged",
  });
  expect(lastRequest()).toEqual({
    method: "POST",
    path: "/admin/memory/page-edits/preview",
    body: {
      path: "topics/a.md",
      base_revision: "sha256:base",
      content: "# A\nchanged",
      actor: "user:desktop",
    },
  });
});
```

Test snake/camel mapping for all operation kinds, questions, revisions, history pagination, source roles/timestamps, and backlink context.

- [ ] **Step 2: Run tests and confirm methods are absent**

Run from `apps/desktop`:

`bun test tests/memoryApi.test.ts`

Expected: FAIL on missing exports.

- [ ] **Step 3: Add narrow API methods and domain types**

Keep raw transport shapes private to `memoryArtifacts.ts`; components consume camel-cased domain types. Model every operation as a discriminated union:

```ts
export type MemoryOperation =
  | { kind: "ADD"; id: string; text: string; scope: MemoryScope; sources: SourceRef[] }
  | { kind: "SUPERSEDE" | "MERGE"; id: string; text: string; targetIds: string[]; sources: SourceRef[] }
  | { kind: "RETRACT"; id: string; targetIds: string[]; sources: SourceRef[] }
  | { kind: "NOOP"; id: string; reason: string }
  | { kind: "ASK"; id: string; question: string; targetIds: string[] };
```

- [ ] **Step 4: Run tests and commit**

Run: `bun test tests/memoryApi.test.ts`

Run: `bun run typecheck`

Expected: PASS.

Run: `git add apps/desktop/src/api/memoryArtifacts.ts apps/desktop/src/features/memory/lib/notebookTypes.ts apps/desktop/tests/memoryApi.test.ts && git commit -m "feat(desktop): add memory notebook API contract"`

### Task 2: Note-first Notebook Shell and Index Rail

**Files:**
- Modify: `apps/desktop/src/features/memory/components/ArtifactMemoryView.tsx`
- Create: `apps/desktop/src/features/memory/components/NotebookRail.tsx`
- Create: `apps/desktop/src/features/memory/components/MemoryNote.tsx`
- Modify: `apps/desktop/src/features/memory/components/FileDetailPane.tsx`
- Modify: `apps/desktop/src/features/memory/components/MemoryFileTree.tsx`
- Create: `apps/desktop/tests/memoryNotebook.test.tsx`
- Modify: `apps/desktop/tests/memorySimplified.test.tsx`
- Modify: `apps/desktop/tests/memoryProse.test.tsx`

**Interfaces:**
- `ArtifactMemoryView` owns selected path, rail state, inspector state, and detail cache
- `NotebookRail` consumes root/nested index descriptions plus search results
- `MemoryNote` renders content as the dominant surface

- [ ] **Step 1: Write failing notebook hierarchy tests**

Render the view with artifact metadata and assert: no Files/Records segmented toggle, index sections/descriptions are primary, machine paths are hidden, Files utility is collapsed, note title/prose precedes metadata, and loading/empty/error states retain the three-zone layout.

- [ ] **Step 2: Run tests and confirm current inspector layout**

Run: `bun test tests/memoryNotebook.test.tsx tests/memorySimplified.test.tsx tests/memoryProse.test.tsx`

Expected: FAIL because the mode toggle and folder-count tree are primary.

- [ ] **Step 3: Refactor `ArtifactMemoryView` into state orchestration**

Remove `mode: "files" | "records"`. Keep one selected page. Load list metadata once, fetch selected detail by revision, and place diagnostic record browsing behind a labeled overflow/command action.

- [ ] **Step 4: Build meaning-first `NotebookRail`**

Render root `index.md` sections and nested README descriptions. Put search/quick-switcher input first. Keep arbitrary filesystem paths under a collapsed `Files` utility. Exclude `raw/`, `.arden/`, health, and generated maintenance pages.

- [ ] **Step 5: Make the note the dominant surface**

Use the existing `Markdown` renderer and wikilink navigation. Title and prose lead; properties collapse to one quiet summary. Move path/copy, labels, revision, and generation metadata into footer/overflow. Keep timeline as a disclosure.

- [ ] **Step 6: Run tests, typecheck, and commit**

Run: `bun test tests/memoryNotebook.test.tsx tests/memorySimplified.test.tsx tests/memoryProse.test.tsx`

Run: `bun run typecheck`

Expected: PASS.

Run: `git add apps/desktop/src/features/memory/components/ArtifactMemoryView.tsx apps/desktop/src/features/memory/components/NotebookRail.tsx apps/desktop/src/features/memory/components/MemoryNote.tsx apps/desktop/src/features/memory/components/FileDetailPane.tsx apps/desktop/src/features/memory/components/MemoryFileTree.tsx apps/desktop/tests/memoryNotebook.test.tsx apps/desktop/tests/memorySimplified.test.tsx apps/desktop/tests/memoryProse.test.tsx && git commit -m "feat(desktop): make memory a note-first notebook"`

### Task 3: History, Link Preview, and Trust Inspector

**Files:**
- Create: `apps/desktop/src/features/memory/lib/navigationHistory.ts`
- Modify: `apps/desktop/src/features/memory/lib/wikiResolution.ts`
- Create: `apps/desktop/src/features/memory/lib/artifactCache.ts`
- Create: `apps/desktop/src/features/memory/components/WikiLinkPreview.tsx`
- Create: `apps/desktop/src/features/memory/components/MemoryInspector.tsx`
- Modify: `apps/desktop/src/features/memory/components/ArtifactMemoryView.tsx`
- Create: `apps/desktop/tests/memoryNavigation.test.tsx`
- Create: `apps/desktop/tests/memoryInspector.test.tsx`

**Interfaces:**
- Produces: `NavigationHistory.push/back/forward/replaceCurrent`
- Produces: revision-keyed artifact detail cache
- Inspector tabs/sections: links, evidence, scope, lifecycle, page events, pending review

- [ ] **Step 1: Write failing navigation and inspector tests**

Test link click history, `Cmd/Ctrl+[` and `Cmd/Ctrl+]`, disabled endpoints, alias resolution, 300 ms intent-delayed preview, keyboard-focus preview, cache hit by path/revision, cache invalidation on revision change, backlinks with context, exact source activation, and pending question visibility.

- [ ] **Step 2: Run tests and confirm navigation is stateless**

Run: `bun test tests/memoryNavigation.test.tsx tests/memoryInspector.test.tsx`

Expected: FAIL on missing history, previews, and inspector.

- [ ] **Step 3: Implement bounded notebook history**

Keep path plus scroll anchor per entry, cap at 100, truncate the forward branch on new navigation, and ignore consecutive duplicate locations. Restore focus/scroll after detail render.

- [ ] **Step 4: Add revision-keyed detail caching and previews**

List responses stay metadata-only. On hover intent or keyboard focus, fetch the target detail, cache by `path@revision`, and show title plus first meaningful paragraph in existing `HoverPopover`/`AnchoredPopover` primitives. Abort stale requests on target change.

- [ ] **Step 5: Build the optional trust inspector**

Use server link/history payloads. Clearly separate record scope from page placement. Show source kind, role, exact timestamp/precision, stable reference, predecessor/successor relationships, event actor/revisions, and missing/changed evidence states.

- [ ] **Step 6: Run tests, typecheck, and commit**

Run: `bun test tests/memoryNavigation.test.tsx tests/memoryInspector.test.tsx`

Run: `bun run typecheck`

Expected: PASS.

Run: `git add apps/desktop/src/features/memory/lib/navigationHistory.ts apps/desktop/src/features/memory/lib/wikiResolution.ts apps/desktop/src/features/memory/lib/artifactCache.ts apps/desktop/src/features/memory/components/WikiLinkPreview.tsx apps/desktop/src/features/memory/components/MemoryInspector.tsx apps/desktop/src/features/memory/components/ArtifactMemoryView.tsx apps/desktop/tests/memoryNavigation.test.tsx apps/desktop/tests/memoryInspector.test.tsx && git commit -m "feat(desktop): add notebook navigation and trust context"`

### Task 4: Shared Diff Contract and `@pierre/diffs` Prototype Gate

**Files:**
- Create: `apps/desktop/src/components/ui/DiffReview.tsx`
- Create: `apps/desktop/src/components/ui/RawDiffRenderer.tsx`
- Create: `apps/desktop/src/components/ui/RenderedProseDiff.tsx`
- Create: `apps/desktop/src/components/ui/diffReviewTypes.ts`
- Create: `apps/desktop/tests/diffReview.test.tsx`
- Create: `apps/desktop/tests/rawDiffRenderer.test.tsx`
- Modify: `apps/desktop/package.json`
- Modify: `apps/desktop/bun.lock`

**Interfaces:**
- Produces: `DiffReview` with rendered/raw modes and memory effects
- `RawDiffRenderer` is the only module importing `@pierre/diffs/react`
- `DiffReview` receives validated before/after content; it never derives operations

- [ ] **Step 1: Write failing renderer-independent contract tests**

```tsx
render(
  <DiffReview
    before={{ path: "topics/a.md", content: "# A\nold" }}
    after={{ path: "topics/a.md", content: "# A\nnew" }}
    operations={[askOperation]}
    decisions={{}}
    onDecision={onDecision}
  />,
);
expect(screen.getByRole("button", { name: "Apply changes" })).toBeDisabled();
expect(screen.getByRole("radio", { name: "Note only" })).not.toBeChecked();
expect(screen.getByRole("radio", { name: "Forget memory" })).not.toBeChecked();
```

Test rendered/raw toggle, split/stacked breakpoint input, collapsed unchanged hunks, wrapping, line numbers, frontmatter/whitespace display, operation labels, keyboard traversal, and reduced motion.

- [ ] **Step 2: Run tests and confirm shared review components are absent**

Run: `bun test tests/diffReview.test.tsx tests/rawDiffRenderer.test.tsx`

Expected: FAIL on missing modules.

- [ ] **Step 3: Implement `DiffReview` without a package dependency**

Define stable arden props and compose `RenderedProseDiff`, a lazy raw-renderer slot, and the memory-effects pane. Wide layout is split; narrow is stacked. Use restrained change bars and word highlights in rendered mode. Require explicit decision for every `ASK`.

- [ ] **Step 4: Install and isolate the preferred renderer**

Run: `bun add @pierre/diffs`

Implement `RawDiffRenderer` with a dynamic import from `@pierre/diffs/react`, using its two-file React diff component in controlled mode, Markdown language, wrapping, line numbers, collapsed hunks, `split`/`stacked` layout, and arden light/dark Shiki themes. Do not import worker-pool APIs.

- [ ] **Step 5: Verify the compatibility gate**

Run: `bun test tests/diffReview.test.tsx tests/rawDiffRenderer.test.tsx`

Run: `bun run typecheck`

Run: `bun run build`

Expected: PASS, with the package in a lazy production chunk rather than the startup chunk.

Manually verify in packaged Electron: light/dark Shadow DOM theme variables, split/stacked resize, wrapping/selection, keyboard and screen-reader labels, reduced motion, and a 5,000-line Markdown diff without a worker pool. Record bundle sizes before/after in the commit message body.

If any gate cannot be fixed inside the adapter, remove the dependency and implement the same `RawDiffRenderer` contract with arden primitives; do not leak a partial package API into memory features.

- [ ] **Step 6: Commit the shared diff surface**

Run: `git add apps/desktop/src/components/ui/DiffReview.tsx apps/desktop/src/components/ui/RawDiffRenderer.tsx apps/desktop/src/components/ui/RenderedProseDiff.tsx apps/desktop/src/components/ui/diffReviewTypes.ts apps/desktop/tests/diffReview.test.tsx apps/desktop/tests/rawDiffRenderer.test.tsx apps/desktop/package.json apps/desktop/bun.lock && git commit -m "feat(desktop): add shared change review surface"`

### Task 5: Draft-preserving Editor and Save Review

**Files:**
- Create: `apps/desktop/src/features/memory/components/MemoryEditor.tsx`
- Create: `apps/desktop/src/features/memory/components/MemoryEditReview.tsx`
- Create: `apps/desktop/src/features/memory/lib/draftStore.ts`
- Modify: `apps/desktop/src/features/memory/components/MemoryNote.tsx`
- Modify: `apps/desktop/src/features/memory/components/ArtifactMemoryView.tsx`
- Create: `apps/desktop/tests/memoryEditing.test.tsx`

**Interfaces:**
- `Cmd/Ctrl+E` toggles note/editor for editable pages
- `Cmd/Ctrl+S` requests preview and opens `MemoryEditReview`
- Draft key is `path + baseRevision`; SSE never silently replaces it

- [ ] **Step 1: Write failing edit-flow tests**

Test keyboard entry/save, read-only engine pages, exact preview payload, unresolved `ASK`, explicit decision, apply result, analysis-unavailable pending save, stale 409, SSE while clean, SSE while dirty, external conflict review, cancel preserving draft, and successful save clearing only the matching draft.

- [ ] **Step 2: Run tests and confirm the UI is read-only**

Run: `bun test tests/memoryEditing.test.tsx tests/diffReview.test.tsx`

Expected: FAIL on missing editor/review.

- [ ] **Step 3: Add a plain Markdown editor**

Use the existing `Textarea` and note type ramp; do not add CodeMirror. Track base content/revision separately from draft content. Provide visible dirty state, Save, Cancel, and keyboard shortcuts without stealing shortcuts from focused controls.

- [ ] **Step 4: Open non-mutating review on save**

Pass server preview data directly to `DiffReview`: rendered prose first, raw Markdown toggle, then memory effects. Apply stays disabled until every `ASK` has `note_only` or `forget_memory`; neither is default.

- [ ] **Step 5: Handle revision and SSE conflicts**

When the selected page revision changes and no draft exists, refresh detail. With a draft, retain it and show three-way conflict review using base/current/draft. A 409 uses the same path. Never replace textarea content automatically.

- [ ] **Step 6: Support post-save external review**

For external events marked `review_required`, show `Resolve` rather than `Apply`; page content is already current, so only memory-effect decisions are submitted.

- [ ] **Step 7: Run tests, typecheck, and commit**

Run: `bun test tests/memoryEditing.test.tsx tests/diffReview.test.tsx tests/memoryNotebook.test.tsx`

Run: `bun run typecheck`

Expected: PASS.

Run: `git add apps/desktop/src/features/memory/components/MemoryEditor.tsx apps/desktop/src/features/memory/components/MemoryEditReview.tsx apps/desktop/src/features/memory/lib/draftStore.ts apps/desktop/src/features/memory/components/MemoryNote.tsx apps/desktop/src/features/memory/components/ArtifactMemoryView.tsx apps/desktop/tests/memoryEditing.test.tsx && git commit -m "feat(desktop): review memory page edits before save"`

### Task 6: Lifecycle Actions, Approval Reuse, and Product Polish

**Files:**
- Modify: `apps/desktop/src/features/memory/components/MemoryInspector.tsx`
- Modify: `apps/desktop/src/features/chat/components/ApprovalReviewModal.tsx`
- Modify: `apps/desktop/src/components/ui/DiffReview.tsx`
- Modify: `apps/desktop/src/components/ui/README.md`
- Modify: `apps/desktop/tests/memoryInspector.test.tsx`
- Modify: `apps/desktop/tests/approvalDenyReason.test.tsx`
- Create: `apps/desktop/tests/approvalDiffReview.test.tsx`

**Interfaces:**
- Inspector actions: correct, retract/forget, dispute, archive where server permits
- Approval modal reuses raw `DiffReview` mode without memory effects
- Existing deny-reason and approval behavior remains unchanged

- [ ] **Step 1: Write failing action and approval-reuse tests**

Test action labels/confirmation, exact target IDs, source navigation, paginated history, lesson kind, real scope filter, denial reason retention, raw file diff, and no memory-effects region for tool approvals.

- [ ] **Step 2: Run tests**

Run: `bun test tests/memoryInspector.test.tsx tests/approvalDenyReason.test.tsx tests/approvalDiffReview.test.tsx`

Expected: FAIL because actions and shared approval rendering are not wired.

- [ ] **Step 3: Add explicit lifecycle actions**

Correction opens page editing or a targeted operation preview; retract/forget and dispute require confirmation and show affected evidence/relationships. Do not hide semantic effects behind generic Delete.

- [ ] **Step 4: Migrate approval rendering to `DiffReview`**

Replace only the modal's hand-colored unified-line renderer. Preserve approval state, deny reason, keyboard behavior, and API calls. Configure raw mode as the only mode and omit memory operations.

- [ ] **Step 5: Document the shared component contract**

Add usage, accessibility, lazy-loading, renderer isolation, and “server operations are authoritative” rules to the UI README.

- [ ] **Step 6: Run full desktop verification**

Run: `bun test tests/memory*.test.tsx tests/diffReview.test.tsx tests/rawDiffRenderer.test.tsx tests/approvalDenyReason.test.tsx tests/approvalDiffReview.test.tsx`

Run: `bun run typecheck`

Run: `bun run lint`

Run: `bun run build`

Expected: PASS.

- [ ] **Step 7: Commit action and approval integration**

Run: `git add apps/desktop/src/features/memory/components/MemoryInspector.tsx apps/desktop/src/features/chat/components/ApprovalReviewModal.tsx apps/desktop/src/components/ui/DiffReview.tsx apps/desktop/src/components/ui/README.md apps/desktop/tests/memoryInspector.test.tsx apps/desktop/tests/approvalDenyReason.test.tsx apps/desktop/tests/approvalDiffReview.test.tsx && git commit -m "feat(desktop): reuse diff review across trusted changes"`

## Completion Gate

- [ ] Exercise notebook read, link preview, back/forward, inspector, edit, `ASK`, conflict, external resolve, and exact-source navigation against a real local server.
- [ ] Verify keyboard-only operation and screen-reader names for rail, preview, editor, diff modes, hunk expansion, decisions, and Apply/Resolve.
- [ ] Verify narrow and wide layouts, light/dark themes, reduced motion, long wrapped Markdown, and 200+ artifact navigation.
- [ ] Confirm startup bundle excludes `@pierre/diffs` and opening the first diff loads it once.
- [ ] Confirm SSE refresh never drops a dirty draft.
- [ ] Capture final notebook and diff screenshots for review; do not add a local graph or extra Obsidian-like chrome.
