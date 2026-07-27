import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ChevronDown,
  Clock,
  MoreHorizontal,
  Activity01,
  Radio,
  ShieldOff,
  AiMagic,
  Check,
  Stop,
  Trash2,
  TriangleAlert,
  X,
} from "@/components/icons";
import clsx from "clsx";
import { useStore } from "@/stores";
import type { Automation, AutomationRun, CreateAutomationPayload } from "@/api/types";
import { listAutomationRunsApi } from "@/api/automations";
import {
  createAutomation,
  deleteAutomation,
  generateAutomationDescription,
  runAutomation,
  toggleAutomation,
  updateAutomation,
} from "@/actions/automations";
import { formatDuration } from "@/lib/agentRun";
import { TEMPLATES } from "@/features/automations/lib/templates";
import {
  buildPayload,
  emptyForm,
  formFromAutomation,
  formFromPreset,
  scheduleLabel,
  splitKeywords,
  type FormState,
  type Schedule,
} from "@/features/automations/lib/schedule";
import {
  ModelMenuPicker,
  availableModelChoices,
  shortModelLabel,
} from "@/components/ui/ModelPickers";
import { ScheduleTriggerPeek } from "@/features/automations/components/ScheduleTriggerPeek";
import { PeekSurface } from "@/components/workspace/PeekSurface";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";
import { ContextMenu, type ContextMenuEntry, type ContextMenuPosition } from "@/components/ui/ContextMenu";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { IconButton } from "@/components/ui/IconButton";
import { MenuItem } from "@/components/ui/MenuItem";
import { Skeleton } from "@/components/ui/Skeleton";
import { SwitchControl } from "@/components/ui/SwitchControl";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { DynamicIconSwap } from "@/components/ui/IconSwap";
import { TabPanels } from "@/components/ui/TabPanels";
import { DialogLayer } from "@/components/workspace/DialogLayer";
import { ICON } from "@/lib/icons";
import { useTimeoutFlag } from "@/lib/hooks";
import { MOTION } from "@/lib/tokens/motion";

function cloneForm(form: FormState): FormState {
  return { ...form, schedule: { ...form.schedule } };
}

/** The save boundary is the exact payload sent to the server, not a loose
 * field comparison. This keeps future payload additions inside the same
 * contract without inventing a parallel dirty-state shape. */
function samePayload(left: FormState, right: FormState): boolean {
  return JSON.stringify(buildPayload(left, { includeDescription: false }))
    === JSON.stringify(buildPayload(right, { includeDescription: false }));
}

function runStampParts(iso: string): { day: string; time: string } {
  const date = new Date(iso);
  const today = new Date();
  const time = date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return {
    day: date.toDateString() === today.toDateString()
      ? "today"
      : date.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
    time,
  };
}

function runStatusWord(run: AutomationRun): string {
  const status = run.status.toLowerCase();
  if (status === "running" || status === "pending") return "running";
  if (status === "failed" || status === "error") return "failed";
  if (status === "cancelled") return "cancelled";
  if (status === "interrupted") return "interrupted";
  return "completed";
}

