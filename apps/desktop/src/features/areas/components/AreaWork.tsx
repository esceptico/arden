import { useMemo, useState } from "react";
import { Check, Pause, Pencil, Play, Plus, X } from "lucide-react";
import type { AreaOutcome, AreaWorkItem, AreaWorkSnapshot } from "@/api/areas";
import { createAreaOutcome, updateAreaOutcome, updateAreaWorkItem } from "@/actions/areas";
import { useStore } from "@/stores";

function stableKey(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 80);
}

function ActionButton({ label, onClick, children }: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="inline-grid size-6 place-items-center rounded-md text-faint transition-colors duration-check hover:bg-surface-soft hover:text-ink"
    >
      {children}
    </button>
  );
}

export function AreaWork({ areaKey, work }: { areaKey: string; work: AreaWorkSnapshot }) {
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [criteria, setCriteria] = useState("");
  const [busy, setBusy] = useState(false);

  const outcomes = useMemo(
    () => [...work.outcomes].sort((a, b) => b.priority - a.priority),
    [work.outcomes],
  );
  const primary = outcomes.find((outcome) => outcome.status === "active" || outcome.status === "paused");
  const activeItems = work.work_items.filter((item) => item.status === "active" || item.status === "in_progress");
  const current = activeItems.find((item) => item.status === "in_progress") ?? activeItems.find((item) => item.kind !== "blocker");
  const blockers = activeItems.filter((item) => item.kind === "blocker");
  const remaining = activeItems.filter((item) => item.item_id !== current?.item_id && item.kind !== "blocker");

  const fail = (message: string) => {
    useStore.getState().pushToast({
      id: `area-work-fail:${areaKey}:${Date.now()}`,
      title: message,
      status: "failed",
      target: { kind: "automation" },
    });
  };

  const mutate = async (operation: () => Promise<void>, error: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await operation();
    } catch {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  const submitNew = async (event: React.FormEvent) => {
    event.preventDefault();
    const cleanTitle = title.trim();
    const cleanCriteria = criteria.trim();
    const key = stableKey(cleanTitle);
    if (!key || !cleanCriteria) return;
    await mutate(async () => {
      await createAreaOutcome(areaKey, {
        key, title: cleanTitle, success_criteria: cleanCriteria, priority: 3,
      });
      setAdding(false);
      setTitle("");
      setCriteria("");
    }, "Couldn’t create the outcome");
  };

  const changeOutcomeStatus = (outcome: AreaOutcome, status: AreaOutcome["status"]) => {
    void mutate(
      () => updateAreaOutcome(areaKey, outcome.stable_key, {
        expected_updated_at: outcome.updated_at, status,
      }),
      "Couldn’t update the outcome",
    );
  };

  const saveTitle = (outcome: AreaOutcome) => {
    const next = title.trim();
    if (!next || next === outcome.title) {
      setEditing(null);
      return;
    }
    void mutate(async () => {
      await updateAreaOutcome(areaKey, outcome.stable_key, {
        expected_updated_at: outcome.updated_at, title: next,
      });
      setEditing(null);
    }, "Couldn’t rename the outcome");
  };

  return (
    <section aria-label="Area work" className="grid gap-3">
      <div className="flex items-center justify-between">
        <span className="text-2xs font-semibold uppercase tracking-wide text-faint">Work</span>
        <button
          type="button"
          onClick={() => setAdding((value) => !value)}
          className="inline-flex items-center gap-1 text-xs text-muted transition-colors hover:text-ink"
        >
          <Plus size={12} /> Add outcome
        </button>
      </div>

      {adding && (
        <form onSubmit={(event) => void submitNew(event)} className="grid gap-2 rounded-lg bg-surface-soft/50 p-3">
          <input
            name="outcome-title"
            autoFocus
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Outcome"
            className="bg-transparent text-sm text-ink outline-none placeholder:text-faint"
          />
          <input
            name="success-criteria"
            value={criteria}
            onChange={(event) => setCriteria(event.target.value)}
            placeholder="What does done look like?"
            className="bg-transparent text-xs text-ink-soft outline-none placeholder:text-faint"
          />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setAdding(false)} className="text-xs text-faint hover:text-ink">Cancel</button>
            <button type="submit" disabled={busy} className="text-xs font-medium text-accent disabled:opacity-50">Create</button>
          </div>
        </form>
      )}

      {primary ? (
        <div className="grid gap-1.5">
          <div className="flex min-w-0 items-center gap-2">
            {editing === primary.stable_key ? (
              <input
                autoFocus
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                onBlur={() => saveTitle(primary)}
                onKeyDown={(event) => event.key === "Enter" && saveTitle(primary)}
                className="min-w-0 flex-1 rounded-md bg-surface-soft px-2 py-1 text-sm font-medium text-ink outline-none"
              />
            ) : (
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">{primary.title}</span>
            )}
            <div className="flex shrink-0 items-center" aria-disabled={busy}>
              <ActionButton label={`Edit outcome ${primary.title}`} onClick={() => { setEditing(primary.stable_key); setTitle(primary.title); }}>
                <Pencil size={12} />
              </ActionButton>
              {primary.status === "paused" ? (
                <ActionButton label={`Resume outcome ${primary.title}`} onClick={() => changeOutcomeStatus(primary, "active")}><Play size={12} /></ActionButton>
              ) : (
                <ActionButton label={`Pause outcome ${primary.title}`} onClick={() => changeOutcomeStatus(primary, "paused")}><Pause size={12} /></ActionButton>
              )}
              <ActionButton label={`Complete outcome ${primary.title}`} onClick={() => changeOutcomeStatus(primary, "completed")}><Check size={13} /></ActionButton>
              <ActionButton label={`Cancel outcome ${primary.title}`} onClick={() => changeOutcomeStatus(primary, "cancelled")}><X size={13} /></ActionButton>
            </div>
          </div>
          <p className="m-0 text-xs leading-relaxed text-muted">Done when {primary.success_criteria}</p>
        </div>
      ) : outcomes.length > 0 ? (
        <p className="m-0 text-xs text-faint">No active outcome.</p>
      ) : !adding ? (
        <p className="m-0 text-xs text-faint">Give the Custodian an outcome to own.</p>
      ) : null}

      {current && <WorkRow areaKey={areaKey} item={current} label="Now" />}
      {blockers.map((item) => <WorkRow key={item.item_id} areaKey={areaKey} item={item} label="Needs you" />)}
      {remaining.length > 0 && (
        <details className="text-xs text-muted">
          <summary className="cursor-pointer select-none text-faint hover:text-muted">{remaining.length} more</summary>
          <div className="mt-2 grid gap-2 pl-2">
            {remaining.map((item) => <WorkRow key={item.item_id} areaKey={areaKey} item={item} />)}
          </div>
        </details>
      )}
    </section>
  );
}

function WorkRow({ areaKey, item, label }: { areaKey: string; item: AreaWorkItem; label?: string }) {
  const complete = () => void updateAreaWorkItem(areaKey, item.stable_key, {
    expected_updated_at: item.updated_at,
    status: "completed",
  });
  return (
    <div className="flex min-w-0 items-center gap-2 rounded-md bg-surface-soft/35 px-2.5 py-2">
      {label && <span className={item.kind === "blocker" ? "shrink-0 text-xs font-medium text-warn" : "shrink-0 text-xs text-faint"}>{label}</span>}
      <span className="min-w-0 flex-1 truncate text-xs text-ink-soft">{item.text}</span>
      {item.kind !== "blocker" && (
        <ActionButton label={`Complete work ${item.text}`} onClick={complete}><Check size={12} /></ActionButton>
      )}
    </div>
  );
}
