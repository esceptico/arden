import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { useStore } from "@/stores";
import { fetchSliceDetail, updateSliceAutonomy } from "@/actions/slices";
import { runAutomation } from "@/actions/automations";
import { createSession, switchSession } from "@/actions/sessions";
import { sendMessage } from "@/actions/messages";
import { Eye, Zap } from "lucide-react";
import { AskCard } from "@/features/slices/components/AskCard";
import { AgentPresence, type AgentInfo } from "@/features/slices/components/AgentPresence";
import { OpenLoops } from "@/features/slices/components/OpenLoops";
import { SliceActivity } from "@/features/slices/components/SliceActivity";
import { ScrollFadeTop, ScrollFadeBottom } from "@/components/ui/ScrollBlur";
import { RISE_IN, RISE_SETTLED, DISSOLVE_OUT, withExit, EXIT_ROW, MOTION, EASE_DECELERATE } from "@/lib/tokens/motion";

/** Slice room: the full-screen detail view for one slice, opened from the
 *  Home slices strip / focus rows / related chips (`openSlice(key)` — same
 *  store-mediated slot Task 10 wired for Home vs Chat). Renders in App.tsx
 *  wherever `openSliceKey` is set, ahead of the Home/Chat branch.
 *
 *  Layout: back link → title + autonomy control → last-activity line →
 *  top ask's AskCard → OpenLoops → SliceActivity → RELATED chips → a
 *  scoped composer that provisions a slice-tagged session on first send. */
