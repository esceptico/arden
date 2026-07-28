import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, MoreHorizontal } from "@/components/icons";
import { ContextMenu, type ContextMenuEntry, type ContextMenuPosition } from "@/components/ui/ContextMenu";
import { IconSwap } from "@/components/ui/IconSwap";
import { MemoryEditor } from "@/features/memory/components/MemoryEditor";
import { MemoryNote } from "@/features/memory/components/MemoryNote";
import { NotebookRail, type MemoryRailMode } from "@/features/memory/components/NotebookRail";
import {
  WikiMaintenanceReviewSheet,
  WikiMaintenanceReviewStatusSheet,
} from "@/features/memory/components/WikiMaintenanceReviewSheet";
import {
  WikiRenameApprovalSheet,
  WikiRenameApprovalStatusSheet,
} from "@/features/memory/components/WikiRenameApprovalSheet";
import { useWikiMaintenanceReviews } from "@/features/memory/hooks/useWikiMaintenanceReviews";
import { useWikiRenameApprovals } from "@/features/memory/hooks/useWikiRenameApprovals";
import { serializeFrontmatter, splitFrontmatter } from "@/features/memory/lib/format";
import { buildWorkspaceTree } from "@/features/memory/lib/workspaceTree";
import { archiveWikiPage, listFacts, listWikiPages, readWikiPage, readWikiPageHistory, readWikiPageLinks, updateWikiPage, type WikiHistoryCommit, type WikiLink, type WikiPage, type WikiPageSummary } from "@/api/wiki";
import type { AppConfig } from "@/api/core";
import type {
  MemoryArtifactDetail,
  MemoryArtifactSummary,
  MemoryItem,
} from "@/features/memory/lib/notebookTypes";
import type {
  MemoryFrontmatter,
  MemoryFrontmatterValue,
} from "@/features/memory/components/MemoryProperties";

interface Editing { page: MemoryArtifactDetail; draft: string }
interface Actions extends ContextMenuPosition { page: MemoryArtifactDetail }

