import { useEffect, useLayoutEffect, useRef, useState, type MutableRefObject } from "react";
import { motion } from "motion/react";
import type { AreaAsk } from "@/api/areas";
import { useStore } from "@/stores";
import { formatRelativePast } from "@/lib/format";
import { ASK_KIND, splitAreaAsk } from "@/lib/areaKind";
import { fetchAreasOverview, replyToAsk, resolveAsk } from "@/actions/areas";
import { runAutomation } from "@/actions/automations";
import { switchSession } from "@/actions/sessions";
import { primaryActionFor } from "@/lib/askActions";
import type { HomeDeckSlot } from "@/features/home/hooks/useHomeKeyboard";
import { AskActionPeek } from "@/components/ui/AskActionPeek";
import {
  TRACE_ROW_ENTER,
  TRACE_ROW_EXIT,
  TRACE_ROW_TRANSITION,
  TRACE_ROW_VISIBLE,
} from "@/lib/tokens/motion";

const SNOOZE_OPTIONS = [
  { label: "In an hour", delay: 60 * 60_000 },
  { label: "Tonight", delay: 8 * 60 * 60_000 },
  { label: "Tomorrow", delay: 24 * 60 * 60_000 },
] as const;

interface FocusRowProps {
  ask: AreaAsk;
  areaTitle: string;
  expanded?: boolean;
  onDeckDirection?: (direction: -1 | 0 | 1) => void;
  onSnoozed?: (ask: AreaAsk, wakeLabel: string) => void;
  onCommitted?: (ask: AreaAsk) => void;
  keyboardActionsRef?: MutableRefObject<FocusRowKeyboardActions | null>;
}

export interface FocusRowKeyboardActions {
  activateSlot: (slot: HomeDeckSlot) => void;
  toggleSnooze: () => void;
  closeFoot: () => boolean;
  footMode: "actions" | "reply" | "snooze";
}

/** The head card keeps the ask actionable in-place; peer asks remain compact
 * rows so the desk only asks for one decision at a time. */
