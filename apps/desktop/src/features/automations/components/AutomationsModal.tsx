import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft02, CalendarClock, Plus, Search, Settings, X } from "@/components/icons";
import { useStore } from "@/stores";
import { duplicateAutomation, fetchAutomations, runAutomation, toggleAutomation } from "@/actions/automations";
import { listAutomationSuggestionsApi } from "@/api/automations";
import { goToNewSessionHome, switchSession } from "@/actions/sessions";
import type { Automation, AutomationRun, AutomationSuggestion, CreateAutomationPayload } from "@/api/types";
import { isIterationLoop } from "@/lib/automationFilters";
import { AutomationRail, groupAutomations } from "@/features/automations/components/AutomationRail";
import { AutomationDetail, type DetailSeed } from "@/features/automations/components/AutomationDetail";
import { NewAutomationMenu } from "@/features/automations/components/NewAutomationMenu";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { Markdown } from "@/components/ui/Markdown";
import { TabPanels } from "@/components/ui/TabPanels";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { SidebarToggle } from "@/components/ui/SidebarToggle";
import { Takeover } from "@/components/workspace/Takeover";
import { DialogLayer } from "@/components/workspace/DialogLayer";
import { PaneResizeHandle } from "@/components/workspace/PaneResizeHandle";
import { PeekSurface } from "@/components/workspace/PeekSurface";
import { ShellBackButton } from "@/components/workspace/ShellBackButton";
import { ICON } from "@/lib/icons";
import "./automations-workspace.css";

const RAIL_WIDTH_KEY = "arden.desktop.automations.railWidth";
const RAIL_MIN = 220;
const RAIL_MAX = 400;
const RAIL_DEFAULT = 288;

type Draft = { key: number; preset: CreateAutomationPayload | null };

type Intent =
  | { kind: "back" }
  | { kind: "channel"; sessionId: string }
  | { kind: "close" }
  | { kind: "discard-draft" }
  | { kind: "home" }
  | { kind: "new"; preset: CreateAutomationPayload | null }
  | { kind: "select"; taskId: string };

type RunPeek = { run: AutomationRun; runTitle: string };

function reportAutomationActionFailure(action: string, automation: Automation, error: unknown): void {
  useStore.getState().pushToast({
    id: `automation-${action}-fail:${automation.task_id}:${Date.now()}`,
    title: `Couldn’t ${action} automation`,
    detail: error instanceof Error ? error.message : undefined,
    status: "failed",
    target: { kind: "automation" },
  });
}

function AutomationRunPeek({ view, onClose }: { view: RunPeek | null; onClose: () => void }) {
  const retainedView = useRef<RunPeek | null>(view);
  if (view) retainedView.current = view;
  const visibleView = retainedView.current;
  if (!visibleView) return null;

  const content =
    visibleView.run.status === "failed"
      ? `## Run failed\n\n${visibleView.run.error ?? "No error was recorded."}`
      : visibleView.run.result ?? "_This run completed without output._";

  return (
    <PeekSurface
      open={view != null}
      className="automation-result-peek surface-peek"
      ariaLabel="Run result"
      onClose={onClose}
      layer="automation-result-peek"
      closeOnOutsidePointerDown
      outsidePointerDownIgnoreSelector=".surface-popover, [data-run-peek-owner]"
    >
      <header className="arden-peek-rule-below">
        <div>
          <span>Run result</span>
          <b>{visibleView.runTitle}</b>
        </div>
        <IconButton data-peek-close onClick={onClose} aria-label="Close run result" title="Close run result">
          <X size={ICON.SM} />
        </IconButton>
      </header>
      <div className="automation-result-peek__body">
        <Markdown content={content} typeset />
      </div>
    </PeekSurface>
  );
}

/** A full-window master-detail workspace. Its open/close store actions stay
 * stable while the old PageModal chrome disappears, so callers do not need a
 * parallel navigation API during the shell migration. */
