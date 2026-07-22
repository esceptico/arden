import { useEffect, useMemo, useRef, useState } from "react";
import { CalendarClock, Plus, X } from "@/components/icons";
import { useStore } from "@/stores";
import { fetchAutomations, fetchAutomationSuggestions } from "@/actions/automations";
import type { CreateAutomationPayload } from "@/api/types";
import { suggestionToPayload } from "@/api/automations";
import { isIterationLoop } from "@/lib/automationFilters";
import { AutomationRail, groupAutomations } from "@/features/automations/components/AutomationRail";
import { AutomationDetail, type DetailSeed } from "@/features/automations/components/AutomationDetail";
import { NewAutomationMenu } from "@/features/automations/components/NewAutomationMenu";
import { Button } from "@/components/ui/Button";
import { Empty } from "@/components/ui/EmptyState";
import { IconButton } from "@/components/ui/IconButton";
import { PageModal } from "@/components/ui/PageModal";
import { ICON } from "@/lib/icons";

/** Automations as a master-detail instrument panel: one rail of everything
 *  (yours / area agents / system), one detail pane where the selected
 *  automation is read, edited, and trusted — no tabs, no stacked editor
 *  modal. Templates and suggestions live in the New menu. */
export function AutomationsModal() {
  const open = useStore((s) => s.automationsOpen);
  const close = useStore((s) => s.closeAutomations);
  const origin = useStore((s) => s.modalOrigin);
  const allAutomations = useStore((s) => s.automations);
  const targetId = useStore((s) => s.automationTargetId);
  const clearTarget = useStore((s) => s.clearAutomationTarget);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<CreateAutomationPayload | null | false>(false);
  // Bumped per draft so re-seeding (picking another suggestion/template)
  // remounts the detail pane with the new preset — a constant "draft" key
  // would keep the first draft's form state alive.
  const [draftNonce, setDraftNonce] = useState(0);
  const [newOpen, setNewOpen] = useState(false);
  const newBtnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    void fetchAutomations();
    void fetchAutomationSuggestions();
  }, [open]);

  // Loops surface in the composer chip; the automations window shows the
  // scheduled/triggered population.
  const automations = useMemo(
    () => (allAutomations ? allAutomations.filter((a) => !isIterationLoop(a)) : null),
    [allAutomations],
  );

  const selected = useMemo(
    () => automations?.find((a) => a.task_id === selectedId) ?? null,
    [automations, selectedId],
  );

  useEffect(() => {
    if (!open || !targetId || !automations?.some((automation) => automation.task_id === targetId)) return;
    setDraft(false);
    setSelectedId(targetId);
    clearTarget();
  }, [automations, clearTarget, open, targetId]);

  // Default selection: first of the user's own, else first of anything.
  useEffect(() => {
    if (!open || draft !== false || !automations || automations.length === 0) return;
    if (selected) return;
    const groups = groupAutomations(automations);
    const first = groups.user[0] ?? groups.area[0] ?? groups.system[0];
    if (first) setSelectedId(first.task_id);
  }, [open, automations, selected, draft]);

  // Reset transient state when the window closes.
  useEffect(() => {
    if (open) return;
    setDraft(false);
    setNewOpen(false);
  }, [open]);

  const startDraft = (preset: CreateAutomationPayload | null) => {
    setDraft(preset);
    setDraftNonce((n) => n + 1);
    setSelectedId(null);
  };

  const seed: DetailSeed | null =
    draft !== false
      ? { kind: "draft", preset: draft }
      : selected
        ? { kind: "existing", automation: selected }
        : null;

  return (
    <PageModal
      open={open}
      onClose={close}
      origin={origin}
      ariaLabel="Automations"
      size="w-[min(1120px,calc(100vw-32px))] h-[min(720px,calc(100vh-32px))] sm:w-[min(1120px,calc(100vw-80px))] sm:h-[min(720px,calc(100vh-80px))]"
      grid="grid-rows-[minmax(0,1fr)]"
    >
      <div className="grid grid-cols-[280px_minmax(0,1fr)] min-h-0 h-full">
        {/* rail — anchored chrome, one ladder step below the pane */}
        <aside className="grid grid-rows-[auto_minmax(0,1fr)] min-h-0 bg-surface-sunken/50">
          <div className="flex items-center justify-between pl-[18px] pr-3.5 pt-4 pb-2.5">
            <h2 className="m-0 text-md font-semibold tracking-[-0.01em] text-ink">Automations</h2>
            <Button
              ref={newBtnRef}
              size="sm"
              leadingIcon={Plus}
              onClick={() => setNewOpen((v) => !v)}
            >
              New
            </Button>
          </div>
          <AutomationRail
            automations={automations}
            selectedId={draft === false ? selectedId : null}
            onSelect={(id) => {
              setDraft(false);
              setSelectedId(id);
            }}
            onPickSuggestion={(s) => startDraft(suggestionToPayload(s))}
          />
        </aside>

        {/* detail */}
        <section className="relative grid min-h-0">
          {!seed && (
            <div className="absolute top-3.5 right-3.5 z-[1]">
              <IconButton onClick={close} aria-label="Close">
                <X size={ICON.SM} strokeWidth={2} />
              </IconButton>
            </div>
          )}
          {seed ? (
            <AutomationDetail
              key={seed.kind === "existing" ? seed.automation.task_id : `draft-${draftNonce}`}
              seed={seed}
              onClose={close}
              onCreated={() => setDraft(false)}
            />
          ) : (
            <Empty
              icon={CalendarClock}
              hint="Start from a template, or write a prompt and a schedule from scratch."
              action={
                <div className="flex items-center gap-2">
                  <Button variant="secondary" size="md" onClick={() => setNewOpen(true)}>
                    Browse templates
                  </Button>
                  <Button variant="quiet" size="md" onClick={() => startDraft(null)}>
                    Start from scratch
                  </Button>
                </div>
              }
            >
              No automations yet.
            </Empty>
          )}
        </section>
      </div>

      <NewAutomationMenu
        open={newOpen}
        onClose={() => setNewOpen(false)}
        anchor={newBtnRef}
        onPick={startDraft}
      />
    </PageModal>
  );
}
