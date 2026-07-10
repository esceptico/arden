import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { useStore } from "@/stores";
import { createAreaPage, fetchAreaDetail, replyToAsk, updateAreaAutonomy } from "@/actions/areas";
import { runAutomation } from "@/actions/automations";
import { createSession, goToNewSessionHome, switchSession } from "@/actions/sessions";
import { sendMessage } from "@/actions/messages";
import { Bot, Eye, FilePlus2, Zap } from "lucide-react";
import { AskCard } from "@/features/areas/components/AskCard";
import { AgentPresence, type AgentInfo } from "@/features/areas/components/AgentPresence";
import { AgentStatusLine, AreaSettingsButton } from "@/features/areas/components/AreaControls";
import { OpenLoops } from "@/features/areas/components/OpenLoops";
import { AreaActivity } from "@/features/areas/components/AreaActivity";
import { AreaWork } from "@/features/areas/components/AreaWork";
import { ScrollFadeTop, ScrollFadeBottom } from "@/components/ui/ScrollBlur";
import { RISE_IN, RISE_SETTLED, DISSOLVE_OUT, withExit, EXIT_ROW, MOTION, EASE_DECELERATE } from "@/lib/tokens/motion";

/** Area room: the full-screen detail view for one area, opened from the
 *  Home areas strip / focus rows / related chips (`openArea(key)` — same
 *  store-mediated slot Task 10 wired for Home vs Chat). Renders in App.tsx
 *  wherever `openAreaKey` is set, ahead of the Home/Chat branch.
 *
 *  Layout: back link → title + autonomy control → last-activity line →
 *  top ask's AskCard → OpenLoops → AreaActivity → RELATED chips → a
 *  scoped composer that provisions an area-tagged session on first send. */
