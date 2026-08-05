import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import { ApiError, type AppConfig } from "@/api/core";
import { applyPageEdit, previewPageEdit } from "@/api/memoryArtifacts";
import type { DiffReviewDecision, DiffReviewOperation } from "@/components/ui/diffReviewTypes";
import type { MemoryConflict } from "@/features/memory/components/MemoryEditReview";
import type { MemoryFrontmatter } from "@/features/memory/components/MemoryProperties";
import { clearDraft, clearDraftIfMatches, getDraft, setDraft } from "@/features/memory/lib/draftStore";
import { serializeFrontmatter, splitFrontmatter } from "@/features/memory/lib/format";
import type {
  MemoryArtifactDetail,
  MemoryOperation,
  PageEditPreview,
} from "@/features/memory/lib/notebookTypes";

export interface EditingSession {
  path: string;
  title: string;
  baseRevision: string;
  baseContent: string;
  draftContent: string;
}

type ReviewState =
  | { kind: "preview"; generation: number; snapshot: EditSnapshot; preview: PageEditPreview }
  | { kind: "conflict"; generation: number; snapshot: EditSnapshot; conflict: MemoryConflict };

interface EditSnapshot {
  requestGeneration: number;
  path: string;
  baseRevision: string;
  baseContent: string;
  candidateContent: string;
}

interface ReviewPresentation {
  path: string;
  baseContent: string;
  draftContent: string;
  operations: DiffReviewOperation[];
  analysisPending: boolean;
}

interface UseMemoryEditingOptions {
  config: AppConfig;
  detail: MemoryArtifactDetail | null;
  visibleDetail: MemoryArtifactDetail | null;
  selectedPath: string | null;
  layoutRef: RefObject<HTMLDivElement | null>;
  mutationPendingRef: RefObject<boolean>;
  onSaved: (path: string, revision: string) => void;
  onRebased: (path: string, revision: string) => void;
}

function editingMatchesSnapshot(editing: EditingSession | null, snapshot: EditSnapshot): boolean {
  return editing?.path === snapshot.path
    && editing.baseRevision === snapshot.baseRevision
    && editing.baseContent === snapshot.baseContent
    && editing.draftContent === snapshot.candidateContent;
}

function snapshotEditing(editing: EditingSession, requestGeneration: number): EditSnapshot {
  return {
    requestGeneration,
    path: editing.path,
    baseRevision: editing.baseRevision,
    baseContent: editing.baseContent,
    candidateContent: editing.draftContent,
  };
}

function conflictFromDrift(
  draft: EditingSession,
  detail: MemoryArtifactDetail,
  requestGeneration: number,
  generation: number,
): ReviewState {
  return {
    kind: "conflict",
    generation,
    snapshot: snapshotEditing(draft, requestGeneration),
    conflict: {
      currentRevision: detail.revision,
      currentContent: detail.editableContent ?? detail.content,
    },
  };
}

