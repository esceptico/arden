import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
} from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { ChevronDown, X } from "@/components/icons";
import type { AreaAsk, AreaBriefItem, AreasBrief } from "@/api/areas";
import type { Automation } from "@/api/types";
import { useStore } from "@/stores";
import { formatElapsed, formatOpenSince, formatRelativeFuture, formatRelativePastAgo } from "@/lib/format";
import { fetchAreasOverview, resolveAsk } from "@/actions/areas";
import { switchSession } from "@/actions/sessions";
import { FocusRow, type FocusRowKeyboardActions } from "@/features/home/components/FocusRow";
import type { HomeDeckShortcutActions } from "@/features/home/hooks/useHomeKeyboard";
import { IconButton } from "@/components/ui/IconButton";
import { RollingToken } from "@/components/ui/RollingToken";
import { TabPanels, useTabDirection } from "@/components/ui/TabPanels";
import { PeekSurface } from "@/components/workspace/PeekSurface";
import { ICON } from "@/lib/icons";
import {
  BLUR,
  CONTENT_ENTER_TRANSITION,
  DECK_ENTER_TRANSITION,
  DECK_EXIT_TRANSITION,
  DECK_VARIANTS,
  DISCLOSURE,
  DISTANCE,
  MOTION,
  TRACE_ROW_TRANSITION,
} from "@/lib/tokens/motion";

type AmbientKind = "working" | "scheduled" | "done" | "aside";

/** Type-level fallback only — the server already sends this as `area_title`
 *  for an ask that belongs to no area. */
const UNFILED_AREA_TITLE = "Chats";

interface AmbientItem {
  id: string;
  label: string;
  detail: string;
  meta?: string;
  tone?: "success" | "warning" | "danger";
  areaId?: string;
  /** Raw timestamp used to order the lane (soonest-next / freshest-first). */
  when?: string;
  onOpen: () => void;
}

interface SnoozedAsk {
  askId: string;
  /** null for an ask raised from a plain chat — it opens its session instead. */
  areaId: string | null;
  sessionId: string | null;
  areaTitle: string;
  text: string;
  wakeLabel: string;
}

const DECK_MOTION_VARIANTS = {
  enter: (direction: number) => DECK_VARIANTS.enter(direction),
  center: {
    ...DECK_VARIANTS.center,
    transition: DECK_ENTER_TRANSITION,
  },
  exit: (direction: number) => ({
    ...DECK_VARIANTS.exit(direction),
    transition: DECK_EXIT_TRANSITION,
  }),
};

function briefItem(item: AreaBriefItem, openArea: (key: string) => void, done = false): AmbientItem {
  const when = done ? item.completed_at ?? item.updated_at : item.updated_at;
  return {
    id: `${done ? "done" : "work"}:${item.area_id}:${item.stable_key}`,
    label: item.area_title,
    detail: item.outcome_title ? `${item.outcome_title} · ${item.text}` : item.text,
    // Open work is not running — "open 12h" says what the number means,
    // where "for 12h" (live effort) and "12h ago" (past event) both lie.
    meta: when ? (done ? formatRelativePastAgo(when) : formatOpenSince(when)) : undefined,
    tone: done ? "success" : undefined,
    areaId: item.area_id,
    when: when ?? undefined,
    onOpen: () => openArea(item.area_id),
  };
}

function automationItem(
  automation: Automation,
  openAutomations: (taskId: string, options?: { run: "latest" }) => void,
  state: "working" | "scheduled" | "done" | "aside",
): AmbientItem {
  const isFailed = automation.last_status === "failed";
  const detail = state === "working"
    ? "running"
    : state === "scheduled"
      ? automation.next_run_at ? `next ${formatRelativeFuture(automation.next_run_at)}` : "awaiting a schedule"
      : state === "done"
        ? "completed"
        : isFailed ? "failed — needs review" : "paused";
  const timestamp = state === "working"
    ? automation.running_since
    : state === "scheduled"
      ? automation.next_run_at
      : automation.last_run_at;
  return {
    id: `automation:${state}:${automation.task_id}`,
    label: automation.name,
    detail,
    meta: timestamp
      ? state === "scheduled"
        ? formatRelativeFuture(timestamp)
        : state === "working" ? formatElapsed(timestamp) : formatRelativePastAgo(timestamp)
      : undefined,
    tone: isFailed ? "danger" : state === "done" ? "success" : undefined,
    when: timestamp ?? undefined,
    // Finished work links to its result, not just the automation's settings.
    onOpen: () => openAutomations(
      automation.task_id,
      state === "done" || isFailed ? { run: "latest" } : undefined,
    ),
  };
}

