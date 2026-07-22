import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence } from "motion/react";
import { Play, Radio, TriangleAlert, X } from "@/components/icons";
import clsx from "clsx";
import { useStore } from "@/stores";
import type { Automation, AutomationRun, CreateAutomationPayload } from "@/api/types";
import { listAutomationRunsApi } from "@/api/automations";
import {
  createAutomation,
  deleteAutomation,
  runAutomation,
  toggleAutomation,
  updateAutomation,
} from "@/actions/automations";
import { switchSession } from "@/actions/sessions";
import { fetchServerConfig, updateServerConfig } from "@/actions/server";
import { isChannelAutomation } from "@/lib/automationFilters";
import { formatDuration, formatRelative } from "@/lib/agentRun";
import { automationTrustLabel, automationTrustTone } from "@/features/automations/lib/automationTrust";
import { TEMPLATES } from "@/features/automations/lib/templates";
import {
  buildPayload,
  emptyForm,
  formFromAutomation,
  formFromPreset,
  splitKeywords,
  type FormState,
} from "@/features/automations/lib/schedule";
import { RunRuler, rulerRunsFrom, rulerSpanMs, spanLabel } from "@/features/automations/components/RunRuler";
import { ModelReasoningPicker } from "@/components/ui/ComposerSelectors";
import { ScheduleChip } from "@/features/automations/components/ScheduleChip";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { ConfirmDeleteButton } from "@/components/ui/ConfirmDeleteButton";
import { IconButton } from "@/components/ui/IconButton";
import { ShowMore } from "@/components/ui/ShowMore";
import { Skeleton } from "@/components/ui/Skeleton";
import { SwitchControl } from "@/components/ui/SwitchControl";
import { Tooltip } from "@/components/ui/Tooltip";
import { ICON } from "@/lib/icons";

function SectionLabel({
  children,
  aux,
}: {
  children: string;
  aux?: string;
}) {
  return (
    <div className="flex items-baseline justify-between mb-2 text-2xs font-medium uppercase tracking-[0.08em] text-faint">
      <span>{children}</span>
      {aux && <span className="normal-case tracking-normal font-normal">{aux}</span>}
    </div>
  );
}