function revisionConflict(reason: unknown): MemoryConflict | null {
  if (!(reason instanceof ApiError) || reason.status !== 409 || !reason.data || typeof reason.data !== "object") return null;
  const detail = (reason.data as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return null;
  const value = detail as { error?: unknown; current_revision?: unknown; current_content?: unknown };
  if (value.error !== "page_revision_conflict" || typeof value.current_revision !== "string" || typeof value.current_content !== "string") return null;
  return { currentRevision: value.current_revision, currentContent: value.current_content };
}

function toDiffOperation(operation: MemoryOperation): DiffReviewOperation {
  switch (operation.kind) {
    case "ADD":
      return { kind: "ADD", id: operation.id, text: operation.text, memoryKind: operation.memoryKind, scope: operation.scope };
    case "SUPERSEDE":
    case "MERGE":
      return { kind: operation.kind, id: operation.id, text: operation.text, memoryKind: operation.memoryKind, scope: operation.scope, targetIds: operation.targetIds };
    case "RETRACT":
      return { kind: "RETRACT", id: operation.id, targetIds: operation.targetIds };
    case "NOOP":
      return { kind: "NOOP", id: operation.id, reason: operation.reason };
    case "ASK":
      return { kind: "ASK", id: operation.id, question: operation.question, targetIds: operation.targetIds };
  }
}

const INTERACTIVE_SELECTOR = [
  "a[href]",
  "button",
  "input",
  "textarea",
  "select",
  "summary",
  "[contenteditable]:not([contenteditable='false'])",
  "[role]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function isInteractiveShortcutTarget(element: Element | null): boolean {
  return element instanceof HTMLElement && element.closest(INTERACTIVE_SELECTOR) != null;
}

export function useMemoryEditing({
  config,
  detail,
  visibleDetail,
  selectedPath,
  layoutRef,
  mutationPendingRef,
  onSaved,
  onRebased,
}: UseMemoryEditingOptions) {
  const [editing, setEditing] = useState<EditingSession | null>(null);
  const [review, setReview] = useState<ReviewState | null>(null);
  const [reviewClosing, setReviewClosing] = useState(false);
  const [decisions, setDecisions] = useState<Record<string, DiffReviewDecision>>({});
  const [editPending, setEditPending] = useState(false);
  const [reviewPending, setReviewPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const editingRef = useRef<EditingSession | null>(null);
  const reviewRef = useRef<ReviewState | null>(null);
  const requestGeneration = useRef(0);
  const previewController = useRef<AbortController | null>(null);
  const reviewExitAction = useRef<(() => void) | null>(null);
  const reviewGeneration = useRef(0);
  const applyGeneration = useRef(0);
  const restoreNoteFocus = useRef(false);
  const mountedRef = useRef(true);

  editingRef.current = editing;
  reviewRef.current = review;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestGeneration.current += 1;
      applyGeneration.current += 1;
      previewController.current?.abort();
    };
  }, []);

  const closeEditor = useCallback(() => {
    requestGeneration.current += 1;
    previewController.current?.abort();
    setEditPending(false);
    setEditing(null);
  }, []);

  const beginEditing = useCallback(() => {
    if (!visibleDetail || visibleDetail.path !== selectedPath || !visibleDetail.editable || visibleDetail.editableContent == null) return;
    const baseContent = visibleDetail.editableContent;
    requestGeneration.current += 1;
    setEditing({
      path: visibleDetail.path,
      title: visibleDetail.title,
      baseRevision: visibleDetail.revision,
      baseContent,
      draftContent: getDraft(visibleDetail.path, visibleDetail.revision) ?? baseContent,
    });
    setReview(null);
    setDecisions({});
    setError(null);
  }, [selectedPath, visibleDetail]);

  const changeDraft = useCallback((draftContent: string) => {
    const current = editingRef.current;
    if (!current) return;
    requestGeneration.current += 1;
    previewController.current?.abort();
    setEditPending(false);
    if (draftContent === current.baseContent) clearDraft(current.path, current.baseRevision);
    else setDraft(current.path, current.baseRevision, draftContent);
    setEditing({ ...current, draftContent });
  }, []);

  useEffect(() => {
    if (!editing || !detail || detail.path !== editing.path || detail.revision === editing.baseRevision) return;
    if (reviewPending) return;
    const currentContent = detail.editableContent ?? detail.content;
    if (editing.draftContent === editing.baseContent) {
      clearDraft(editing.path, editing.baseRevision);
      setEditing({
        path: detail.path,
        title: detail.title,
        baseRevision: detail.revision,
        baseContent: currentContent,
        draftContent: currentContent,
      });
      return;
    }
    setReview((current) => current?.kind === "conflict" && current.conflict.currentRevision === detail.revision
      ? current
      : conflictFromDrift(editing, detail, requestGeneration.current, ++reviewGeneration.current));
  }, [detail, editing, reviewPending]);

  const previewSnapshot = useCallback(async (snapshot: EditSnapshot, requireCurrentDraft: boolean) => {
    previewController.current?.abort();
    const controller = new AbortController();
    previewController.current = controller;
    setEditPending(true);
    setError(null);
    try {
      const preview = await previewPageEdit(config, {
        path: snapshot.path,
        baseRevision: snapshot.baseRevision,
        content: snapshot.candidateContent,
        actor: "user:desktop",
      }, { signal: controller.signal });
      if (
        controller.signal.aborted
        || requestGeneration.current !== snapshot.requestGeneration
        || (requireCurrentDraft && !editingMatchesSnapshot(editingRef.current, snapshot))
      ) return;
      setDecisions({});
      setReview({ kind: "preview", generation: ++reviewGeneration.current, snapshot, preview });
    } catch (reason) {
      if (
        controller.signal.aborted
        || !mountedRef.current
        || requestGeneration.current !== snapshot.requestGeneration
        || (requireCurrentDraft && !editingMatchesSnapshot(editingRef.current, snapshot))
      ) return;
      const conflict = revisionConflict(reason);
      if (conflict) setReview({ kind: "conflict", generation: ++reviewGeneration.current, snapshot, conflict });
      else setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (!controller.signal.aborted && requestGeneration.current === snapshot.requestGeneration) setEditPending(false);
    }
  }, [config]);

  const saveFrontmatter = useCallback((next: MemoryFrontmatter) => {
    if (!visibleDetail || visibleDetail.path !== selectedPath || visibleDetail.editableContent == null || mutationPendingRef.current) return;
    const { body } = splitFrontmatter(visibleDetail.editableContent);
    const candidateContent = serializeFrontmatter(next) + body;
    if (candidateContent === visibleDetail.editableContent) return;
    const snapshot: EditSnapshot = {
      requestGeneration: ++requestGeneration.current,
      path: visibleDetail.path,
      baseRevision: visibleDetail.revision,
      baseContent: visibleDetail.editableContent,
      candidateContent,
    };
    void previewSnapshot(snapshot, false);
  }, [mutationPendingRef, previewSnapshot, selectedPath, visibleDetail]);

  const requestEditPreview = useCallback(() => {
    const current = editingRef.current;
    if (!current || current.draftContent === current.baseContent || editPending) return;
    const snapshot = snapshotEditing(current, ++requestGeneration.current);
    void previewSnapshot(snapshot, true);
  }, [editPending, previewSnapshot]);

  const decisionsForServer = useCallback((operations: readonly MemoryOperation[], questions: readonly { id: string; operationIndex: number }[]) => {
    const result: Record<string, DiffReviewDecision> = {};
    for (const question of questions) {
      const operation = operations[question.operationIndex];
      const decision = operation ? decisions[operation.id] : undefined;
      if (decision) result[question.id] = decision;
    }
    return result;
  }, [decisions]);

  const setDecision = useCallback((operationId: string, decision: DiffReviewDecision) => {
    setDecisions((current) => ({ ...current, [operationId]: decision }));
  }, []);

  /** Keep the blocking sheet mounted through its exit animation. */
  const requestReviewExit = useCallback((afterExit: () => void) => {
    if (!reviewRef.current || reviewExitAction.current) return;
    reviewExitAction.current = afterExit;
    setReviewClosing(true);
  }, []);

  const finishReviewExit = useCallback(() => {
    const afterExit = reviewExitAction.current;
    reviewExitAction.current = null;
    setReviewClosing(false);
    afterExit?.();
  }, []);

  const completeLocalSave = useCallback((revision: string, snapshot: EditSnapshot, activeReviewGeneration: number) => {
    const finish = () => {
      clearDraftIfMatches(snapshot.path, snapshot.baseRevision, snapshot.candidateContent);
      if (!mountedRef.current || reviewRef.current?.generation !== activeReviewGeneration) return;
      if (editingMatchesSnapshot(editingRef.current, snapshot)) setEditing(null);
      setReview((current) => current?.generation === activeReviewGeneration ? null : current);
      setDecisions({});
      setError(null);
      restoreNoteFocus.current = true;
      onSaved(snapshot.path, revision);
    };
    if (!mountedRef.current) {
      finish();
      return;
    }
    requestReviewExit(finish);
  }, [onSaved, requestReviewExit]);

  const returnFromReview = useCallback(() => {
    if (reviewPending) return;
    requestReviewExit(() => {
      setReview(null);
      setDecisions({});
      setError(null);
    });
  }, [requestReviewExit, reviewPending]);

  const rebaseConflict = useCallback(() => {
    if (!editing || review?.kind !== "conflict") return;
    const { currentRevision, currentContent } = review.conflict;
    const { snapshot } = review;
    requestReviewExit(() => {
      setDraft(snapshot.path, currentRevision, snapshot.candidateContent);
      setEditing({
        ...editing,
        path: snapshot.path,
        baseRevision: currentRevision,
        baseContent: currentContent,
        draftContent: snapshot.candidateContent,
      });
      onRebased(editing.path, currentRevision);
      setReview(null);
      setDecisions({});
      setError(null);
    });
  }, [editing, onRebased, requestReviewExit, review]);

  const applyReview = useCallback(async () => {
    const activeReview = reviewRef.current;
    if (!activeReview || activeReview.kind === "conflict" || mutationPendingRef.current) return;
    const transactionGeneration = ++applyGeneration.current;
    const activeReviewGeneration = activeReview.generation;
    const submittedDecisions = decisionsForServer(activeReview.preview.operations, activeReview.preview.questions);
    mutationPendingRef.current = true;
    setReviewPending(true);
    setError(null);
    try {
      const result = await applyPageEdit(config, {
        previewId: activeReview.preview.id,
        decisions: submittedDecisions,
        savePending: activeReview.preview.analysisPending,
      });
      if (applyGeneration.current !== transactionGeneration) return;
      completeLocalSave(result.revision, activeReview.snapshot, activeReviewGeneration);
    } catch (reason) {
      if (
        !mountedRef.current
        || applyGeneration.current !== transactionGeneration
        || reviewRef.current?.generation !== activeReviewGeneration
      ) return;
      const conflict = revisionConflict(reason);
      if (conflict) {
        setReview({
          kind: "conflict",
          generation: ++reviewGeneration.current,
          snapshot: activeReview.snapshot,
          conflict,
        });
      } else {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (applyGeneration.current === transactionGeneration) {
        mutationPendingRef.current = false;
        if (mountedRef.current) setReviewPending(false);
      }
    }
  }, [completeLocalSave, config, decisionsForServer, mutationPendingRef]);

  useEffect(() => {
    if (mutationPendingRef.current || !editing || selectedPath === editing.path) return;
    setEditing(null);
    setReview(null);
    setDecisions({});
    setError(null);
  }, [editing, mutationPendingRef, selectedPath]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !editingRef.current || reviewRef.current) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      closeEditor();
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [closeEditor]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (mutationPendingRef.current) return;
      if ((!event.metaKey && !event.ctrlKey) || event.altKey || event.shiftKey) return;
      const key = event.key.toLowerCase();
      if (key !== "e" && key !== "s") return;
      const target = event.target instanceof Element ? event.target : null;
      const focused = document.activeElement;
      const editorTextarea = [target, focused].some((element) =>
        element instanceof HTMLTextAreaElement && element.closest("[data-memory-editor]") != null,
      );
      const editButton = [target, focused].some((element) =>
        element instanceof HTMLElement && element.closest('button[aria-label="Edit memory note"]') != null,
      );
      if (!editorTextarea && !editButton && [target, focused].some(isInteractiveShortcutTarget)) return;
      if (key === "e") {
        if (!editing && (!visibleDetail?.editable || visibleDetail.editableContent == null)) return;
        event.preventDefault();
        if (editing) closeEditor();
        else beginEditing();
        return;
      }
      if (!editing || review) return;
      event.preventDefault();
      requestEditPreview();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [beginEditing, closeEditor, editing, mutationPendingRef, requestEditPreview, review, visibleDetail]);

  useEffect(() => {
    if (!restoreNoteFocus.current || editing || review || !visibleDetail) return;
    const tryFocus = () => {
      const article = Array.from(layoutRef.current?.querySelectorAll<HTMLElement>("[data-memory-note-path]") ?? [])
        .filter((candidate) => candidate.dataset.memoryNotePath === visibleDetail.path)
        .at(-1);
      if (!article) return;
      if (document.activeElement !== document.body && document.activeElement !== article && !restoreNoteFocus.current) return;
      article.focus({ preventScroll: true });
      if (document.activeElement === article) restoreNoteFocus.current = false;
    };
    queueMicrotask(tryFocus);
    const retry = window.setTimeout(tryFocus, 260);
    return () => window.clearTimeout(retry);
  }, [editing, layoutRef, review, visibleDetail]);

  const reviewPresentation = useMemo<ReviewPresentation | null>(() => {
    if (!review) return null;
    return {
      path: review.snapshot.path,
      baseContent: review.snapshot.baseContent,
      draftContent: review.snapshot.candidateContent,
      operations: review.kind === "preview" ? review.preview.operations.map(toDiffOperation) : [],
      analysisPending: review.kind === "preview" && review.preview.analysisPending,
    };
  }, [review]);

  return {
    editing,
    review,
    reviewClosing,
    decisions,
    editPending,
    reviewPending,
    error,
    reviewPresentation,
    beginEditing,
    closeEditor,
    changeDraft,
    saveFrontmatter,
    finishReviewExit,
    returnFromReview,
    rebaseConflict,
    applyReview,
    setDecision,
  };
}