function runSummary(run: AutomationRun): string {
  const status = runStatusWord(run);
  if (status === "running") return "Run is in progress.";
  if (status === "failed") return run.error ?? "The run failed without a recorded error.";
  if (status === "cancelled") return "Run was cancelled.";
  if (status === "interrupted") return "Run was interrupted.";
  const lines = (run.result ?? "")
    .split(/\r?\n/)
    .map((line) => line.replace(/^#{1,6}\s+/, "").replace(/^[-*+>]\s+/, "").replace(/\*\*/g, "").trim())
    .filter(Boolean);
  return lines.find((line) => line.includes(" ")) ?? lines[0] ?? "Completed. No output.";
}

function runDuration(run: AutomationRun): string {
  if (!run.ended_at) return "in progress";
  const milliseconds = Date.parse(run.ended_at) - Date.parse(run.started_at);
  return Number.isFinite(milliseconds) && milliseconds >= 1000 ? formatDuration(milliseconds) : "not recorded";
}

function groupLabel(automation: Automation | null): string {
  if (!automation) return "automations / new";
  if (automation.builtin || automation.handler?.startsWith("knowledge_")) return "automations / system";
  if (automation.task_id.startsWith("area:")) return "automations / area agent";
  return "automations / yours";
}

function SectionHeading({
  children,
  meta,
  action,
}: {
  children: string;
  meta?: string;
  action?: ReactNode;
}) {
  return (
    <header className="automation-detail__section-heading">
      <h2>{children}</h2>
      {meta && <span>{meta}</span>}
      {action}
    </header>
  );
}

export type DetailSeed =
  | { kind: "existing"; automation: Automation }
  | { kind: "draft"; preset: CreateAutomationPayload | null };

type BusyAction = "create" | "delete" | "run" | "save" | "toggle";

interface RunContextMenuState extends ContextMenuPosition {
  run: AutomationRun;
  runTitle: string;
}

export function AutomationDetail({
  seed,
  onClose,
  onCreated,
  onDiscardDraft,
  onDeleted,
  onDirtyChange,
  onOpenRun,
  onOpenTrigger,
  onOpenChannel,
  openLatestRun = false,
  onLatestRunConsumed,
}: {
  seed: DetailSeed;
  onClose: () => void;
  onCreated: () => void;
  onDiscardDraft?: () => void;
  onDeleted?: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  onOpenRun?: (run: AutomationRun, runTitle: string) => void;
  onOpenTrigger?: () => void;
  onOpenChannel?: (sessionId: string) => void;
  /** Deep-link intent from activity rows: show the latest result on arrival. */
  openLatestRun?: boolean;
  onLatestRunConsumed?: () => void;
}) {
  const automation = seed.kind === "existing" ? seed.automation : null;
  const builtin = Boolean(automation?.builtin);
  const config = useStore((state) => state.config);
  const sessions = useStore((state) => state.sessions);

  const [form, setForm] = useState<FormState>(() =>
    automation
      ? formFromAutomation(automation)
      : seed.kind === "draft" && seed.preset
        ? formFromPreset(seed.preset)
        : emptyForm(),
  );
  const [savedForm, setSavedForm] = useState<FormState>(() => cloneForm(
    automation
      ? formFromAutomation(automation)
      : seed.kind === "draft" && seed.preset
        ? formFromPreset(seed.preset)
        : emptyForm(),
  ));
  const [runs, setRuns] = useState<AutomationRun[] | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [busy, setBusy] = useState<BusyAction | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [saved, flashSaved] = useTimeoutFlag(1600);
  const [moreOpen, setMoreOpen] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [triggerDraft, setTriggerDraft] = useState<Schedule | null>(null);
  // Instructions are read in the pane and edited in a peek: the prose is long
  // and the pane is an instrument panel of compact rows. Null = closed; the
  // draft means Cancel/Escape leave the form untouched (trigger-peek contract).
  const [promptDraft, setPromptDraft] = useState<string | null>(null);
  const [runContextMenu, setRunContextMenu] = useState<RunContextMenuState | null>(null);
  const [descriptionState, setDescriptionState] = useState<"idle" | "loading" | "ready" | "error">(
    automation?.description ? "ready" : "idle",
  );
  const descriptionRequestTaskRef = useRef<string | null>(null);
  const descriptionActiveTaskRef = useRef<string | null>(null);
  const moreActionRef = useRef<HTMLButtonElement>(null);

  const taskId = automation?.task_id ?? null;
  const dirty = useMemo(() => !samePayload(form, savedForm), [form, savedForm]);
  const running = automation?.running_since != null;
  const valid =
    form.prompt.trim().length > 0 &&
    (form.schedule.kind !== "message" || splitKeywords(form.schedule.channel).length > 0);
  const unsafeAutoApprove =
    form.schedule.kind === "message" &&
    form.auto_approve &&
    form.schedule.fromUser.trim().length === 0;
  const channel = automation
    ? sessions.find((session) => session.origin_automation_id === automation.task_id) ?? null
    : null;
  const ledger = (runs ?? []).slice(0, 5);
  const ledgerKey = runs === null
    ? "loading"
    : runsError
      ? `error:${runsError}`
      : ledger.map((run) => `${run.id}:${run.status}:${run.ended_at ?? ""}`).join("|") || "empty";
  const wasRunning = useRef(running);
  const [runCompleted, setRunCompleted] = useState(false);

  useEffect(() => {
    let timer: number | undefined;
    if (running) {
      setRunCompleted(false);
    } else if (wasRunning.current) {
      setRunCompleted(true);
      timer = window.setTimeout(() => setRunCompleted(false), MOTION.acknowledge * 1000);
    }
    wasRunning.current = running;
    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [running]);

  const pauseLabel = busy === "toggle" ? "Updating" : automation?.enabled ? "Pause" : "Resume";
  const runVisualState = runCompleted ? "completed" : running || busy === "run" ? "running" : "idle";
  const runLabel = runVisualState === "completed"
    ? "Completed"
    : runVisualState === "running"
      ? "Running"
      : "Run now";

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  useEffect(() => {
    setDescriptionState(automation?.description ? "ready" : "idle");
  }, [automation?.description, taskId]);

  useEffect(() => {
    descriptionActiveTaskRef.current = taskId;
    return () => {
      if (descriptionActiveTaskRef.current === taskId) {
        descriptionActiveTaskRef.current = null;
      }
    };
  }, [taskId]);

  useEffect(() => {
    if (
      !taskId
      || seed.kind !== "existing"
      || form.description
      || descriptionRequestTaskRef.current === taskId
    ) return;
    descriptionRequestTaskRef.current = taskId;
    setDescriptionState("loading");
    void generateAutomationDescription(taskId)
      .then((updated) => {
        if (descriptionActiveTaskRef.current !== taskId) return;
        setForm((current) => ({ ...current, description: updated.description }));
        setSavedForm((current) => ({ ...current, description: updated.description }));
        setDescriptionState(updated.description ? "ready" : "error");
      })
      .catch(() => {
        if (descriptionActiveTaskRef.current === taskId) setDescriptionState("error");
      });
  }, [form.description, seed.kind, taskId]);

  const loadRuns = useCallback(async () => {
    if (!taskId) return;
    setRunsError(null);
    try {
      setRuns(await listAutomationRunsApi(config, taskId, 30));
    } catch (error) {
      setRuns([]);
      setRunsError(error instanceof Error ? error.message : "Couldn't load recent runs.");
    }
  }, [config, taskId]);

  useEffect(() => {
    if (!taskId) {
      setRuns(null);
      setRunsError(null);
      return;
    }
    void loadRuns();
  }, [automation?.last_run_at, automation?.running_since, loadRuns, taskId]);

  const save = async () => {
    if (!taskId || busy || !valid || !dirty) return;
    setBusy("save");
    setMutationError(null);
    try {
      const updated = await updateAutomation(
        taskId,
        buildPayload(form, { includeDescription: false }),
      );
      const nextForm = formFromAutomation(updated);
      setForm(nextForm);
      setSavedForm(cloneForm(nextForm));
      setDescriptionState(updated.description ? "ready" : "error");
      flashSaved();
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Couldn't save changes.");
    } finally {
      setBusy(null);
    }
  };

  const create = async () => {
    if (busy || !valid) return;
    setBusy("create");
    setMutationError(null);
    try {
      await createAutomation(buildPayload(form));
      onDirtyChange?.(false);
      onCreated();
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Couldn't create automation.");
    } finally {
      setBusy(null);
    }
  };

  const toggle = async () => {
    if (!taskId || busy || dirty) return;
    setBusy("toggle");
    setMutationError(null);
    try {
      await toggleAutomation(taskId);
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Couldn't update automation.");
    } finally {
      setBusy(null);
    }
  };

  const run = async () => {
    if (!taskId || busy || dirty || !automation?.enabled || running) return;
    setBusy("run");
    setMutationError(null);
    try {
      await runAutomation(taskId);
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Couldn't start this run.");
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!taskId || busy) return;
    setBusy("delete");
    setMutationError(null);
    try {
      await deleteAutomation(taskId);
      setDeleteConfirmOpen(false);
      onDirtyChange?.(false);
      onDeleted?.();
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Couldn't delete automation.");
    } finally {
      setBusy(null);
    }
  };

  const updateForm = (patch: Partial<FormState>) => {
    setMutationError(null);
    setForm((current) => ({ ...current, ...patch }));
  };

  const openRunContextMenu = useCallback((run: AutomationRun, runTitle: string, position: ContextMenuPosition) => {
    if (!onOpenRun) return;
    setRunContextMenu({ run, runTitle, ...position });
  }, [onOpenRun]);

  const openRunResult = useCallback((run: AutomationRun, runTitle: string) => {
    setTriggerDraft(null);
    onOpenRun?.(run, runTitle);
  }, [onOpenRun]);

  /* Activity-row deep link: once runs land, surface the newest result
   *  instead of stopping at the settings detail. */
  useEffect(() => {
    if (!openLatestRun || runs === null) return;
    onLatestRunConsumed?.();
    const run = runs[0];
    if (!run) return;
    const stamp = runStampParts(run.started_at);
    openRunResult(run, `${stamp.day} ${stamp.time}`.trim());
  }, [onLatestRunConsumed, openLatestRun, openRunResult, runs]);

  const runContextEntries = useMemo<ContextMenuEntry[]>(() => {
    if (!runContextMenu || !onOpenRun) return [];
    return [{
      id: "open-result",
      label: "Open result",
      onSelect: () => openRunResult(runContextMenu.run, runContextMenu.runTitle),
    }];
  }, [onOpenRun, openRunResult, runContextMenu]);

  return (
    <article className="automation-detail" aria-labelledby="automation-detail-title">
      <header className="automation-detail__header">
        <div className="automation-detail__identity">
          <div className="automation-detail__crumb">{groupLabel(automation)}</div>
          <input
            id="automation-detail-title"
            value={form.name}
            onChange={(event) => updateForm({ name: event.target.value })}
            placeholder="Untitled automation"
            spellCheck={false}
            readOnly={builtin}
            autoFocus={seed.kind === "draft" && !seed.preset}
            aria-label="Automation name"
            className="automation-detail__title"
          />
          <p className="automation-detail__summary">
            {form.description?.trim()
              || (seed.kind === "draft"
                ? "Define what should happen and when it should run."
                : descriptionState === "loading"
                  ? "Writing a short description…"
                  : "Description unavailable.")}
          </p>
        </div>

      </header>

      <div className="automation-detail__scroll scroll-thin scroll-fade" data-page-enter-item data-page-skeleton>
        <section className="automation-detail__section">
          <SectionHeading
            action={seed.kind !== "draft" && !builtin ? (
              <Button size="sm" onClick={() => setPromptDraft(form.prompt)}>Edit</Button>
            ) : undefined}
          >
            Instructions
          </SectionHeading>
          {seed.kind === "draft" ? (
            /* Drafts are write-first: a new automation opening as a read-only
               box with an Edit button would be a worse first run. */
            <textarea
              value={form.prompt}
              onChange={(event) => updateForm({ prompt: event.target.value })}
              placeholder="What should the agent do when this automation fires?"
              spellCheck={false}
              rows={7}
              className="automation-detail__prompt"
            />
          ) : (
            <div className="automation-detail__prompt-read">
              <div className="automation-detail__prompt-read-scroll scroll-thin scroll-fade">
                <p data-empty={form.prompt.trim() ? undefined : "true"}>
                  {form.prompt.trim() || "No instructions yet."}
                </p>
              </div>
            </div>
          )}
          {seed.kind === "draft" && !form.prompt.trim() && (
            <div className="automation-detail__templates">
              <span>Start with a template</span>
              <div>
                {TEMPLATES.map((template) => (
                  <Button
                    key={template.id}
                    size="sm"
                    leadingIcon={template.icon}
                    onClick={() => {
                      setMutationError(null);
                      setForm(formFromPreset(template.payload));
                    }}
                  >
                    {template.name}
                  </Button>
                ))}
              </div>
            </div>
          )}
          {builtin && (
            <p className="automation-detail__read-only-note">
              Instructions are code-owned. You can adjust the schedule, model, approval setting, pause state, or run it now.
            </p>
          )}
        </section>

        <section className="automation-detail__section">
          <SectionHeading meta={form.auto_approve ? "runs unattended" : "asks before writes"}>
            Trigger & permissions
          </SectionHeading>
          <div className="automation-detail__control-list">
            <button
              type="button"
              className="automation-detail__control-row automation-detail__control-row--button"
              aria-haspopup="dialog"
              aria-expanded={triggerDraft !== null}
              data-trigger-peek-owner
              onClick={() => {
                onOpenTrigger?.();
                setTriggerDraft((current) => current ?? { ...form.schedule });
              }}
            >
              <span className="automation-detail__control-label">
                <Clock size={ICON.SM} aria-hidden />
                <span>
                  <b>Trigger</b>
                  <small>When this automation starts</small>
                </span>
              </span>
              <span className="automation-detail__control-value" title={scheduleLabel(form.schedule)}>
                {scheduleLabel(form.schedule)}
              </span>
              <ChevronDown className="automation-detail__row-chevron" size={12} aria-hidden />
            </button>
            <AutomationModelPicker
              model={form.model}
              onChange={(model) => updateForm({ model })}
            />
            <div className="automation-detail__control-row automation-detail__control-row--switch">
              <span className="automation-detail__control-label">
                <ShieldOff size={ICON.SM} aria-hidden />
                <span>
                  <b>Auto-Approve</b>
                  <small>Run tools without pausing for approval</small>
                </span>
              </span>
              <SwitchControl
                size="sm"
                checked={form.auto_approve}
                onChange={(autoApprove) => updateForm({ auto_approve: autoApprove })}
                aria-label="Auto-Approve"
              />
            </div>
          </div>
          {unsafeAutoApprove && (
            <Callout tone="warn" icon={TriangleAlert} title="Add a sender gate" className="automation-detail__notice">
              Anyone in this channel can start a full-tool unattended run while Auto-Approve is on.
            </Callout>
          )}
        </section>

        {automation && (
          <section className="automation-detail__section">
            <SectionHeading meta="open a result">Recent runs</SectionHeading>
            <TabPanels value={ledgerKey} direction={-1} axis="y">
              {runs === null ? (
                <div className="automation-detail__run-skeletons" aria-label="Loading recent runs">
                  {Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} height={42} radius={8} />)}
                </div>
              ) : runsError ? (
                <Callout
                  tone="bad"
                  title="Couldn't load recent runs"
                  className="automation-detail__notice"
                  action={<Button variant="quiet" size="sm" onClick={() => void loadRuns()}>Try again</Button>}
                >
                  {runsError}
                </Callout>
              ) : ledger.length === 0 ? (
                <div className="automation-detail__runs-empty">No runs yet. The first result will appear here.</div>
              ) : (
                <div className="automation-detail__run-ledger">
                  {ledger.map((run) => {
                    const status = runStatusWord(run);
                    const stamp = runStampParts(run.started_at);
                    const runTitle = `${stamp.day} ${stamp.time}`.trim();
                    return (
                      <button
                        key={run.id}
                        type="button"
                        className="automation-detail__run"
                        data-status={status}
                        data-run-peek-owner
                        aria-label={`${stamp.day} ${stamp.time}: ${status}. ${runSummary(run)}`}
                        onClick={() => openRunResult(run, runTitle)}
                        onContextMenu={onOpenRun ? (event) => {
                          event.preventDefault();
                          openRunContextMenu(run, runTitle, {
                            x: event.clientX,
                            y: event.clientY,
                            trigger: event.currentTarget,
                            source: "pointer",
                          });
                        } : undefined}
                        onKeyDown={onOpenRun ? (event) => {
                          if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
                          event.preventDefault();
                          const rect = event.currentTarget.getBoundingClientRect();
                          openRunContextMenu(run, runTitle, {
                            x: rect.left + 12,
                            y: rect.bottom - 4,
                            trigger: event.currentTarget,
                            source: "keyboard",
                          });
                        } : undefined}
                      >
                        <i className="automation-detail__run-status" aria-hidden />
                        <time dateTime={run.started_at}>{stamp.day}</time>
                        <time dateTime={run.started_at}>{stamp.time}</time>
                        <span className="automation-detail__run-summary">{runSummary(run)}</span>
                        <span>{runDuration(run)}</span>
                        <ChevronDown size={12} aria-hidden />
                      </button>
                    );
                  })}
                </div>
              )}
            </TabPanels>
          </section>
        )}

        {mutationError && (
          <Callout tone="bad" title="Couldn't update automation" className="automation-detail__notice">
            {mutationError}
          </Callout>
        )}
      </div>

      <footer className="automation-detail__footer">
        <span className={clsx("automation-detail__save-state", (dirty || saved || busy === "save") && "is-visible")} aria-live="polite">
          {busy === "save" ? "Saving changes" : dirty ? "Unsaved changes" : saved ? "Saved" : ""}
        </span>
        <span className="automation-detail__footer-spacer" />
        {seed.kind === "draft" ? (
          <>
            <Button variant="quiet" onClick={onDiscardDraft ?? onCreated} disabled={busy === "create"}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void create()} disabled={!valid || busy === "create"}>
              {busy === "create" ? "Creating" : "Create"}
            </Button>
          </>
        ) : (
          <>
            <Button
              size="md"
              className="automation-detail__pause"
              onClick={() => void toggle()}
              disabled={Boolean(busy) || dirty}
            >
              <BlurSwap swapKey={pauseLabel}>{pauseLabel}</BlurSwap>
            </Button>
            {dirty && (
              <Button variant="primary" size="sm" onClick={() => void save()} disabled={!valid || Boolean(busy)}>
                {busy === "save" ? "Saving" : "Save changes"}
              </Button>
            )}
            <Button
              variant={dirty ? "secondary" : "primary"}
              size="md"
              disabled={!automation?.enabled || running || runCompleted || dirty || Boolean(busy)}
              onClick={() => void run()}
            >
              <DynamicIconSwap swapKey={runVisualState}>
                {runVisualState === "running"
                  ? <Stop size={ICON.SM} />
                  : runVisualState === "completed"
                    ? <Check size={ICON.SM} />
                    : <Activity01 size={ICON.SM} />}
              </DynamicIconSwap>
              <BlurSwap swapKey={runLabel}>{runLabel}</BlurSwap>
            </Button>
            <IconButton
              ref={moreActionRef}
              size="sm"
              tone="faint"
              active={moreOpen}
              aria-label="More automation actions"
              title="More automation actions"
              onClick={() => setMoreOpen((open) => !open)}
            >
              <MoreHorizontal size={ICON.SM} />
            </IconButton>
          </>
        )}
      </footer>

      <PeekSurface
        open={promptDraft !== null}
        onClose={() => setPromptDraft(null)}
        className="automation-prompt-peek surface-peek"
        ariaLabel="Edit instructions"
        layer="automation-prompt-peek"
        focusOnOpen
        focusSelector=".automation-prompt-peek textarea"
        closeOnOutsidePointerDown
      >
        <header className="automation-trigger-peek__header arden-peek-rule-below">
          <b>Instructions</b>
          <IconButton
            data-peek-close
            onClick={() => setPromptDraft(null)}
            aria-label="Close instructions"
            title="Close instructions"
          >
            <X size={ICON.SM} />
          </IconButton>
        </header>
        <div className="automation-prompt-peek__body">
          <textarea
            value={promptDraft ?? ""}
            onChange={(event) => setPromptDraft(event.target.value)}
            placeholder="What should the agent do when this automation fires?"
            spellCheck={false}
            aria-label="Instructions"
          />
        </div>
        <footer className="automation-prompt-peek__footer arden-peek-rule-above">
          <Button variant="quiet" size="md" onClick={() => setPromptDraft(null)}>Cancel</Button>
          <Button
            variant="primary"
            size="md"
            disabled={(promptDraft ?? "").trim().length === 0}
            onClick={() => {
              updateForm({ prompt: promptDraft ?? "" });
              setPromptDraft(null);
            }}
          >
            Save instructions
          </Button>
        </footer>
      </PeekSurface>

      <ScheduleTriggerPeek
        open={triggerDraft !== null}
        schedule={triggerDraft ?? form.schedule}
        onChange={setTriggerDraft}
        onSave={(schedule) => {
          updateForm({ schedule });
          setTriggerDraft(null);
        }}
        onClose={() => setTriggerDraft(null)}
      />

      <ContextMenu
        state={runContextMenu}
        onClose={() => setRunContextMenu(null)}
        entries={runContextEntries}
        ariaLabel="Run actions"
      />

      {automation && (
        <AnchoredPopover
          open={moreOpen}
          onClose={() => setMoreOpen(false)}
          anchor={moreActionRef}
          variant="menu"
          ariaLabel="More automation actions"
          className="automation-detail__more-menu"
        >
          {channel && onOpenChannel && (
            <MenuItem
              role="menuitem"
              leading={<Radio size={14} />}
              onClick={() => {
                setMoreOpen(false);
                onOpenChannel(channel.session_id);
              }}
            >
              Open channel
            </MenuItem>
          )}
          <MenuItem
            role="menuitem"
            leading={<X size={14} />}
            onClick={() => {
              setMoreOpen(false);
              onClose();
            }}
          >
            Close automations
          </MenuItem>
          {!builtin && (
            <MenuItem
              role="menuitem"
              leading={<Trash2 size={14} />}
              className="text-bad hover:!text-bad focus-visible:!text-bad"
              disabled={busy === "delete"}
              onClick={() => {
                setMoreOpen(false);
                setDeleteConfirmOpen(true);
              }}
            >
              Delete automation
            </MenuItem>
          )}
        </AnchoredPopover>
      )}

      <DialogLayer
        open={deleteConfirmOpen}
        alert
        className="automation-delete-dialog"
        labelledBy="delete-automation-title"
        onClose={() => setDeleteConfirmOpen(false)}
      >
        <div className="automation-delete-dialog__panel">
          <div>
            <h2 id="delete-automation-title">Delete this automation?</h2>
            <p>Its schedule and run history will no longer be available here.</p>
          </div>
          <footer>
            <Button variant="quiet" onClick={() => setDeleteConfirmOpen(false)} disabled={busy === "delete"}>
              Cancel
            </Button>
            <Button variant="danger" autoFocus onClick={() => void remove()} disabled={busy === "delete"}>
              {busy === "delete" ? "Deleting" : "Delete"}
            </Button>
          </footer>
        </div>
      </DialogLayer>
    </article>
  );
}

