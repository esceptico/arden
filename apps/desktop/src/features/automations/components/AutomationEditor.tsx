import { useEffect, useState } from "react";
import { AnimatePresence } from "motion/react";
import { RotateCcw, TriangleAlert, X } from "lucide-react";
import { createAutomation, updateAutomation } from "@/actions/automations";
import type { UpdateAutomationPayload } from "@/api/types";
import { ICON } from "@/lib/icons";
import { PageModal } from "@/components/ui/PageModal";
import { Callout } from "@/components/ui/Callout";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { SwitchControl } from "@/components/ui/SwitchControl";
import { Tooltip } from "@/components/ui/Tooltip";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { ScheduleChip } from "@/features/automations/components/ScheduleChip";
import type { EditorSeed, FormState } from "@/features/automations/lib/schedule";
import {
  buildPayload,
  emptyForm,
  formFromAutomation,
  formFromPreset,
  splitKeywords,
} from "@/features/automations/lib/schedule";

export type { EditorSeed, FormState };
export { buildPayload, formFromPreset };

export function AutomationEditor({
  seed,
  onClose,
}: {
  seed: EditorSeed | null;
  onClose: () => void;
}) {
  const [form, setForm] = useState<FormState>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = !!seed;

  // (Re)hydrate the form whenever a new seed arrives.
  useEffect(() => {
    if (!seed) return;
    if (seed.kind === "edit") setForm(formFromAutomation(seed.automation));
    else if (seed.preset) setForm(formFromPreset(seed.preset));
    else setForm(emptyForm());
    setError(null);
  }, [seed]);

  // Escape is handled by PageModal; this only adds the Cmd/Ctrl+Enter submit.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void submit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Builtins run server-side handlers: what they do is code-owned (name and
  // prompt would be re-stamped at boot), but when they run is the user's dial.
  const builtin = seed?.kind === "edit" && seed.automation.builtin;
  const isMessage = form.schedule.kind === "message";
  const valid =
    form.prompt.trim().length > 0 &&
    (!isMessage || splitKeywords(form.schedule.channel).length > 0);
  /** Message triggers act on untrusted external input. Without a sender gate,
   *  anyone who can post to the channel can drive a full-tool unattended run. */
  const unsafeAutoApprove =
    isMessage && form.auto_approve && form.schedule.fromUser.trim().length === 0;

  const submit = async () => {
    if (!valid || saving || !seed) return;
    setSaving(true);
    setError(null);
    try {
      const payload = buildPayload(form);
      if (seed.kind === "edit") {
        const patch: UpdateAutomationPayload = { ...payload };
        await updateAutomation(seed.automation.task_id, patch);
      } else {
        await createAutomation(payload);
      }
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const reset = () => {
    if (!seed) return;
    if (seed.kind === "edit") setForm(formFromAutomation(seed.automation));
    else if (seed.preset) setForm(formFromPreset(seed.preset));
    else setForm(emptyForm());
  };

  return (
    <PageModal
      open={open}
      onClose={onClose}
      elevated
      size="w-[min(640px,calc(100vw-80px))] max-h-[calc(100vh-80px)]"
      grid="grid-rows-[auto_minmax(0,1fr)_auto]"
      ariaLabel="Automation editor"
    >
      {seed && (
        <>
          <header className="modal-header flex items-center justify-between gap-2">
            <input
              value={form.name}
              onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
              placeholder="Untitled automation"
              spellCheck={false}
              readOnly={builtin}
              autoFocus={seed.kind === "create" && !seed.preset}
              className="flex-1 min-w-0 h-7 bg-transparent border-0 text-lg font-semibold tracking-[-0.012em] text-ink outline-none placeholder:text-muted read-only:text-muted"
            />
            <div className="flex items-center gap-0.5 text-faint">
              <IconButton tone="faint" onClick={reset} title="Reset" aria-label="Reset">
                <RotateCcw size={ICON.MD} strokeWidth={2} />
              </IconButton>
              <IconButton tone="faint" onClick={onClose} title="Close" aria-label="Close">
                <X size={ICON.MD} strokeWidth={2} />
              </IconButton>
            </div>
          </header>

          <div className="px-5 pb-2 grid grid-rows-[minmax(0,1fr)] min-h-0">
            <textarea
              value={form.prompt}
              onChange={(e) => setForm((p) => ({ ...p, prompt: e.target.value }))}
              placeholder="What should the agent do when this automation fires?"
              spellCheck={false}
              rows={6}
              readOnly={builtin}
              className="w-full h-full min-h-[180px] resize-none bg-transparent border-0 text-md leading-[1.6] text-ink tracking-[-0.005em] outline-none placeholder:text-muted read-only:text-muted"
            />
          </div>

          <AnimatePresence initial={false}>
            {unsafeAutoApprove && (
              <Callout key="unsafe" tone="warn" icon={TriangleAlert} className="mx-5 mb-3">
                Auto-Approve is on with no <strong className="font-semibold">From user</strong> gate.
                Anyone who can post to this channel can drive a full-tool, unattended run. Set a
                sender, or turn Auto-Approve off.
              </Callout>
            )}

            {isMessage && (
              <Callout key="message-info" tone="neutral" className="mx-5 mb-3">
                To search a specific repo, move this automation's channel to the target area
                from the sidebar after it's created.
              </Callout>
            )}

            {error && (
              <Callout key="save-error" tone="bad" title="Couldn't save" className="mx-5 mb-3">
                {error}
              </Callout>
            )}
          </AnimatePresence>

          <footer className="flex items-center justify-between gap-2 px-3 py-2.5 bg-surface-soft/40">
            <div className="flex items-center gap-2">
              <ScheduleChip
                schedule={form.schedule}
                onChange={(schedule) => setForm((p) => ({ ...p, schedule }))}
              />
              {/* Help text lives in a hover tooltip, not a disclosure below the
                  switch — the old Collapse grew the whole footer when toggled on. */}
              <Tooltip label="Runs execute without asking for approval first.">
                <div
                  className="inline-flex items-center gap-1.5 px-1 select-none"
                  onClick={(e) => {
                    if ((e.target as HTMLElement).closest("button")) return;
                    setForm((p) => ({ ...p, auto_approve: !p.auto_approve }));
                  }}
                >
                  <SwitchControl
                    size="sm"
                    checked={form.auto_approve}
                    onChange={(next) => setForm((p) => ({ ...p, auto_approve: next }))}
                    aria-label="Auto-Approve"
                  />
                  <span className="text-sm text-muted">Auto-Approve</span>
                </div>
              </Tooltip>
            </div>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                onClick={onClose}
                disabled={saving}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={() => void submit()}
                disabled={!valid || saving}
                className="min-w-[72px]"
              >
                <BlurSwap swapKey={saving ? "saving" : seed.kind} blur={2}>
                  {saving ? "Saving…" : seed.kind === "edit" ? "Save" : "Create"}
                </BlurSwap>
              </Button>
            </div>
          </footer>
        </>
      )}
    </PageModal>
  );
}