export function SliceRoom({ sliceKey }: { sliceKey: string }) {
  const detail = useStore((s) => s.slices.detailByKey[sliceKey]);
  const overviewSlices = useStore((s) => s.slices.overview?.slices);
  const openSlice = useStore((s) => s.openSlice);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  useEffect(() => {
    void fetchSliceDetail(sliceKey);
  }, [sliceKey]);

  if (!detail) {
    return (
      <motion.div
        initial={RISE_IN}
        animate={RISE_SETTLED}
        exit={withExit(DISSOLVE_OUT, EXIT_ROW)}
        transition={{ duration: MOTION.trace, ease: EASE_DECELERATE }}
        /* absolute inset-0 like the loaded room: this branch is a crossfade
           surface too — in flow it would stack under the overlapping peer. */
        className="absolute inset-0 px-4 pt-[12vh]"
      >
        <div className="mx-auto grid w-[640px] max-w-full gap-6">
        <button
          type="button"
          onClick={() => openSlice(null)}
          className="justify-self-start text-sm text-faint hover:text-ink-soft"
        >
          ← Home
        </button>
          <p className="text-sm text-faint">Loading…</p>
        </div>
      </motion.div>
    );
  }

  const topAsk = detail.asks[0] ?? null;
  const relatedTitle = (key: string) => overviewSlices?.find((s) => s.key === key)?.title ?? key;

  // The slice's standing agent (an automation keyed `slice:{key}`), rendered
  // as a first-class presence (AgentPresence) rather than a footnote. Its
  // channel session is excluded from ACTIVITY — that list is the user's own
  // chats, not infrastructure.
  const agentAuto = detail.automations.find(
    (a): a is AgentInfo & { task_id: string } =>
      typeof a === "object" && a !== null && (a as { task_id?: string }).task_id === `slice:${sliceKey}`,
  );
  const agentChannelId = agentAuto?.thread_id ?? null;
  const userSessions = detail.sessions.filter((s) => s.session_id !== agentChannelId);

  const isEmpty =
    detail.open_loops.length === 0 && detail.asks.length === 0 && userSessions.length === 0;

  const runAgentNow = async () => {
    if (!agentAuto?.task_id) return;
    try {
      await runAutomation(agentAuto.task_id);
      await fetchSliceDetail(sliceKey);
    } catch {
      useStore.getState().pushToast({
        id: `slice-run-fail:${sliceKey}`,
        title: "Couldn’t start the agent",
        status: "failed",
        target: { kind: "automation" },
      });
    }
  };

  const discussAsk = (ask: { text: string }) => {
    setDraft(`About "${ask.text}" — `);
    document.getElementById("slice-composer-input")?.focus();
  };

  const grantAct = async () => {
    try {
      await updateSliceAutonomy(sliceKey, "act");
    } catch {
      useStore.getState().pushToast({
        id: `slice-autonomy-fail:${sliceKey}`,
        title: "Couldn’t grant act autonomy",
        status: "failed",
        target: { kind: "automation" },
      });
      await fetchSliceDetail(sliceKey);
    }
  };

  const revokeAct = async () => {
    try {
      await updateSliceAutonomy(sliceKey, "observe");
    } catch {
      useStore.getState().pushToast({
        id: `slice-autonomy-fail:${sliceKey}`,
        title: "Couldn’t revoke act autonomy",
        status: "failed",
        target: { kind: "automation" },
      });
      await fetchSliceDetail(sliceKey);
    }
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setDraft("");
    try {
      // sliceKey IS the project_id (unification) — filing is a plain
      // create-in-project; the room's sessions list keys off project_id.
      await createSession(sliceKey);
      await sendMessage(text);
    } catch {
      setDraft(text);
      useStore.getState().pushToast({
        id: `slice-send-fail:${sliceKey}`,
        title: "Couldn’t send message",
        status: "failed",
        target: { kind: "automation" },
      });
    } finally {
      setSending(false);
    }
  };

  return (
    <motion.div
      initial={RISE_IN}
      animate={RISE_SETTLED}
      exit={withExit(DISSOLVE_OUT, EXIT_ROW)}
      transition={{ duration: MOTION.trace, ease: EASE_DECELERATE }}
      /* absolute inset-0 (not h-full): mirrors Home — the crossfade mounts
         both surfaces, and popping/flow-stacking must never move the pinned
         composer. */
      className="absolute inset-0 overflow-hidden"
    >
      {/* Fixed-viewport column — the room never scrolls as a whole. The
          title, agent status, ask, and composer stay pinned; only the open
          loops / activity / related list scrolls internally, so what needs
          you (the ask) is always in view. overflow-x-hidden is the
          structural backstop against a child that loses its min-w-0. */}
      <div className="mx-auto flex h-full w-[640px] max-w-full flex-col px-4 pt-14 pb-6">
        <div className="grid shrink-0 gap-6">
      <button
        type="button"
        onClick={() => openSlice(null)}
        className="justify-self-start text-sm text-faint hover:text-ink-soft"
      >
        ← Home
      </button>

      <div className="grid gap-1.5">
        {/* The agent's permission dial, a clear pill beside the title: Eye =
            observe (reads + updates the page only), Zap = act (may run this
            slice's automations/workflows). A plain click toggles — the
            "irreversible steps still ask you" contract is the safety net, so
            no hold-to-arm ceremony. Absent entirely on plain containers
            (autonomy null = no standing agent to dial). */}
        <div className="flex min-w-0 items-center gap-3">
          <h1 className="m-0 min-w-0 truncate text-2xl font-medium tracking-[-0.015em] text-ink">
            {detail.title}
          </h1>
          {detail.autonomy === null ? null : detail.autonomy === "observe" ? (
            <button
              type="button"
              onClick={() => void grantAct()}
              title="The agent only reads this slice and updates its page — it takes no action on its own. Click to let it act: run this slice's automations and workflows. Irreversible steps still ask you first."
              className="shrink-0 self-center inline-flex h-7 items-center gap-1.5 rounded-full border border-line-soft px-3 text-xs font-medium text-muted transition-[color,border-color,scale] duration-check ease-out hover:border-line-strong hover:text-ink active:scale-[0.97]"
            >
              <Eye size={13} strokeWidth={2} />
              Observing
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void revokeAct()}
              title="The agent can run this slice's automations and workflows on its own. Click to return it to observe-only."
              className="shrink-0 self-center inline-flex h-7 items-center gap-1.5 rounded-full border border-accent-soft px-3 text-xs font-medium text-accent transition-[opacity,scale] duration-check ease-out hover:opacity-80 active:scale-[0.97]"
            >
              <Zap size={13} strokeWidth={2} />
              Acting
            </button>
          )}
        </div>
      </div>

      {agentAuto && (
        <AgentPresence
          agent={agentAuto}
          onRunNow={() => void runAgentNow()}
          onOpenChannel={() => agentChannelId && void switchSession(agentChannelId)}
        />
      )}

      {topAsk && (
        <AnimatePresence initial={false}>
          <AskCard key={topAsk.id} ask={topAsk} onDiscuss={discussAsk} />
        </AnimatePresence>
      )}
        </div>

        {/* The only scroller: the slice's reference material. */}
        <div className="relative mt-6 min-h-0 flex-1 overflow-y-auto overflow-x-hidden scroll-thin">
          <ScrollFadeTop />
          <ScrollFadeBottom />
          <div className="grid content-start gap-6 pb-1">
            {isEmpty && (
              <p className="m-0 text-sm text-faint">
                Nothing on file yet — message the slice below, or its agent will report after its next run.
              </p>
            )}

            <OpenLoops
              loops={detail.open_loops}
              onDiscuss={(loop) => {
                setDraft(`About the open loop "${loop}" — `);
                document.getElementById("slice-composer-input")?.focus();
              }}
            />
            <SliceActivity sessions={userSessions} />

            {detail.related.length > 0 && (
              <div className="grid gap-2">
                <span className="text-2xs font-semibold tracking-wide text-faint uppercase">Related</span>
                <div className="flex flex-wrap gap-1.5">
                  {detail.related.map((key) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => openSlice(key)}
                      className="rounded-full bg-surface-soft px-2.5 py-1 text-xs text-ink-soft transition-[color,scale] duration-check ease-out hover:text-ink active:scale-[0.97]"
                    >
                      {relatedTitle(key)}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="shrink-0 pt-3">
          <div className="flex h-[52px] w-full items-center gap-2 rounded-xl border border-line bg-surface-2 px-4 shadow-md">
            <input
              id="slice-composer-input"
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
              placeholder={`Message in ${detail.title}…`}
              disabled={sending}
              className="min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none disabled:opacity-60"
            />
          </div>
        </div>
      </div>
    </motion.div>
  );
}