const DEFAULT_MODEL_LABEL = "session default";

function AutomationModelPicker({
  model,
  onChange,
}: {
  model: string | null;
  onChange: (model: string | null) => void;
}) {
  const serverModels = useStore((state) => state.serverModels);
  const availableModels = useMemo(
    () => availableModelChoices(model, serverModels?.models),
    [model, serverModels],
  );
  const options = [
    { value: "", label: DEFAULT_MODEL_LABEL },
    ...availableModels.map((choice) => ({ value: choice, label: shortModelLabel(choice) })),
  ];
  const displayValue = model ? shortModelLabel(model) : DEFAULT_MODEL_LABEL;

  return (
    <ModelMenuPicker
      value={model ?? ""}
      options={options}
      ariaLabel="Automation model"
      popupClassName="automation-model-menu"
      triggerClassName="automation-detail__control-row automation-detail__control-row--button automation-detail__model-trigger"
      disabled={!serverModels}
      onValueChange={(selected) => onChange(selected || null)}
      trigger={(
        <>
          <span className="automation-detail__control-label">
            <AiMagic size={ICON.SM} aria-hidden />
            <span>
              <b>Model</b>
              <small>Uses the session default unless overridden</small>
            </span>
          </span>
          <span className="automation-detail__control-value" title={model ?? DEFAULT_MODEL_LABEL}>
            <BlurSwap swapKey={displayValue}>{displayValue}</BlurSwap>
          </span>
          <ChevronDown className="automation-detail__row-chevron" size={12} aria-hidden />
        </>
      )}
    />
  );
}