/** ISO timestamps order lexicographically; missing ones sink to the end. */
function soonestFirst(a: AmbientItem, b: AmbientItem): number {
  return (a.when ?? "￿").localeCompare(b.when ?? "￿");
}

function freshestFirst(a: AmbientItem, b: AmbientItem): number {
  return (b.when ?? "").localeCompare(a.when ?? "");
}

function newestOf(items: AmbientItem[]): string | undefined {
  let max: string | undefined;
  for (const item of items) {
    if (item.when && (!max || item.when > max)) max = item.when;
  }
  return max;
}

/** Notification-center semantics without a cursor yet = nothing is "new";
 *  the baseline effect stamps first-seen quietly instead of a wall of new. */
function unseenCount(items: AmbientItem[], cursor: string | undefined): number {
  if (cursor === undefined) return 0;
  let count = 0;
  for (const item of items) {
    if (item.when && item.when > cursor) count += 1;
  }
  return count;
}

function AmbientStrip({
  kind,
  label,
  items,
  unseen,
  open,
  reflowKey,
  onToggle,
}: {
  kind: AmbientKind;
  label: string;
  items: AmbientItem[];
  unseen: number;
  open: boolean;
  reflowKey: string;
  onToggle: () => void;
}) {
  const hasItems = items.length > 0;
  const preview = hasItems ? items[0] : null;
  const prefix = preview ? previewPrefix(kind, preview) : null;

  return (
    <motion.div
      layout="position"
      layoutDependency={reflowKey}
      className="mission-control__strip"
      data-kind={kind}
      transition={DISCLOSURE.followers}
    >
      <button
        type="button"
        data-strip-trigger
        className="mission-control__strip-head arden-row"
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={!hasItems}
        onClick={onToggle}
      >
        <strong>{label}</strong>
        {preview && (
          <span className="mission-control__strip-preview">
            {prefix && <><em>{prefix}</em>{" "}</>}
            {preview.label}
            {preview.meta ? ` · ${preview.meta}` : ""}
          </span>
        )}
        {hasItems && (
          <span className="mission-control__strip-count" data-new={unseen > 0 || undefined}>
            {unseen > 0 ? `${unseen} new` : items.length}
          </span>
        )}
        {hasItems && <ChevronDown className="mission-control__strip-chevron" aria-hidden="true" />}
      </button>
    </motion.div>
  );
}

const STRIP_LABELS: Record<AmbientKind, string> = {
  working: "Working",
  scheduled: "Scheduled",
  done: "Done",
  aside: "Aside",
};

const STRIP_ORDER: readonly AmbientKind[] = ["working", "scheduled", "done", "aside"];

const STRIP_PHRASES: Record<AmbientKind, { some: string; none: string }> = {
  working: { some: "in motion", none: "nothing moving" },
  scheduled: { some: "queued", none: "nothing queued" },
  done: { some: "landed today", none: "nothing new" },
  aside: { some: "set aside", none: "nothing held" },
};

/* Each lane's preview answers a different question at a glance: what's
   moving, what happens next, what just landed, what's parked. */
const STRIP_PREVIEW_PREFIX: Record<AmbientKind, string> = {
  working: "now:",
  scheduled: "next:",
  done: "latest:",
  aside: "held:",
};

/** "now:" claims this instant, but a working item carries its own elapsed
 *  duration ("for 11h") — asserting both reads as a contradiction, so the
 *  duration speaks alone and "now:" only fills in for an undated item. */