export function AutomationsModal() {
  const open = useStore((state) => state.automationsOpen);
  const close = useStore((state) => state.closeAutomations);
  const allAutomations = useStore((state) => state.automations);
  const targetId = useStore((state) => state.automationTargetId);
  const targetRun = useStore((state) => state.automationTargetRun);
  const clearTarget = useStore((state) => state.clearAutomationTarget);
  const openSettings = useStore((state) => state.openSettings);
  const config = useStore((state) => state.config);

  const newButtonRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const railRef = useRef<HTMLElement>(null);
  const draftSequence = useRef(0);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [suggestion, setSuggestion] = useState<AutomationSuggestion | null>(null);
  const [query, setQuery] = useState("");
  const [compactDetail, setCompactDetail] = useState(false);
  const [detailDirty, setDetailDirty] = useState(false);
  const [pendingIntent, setPendingIntent] = useState<Intent | null>(null);
  const [runPeek, setRunPeek] = useState<RunPeek | null>(null);
  /* One-shot deep-link intent: opened-from-activity rows land on the
     automation AND its latest result, not just the settings detail. */
  const [openLatestRun, setOpenLatestRun] = useState(false);
  const [railHidden, setRailHidden] = useState(false);
  const [detailAxis, setDetailAxis] = useState<"x" | "y">("y");
  const [detailDirection, setDetailDirection] = useState(1);

  // Loops remain a composer-local control. The workspace is only for
  // scheduled/triggered automations.
  const automations = useMemo(
    () => (allAutomations ? allAutomations.filter((automation) => !isIterationLoop(automation)) : null),
    [allAutomations],
  );
  const selected = useMemo(
    () => automations?.find((automation) => automation.task_id === selectedId) ?? null,
    [automations, selectedId],
  );
  const seed: DetailSeed | null = draft
    ? { kind: "draft", preset: draft.preset }
    : selected
      ? { kind: "existing", automation: selected }
      : null;
  const detailKey = draft
    ? `draft-${draft.key}`
    : selected
      ? selected.task_id
      : "empty";
  const executeIntent = useCallback((intent: Intent) => {
    setPendingIntent(null);
    setRunPeek(null);
    setDetailDirty(false);

    if (intent.kind === "back") {
      setCompactDetail(false);
      return;
    }
    if (intent.kind === "close") {
      close();
      return;
    }
    if (intent.kind === "home") {
      close();
      goToNewSessionHome();
      return;
    }
    if (intent.kind === "channel") {
      void switchSession(intent.sessionId);
      close();
      return;
    }
    if (intent.kind === "discard-draft") {
      setDetailAxis("y");
      setDetailDirection(1);
      setDraft(null);
      setCompactDetail(false);
      return;
    }
    if (intent.kind === "select") {
      const currentIndex = automations?.findIndex((automation) => automation.task_id === selectedId) ?? -1;
      const nextIndex = automations?.findIndex((automation) => automation.task_id === intent.taskId) ?? -1;
      setDetailAxis("y");
      setDetailDirection(currentIndex >= 0 && nextIndex < currentIndex ? -1 : 1);
      setDraft(null);
      setSelectedId(intent.taskId);
      setCompactDetail(true);
      return;
    }

    draftSequence.current += 1;
    setDetailAxis("x");
    setDetailDirection(1);
    setDraft({ key: draftSequence.current, preset: intent.preset });
    setCompactDetail(true);
  }, [automations, close, selectedId]);

  const requestIntent = useCallback((intent: Intent) => {
    if (intent.kind === "back") {
      executeIntent(intent);
      return;
    }
    if (detailDirty) {
      setPendingIntent(intent);
      return;
    }
    executeIntent(intent);
  }, [detailDirty, executeIntent]);

  const requestDismiss = useCallback(() => {
    if (runPeek) {
      setRunPeek(null);
      return;
    }
    if (pendingIntent) {
      setPendingIntent(null);
      return;
    }
    if (newOpen) {
      setNewOpen(false);
      return;
    }
    requestIntent({ kind: "close" });
  }, [newOpen, pendingIntent, requestIntent, runPeek]);

  const runRailAutomation = useCallback(async (automation: Automation) => {
    try {
      await runAutomation(automation.task_id);
    } catch (error) {
      reportAutomationActionFailure("run", automation, error);
    }
  }, []);

  const duplicateRailAutomation = useCallback(async (automation: Automation) => {
    try {
      const duplicate = await duplicateAutomation(automation);
      requestIntent({ kind: "select", taskId: duplicate.task_id });
    } catch (error) {
      reportAutomationActionFailure("duplicate", automation, error);
    }
  }, [requestIntent]);

  const toggleRailAutomation = useCallback(async (automation: Automation) => {
    try {
      await toggleAutomation(automation.task_id);
    } catch (error) {
      reportAutomationActionFailure(automation.enabled ? "pause" : "resume", automation, error);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    void fetchAutomations();
  }, [open]);

  // The mock has one personalized slot. It is populated only from the
  // server's active candidates; an empty/unavailable response leaves no
  // fabricated "For you" section behind.
  useEffect(() => {
    if (!open) {
      setSuggestion(null);
      return;
    }
    let current = true;
    void listAutomationSuggestionsApi(config)
      .then((suggestions) => {
        if (current) setSuggestion(suggestions[0] ?? null);
      })
      .catch(() => {
        if (current) setSuggestion(null);
      });
    return () => {
      current = false;
    };
  }, [config, open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  useEffect(() => {
    if (!open || !targetId || !automations?.some((automation) => automation.task_id === targetId)) return;
    setDraft(null);
    setDetailAxis("y");
    setDetailDirection(1);
    setSelectedId(targetId);
    setCompactDetail(true);
    setOpenLatestRun(targetRun === "latest");
    clearTarget();
  }, [automations, clearTarget, open, targetId, targetRun]);

  // Default to a user automation, then area/system. If the selection was
  // deleted, this also lands the workspace on a valid remaining row.
  // A resolvable deep-link target defers to the target effect above — both
  // run in the same commit, where `selected` is still stale-null, and this
  // one would otherwise clobber the targeted selection with the default.
  useEffect(() => {
    if (!open || draft || !automations || selected) return;
    if (targetId && automations.some((automation) => automation.task_id === targetId)) return;
    const groups = groupAutomations(automations);
    const first = groups.user[0] ?? groups.area[0] ?? groups.system[0];
    if (first) setSelectedId(first.task_id);
  }, [automations, draft, open, selected, targetId]);

  useEffect(() => {
    if (open) return;
    setSelectedId(null);
    setDraft(null);
    setNewOpen(false);
    setQuery("");
    setCompactDetail(false);
    setDetailDirty(false);
    setPendingIntent(null);
    setRunPeek(null);
    setRailHidden(false);
    setDetailAxis("y");
    setDetailDirection(1);
  }, [open]);

  // Restore the persisted rail width when the rail attaches — it remounts
  // with the Takeover (after `open` flips), so an open-keyed effect would
  // fire before the node exists.
  const attachRail = useCallback((node: HTMLElement | null) => {
    railRef.current = node;
    if (!node) return;
    const stored = Number(localStorage.getItem(RAIL_WIDTH_KEY));
    if (Number.isFinite(stored) && stored > 0) {
      node.style.setProperty(
        "--automation-rail-w",
        `${Math.max(RAIL_MIN, Math.min(RAIL_MAX, stored))}px`,
      );
    }
  }, []);

  return (
    <Takeover
      open={open}
      onClose={requestDismiss}
      ariaLabel="Automations"
      className={`automation-workspace${compactDetail ? " automation-workspace--detail" : ""}${railHidden ? " automation-workspace--rail-hidden" : ""}`}
    >
      <SidebarToggle
        hidden={railHidden}
        onToggle={() => setRailHidden((hidden) => !hidden)}
        labels={{
          hide: "Hide automation list",
          show: "Show automation list",
          shortcut: false,
        }}
      />
      <ShellBackButton onClick={() => requestIntent({ kind: "home" })} />
      <aside
        ref={attachRail}
        className="automation-workspace__rail"
        aria-label="Automations"
        data-page-enter-item="chrome"
        aria-hidden={railHidden}
        inert={railHidden ? true : undefined}
      >
        <PaneResizeHandle
          layoutRef={railRef}
          cssVar="--automation-rail-w"
          storageKey={RAIL_WIDTH_KEY}
          min={RAIL_MIN}
          max={RAIL_MAX}
          defaultWidth={RAIL_DEFAULT}
          edge="end"
          label="Resize automation list"
        />
        <header className="automation-workspace__rail-header">
          <h1>Automations</h1>
          <Button
            ref={newButtonRef}
            variant="secondary"
            size="md"
            leadingIcon={Plus}
            active={newOpen}
            onClick={() => setNewOpen((current) => !current)}
          >
            New
          </Button>
        </header>

        <label className="automation-workspace__search">
          <Search size={ICON.SM} aria-hidden="true" />
          <input
            ref={searchRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            type="search"
            placeholder="Find an automation"
            aria-label="Find an automation"
          />
          <kbd>⌘F</kbd>
        </label>

        <AutomationRail
          automations={automations}
          selectedId={draft ? null : selectedId}
          query={query}
          onSelect={(taskId) => requestIntent({ kind: "select", taskId })}
          onRunNow={runRailAutomation}
          onDuplicate={duplicateRailAutomation}
          onToggle={toggleRailAutomation}
        />

        <footer className="automation-workspace__rail-footer">
          <button
            type="button"
            className="automation-workspace__rail-utility"
            onClick={() => openSettings()}
          >
            <Settings size={ICON.SM} aria-hidden="true" />
            <span>Settings</span>
          </button>
          <ThemeToggle />
        </footer>
      </aside>

      <main className="automation-workspace__main" data-page-enter-item>
        <Button
          className="automation-workspace__back"
          variant="quiet"
          leadingIcon={ArrowLeft02}
          onClick={() => requestIntent({ kind: "back" })}
        >
          Automations
        </Button>
        <TabPanels
          value={detailKey}
          direction={detailDirection}
          axis={detailAxis}
          className="automation-workspace__detail-swap"
        >
          {seed ? (
            <AutomationDetail
              seed={seed}
              onClose={() => requestIntent({ kind: "close" })}
              onCreated={() => {
                setDraft(null);
                setSelectedId(null);
                setDetailDirty(false);
                setCompactDetail(false);
              }}
              onDiscardDraft={() => requestIntent({ kind: "discard-draft" })}
              onDeleted={() => {
                setSelectedId(null);
                setDetailDirty(false);
                setCompactDetail(false);
              }}
              onDirtyChange={setDetailDirty}
              onOpenChannel={(sessionId) => requestIntent({ kind: "channel", sessionId })}
              onOpenRun={(run, runTitle) => setRunPeek({ run, runTitle })}
              onOpenTrigger={() => setRunPeek(null)}
              openLatestRun={openLatestRun}
              onLatestRunConsumed={() => setOpenLatestRun(false)}
            />
          ) : (
            <section className="automation-workspace__empty">
              <CalendarClock size={30} aria-hidden="true" />
              <div>
                <h2>No automations yet.</h2>
                <p>Start from a template or write the first one from scratch.</p>
              </div>
              <div>
                <Button variant="secondary" onClick={() => setNewOpen(true)}>Browse templates</Button>
                <Button variant="quiet" onClick={() => requestIntent({ kind: "new", preset: null })}>Start from scratch</Button>
              </div>
            </section>
          )}
        </TabPanels>
      </main>

      <NewAutomationMenu
        open={newOpen}
        onClose={() => setNewOpen(false)}
        anchor={newButtonRef}
        suggestion={suggestion}
        onPick={(preset) => requestIntent({ kind: "new", preset })}
      />

      <AutomationRunPeek view={runPeek} onClose={() => setRunPeek(null)} />

      <DialogLayer
        open={pendingIntent != null}
        className="automation-discard-dialog"
        alert
        labelledBy="discard-automation-title"
        onClose={() => setPendingIntent(null)}
      >
        <div className="automation-discard-dialog__panel">
          <div>
            <h2 id="discard-automation-title">Discard unsaved changes?</h2>
            <p>Your changes have not been saved. You can keep editing or discard them.</p>
          </div>
          <footer>
            <Button variant="quiet" onClick={() => setPendingIntent(null)}>Keep editing</Button>
            <Button
              variant="danger"
              autoFocus
              onClick={() => {
                if (pendingIntent) executeIntent(pendingIntent);
              }}
            >
              Discard changes
            </Button>
          </footer>
        </div>
      </DialogLayer>
    </Takeover>
  );
}
