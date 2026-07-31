import { useEffect, useState } from "react";
import type { AreaAttention, AreaDetail, AreaInterrupts } from "@/api/areas";
import { useStore } from "@/stores";
import { createAreaPage, updateAreaAutonomy, updateAreaSettings } from "@/actions/areas";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";

/** AREA_ATTENTION_PRESETS, apps/server/arden/constants.py — mirrored so the
 *  control can state the cadence it is choosing rather than only naming it. */
export const ATTENTION_CADENCE: Record<AreaAttention, string> = {
  active: "Checks 2h–48h apart, up to 8 runs a day.",
  ambient: "Checks 12h–7d apart, up to 3 runs a day.",
  dormant: "Checks 3d–14d apart, at most 1 run a day.",
};

const INTERRUPT_REACH: Record<AreaInterrupts, string> = {
  none: "Nothing is pushed. Asks wait on Home.",
  asks: "Questions and proposed actions are pushed. FYIs wait on Home.",
  all: "Every ask is pushed, including FYIs.",
};

const AUTONOMY_CONTRACT = {
  off: "Nothing watches this area. Chats still file here.",
  observe:
    "Reads anything and edits this area’s page. Anything touching the outside world is proposed for your approval first.",
  act: "May also run child automations owned by this area. Their consequential actions still need approval.",
} as const;

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="area-settings__row">
      <span className="area-settings__row-label">
        <b>{label}</b>
        {hint ? <small>{hint}</small> : null}
      </span>
      {children}
    </div>
  );
}

/**
 * The custodian's operating contract, in the room rather than behind a trigger.
 * An area with no asks and no outcome is otherwise a blank page, and these are
 * the only things there are to decide about it.
 *
 * Settings apply live: no Save, no footer, per the action-placement contract.
 * Every control states the consequence it commits to rather than only naming
 * the field, because none of these have an obvious effect from the label.
 */
export function AreaSettings({
  area,
  pageOpen,
  onTogglePage,
}: {
  area: AreaDetail;
  /** The page preview is owned by the room: this section sits inside the
   *  scroller, whose scroll-fade mask would clip a peek rendered here. */
  pageOpen: boolean;
  onTogglePage: () => void;
}) {
  const pushToast = useStore((s) => s.pushToast);
  const [instructions, setInstructions] = useState(area.instructions ?? "");
  const [busy, setBusy] = useState(false);

  // Adopt the row whenever the area changes so a draft never leaks from the
  // area it was typed in.
  useEffect(() => {
    setInstructions(area.instructions ?? "");
  }, [area.key, area.instructions]);

  const fail = (what: string, error: unknown) =>
    pushToast({
      id: `area-settings-fail:${area.key}:${what}:${Date.now()}`,
      title: `Couldn’t ${what}`,
      detail: error instanceof Error ? error.message : undefined,
      status: "failed",
      target: { kind: "automation", taskId: `area:${area.key}` },
    });

  const run = async (what: string, action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    try {
      await action();
    } catch (error) {
      fail(what, error);
    } finally {
      setBusy(false);
    }
  };

  const autonomy = area.autonomy ?? "off";
  const attention = area.attention ?? "ambient";
  const interrupts = area.interrupts ?? "asks";

  const commitInstructions = () => {
    const next = instructions.trim() || null;
    if (next === (area.instructions ?? null)) return;
    void run("save instructions", () => updateAreaSettings(area.key, { instructions: next }));
  };

  return (
    <section className="area-settings" aria-labelledby="area-settings-title">
      <h2 id="area-settings-title">How this area is watched</h2>

      <div className="area-settings__section">
        <Row label="Custodian" hint="A standing agent that watches this area.">
          <Select
            aria-label="Custodian mode"
            value={autonomy}
            onChange={(next) =>
              void run("change the custodian", () =>
                updateAreaAutonomy(area.key, next === "off" ? null : (next as "observe" | "act")),
              )
            }
            options={[
              { value: "off", label: "Off" },
              { value: "observe", label: "Observe" },
              { value: "act", label: "Act" },
            ]}
            disabled={busy}
          />
        </Row>
        <p className="area-settings__consequence">{AUTONOMY_CONTRACT[autonomy]}</p>
      </div>

      {area.autonomy ? (
        <>
          <div className="area-settings__section">
            <Row label="Attention" hint="How hard it looks, and how often it may run.">
              <Select
                aria-label="Attention"
                value={attention}
                onChange={(next) =>
                  void run("change attention", () =>
                    updateAreaSettings(area.key, { attention: next as AreaAttention }),
                  )
                }
                options={[
                  { value: "dormant", label: "Dormant" },
                  { value: "ambient", label: "Ambient" },
                  { value: "active", label: "Active" },
                ]}
                disabled={busy}
              />
            </Row>
            <p className="area-settings__consequence">{ATTENTION_CADENCE[attention]}</p>
          </div>

          <div className="area-settings__section">
            <Row label="Interrupts" hint="Which of its asks reach you outside the app.">
              <Select
                aria-label="Interrupts"
                value={interrupts}
                onChange={(next) =>
                  void run("change interrupts", () =>
                    updateAreaSettings(area.key, { interrupts: next as AreaInterrupts }),
                  )
                }
                options={[
                  { value: "none", label: "None" },
                  { value: "asks", label: "Decisions" },
                  { value: "all", label: "Everything" },
                ]}
                disabled={busy}
              />
            </Row>
            <p className="area-settings__consequence">{INTERRUPT_REACH[interrupts]}</p>
          </div>

          <div className="area-settings__section area-settings__section--stacked">
            <span className="area-settings__row-label">
              <b>Standing instructions</b>
              <small>What you want from this area, in your words. Read on every run.</small>
            </span>
            <Textarea
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              onBlur={commitInstructions}
              rows={3}
              placeholder="e.g. Propose the next concrete step instead of asking me what I want. Skip anything I can’t act on this week."
              aria-label="Standing instructions"
            />
          </div>
        </>
      ) : null}

      <div className="area-settings__section">
        <Row
          label="Page"
          hint={
            area.page_path
              ? "What the custodian has written down about this area."
              : "The custodian has nowhere to write down what it learns."
          }
        >
          {area.page_path ? (
            <button
              type="button"
              className="area-settings__page-link"
              data-area-page-owner
              aria-expanded={pageOpen}
              onClick={onTogglePage}
            >
              {area.page_path}
            </button>
          ) : (
            <Button
              variant="secondary"
              size="sm"
              disabled={busy}
              onClick={() => void run("create the page", () => createAreaPage(area.key))}
            >
              Create page
            </Button>
          )}
        </Row>
      </div>

    </section>
  );
}