export function AreaRoom({ areaKey }: { areaKey: string }) {
  const detail = useStore((s) => s.areas.detailByKey[areaKey]);
  const overviewAreas = useStore((s) => s.areas.overview?.areas);
  const openArea = useStore((s) => s.openArea);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [setupBusy, setSetupBusy] = useState(false);
  const [replyingTo, setReplyingTo] = useState<string | null>(null);

  useEffect(() => {
    void fetchAreaDetail(areaKey);
  }, [areaKey]);

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
          onClick={() => goToNewSessionHome()}
          className="justify-self-start text-sm text-faint hover:text-ink-soft"
        >
          ← Home
        </button>
          <p className="text-sm text-faint">Loading…</p>
        </div>
      </motion.div>
    );
  }

  const relatedTitle = (key: string) => overviewAreas?.find((s) => s.key === key)?.title ?? key;

  // The area's standing agent (an automation keyed `area:{key}`), rendered
  // as a first-class presence (AgentPresence) rather than a footnote. Its
  // channel session is excluded from ACTIVITY — that list is the user's own
  // chats, not infrastructure.
  const agentAuto = detail.automations.find(
    (a): a is AgentInfo & { task_id: string } =>
      typeof a === "object" && a !== null && (a as { task_id?: string }).task_id === `area:${areaKey}`,
  );
  const agentChannelId = agentAuto?.thread_id ?? null;
  const userSessions = detail.sessions.filter((s) => s.session_id !== agentChannelId);

  const isEmpty =
    detail.open_loops.length === 0 && detail.asks.length === 0 && userSessions.length === 0
    && detail.work.outcomes.length === 0 && detail.work.work_items.length === 0;

  const runAgentNow = async () => {
    if (!agentAuto?.task_id) return;
    try {
      await runAutomation(agentAuto.task_id);
      await fetchAreaDetail(areaKey);
    } catch {
      useStore.getState().pushToast({
        id: `area-run-fail:${areaKey}`,
        title: "Couldn’t start the agent",
        status: "failed",
        target: { kind: "automation" },
      });
    }
  };

  const discussAsk = (ask: { id: string; text: string }) => {
    setReplyingTo(ask.id);
    setDraft("");
    document.getElementById("area-composer-input")?.focus();
  };

  const grantAct = async () => {
    try {
      await updateAreaAutonomy(areaKey, "act");
    } catch {
      useStore.getState().pushToast({
        id: `area-autonomy-fail:${areaKey}`,
        title: "Couldn’t grant act autonomy",
        status: "failed",
        target: { kind: "automation" },
      });
      await fetchAreaDetail(areaKey);
    }
  };

  const revokeAct = async () => {
    try {
      await updateAreaAutonomy(areaKey, "observe");
    } catch {
      useStore.getState().pushToast({
        id: `area-autonomy-fail:${areaKey}`,
        title: "Couldn’t revoke act autonomy",
        status: "failed",
        target: { kind: "automation" },
      });
      await fetchAreaDetail(areaKey);
    }
  };

  const setupPage = async () => {
    if (setupBusy) return;
    setSetupBusy(true);
    try {
      await createAreaPage(areaKey);
    } catch {
      useStore.getState().pushToast({
        id: `area-page-fail:${areaKey}`,
        title: "Couldn’t create the Area page",
        status: "failed",
        target: { kind: "automation" },
      });
    } finally {
      setSetupBusy(false);
    }
  };

  const delegate = async () => {
    if (setupBusy) return;
    setSetupBusy(true);
    try {
      await updateAreaAutonomy(areaKey, "observe");
      await fetchAreaDetail(areaKey);
    } catch {
      useStore.getState().pushToast({
        id: `area-delegate-fail:${areaKey}`,
        title: "Couldn’t delegate this Area",
        status: "failed",
        target: { kind: "automation" },
      });
    } finally {
      setSetupBusy(false);
    }
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setDraft("");
    try {
      if (replyingTo) {
        await replyToAsk(areaKey, replyingTo, text);
        setReplyingTo(null);
        return;
      }
      // areaKey IS the area_id (unification) — filing is a plain
      // create-in-area; the room's sessions list keys off area_id.
      await createSession(areaKey);
      await sendMessage(text);
    } catch {
      setDraft(text);
      useStore.getState().pushToast({
        id: `area-send-fail:${areaKey}`,
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
        onClick={() => goToNewSessionHome()}
        className="justify-self-start text-sm text-faint hover:text-ink-soft"
      >
        ← Home
      </button>

      <div className="grid gap-1.5">
        {/* The agent's permission dial, a clear pill beside the title: Eye =
            observe (reads + updates the page only), Zap = act (may run this
            area's automations/workflows). A plain click toggles — the
            "irreversible steps still ask you" contract is the safety net, so
            no hold-to-arm ceremony. Absent entirely on plain containers
            (autonomy null = no standing agent to dial). */}
        <div className="flex min-w-0 items-center gap-3">
          <h1 className="m-0 min-w-0 truncate text-2xl font-medium tracking-[-0.015em] text-ink">
            {detail.title}
          </h1>
          {detail.page_path === null ? (
            <button
              type="button"
              disabled={setupBusy}
              onClick={() => void setupPage()}
              className="shrink-0 inline-flex h-7 items-center gap-1.5 rounded-full border border-line-soft px-3 text-xs font-medium text-muted hover:border-line-strong hover:text-ink disabled:opacity-50"
            >
              <FilePlus2 size={13} strokeWidth={2} />
              Create page
            </button>
          ) : detail.autonomy === null ? (
            <button
              type="button"
              disabled={setupBusy}
              onClick={() => void delegate()}
              className="shrink-0 inline-flex h-7 items-center gap-1.5 rounded-full border border-line-soft px-3 text-xs font-medium text-muted hover:border-line-strong hover:text-ink disabled:opacity-50"
            >
              <Bot size={13} strokeWidth={2} />
              Delegate
            </button>
          ) : detail.autonomy === "observe" ? (
            <button
              type="button"
              onClick={() => void grantAct()}
              title="The agent reads connected sources and updates this Area’s page, but takes no outward action. Click to let it run automations owned by this Area; consequential steps still require approval."
              className="shrink-0 self-center inline-flex h-7 items-center gap-1.5 rounded-full border border-line-soft px-3 text-xs font-medium text-muted transition-[color,border-color,scale] duration-check ease-out hover:border-line-strong hover:text-ink active:scale-[0.97]"
            >
              <Eye size={13} strokeWidth={2} />
              Observing
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void revokeAct()}
              title="The agent can run child automations owned by this Area; consequential steps still require approval. Click to return it to observe-only."
              className="shrink-0 self-center inline-flex h-7 items-center gap-1.5 rounded-full border border-accent-soft px-3 text-xs font-medium text-accent transition-[opacity,scale] duration-check ease-out hover:opacity-80 active:scale-[0.97]"
            >
              <Zap size={13} strokeWidth={2} />
              Acting
            </button>
          )}
          {detail.autonomy !== null && <AreaSettingsButton detail={detail} />}
        </div>
        <AgentStatusLine detail={detail} />
      </div>

      {agentAuto && (
        <AgentPresence
          agent={agentAuto}
          onRunNow={() => void runAgentNow()}
          onOpenChannel={() => agentChannelId && void switchSession(agentChannelId)}
        />
      )}

      {detail.asks.length > 0 && (
        /* Every active ask gets its card — a run nominates up to three, each
           with its own kind-typed affordances. */
        <div className="grid gap-2">
          <AnimatePresence initial={false}>
            {detail.asks.map((ask) => (
              <AskCard key={ask.id} ask={ask} onDiscuss={discussAsk} />
            ))}
          </AnimatePresence>
        </div>
      )}
        </div>

        {/* The only scroller: the area's reference material. */}
        <div className="relative mt-6 min-h-0 flex-1 overflow-y-auto overflow-x-hidden scroll-thin">
          <ScrollFadeTop />
          <ScrollFadeBottom />
          <div className="grid content-start gap-6 pb-1">
            {isEmpty && (
              <p className="m-0 text-sm text-faint">
                {detail.page_path
                  ? "Nothing needs attention yet — start a conversation below or delegate the Area."
                  : "This Area has conversations but no shared page yet. Create one when the domain needs durable context."}
              </p>
            )}

            <AreaWork areaKey={areaKey} work={detail.work} />

            <OpenLoops
              loops={detail.open_loops}
              onDiscuss={(loop) => {
                setDraft(`About the open loop "${loop}" — `);
                document.getElementById("area-composer-input")?.focus();
              }}
            />
            <AreaActivity sessions={userSessions} />

            {detail.related.length > 0 && (
              <div className="grid gap-2">
                <span className="text-2xs font-semibold tracking-wide text-faint uppercase">Related</span>
                <div className="flex flex-wrap gap-1.5">
                  {detail.related.map((key) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => openArea(key)}
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
              id="area-composer-input"
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
              placeholder={replyingTo ? "Reply to the Custodian…" : `Message in ${detail.title}…`}
              disabled={sending}
              className="min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none disabled:opacity-60"
            />
          </div>
        </div>
      </div>
    </motion.div>
  );
}