export function FocusRow({
  ask,
  areaTitle,
  expanded = false,
  onDeckDirection,
  onSnoozed,
  onCommitted,
  keyboardActionsRef,
}: FocusRowProps) {
  const openArea = useStore((s) => s.openArea);
  const automations = useStore((s) => s.automations);
  const pushToast = useStore((s) => s.pushToast);
  const kind = ASK_KIND[ask.kind];
  const age = formatRelativePast(ask.created_at);
  const { title, detail } = splitAreaAsk(ask.text);
  const [busy, setBusy] = useState(false);
  const [replyOpen, setReplyOpen] = useState(false);
  const [reply, setReply] = useState("");
  const [snoozeOpen, setSnoozeOpen] = useState(false);
  const [actionOpen, setActionOpen] = useState(false);
  const replyRef = useRef<HTMLInputElement>(null);
  const busyRef = useRef(false);
  const footMode = replyOpen ? "reply" : snoozeOpen ? "snooze" : "actions";
  const primaryAction = primaryActionFor(ask, automations, {
    switchSession: (sessionId) => void switchSession(sessionId),
    runAutomation: (taskId) => void runAutomation(taskId),
    openArea,
  });
  /** An unfiled ask has no area room to open — fall back to its own action. */
  const openContext = () => {
    if (ask.area_key) openArea(ask.area_key);
    else primaryAction?.run();
  };

  const mutate = async (run: () => Promise<void>, failureTitle: string): Promise<boolean> => {
    if (busyRef.current) return false;
    busyRef.current = true;
    setBusy(true);
    try {
      await run();
      await fetchAreasOverview();
      return true;
    } catch {
      pushToast({
        id: `mission-control-ask-failed:${ask.id}`,
        title: failureTitle,
        status: "failed",
        target: { kind: "automation" },
      });
      return false;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  const settle = async (state: "done" | "dismissed", resolution: string, failureTitle: string): Promise<boolean> => {
    const succeeded = await mutate(
      () => resolveAsk(ask.id, state, undefined, resolution),
      failureTitle,
    );
    if (succeeded) onCommitted?.(ask);
    return succeeded;
  };

  useEffect(() => {
    if (!replyOpen) return;
    setReply("");
    const timer = window.setTimeout(() => replyRef.current?.focus(), 30);
    return () => window.clearTimeout(timer);
  }, [replyOpen]);

  const sendReply = async () => {
    const text = reply.trim();
    if (!text) return;
    onDeckDirection?.(1);
    const succeeded = await mutate(() => replyToAsk(ask.id, text), "Couldn’t send the reply");
    if (succeeded) {
      setReply("");
      onCommitted?.(ask);
    }
  };

  const snooze = async (option: (typeof SNOOZE_OPTIONS)[number]) => {
    onDeckDirection?.(0);
    const succeeded = await mutate(
      () => resolveAsk(ask.id, "snoozed", new Date(Date.now() + option.delay).toISOString()),
      "Couldn’t set this aside",
    );
    if (succeeded) {
      onSnoozed?.(ask, option.label.toLowerCase());
      onCommitted?.(ask);
    }
  };

  const openReply = () => {
    if (busyRef.current) return;
    setSnoozeOpen(false);
    setReplyOpen(true);
  };

  const toggleSnooze = () => {
    if (busyRef.current) return;
    setReplyOpen(false);
    setSnoozeOpen((open) => !open);
  };

  const closeFoot = (): boolean => {
    if (!replyOpen && !snoozeOpen) return false;
    setReplyOpen(false);
    setSnoozeOpen(false);
    return true;
  };

  /* One list, so the number badges and the keyboard cannot disagree. They did:
   * a review showed Approve, Reject, Review, Later but numbered them 1, 2, —, 3,
   * because the open-the-area verb was rendered outside the slot scheme. */
  const verbs: Array<{ label: string; variant: "primary" | "quiet"; tone?: "danger"; run: () => void }> = [
    ask.kind === "review"
      ? {
          label: "Approve",
          variant: "primary",
          run: () => {
            onDeckDirection?.(1);
            void settle("done", "approved", "Couldn’t approve");
          },
        }
      : ask.kind === "question"
        ? { label: "Answer", variant: "primary", run: openReply }
        : {
            label: "Got it",
            variant: "primary",
            run: () => {
              onDeckDirection?.(1);
              void settle("done", "acknowledged", "Couldn’t clear this");
            },
          },
    ...(ask.kind === "review"
      ? [{
          label: "Reject",
          variant: "quiet" as const,
          tone: "danger" as const,
          run: () => {
            onDeckDirection?.(-1);
            void settle("dismissed", "rejected", "Couldn’t reject");
          },
        }]
      : []),
    {
      label: primaryAction?.label ?? "Open area",
      variant: "quiet",
      run: () => (primaryAction ? primaryAction.run() : openContext()),
    },
    { label: "Later", variant: "quiet", run: toggleSnooze },
  ];

  const activateSlot = (slot: HomeDeckSlot) => {
    if (busyRef.current) return;
    if (snoozeOpen) {
      const option = SNOOZE_OPTIONS[slot - 1];
      if (option) void snooze(option);
      return;
    }
    if (replyOpen) return;
    verbs[slot - 1]?.run();
  };

  useLayoutEffect(() => {
    if (!expanded || !keyboardActionsRef) return;
    const actions: FocusRowKeyboardActions = {
      activateSlot,
      toggleSnooze,
      closeFoot,
      footMode,
    };
    keyboardActionsRef.current = actions;
    return () => {
      if (keyboardActionsRef.current === actions) keyboardActionsRef.current = null;
    };
  }, [activateSlot, closeFoot, expanded, footMode, keyboardActionsRef, toggleSnooze]);

  if (!expanded) {
    return (
      <motion.button
        layout="position"
        type="button"
        data-area-id={ask.area_key ?? undefined}
        onClick={openContext}
        initial={TRACE_ROW_ENTER}
        animate={TRACE_ROW_VISIBLE}
        exit={TRACE_ROW_EXIT}
        transition={TRACE_ROW_TRANSITION}
        className="mission-control__peer arden-row"
      >
        <span className="mission-control__peer-title">{title}</span>
        <span className="mission-control__peer-meta arden-attention-meta" data-columns="3">
          <span>{kind.label.toLowerCase()}</span>
          <span className="arden-attention-meta__separator" aria-hidden="true">·</span>
          <span>{areaTitle}</span>
          <span className="arden-attention-meta__separator" aria-hidden="true">·</span>
          <time dateTime={ask.created_at}>{age} ago</time>
        </span>
      </motion.button>
    );
  }

  return (
    <article
      data-area-id={ask.area_key ?? undefined}
      className="mission-control__focus"
    >
      <p className="mission-control__focus-context">
        <button type="button" onClick={openContext}>{areaTitle}</button>
        <span aria-hidden>·</span>
        <span>{kind.label.toLowerCase()}</span>
        <time dateTime={ask.created_at}>{age} ago</time>
      </p>
      <h2>{title}</h2>
      {(ask.why_now || detail) && <p className="mission-control__focus-reason">{ask.why_now ?? detail}</p>}
      {ask.what_next && <p className="mission-control__focus-next">{ask.what_next}</p>}
      {ask.action && (
        <>
          <button
            type="button"
            className="mission-control__focus-action"
            data-ask-action-owner
            aria-haspopup="dialog"
            aria-expanded={actionOpen}
            onClick={() => setActionOpen(true)}
          >
            <span>Approving runs</span>
            <em>{ask.action}</em>
            <span className="mission-control__focus-action-more">Read it</span>
          </button>
          <AskActionPeek
            action={ask.action}
            title={title}
            open={actionOpen}
            busy={busy}
            onApprove={() => {
              setActionOpen(false);
              onDeckDirection?.(1);
              void settle("done", "approved", "Couldn’t approve");
            }}
            onClose={() => setActionOpen(false)}
          />
        </>
      )}

      <div className="mission-control__focus-foot">
        <div
          className="mission-control__focus-actions"
          data-io-hidden={footMode === "actions" ? undefined : ""}
          aria-hidden={footMode === "actions" ? undefined : true}
          inert={footMode === "actions" ? undefined : true}
        >
          {verbs.map((verb, index) => (
            <button
              key={verb.label}
              type="button"
              className={verb.label === "Later" ? "mission-control__later arden-button" : "arden-button"}
              data-variant={verb.variant}
              data-tone={verb.tone}
              disabled={busy}
              aria-expanded={verb.label === "Later" ? snoozeOpen : undefined}
              onClick={() => activateSlot((index + 1) as HomeDeckSlot)}
            >
              <span className="mission-control__verb-key" aria-hidden>{index + 1}</span>
              {verb.label}
            </button>
          ))}
        </div>

        <form
          className="mission-control__reply"
          data-io-hidden={footMode === "reply" ? undefined : ""}
          aria-hidden={footMode === "reply" ? undefined : true}
          inert={footMode === "reply" ? undefined : true}
          onSubmit={(event) => {
            event.preventDefault();
            void sendReply();
          }}
        >
          <input
            ref={replyRef}
            className="arden-field"
            value={reply}
            onChange={(event) => setReply(event.target.value)}
            placeholder="Your answer"
            aria-label="Reply to this request"
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                setReplyOpen(false);
              }
            }}
          />
          <span className="mission-control__reply-hint">↵ send · esc back</span>
        </form>

        <div
          className="mission-control__snooze"
          aria-label="Set aside until"
          data-io-hidden={footMode === "snooze" ? undefined : ""}
          aria-hidden={footMode === "snooze" ? undefined : true}
          inert={footMode === "snooze" ? undefined : true}
        >
          <span>Back when?</span>
          {SNOOZE_OPTIONS.map((option) => (
            <button
              key={option.label}
              type="button"
              className="arden-button"
              data-variant="quiet"
              disabled={busy}
              onClick={() => void snooze(option)}
            >
              <span className="mission-control__verb-key" aria-hidden>{SNOOZE_OPTIONS.indexOf(option) + 1}</span>
              {option.label}
            </button>
          ))}
        </div>
      </div>
    </article>
  );
}