function runStamp(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const hm = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
  if (d.toDateString() === today.toDateString()) return `today ${hm}`;
  return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${hm}`;
}

function runSummary(run: AutomationRun): string {
  if (run.status === "running") return "running…";
  if (run.status === "failed") return `Failed — ${run.error ?? "no error recorded"}`;
  // First line that reads as prose. Automation results often open with the
  // spawned session's codename ("onyx-skua") — a single wordless token is a
  // label, not a summary, so skip past it.
  const lines = (run.result ?? "")
    .split(/\r?\n/)
    .map((l) => l.replace(/^#{1,6}\s+/, "").replace(/^[-*+>]\s+/, "").replace(/\*\*/g, "").trim())
    .filter(Boolean);
  const prose = lines.find((l) => l.includes(" ")) ?? lines[0];
  return prose ?? "Completed — no output.";
}

function runDuration(run: AutomationRun): string {
  if (run.ended_at == null) return "…";
  const ms = Date.parse(run.ended_at) - Date.parse(run.started_at);
  // Sub-second runs are recorder artifacts, not measurements — don't print
  // a fake "0s".
  return Number.isFinite(ms) && ms >= 1000 ? formatDuration(ms) : "—";
}

/** Success / median-duration strip under the ruler — where an automation
 *  earns trust at a glance. Only counts finished runs. */
function statsFor(runs: AutomationRun[], eventDriven: boolean) {
  const done = runs.filter((r) => r.ended_at != null);
  const ok = done.filter((r) => r.status !== "failed").length;
  const durations = done
    .map((r) => Date.parse(r.ended_at as string) - Date.parse(r.started_at))
    // Sub-second runs are recorder artifacts — they'd drag the median to 0s.
    .filter((ms) => Number.isFinite(ms) && ms >= 1000)
    .sort((a, b) => a - b);
  const median = durations.length
    ? formatDuration(durations[Math.floor(durations.length / 2)])
    : null;
  return { total: done.length, ok, median, eventDriven };
}

export type DetailSeed =
  | { kind: "existing"; automation: Automation }
  | { kind: "draft"; preset: CreateAutomationPayload | null };

export function AutomationDetail({
  seed,
  onClose,
  onCreated,
}: {
  seed: DetailSeed;
  onClose: () => void;
  onCreated: () => void;
}) {
  const automation = seed.kind === "existing" ? seed.automation : null;
  const builtin = !!automation?.builtin;
  const config = useStore((s) => s.config);
  const sessions = useStore((s) => s.sessions);
  const setMarkdownView = useStore((s) => s.setViewingMarkdown);

  const [form, setForm] = useState<FormState>(() =>
    automation
      ? formFromAutomation(automation)
      : seed.kind === "draft" && seed.preset
        ? formFromPreset(seed.preset)
        : emptyForm(),
  );
  const [runs, setRuns] = useState<AutomationRun[] | null>(null);
  const [busy, setBusy] = useState<"run" | "save" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hoverRunId, setHoverRunId] = useState<number | null>(null);

  const taskId = automation?.task_id ?? null;

  useEffect(() => {
    if (!taskId) return;
    let cancelled = false;
    setRuns(null);
    listAutomationRunsApi(config, taskId, 30)
      .then((r) => {
        if (!cancelled) setRuns(r);
      })
      .catch(() => {
        if (!cancelled) setRuns([]);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, automation?.last_run_at, automation?.running_since]);

  // Field-level persistence: dials save immediately, text saves on blur.
  // Everything funnels through the same full-form payload the old editor
  // submitted, so the server sees one consistent shape.
  const persist = async (next: FormState) => {
    if (!taskId) return;
    setError(null);
    try {
      await updateAutomation(taskId, buildPayload(next));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const savedText = useRef({ name: form.name, prompt: form.prompt });
  const blurSave = () => {
    if (!taskId) return;
    if (savedText.current.name === form.name && savedText.current.prompt === form.prompt) return;
    savedText.current = { name: form.name, prompt: form.prompt };
    void persist(form);
  };
  const setAndPersist = (patch: Partial<FormState>) => {
    setForm((p) => {
      const next = { ...p, ...patch };
      void persist(next);
      return next;
    });
  };

  const create = async () => {
    if (busy) return;
    setBusy("save");
    setError(null);
    try {
      await createAutomation(buildPayload(form));
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const running = automation?.running_since != null;
  const eventDriven = form.schedule.kind === "message" || form.schedule.kind === "event";
  const channel = automation
    ? (sessions.find((sx) => sx.origin_automation_id === automation.task_id) ?? null)
    : null;
  // The footer switch owns the auto-approve fact — only handler-specific
  // trust labels (read-only / retention / learns context) earn a badge.
  const rawTrust = automation ? automationTrustLabel(automation) : null;
  const trustLabel = rawTrust === "auto-approve" ? null : rawTrust;
  const rulerRuns = useMemo(() => (runs ? rulerRunsFrom(runs) : []), [runs]);
  const stats = useMemo(() => (runs ? statsFor(runs, eventDriven) : null), [runs, eventDriven]);
  const valid =
    form.prompt.trim().length > 0 &&
    (form.schedule.kind !== "message" || splitKeywords(form.schedule.channel).length > 0);
  /** Message triggers act on untrusted external input. Without a sender gate,
   *  anyone who can post to the channel can drive a full-tool unattended run. */
  const unsafeAutoApprove =
    form.schedule.kind === "message" &&
    form.auto_approve &&
    form.schedule.fromUser.trim().length === 0;

  const openRun = (run: AutomationRun) => {
    setMarkdownView({
      title: form.name || "Automation",
      subtitle: `run · ${runStamp(run.started_at)}`,
      content:
        run.status === "failed"
          ? `**Failed** — ${run.error ?? "no error recorded"}`
          : (run.result ?? "_No output._"),
    });
  };

  const ledger = (runs ?? []).slice(0, 5);

  return (
    <div className="grid grid-rows-[minmax(0,1fr)_auto] min-h-0 h-full">
      <div className="min-h-0 overflow-y-auto scroll-thin px-7 pt-5 pb-2">
        {/* header */}
        <div className="flex items-center gap-2.5">
          <input
            value={form.name}
            onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
            onBlur={blurSave}
            placeholder="Untitled automation"
            spellCheck={false}
            readOnly={builtin}
            autoFocus={seed.kind === "draft" && !seed.preset}
            aria-label="Automation name"
            className="flex-1 min-w-0 h-8 bg-transparent border-0 text-xl font-semibold tracking-[-0.014em] text-ink outline-none placeholder:text-muted read-only:text-muted"
          />
          {running && <Badge tone="accent">running</Badge>}
          {automation && isChannelAutomation(automation) && (
            <Badge tone="neutral" leading={<Radio size={9} strokeWidth={2.2} />}>
              channel
            </Badge>
          )}
          {trustLabel && automation && (
            <Badge tone={automationTrustTone(automation)}>{trustLabel}</Badge>
          )}
          {channel && (
            <Tooltip label="Open channel">
              <IconButton
                tone="faint"
                aria-label="Open channel"
                onClick={() => {
                  void switchSession(channel.session_id);
                  onClose();
                }}
              >
                <Radio size={ICON.MD} strokeWidth={2} />
              </IconButton>
            </Tooltip>
          )}
          {automation && !builtin && (
            <ConfirmDeleteButton
              label="Delete automation"
              onConfirm={() => void deleteAutomation(automation.task_id)}
            />
          )}
          <IconButton onClick={onClose} aria-label="Close">
            <X size={ICON.SM} strokeWidth={2} />
          </IconButton>
        </div>

        {/* the instrument */}
        {automation && (
          <div className="mt-5 rounded-[10px] bg-surface-sunken/70 px-[18px] pt-4 pb-3">
            <div className="flex items-baseline justify-between mb-2.5">
              <span className="text-2xs font-medium uppercase tracking-[0.08em] text-faint">
                {eventDriven ? "Fires" : "Runs"} — {spanLabel(rulerSpanMs(rulerRuns))}
              </span>
              {rulerRuns.length > 0 && (
                <span className="font-mono tabular-nums text-xs text-ink">
                  {rulerRuns[rulerRuns.length - 1].label}
                  <span className="text-faint">
                    {" — "}
                    {rulerRuns[rulerRuns.length - 1].ok ? "completed" : "failed"}
                    {rulerRuns[rulerRuns.length - 1].duration
                      ? ` in ${rulerRuns[rulerRuns.length - 1].duration}`
                      : ""}
                  </span>
                </span>
              )}
            </div>
            {runs === null ? (
              <Skeleton height={64} radius={8} />
            ) : (
              <RunRuler
                runs={rulerRuns}
                nextRunAt={automation.enabled ? automation.next_run_at : null}
                paused={!automation.enabled}
                waitingLabel={eventDriven ? "waiting on next match" : "not scheduled"}
                onHoverRun={setHoverRunId}
                onOpenRun={openRun}
              />
            )}
            {stats && stats.total > 0 && (
              <div className="flex gap-5 mt-2.5 flex-wrap">
                <span className="text-2xs text-faint">
                  {stats.eventDriven ? "fires " : "success "}
                  <span className="font-mono tabular-nums text-xs text-ink-soft">
                    {stats.ok} / {stats.total}
                  </span>
                </span>
                {stats.median && (
                  <span className="text-2xs text-faint">
                    median{" "}
                    <span className="font-mono tabular-nums text-xs text-ink-soft">
                      {stats.median}
                    </span>
                  </span>
                )}
                <span className="text-2xs text-faint">
                  {automation.enabled
                    ? automation.next_run_at
                      ? <>next <span className="font-mono tabular-nums text-xs text-ink-soft">{formatRelative(automation.next_run_at)}</span></>
                      : stats.eventDriven
                        ? "waiting on next match"
                        : "not scheduled"
                    : "paused"}
                </span>
              </div>
            )}
          </div>
        )}

        {/* prompt — the textarea grows to its content (no inner scroll that
            would swallow the wheel); ShowMore clamps long prompts behind a
            fade + toggle instead. Drafts skip the clamp: writing wants the
            full field. */}
        <div className="mt-5">
          <SectionLabel>Prompt</SectionLabel>
          {(() => {
            const field = (
              <textarea
                value={form.prompt}
                onChange={(e) => setForm((p) => ({ ...p, prompt: e.target.value }))}
                onBlur={blurSave}
                placeholder="What should the agent do when this automation fires?"
                spellCheck={false}
                rows={seed.kind === "draft" ? 8 : 3}
                readOnly={builtin}
                className="w-full resize-none field-sizing-content bg-transparent border-0 text-md leading-[1.6] tracking-[-0.005em] text-ink outline-none placeholder:text-muted read-only:text-muted"
              />
            );
            // Clamp budget: header + instrument + this + five ledger rows must
            // fit the pane WITHOUT scrolling — the collapsed state is the
            // overview, and an overview that scrolls isn't one.
            return seed.kind === "draft" ? field : <ShowMore collapsedHeight={144}>{field}</ShowMore>;
          })()}
          {seed.kind === "draft" && !form.prompt.trim() && (
            <div className="mt-4">
              <SectionLabel>Start from a template</SectionLabel>
              <div className="flex flex-wrap gap-1.5">
                {TEMPLATES.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => setForm(formFromPreset(t.payload))}
                    className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-[7px] bg-surface-soft text-sm text-ink-soft transition-colors duration-check hover:bg-fill-hover hover:text-ink"
                  >
                    <t.icon size={13} strokeWidth={2} className="text-faint" />
                    {t.name}
                  </button>
                ))}
              </div>
            </div>
          )}
          {builtin && (
            <p className="m-0 text-xs text-faint">
              What this runs is code-owned; when it runs is yours — adjust the schedule, pause, or
              run now.
            </p>
          )}
        </div>

        {/* recent runs ledger */}
        {automation && ledger.length > 0 && (
          <div className="mt-5">
            <SectionLabel aux="click to open">Recent runs</SectionLabel>
            {/* Subgrid: time and duration columns hug their widest content
                (no fixed-width voids), yet stay aligned across rows. */}
            <div className="grid grid-cols-[auto_auto_minmax(0,1fr)] gap-x-3 gap-y-px">
              {ledger.map((run) => {
                const failed = run.status === "failed";
                return (
                  <button
                    key={run.id}
                    type="button"
                    onClick={() => openRun(run)}
                    className={clsx(
                      "grid grid-cols-subgrid col-span-3 items-center h-7 px-2 -mx-2 rounded-[6px] text-left",
                      "transition-colors duration-check hover:bg-fill-hover",
                      hoverRunId === run.id && "bg-fill-hover",
                    )}
                  >
                    <span className="font-mono tabular-nums text-xs text-muted whitespace-nowrap">
                      {runStamp(run.started_at)}
                    </span>
                    <span className="font-mono tabular-nums text-xs text-faint text-right">
                      {runDuration(run)}
                    </span>
                    <span
                      className={clsx(
                        "text-sm truncate",
                        failed ? "text-bad" : "text-ink-soft",
                      )}
                    >
                      {runSummary(run)}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        <AnimatePresence initial={false}>
          {unsafeAutoApprove && (
            <Callout key="unsafe" tone="warn" icon={TriangleAlert} className="mt-4">
              Auto-Approve is on with no <strong className="font-semibold">From user</strong> gate.
              Anyone who can post to this channel can drive a full-tool, unattended run. Set a
              sender, or turn Auto-Approve off.
            </Callout>
          )}
          {error && (
            <Callout key="save-error" tone="bad" title="Couldn't save" className="mt-4">
              {error}
            </Callout>
          )}
        </AnimatePresence>
      </div>

      {/* the dials */}
      <footer className="flex items-center gap-2 px-4 py-2.5 bg-surface-soft/40">
        <ScheduleChip
          schedule={form.schedule}
          onChange={(schedule) =>
            seed.kind === "draft"
              ? setForm((p) => ({ ...p, schedule }))
              : setAndPersist({ schedule })
          }
        />
        <AutomationModelPicker
          model={form.model}
          onChange={(model) =>
            seed.kind === "draft" ? setForm((p) => ({ ...p, model })) : setAndPersist({ model })
          }
        />
        <Tooltip label="Runs execute without asking for approval first.">
          <div
            className="inline-flex items-center gap-1.5 px-1 select-none"
            onClick={(e) => {
              if ((e.target as HTMLElement).closest("button")) return;
              const next = !form.auto_approve;
              seed.kind === "draft"
                ? setForm((p) => ({ ...p, auto_approve: next }))
                : setAndPersist({ auto_approve: next });
            }}
          >
            <SwitchControl
              size="sm"
              checked={form.auto_approve}
              onChange={(next) =>
                seed.kind === "draft"
                  ? setForm((p) => ({ ...p, auto_approve: next }))
                  : setAndPersist({ auto_approve: next })
              }
              aria-label="Auto-Approve"
            />
            <span className="text-sm text-muted">Auto-Approve</span>
          </div>
        </Tooltip>
        <div className="flex-1" />
        {seed.kind === "draft" ? (
          <>
            <Button variant="ghost" onClick={onCreated} disabled={busy === "save"}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => void create()}
              disabled={!valid || busy === "save"}
              className="min-w-[72px]"
            >
              {busy === "save" ? "Creating…" : "Create"}
            </Button>
          </>
        ) : (
          <>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => automation && void toggleAutomation(automation.task_id)}
          >
            {automation?.enabled ? "Pause" : "Resume"}
          </Button>
          <Button
            variant="primary"
            size="sm"
            leadingIcon={Play}
            disabled={!automation?.enabled || running || busy === "run"}
            onClick={async () => {
              if (!taskId || busy) return;
              setBusy("run");
              try {
                await runAutomation(taskId);
              } finally {
                setBusy(null);
              }
            }}
          >
            Run now
          </Button>
          </>
        )}
      </footer>
    </div>
  );
}

/** Model dial: the composer's picker over the server model list, with a
 *  leading "session default" pseudo-entry that maps to model=null.
 *
 *  Reasoning effort rides the same per-model config map the composer writes
 *  (`model_reasoning_efforts`) — automation runs resolve their effort from
 *  that map server-side (config.reasoning_effort_for), so there is no
 *  per-automation effort field to invent. The effort column only appears
 *  once a concrete model is chosen. */
const DEFAULT_MODEL_LABEL = "session default";

function AutomationModelPicker({
  model,
  onChange,
}: {
  model: string | null;
  onChange: (model: string | null) => void;
}) {
  const serverModels = useStore((s) => s.serverModels);
  const cfg = useStore((s) => s.serverConfig);
  const groups = useMemo(() => {
    const real = serverModels
      ? serverModels.groups.length > 0
        ? serverModels.groups
        : [{ provider: "models", models: serverModels.models }]
      : [];
    return [{ provider: "default", models: [DEFAULT_MODEL_LABEL] }, ...real];
  }, [serverModels]);

  const modelReasoningEfforts = cfg?.model_reasoning_efforts ?? {};
  const efforts = model
    ? (serverModels?.reasoning_efforts?.[model] ?? cfg?.reasoning_efforts ?? [])
    : [];
  const currentEffort = model
    ? (modelReasoningEfforts[model] ?? cfg?.reasoning_effort ?? null)
    : null;

  return (
    <ModelReasoningPicker
      currentModel={model ?? DEFAULT_MODEL_LABEL}
      currentEffort={currentEffort}
      efforts={efforts}
      groups={groups}
      onSelectModel={(m) => onChange(m === DEFAULT_MODEL_LABEL ? null : m)}
      onSelectEffort={(effort) => {
        if (!model) return;
        void updateServerConfig({ reasoning_model: model, reasoning_effort: effort }).catch(() =>
          fetchServerConfig(),
        );
      }}
    />
  );
}