function frontmatterValue(value: unknown): MemoryFrontmatterValue | undefined {
  if (value == null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  if (Array.isArray(value)) {
    const items = value.map(frontmatterValue);
    return items.some((item) => item === undefined)
      ? undefined
      : items as MemoryFrontmatterValue[];
  }
  if (typeof value === "object") {
    const result: Record<string, MemoryFrontmatterValue> = {};
    for (const [key, item] of Object.entries(value)) {
      const next = frontmatterValue(item);
      if (next === undefined) return undefined;
      result[key] = next;
    }
    return result;
  }
  return undefined;
}

function frontmatter(metadata: Record<string, unknown>): MemoryFrontmatter {
  const result: MemoryFrontmatter = {};
  for (const [key, value] of Object.entries(metadata)) {
    const next = frontmatterValue(value);
    if (next !== undefined) result[key] = next;
  }
  return result;
}

function summary(page: WikiPageSummary): MemoryArtifactSummary {
  return {
    pageId: page.pageId, path: page.path, title: page.title, kind: "topic", source: "wiki", revision: page.version,
    head: page.repositoryHead ?? "", aliases: page.aliases, lifecycle: page.lifecycle,
    snippet: null, summary: null, editable: page.resourceState === "active",
    updatedAt: page.updatedAt, createdAt: page.createdAt,
  };
}

function detail(page: WikiPage): MemoryArtifactDetail {
  const { body } = splitFrontmatter(page.content);
  return {
    ...summary(page),
    content: body,
    editableContent: page.content,
    frontmatter: frontmatter({
      ...page.metadata,
      page_id: page.pageId,
      title: page.title,
      aliases: page.aliases,
      lifecycle: page.lifecycle,
    }),
  };
}

export function ArtifactMemoryView({ config }: { config: AppConfig }) {
  const [pages, setPages] = useState<MemoryArtifactSummary[]>([]);
  const [facts, setFacts] = useState<MemoryItem[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [selected, setSelected] = useState<MemoryArtifactDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [factsLoading, setFactsLoading] = useState(false);
  const [factsError, setFactsError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<MemoryRailMode>("files");
  const [editing, setEditing] = useState<Editing | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [actions, setActions] = useState<Actions | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [links, setLinks] = useState<{ outgoing: WikiLink[]; backlinks: WikiLink[] } | null>(null);
  const [history, setHistory] = useState<WikiHistoryCommit[]>([]);
  const [maintenanceDraft, setMaintenanceDraft] = useState<{
    key: string;
    manual: boolean;
    note: string;
  } | null>(null);
  const mounted = useRef(true);

  const loadPages = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const next = (await listWikiPages(config)).map(summary);
      if (!mounted.current) return;
      setPages(next);
      setSelectedPath((current) => current && next.some((page) => page.path === current) ? current : next[0]?.path ?? null);
    } catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (mounted.current) setLoading(false); }
  }, [config]);
  const {
    draft: renameDraft,
    openDraft: openRenameDraft,
    cancelDraft: cancelRenameDraft,
    activeApproval: activeRenameApproval,
    pending: renamePending,
    error: renameError,
    reconciliationRequired: renameReconciliationRequired,
    verification: renameVerification,
    request: requestRename,
    reconcile: reconcileRename,
    resolve: resolveRename,
  } = useWikiRenameApprovals(config, () => void loadPages());
  const {
    reviews: maintenanceReviews,
    activeReview: maintenanceReview,
    pending: maintenancePending,
    error: maintenanceError,
    verification: maintenanceVerification,
    reconciliationRequired: maintenanceReconciliationRequired,
    refresh: refreshMaintenance,
    resolve: resolveMaintenance,
  } = useWikiMaintenanceReviews(config);
  const renameBlocked = renameDraft != null || activeRenameApproval != null || renameVerification !== "ready";
  const maintenanceBlocked = maintenanceReview != null || maintenanceVerification !== "ready";
  const maintenanceVisible = maintenanceBlocked && !renameBlocked;
  const navigationDisabled = saving || editing != null || renameBlocked || maintenanceBlocked;
  const maintenanceKey = maintenanceReview
    ? `${maintenanceReview.reviewId}:${maintenanceReview.generation}`
    : "";
  const activeMaintenanceDraft = maintenanceDraft?.key === maintenanceKey
    ? maintenanceDraft
    : { key: maintenanceKey, manual: false, note: "" };
  const selectedSummary = pages.find((item) => item.path === selectedPath) ?? null;
  const selectedPageId = selectedSummary?.pageId ?? null;

  useEffect(() => {
    mounted.current = true;
    void loadPages();
    return () => { mounted.current = false; };
  }, [loadPages]);
  const loadFacts = useCallback(async () => {
    setFactsLoading(true);
    setFactsError(null);
    try {
      const next = await listFacts(config);
      if (!mounted.current) return;
      setFacts(next.map((fact) => ({
        id: fact.factId, kind: fact.kind, updatedAt: fact.createdAt, content: fact.text, labels: fact.labels,
      })));
    } catch (reason) {
      if (mounted.current) setFactsError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (mounted.current) setFactsLoading(false);
    }
  }, [config]);
  useEffect(() => {
    if (mode === "facts") void loadFacts();
  }, [loadFacts, mode]);
  useEffect(() => {
    if (maintenanceDraft == null || maintenanceDraft.key === maintenanceKey) return;
    setMaintenanceDraft(null);
  }, [maintenanceDraft, maintenanceKey]);
  useEffect(() => {
    setNotice(null);
    if (!selectedPageId) { setSelected(null); return; }
    const controller = new AbortController();
    setSelected(null);
    void readWikiPage(config, selectedPageId, { signal: controller.signal }).then((next) => {
      if (!controller.signal.aborted) setSelected(detail(next));
    }).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [config, selectedPageId]);
  useEffect(() => {
    if (!selected) { setLinks(null); setHistory([]); return; }
    const controller = new AbortController();
    void Promise.all([
      readWikiPageLinks(config, selected.pageId, { signal: controller.signal }),
      readWikiPageHistory(config, selected.pageId, { signal: controller.signal }),
    ]).then(([nextLinks, nextHistory]) => {
      if (controller.signal.aborted) return;
      setLinks({ outgoing: nextLinks.outgoing, backlinks: nextLinks.backlinks });
      setHistory(nextHistory);
    }).catch((reason) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => controller.abort();
  }, [config, selected?.pageId, selected?.revision]);

  const save = useCallback(async () => {
    if (!editing || editing.draft === editing.page.editableContent) return;
    setSaving(true);
    try {
      const updated = await updateWikiPage(config, { pageId: editing.page.pageId, content: editing.draft, expectedVersion: editing.page.revision, expectedHead: editing.page.head });
      const next = detail(updated);
      setSelected(next);
      setPages((current) => current.map((page) => page.pageId === next.pageId ? summary(updated) : page));
      setEditing(null); setNotice("Saved wiki page.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setSaving(false); }
  }, [config, editing]);

  const saveFrontmatter = useCallback((nextFrontmatter: MemoryFrontmatter) => {
    if (!selected || selected.path === "health.md" || saving) return;
    const { body } = splitFrontmatter(selected.editableContent);
    const content = serializeFrontmatter(nextFrontmatter) + body;
    if (content === selected.editableContent) return;
    setSaving(true);
    setError(null);
    void updateWikiPage(config, {
      pageId: selected.pageId,
      content,
      expectedVersion: selected.revision,
      expectedHead: selected.head,
    }).then((updated) => {
      if (!mounted.current) return;
      const next = detail(updated);
      setSelected(next);
      setPages((current) => current.map((page) =>
        page.pageId === next.pageId ? summary(updated) : page,
      ));
      setNotice("Saved page properties.");
    }).catch((reason) => {
      if (mounted.current) setError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => {
      if (mounted.current) setSaving(false);
    });
  }, [config, saving, selected]);

  const archive = useCallback(async (page: MemoryArtifactDetail) => {
    setSaving(true);
    try {
      await archiveWikiPage(config, { pageId: page.pageId, expectedVersion: page.revision, expectedHead: page.head });
      setPages((current) => current.filter((item) => item.pageId !== page.pageId));
      setSelectedPath((current) => current === page.path ? null : current);
      setNotice(`Archived ${page.title}.`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setSaving(false); }
  }, [config]);

  const entries = useMemo<ContextMenuEntry[]>(() => actions ? [
    { id: "rename", label: "Rename page", onSelect: () => openRenameDraft({ pageId: actions.page.pageId, oldPath: actions.page.path, oldTitle: actions.page.title }) },
    { id: "archive", label: "Archive page", tone: "danger", onSelect: () => void archive(actions.page) },
  ] : [], [actions, archive, openRenameDraft]);
  const tree = useMemo(() => buildWorkspaceTree(pages), [pages]);
  const pageCanMutate = selected != null && selected.path !== "health.md" && !navigationDisabled;
  const pathForPageId = useCallback((pageId: string | null) => pages.find((page) => page.pageId === pageId)?.path ?? null, [pages]);
  const handlers = useMemo(() => {
    const wikiPath = (target: string) => {
      const match = links?.outgoing.find((link) => link.target === target && link.status === "resolved");
      return pathForPageId(match?.targetPageId ?? null);
    };
    const inlinePath = (target: string) => pages.some((page) => page.path === target) ? target : null;
    return {
      exists: (target: string) => wikiPath(target) != null,
      onNavigate: (target: string) => {
        const path = wikiPath(target);
        if (path) setSelectedPath(path);
      },
      existsInline: (target: string) => inlinePath(target) != null,
      onNavigateInline: (target: string) => {
        const path = inlinePath(target);
        if (path) setSelectedPath(path);
      },
    };
  }, [links, pages, pathForPageId]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((!event.metaKey && !event.ctrlKey) || event.key.toLowerCase() !== "s" || !editing) return;
      event.preventDefault();
      void save();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [editing, save]);

  return <>
    <div
      className={`memory-ws memory-focus-cache${collapsed ? " instruments-collapsed" : ""}`}
      data-memory-layout="notebook"
    >
    <aside className="mw-rail"><NotebookRail mode={mode} tree={tree} allNotes={pages} records={facts} selectedPath={selectedPath} loading={loading} error={error} refreshing={loading} recordsLoading={factsLoading} recordsError={factsError} navigationDisabled={navigationDisabled} onModeChange={setMode} onSelect={(path) => setSelectedPath(path)} onRetry={() => void loadPages()} onRetryRecords={() => void loadFacts()} onRefresh={() => void loadPages()} /></aside>
    <section className="mw-doc mw-main">
      <div className={`mw-instruments${collapsed ? " collapsed" : ""}`}>
        <div id="memory-page-instruments" className="mw-instrument-items" inert={collapsed || undefined}>
          {pageCanMutate && !editing && <button type="button" className="mw-instrument mw-instrument-edit" aria-label="Edit memory note" disabled={saving} onClick={() => selected && setEditing({ page: selected, draft: selected.editableContent })}>Edit</button>}
          {editing && <button type="button" className="mw-instrument" disabled={saving} onClick={() => setEditing(null)}>Cancel</button>}
          {editing && <button type="button" className="mw-instrument mw-instrument-edit" disabled={saving || editing.draft === editing.page.editableContent} onClick={() => void save()}>Save</button>}
          {pageCanMutate && !editing && <button type="button" className="mw-instrument" aria-label="Page actions" aria-haspopup="menu" onClick={(event) => { const rect = event.currentTarget.getBoundingClientRect(); setActions({ page: selected!, trigger: event.currentTarget, source: "pointer", x: rect.left, y: rect.bottom }); }}><MoreHorizontal size={16} /></button>}
        </div>
        <button type="button" className="mw-instrument mw-instrument-collapse" aria-label={collapsed ? "Expand instruments" : "Compact instruments"} aria-controls="memory-page-instruments" aria-expanded={!collapsed} onClick={() => setCollapsed((value) => !value)}><IconSwap state={collapsed ? "b" : "a"} iconA={<ChevronRight size={16} />} iconB={<ChevronLeft size={16} />} /></button>
      </div>
      {editing ? <MemoryEditor path={editing.page.path} baseContent={editing.page.editableContent} value={editing.draft} saving={saving} error={error} onChange={(draft) => setEditing((current) => current ? { ...current, draft } : current)} onClose={() => setEditing(null)} /> : <MemoryNote summary={selectedSummary} detail={selected} listLoading={loading} contentLoading={selectedPath != null && selected == null} contentNotice={notice} contentError={error} wikiHandlers={handlers} onFrontmatterChange={pageCanMutate ? saveFrontmatter : undefined} onRetry={() => void loadPages()} />}
    </section>
    {selected && <aside className="mw-context surface-peek" aria-label="Page context">
      <div className="mw-context-plane scroll-thin">
        <section className="mw-ctx-content">
          <h2 className="mw-ctx-label">links</h2>
          {links == null ? <p className="mw-ctx-empty">Loading…</p> : (
            <>
              <p className="mw-ctx-empty">Outgoing {links.outgoing.length} · Incoming {links.backlinks.length}</p>
              {[...links.outgoing, ...links.backlinks].slice(0, 12).map((link, index) => {
                const path = pathForPageId(link.targetPageId);
                return <button key={`${link.sourcePageId}:${link.target}:${index}`} type="button" className="mw-lk-row" disabled={!path} onClick={() => path && setSelectedPath(path)}>
                  <span className="mw-lk-body"><span className="mw-lk-title">{link.alias ?? link.target}</span><span className="mw-lk-sub">{link.status}</span></span>
                </button>;
              })}
            </>
          )}
        </section>
        <section className="mw-ctx-content">
          <h2 className="mw-ctx-label">history</h2>
          {history.length === 0 ? <p className="mw-ctx-empty">No commits recorded.</p> : history.slice(0, 12).map((commit) => <div key={commit.commitId} className="mw-rec">
            <div className="when"><span>{commit.timestamp.slice(0, 16).replace("T", " ")}</span><span className="actor">{commit.actor}</span></div>
            <p className="mw-bl-excerpt">{commit.reason}</p>
          </div>)}
        </section>
      </div>
    </aside>}
    <ContextMenu state={actions} onClose={() => setActions(null)} entries={entries} ariaLabel="Page actions" />
    </div>
    {renameBlocked && <WikiRenameApprovalSheet draft={renameDraft ?? undefined} approval={activeRenameApproval ?? undefined} pending={renamePending} reconciliationRequired={renameReconciliationRequired} error={renameError} onCancel={cancelRenameDraft} onRequest={(input) => void requestRename(input)} onReconcile={() => void reconcileRename()} onAccept={(approvalId) => void resolveRename(approvalId, "accept")} onReject={(approvalId) => void resolveRename(approvalId, "reject")} />}
    {renameBlocked && !renameDraft && !activeRenameApproval && <WikiRenameApprovalStatusSheet
      error={renameVerification === "error" ? renameError : null}
      onRetry={() => void reconcileRename()}
    />}
    {maintenanceVisible && maintenanceReview && <WikiMaintenanceReviewSheet
      key={maintenanceKey}
      config={config}
      review={maintenanceReview}
      position={1}
      total={maintenanceReviews.length}
      pending={maintenancePending}
      checking={maintenanceVerification === "loading"}
      reconciliationRequired={maintenanceReconciliationRequired}
      error={maintenanceError}
      manual={activeMaintenanceDraft.manual}
      note={activeMaintenanceDraft.note}
      onManualChange={(manual) => setMaintenanceDraft({
        key: maintenanceKey,
        manual,
        note: activeMaintenanceDraft.note,
      })}
      onNoteChange={(note) => setMaintenanceDraft({
        key: maintenanceKey,
        manual: true,
        note,
      })}
      onReconcile={() => void refreshMaintenance(true)}
      onResolve={(review, decision) => void resolveMaintenance(review, decision)}
    />}
    {maintenanceVisible && !maintenanceReview && <WikiMaintenanceReviewStatusSheet
      error={maintenanceVerification === "error" ? maintenanceError : null}
      onRetry={() => void refreshMaintenance(true)}
    />}
  </>;
}