function previewPrefix(kind: AmbientKind, item: AmbientItem): string | null {
  return kind === "working" && item.meta ? null : STRIP_PREVIEW_PREFIX[kind];
}

function stripSummary(kind: AmbientKind, count: number): string {
  const phrase = STRIP_PHRASES[kind];
  return count ? `${count} ${phrase.some}` : phrase.none;
}

/** Strip details live at click-depth in a floating peek — the desk itself
 *  never grows or scrolls, and the full list fits behind one head row. */
function AmbientPeek({
  kind,
  ambient,
  onClose,
}: {
  kind: AmbientKind | null;
  ambient: Record<AmbientKind, AmbientItem[]>;
  onClose: () => void;
}) {
  const retainedKind = useRef<AmbientKind | null>(kind);
  if (kind) retainedKind.current = kind;
  const visibleKind = retainedKind.current;
  // Moving down the strip order slides content up-and-out, and vice versa.
  const direction = useTabDirection(STRIP_ORDER, visibleKind ?? STRIP_ORDER[0]);
  if (!visibleKind) return null;
  const label = STRIP_LABELS[visibleKind];
  const items = ambient[visibleKind];

  return createPortal(
    <PeekSurface
      open={kind !== null}
      onClose={onClose}
      closeOnOutsidePointerDown
      outsidePointerDownIgnoreSelector="[data-strip-trigger]"
      ariaLabel={`${label} activity`}
      layer="home-ambient-peek"
      motionPreset="peek"
      className="mission-control__ambient-peek surface-peek fixed z-[var(--z-peek)] flex flex-col overflow-hidden"
    >
      <header className="mission-control__ambient-peek-head arden-peek-rule-below">
        {/* The title rides the same clock and variants as the list so the
            two halves of the swap read as one motion. */}
        <TabPanels
          value={visibleKind}
          direction={direction}
          axis="y"
          className="mission-control__ambient-peek-title"
        >
          <strong>{label}</strong>
          <span> — {stripSummary(visibleKind, items.length)}</span>
        </TabPanels>
        <IconButton
          data-peek-close
          onClick={onClose}
          aria-label={`Close ${label} activity`}
        >
          <X size={ICON.SM} />
        </IconButton>
      </header>
      <TabPanels
        value={visibleKind}
        direction={direction}
        axis="y"
        className="mission-control__ambient-peek-list scroll-thin scroll-fade"
      >
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            data-area-id={item.areaId}
            className="mission-control__ambient-row arden-row"
            onClick={() => {
              onClose();
              item.onOpen();
            }}
          >
            <span className="mission-control__ambient-row-name">{item.label}</span>
            <span className="mission-control__ambient-row-detail">{item.detail}</span>
            <span className="mission-control__ambient-row-meta" data-tone={item.tone}>{item.meta}</span>
          </button>
        ))}
      </TabPanels>
    </PeekSurface>,
    document.body,
  );
}

export function WorkBrief({
  brief,
  automations = [],
  stale = false,
  keyboardActionsRef,
}: {
  brief: AreasBrief;
  automations?: Automation[];
  stale?: boolean;
  keyboardActionsRef?: MutableRefObject<HomeDeckShortcutActions | null>;
}) {
  const openArea = useStore((state) => state.openArea);
  const openAutomations = useStore((state) => state.openAutomations);
  const pushToast = useStore((state) => state.pushToast);
  const ambientSeen = useStore((state) => state.prefs.ambientSeen);
  const setPref = useStore((state) => state.setPref);
  const [snoozed, setSnoozed] = useState<SnoozedAsk[]>([]);
  const [openStrip, setOpenStrip] = useState<AmbientKind | null>(null);
  const [deckDirection, setDeckDirection] = useState<-1 | 0 | 1>(0);
  const askIds = useMemo(() => brief.needs_you.map((ask) => ask.id), [brief.needs_you]);
  const [deckOrder, setDeckOrder] = useState(() => brief.needs_you.map((ask) => ask.id));
  const asksById = useMemo(
    () => new Map(brief.needs_you.map((ask) => [ask.id, ask])),
    [brief.needs_you],
  );
  const deck = useMemo(
    () => deckOrder.flatMap((id) => {
      const ask = asksById.get(id);
      return ask ? [ask] : [];
    }),
    [asksById, deckOrder],
  );
  const head = deck[0];
  const peers = deck.slice(1);
  const previousHeadId = useRef(head?.id);
  const [showZero, setShowZero] = useState(!head);
  const focusActionsRef = useRef<FocusRowKeyboardActions | null>(null);
  const lastCommittedRef = useRef<AreaAsk | null>(null);
  const undoingRef = useRef(false);
  const openStripRef = useRef<AmbientKind | null>(null);

  useEffect(() => {
    setDeckOrder((current) => {
      const present = new Set(askIds);
      const surviving = current.filter((id) => present.has(id));
      const additions = askIds.filter((id) => !surviving.includes(id));
      const next = [...surviving, ...additions];
      return next.length === current.length && next.every((id, index) => id === current[index])
        ? current
        : next;
    });
  }, [askIds]);

  useEffect(() => {
    openStripRef.current = openStrip;
  }, [openStrip]);

  useEffect(() => {
    setSnoozed((current) => current.filter((entry) => !brief.needs_you.some((ask) => ask.id === entry.askId)));
  }, [brief.needs_you]);

  useEffect(() => {
    const previous = previousHeadId.current;
    previousHeadId.current = head?.id;
    if (head) {
      setShowZero(false);
      if (previous !== head.id) {
        const frame = requestAnimationFrame(() => setDeckDirection(0));
        return () => cancelAnimationFrame(frame);
      }
      return;
    }
    if (!previous) {
      setShowZero(true);
      return;
    }
    setShowZero(false);
    const timer = window.setTimeout(
      () => setShowZero(true),
      (MOTION.deckExit + MOTION.deckHandoff) * 1000,
    );
    return () => window.clearTimeout(timer);
  }, [head]);

  const ambient = useMemo(() => {
    const runningAutomations = automations.filter((automation) => automation.running_since);
    const scheduledAutomations = automations.filter(
      (automation) => automation.enabled && !automation.running_since && automation.next_run_at,
    );
    const completedAutomations = automations.filter(
      (automation) => automation.last_status === "completed" && !automation.running_since,
    );
    const asideAutomations = automations.filter(
      (automation) => !automation.enabled || automation.last_status === "failed",
    );

    const openAutomation = (taskId: string, options?: { run: "latest" }) => openAutomations(taskId, options);
    return {
      working: [
        ...brief.in_progress.map((item) => briefItem(item, openArea)),
        ...runningAutomations.map((automation) => automationItem(automation, openAutomation, "working")),
      ].sort(freshestFirst),
      scheduled: scheduledAutomations
        .map((automation) => automationItem(automation, openAutomation, "scheduled"))
        .sort(soonestFirst),
      done: [
        ...brief.done.map((item) => briefItem(item, openArea, true)),
        ...completedAutomations.map((automation) => automationItem(automation, openAutomation, "done")),
      ].sort(freshestFirst),
      aside: [
        ...snoozed.map((entry) => ({
          id: `ask:aside:${entry.askId}`,
          label: entry.areaTitle,
          detail: entry.text,
          meta: `back ${entry.wakeLabel}`,
          tone: "warning" as const,
          areaId: entry.areaId ?? undefined,
          onOpen: () => {
            if (entry.areaId) openArea(entry.areaId);
            else if (entry.sessionId) void switchSession(entry.sessionId);
          },
        })),
        ...asideAutomations.map((automation) => automationItem(automation, openAutomation, "aside")),
      ],
    } satisfies Record<AmbientKind, AmbientItem[]>;
  }, [automations, brief.done, brief.in_progress, openArea, openAutomations, snoozed]);

  /* Unread cursors: a lane with no cursor yet baselines quietly to its
     newest item (first run is never a wall of "new"); an open lane keeps
     its cursor at the newest item, so closing it reads as caught up. */
  useEffect(() => {
    const next = { ...ambientSeen };
    let changed = false;
    for (const lane of STRIP_ORDER) {
      const newest = newestOf(ambient[lane]);
      if (!newest) continue;
      const missing = next[lane] === undefined;
      const openAndBehind = openStrip === lane && (next[lane] ?? "") < newest;
      if (missing || openAndBehind) {
        next[lane] = newest;
        changed = true;
      }
    }
    if (changed) setPref("ambientSeen", next);
  }, [ambient, ambientSeen, openStrip, setPref]);

  /* Layout followers glide only for the desk's own reflows (card swap, deck
     count, strip disclosure). Pure geometry shifts — sidebar dock/undock —
     are carried by WorkspaceStage alone; without this gate the followers
     FLIP a second time on their own curve and drift apart from the deck. */
  const reflowKey = [
    head?.id ?? "none",
    brief.needs_you.length,
    peers.length,
    showZero,
    ambient.working.length,
    ambient.scheduled.length,
    ambient.done.length,
    ambient.aside.length,
  ].join(":");

  const handleSnoozed = (ask: AreaAsk, wakeLabel: string) => {
    setSnoozed((current) => {
      if (current.some((entry) => entry.askId === ask.id)) return current;
      return [{
        askId: ask.id,
        areaId: ask.area_key,
        sessionId: ask.actions.find((action) => action.verb === "open_session")?.ref ?? null,
        areaTitle: ask.area_title ?? ask.area_key ?? UNFILED_AREA_TITLE,
        text: ask.text,
        wakeLabel,
      }, ...current];
    });
  };

  const restoreLastCommitted = () => {
    const ask = lastCommittedRef.current;
    if (!ask || undoingRef.current) return;
    undoingRef.current = true;
    void (async () => {
      try {
        // `active` is the canonical reversible ask state. A reply remains in
        // its agent session; this only returns the decision to the deck.
        await resolveAsk(ask.id, "active");
        lastCommittedRef.current = null;
      } catch {
        pushToast({
          id: `mission-control-undo-failed:${ask.id}`,
          title: "Couldn’t restore this request",
          status: "failed",
          target: { kind: "automation" },
        });
        void fetchAreasOverview();
      } finally {
        undoingRef.current = false;
      }
    })();
  };

  useLayoutEffect(() => {
    if (!keyboardActionsRef) return;
    const actions: HomeDeckShortcutActions = {
      activateSlot: (slot) => focusActionsRef.current?.activateSlot(slot),
      toggleSnooze: () => focusActionsRef.current?.toggleSnooze(),
      closeTransient: () => {
        if (focusActionsRef.current?.closeFoot()) return true;
        if (!openStripRef.current) return false;
        setOpenStrip(null);
        return true;
      },
      skip: () => {
        if (focusActionsRef.current?.footMode !== "actions") return;
        setDeckOrder((current) => current.length > 1 ? [...current.slice(1), current[0]] : current);
      },
      undo: restoreLastCommitted,
    };
    keyboardActionsRef.current = actions;
    return () => {
      if (keyboardActionsRef.current === actions) keyboardActionsRef.current = null;
    };
  }, [keyboardActionsRef]);

  return (
    <section className="mission-control__attention" aria-label="Attention desk" data-page-enter-item>
      <section className="mission-control__deck" data-region="deck" aria-label="Needs you">
        {brief.needs_you.length > 4 && (
          <p className="mission-control__capacity">More than a usual day — still one at a time.</p>
        )}
        <div className="mission-control__deck-slot">
          <div className="mission-control__deck-stack" aria-live="polite">
            <AnimatePresence initial={false} mode="popLayout" custom={deckDirection}>
              {head && (
                <motion.div
                  key={head.id}
                  custom={deckDirection}
                  variants={DECK_MOTION_VARIANTS}
                  initial="enter"
                  animate="center"
                  exit="exit"
                  className="mission-control__deck-card-motion"
                >
                  <FocusRow
                    ask={head}
                    areaTitle={head.area_title ?? head.area_key ?? UNFILED_AREA_TITLE}
                    expanded
                    onDeckDirection={setDeckDirection}
                    onSnoozed={handleSnoozed}
                    onCommitted={(ask) => {
                      lastCommittedRef.current = ask;
                    }}
                    keyboardActionsRef={focusActionsRef}
                  />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
          {/* The headline already covers the single-ask case in words; the
              position counter only exists while it counts a stack. */}
          {head && brief.needs_you.length > 1 && (
            <motion.div
              layout="position"
              layoutDependency={reflowKey}
              transition={{ layout: TRACE_ROW_TRANSITION }}
              className="mission-control__deck-chrome"
            >
              <span>
                <RollingToken value={`1 of ${brief.needs_you.length}`} />
              </span>
            </motion.div>
          )}
          <AnimatePresence initial={false}>
            {showZero && (
              <motion.p
                key="all-handled"
                className="mission-control__zero"
                initial={{
                  opacity: 0,
                  y: DISTANCE.dissolve,
                  filter: `blur(${BLUR.contentSwap}px)`,
                }}
                animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                exit={{ opacity: 0 }}
                transition={CONTENT_ENTER_TRANSITION}
              >
                {stale ? "No requests in the last successful check" : "All requests handled"}
              </motion.p>
            )}
          </AnimatePresence>
        </div>
        {peers.length > 0 && (
          <motion.div
            layout="position"
            layoutDependency={reflowKey}
            transition={{ layout: TRACE_ROW_TRANSITION }}
            className="mission-control__peers"
            aria-label="Waiting behind this one"
          >
            <AnimatePresence initial={false} mode="popLayout">
              {peers.slice(0, 3).map((ask) => (
                <FocusRow key={ask.id} ask={ask} areaTitle={ask.area_title ?? ask.area_key ?? UNFILED_AREA_TITLE} />
              ))}
            </AnimatePresence>
            {peers.length > 3 && <p>+{peers.length - 3} more waiting</p>}
          </motion.div>
        )}
      </section>

      <motion.section
        layout="position"
        layoutDependency={reflowKey}
        transition={{ layout: TRACE_ROW_TRANSITION }}
        className="mission-control__ambient"
        data-region="ambient"
        aria-labelledby="mission-control-activity"
      >
        <h2 id="mission-control-activity" className="arden-section-label">Agent activity</h2>
        <AmbientStrip
          reflowKey={reflowKey}
          kind="working"
          label="Working"
          items={ambient.working}
          unseen={unseenCount(ambient.working, ambientSeen.working)}
          open={openStrip === "working"}
          onToggle={() => setOpenStrip((current) => current === "working" ? null : "working")}
        />
        <AmbientStrip
          reflowKey={reflowKey}
          kind="scheduled"
          label="Scheduled"
          items={ambient.scheduled}
          unseen={unseenCount(ambient.scheduled, ambientSeen.scheduled)}
          open={openStrip === "scheduled"}
          onToggle={() => setOpenStrip((current) => current === "scheduled" ? null : "scheduled")}
        />
        <AmbientStrip
          reflowKey={reflowKey}
          kind="done"
          label="Done"
          items={ambient.done}
          unseen={unseenCount(ambient.done, ambientSeen.done)}
          open={openStrip === "done"}
          onToggle={() => setOpenStrip((current) => current === "done" ? null : "done")}
        />
        <AmbientStrip
          reflowKey={reflowKey}
          kind="aside"
          label="Aside"
          items={ambient.aside}
          unseen={unseenCount(ambient.aside, ambientSeen.aside)}
          open={openStrip === "aside"}
          onToggle={() => setOpenStrip((current) => current === "aside" ? null : "aside")}
        />
      </motion.section>
      <AmbientPeek kind={openStrip} ambient={ambient} onClose={() => setOpenStrip(null)} />
    </section>
  );
}
