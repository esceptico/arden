import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Box } from "@/components/icons";
import clsx from "clsx";
import { useShallow } from "zustand/react/shallow";
import { selectSentUserMessages, useStore } from "@/stores";
import { viewSkill } from "@/actions/skills";
import {
  cancelQueuedMessage,
  enqueueMessage,
  sendMessage,
  steerMessage,
  stopRun,
} from "@/actions/messages";
import { respondToAllApprovals } from "@/actions/approvals";
import { isBuiltin, runBuiltinCommand } from "@/actions/builtins";
import { toggleAuto } from "@/actions/loops";
import { QueueCard } from "@/features/chat/components/QueueCard";
import { CommandPicker } from "@/features/chat/components/CommandPicker";
import { GoalProposalCard } from "@/features/chat/components/GoalProposalCard";
import { ComposerEditingBanner } from "@/features/chat/components/ComposerEditingBanner";
import { ComposerImageStrip } from "@/features/chat/components/ComposerImageStrip";
import { ComposerToolbar, type ComposerAction } from "@/features/chat/components/ComposerToolbar";
import { WorkingStrip } from "@/features/chat/components/WorkingStrip";
import { useListNav } from "@/lib/hooks";
import { ICON } from "@/lib/icons";
import { RISE_IN, RISE_SETTLED } from "@/lib/tokens/motion";
import { workingLabel } from "@/features/chat/lib/workingLabel";
import { filterCommands, useCommandList, type CommandEntry } from "@/features/chat/lib/commands";
import { SECTION_ENTER, SECTION_EXIT } from "@/features/chat/lib/composerMotion";
import { fileToImageBlock, pickerQuery, resize } from "@/features/chat/lib/composerHelpers";
import { recallHistory } from "@/features/chat/lib/composerHistory";
import { QUEUE_MAX_ITEMS } from "@/features/chat/lib/queue";

export function Composer() {
  const draft = useStore((s) => s.draft);
  const setDraft = useStore((s) => s.setDraft);
  const running = useStore((s) => s.running);
  const connected = useStore((s) => s.connected);
  const thinkingIntensity = useStore((s) => s.prefs.thinkingIntensity);
  const pendingApprovalCount = useStore((s) => s.pendingApprovals.length);
  const editingId = useStore((s) => s.editingId);
  const setEditingId = useStore((s) => s.setEditingId);
  const skipApprovals = useStore((s) => s.skipApprovals);
  const pickerOpen = useStore((s) => s.commandPickerOpen);
  const pickerIndex = useStore((s) => s.commandPickerIndex);
  const setPickerOpen = useStore((s) => s.setCommandPickerOpen);
  const setPickerIndex = useStore((s) => s.setCommandPickerIndex);
  const skills = useStore((s) => s.skills);
  const selectedSkill = useStore((s) => s.selectedSkill);
  const setSelectedSkill = useStore((s) => s.setSelectedSkill);
  const pendingImages = useStore((s) => s.pendingImages);
  const addPendingImages = useStore((s) => s.addPendingImages);
  const removePendingImage = useStore((s) => s.removePendingImage);
  const clearPendingImages = useStore((s) => s.clearPendingImages);
  const pendingGoalProposal = useStore((s) => s.pendingGoalProposal);
  const queuedCount = useStore((s) => s.queuedMessages.length);
  const queueFull = queuedCount >= QUEUE_MAX_ITEMS;
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Readline-style recall over the session's sent messages. `historyIndex` is
  // null when not browsing; `stashedDraft` keeps the in-progress text so
  // ArrowDown past the newest entry restores it. Resetting historyIndex (on
  // typing or send) re-stashes on the next ArrowUp.
  const sentMessages = useStore(useShallow(selectSentUserMessages));
  const [historyIndex, setHistoryIndex] = useState<number | null>(null);
  const stashedDraftRef = useRef("");

  const query = useMemo(() => pickerQuery(draft), [draft]);
  const allCommands = useCommandList();
  const filteredCommands = useMemo(
    () => (query !== null ? filterCommands(allCommands, query) : []),
    [allCommands, query],
  );

  const pickerNav = useListNav(
    filteredCommands.length,
    (i) => {
      const entry = filteredCommands[i];
      if (entry) applyPickerSelection(entry);
    },
    { index: pickerIndex, setIndex: setPickerIndex },
  );

  // Track the query for which the user explicitly dismissed the picker (via
  // Escape). The auto-open effect honors this until the query changes.
  const dismissedQueryRef = useRef<string | null>(null);

  // Keep picker open state in sync with the textarea contents.
  useEffect(() => {
    if (query === null) {
      dismissedQueryRef.current = null;
      if (pickerOpen) setPickerOpen(false);
      return;
    }
    if (query === dismissedQueryRef.current) {
      if (pickerOpen) setPickerOpen(false);
      return;
    }
    if (filteredCommands.length === 0) {
      if (pickerOpen) setPickerOpen(false);
      return;
    }
    if (!pickerOpen) setPickerOpen(true);
  }, [query, filteredCommands.length, pickerOpen, setPickerOpen]);

  const hasDraft = draft.trim().length > 0;
  const hasContent = hasDraft || Boolean(selectedSkill) || pendingImages.length > 0;
  // While a run is in flight, submit enqueues onto the active run instead
  // of being blocked. Disable only when disconnected or there's nothing
  // to send.
  const disabled = !connected || !hasContent;
  const composerAction: ComposerAction = running
    ? hasContent
      ? "queue"
      : "stop"
    : "send";

  // Keep the waiting affordance on the surface that produced the action. The
  // strip stays up for the whole run, not just the pre-first-token gap: once
  // it can name the running tool, hiding it the moment output starts would
  // drop the readout exactly when there is something to read.
  const messages = useStore((s) => s.messages);
  const currentRunId = useStore((s) => s.currentRunId);
  const thinkingRunId = useStore((s) => s.thinkingRunId);
  const activeActivityId = useStore((s) => s.activeActivityId);
  const serverThinking = Boolean(thinkingRunId && (!currentRunId || thinkingRunId === currentRunId));
  const workingNow = running || serverThinking;
  // 350ms threshold — fast replies (cached, small models, short tool
  // chains) shouldn't briefly flash the indicator. If the run finishes
  // within the threshold, workingNow flips false before the timer fires
  // and the strip never appears. This is the "spinner only when actually
  // slow" pattern from ChatGPT/Cursor.
  const [showWorking, setShowWorking] = useState(false);
  useEffect(() => {
    if (!workingNow) {
      setShowWorking(false);
      return;
    }
    const id = window.setTimeout(() => setShowWorking(true), 350);
    return () => window.clearTimeout(id);
  }, [workingNow]);
  const activityMessage = activeActivityId ? messages.get(activeActivityId) : null;
  const stripLabel = useMemo(() => workingLabel(activityMessage), [activityMessage]);

  // Layout effect so the height is set before paint — an effect-timed
  // resize lets a multi-line programmatic draft (history recall, edit)
  // paint one frame at the stale height.
  useLayoutEffect(() => {
    if (inputRef.current) resize(inputRef.current);
  }, [draft]);

  const [dragOver, setDragOver] = useState(false);

  function dispatchCommand(text: string): boolean {
    // If the text is a slash-command, route it. Returns true if handled.
    if (!text.startsWith("/")) return false;
    const [head, ...rest] = text.slice(1).split(" ");
    const args = rest.join(" ").trim();
    if (isBuiltin(head)) {
      void runBuiltinCommand(head, args);
      return true;
    }
    return false; // skill or unknown — let sendMessage forward to server
  }

  function applyPickerSelection(entry: CommandEntry) {
    setPickerOpen(false);
    setDraft("");
    if (inputRef.current) {
      inputRef.current.value = "";
      resize(inputRef.current);
    }

    if (entry.kind === "builtin") {
      // Builtins fire-and-forget.
      void runBuiltinCommand(entry.name, "");
      return;
    }

    // Skills attach as a pill above the textarea so the user can type a
    // prompt under the skill before sending. Submit assembles
    // `/<skill-name> <prompt>` and the server's expand_skill_command does
    // the substitution.
    const skill = skills.find((s) => s.name === entry.name);
    if (skill) setSelectedSkill(skill);
    requestAnimationFrame(() => inputRef.current?.focus());
  }

  function submit(delivery: "queue" | "steer" = "queue") {
    const text = draft;
    const skill = selectedSkill;
    const images = pendingImages;
    if (!text.trim() && !skill && images.length === 0) return;
    if (running && delivery === "queue" && queueFull) return;

    setHistoryIndex(null);
    setDraft("");
    setSelectedSkill(null);
    clearPendingImages();
    if (inputRef.current) {
      inputRef.current.value = "";
      resize(inputRef.current);
    }
    setPickerOpen(false);

    const trimmed = text.trim();

    // Pending approvals + a typed draft → reject all and enqueue the
    // text as a user message. The rejection itself uses the default
    // feedback ("User rejected this action"); the user's actual
    // wording lands in chat as a real message so it's visible and
    // persists in history. Agent sees both: rejected tool results
    // followed by the user's next message in the conversation.
    if (pendingApprovalCount > 0 && trimmed) {
      void respondToAllApprovals(false);
      void (delivery === "steer" ? steerMessage(trimmed, images) : enqueueMessage(trimmed, images));
      return;
    }

    // Pure builtin (no skill, no images) — route to the dispatcher.
    if (!skill && images.length === 0 && dispatchCommand(text)) return;

    const fullText = skill
      ? trimmed.length > 0
        ? `/${skill.name} ${trimmed}`
        : `/${skill.name}`
      : text;
    if (running) {
      void (delivery === "steer" ? steerMessage(fullText, images) : enqueueMessage(fullText, images));
      inputRef.current?.focus({ preventScroll: true });
    } else {
      void sendMessage(fullText, images);
    }
  }

  function cancelEdit() {
    setEditingId(null);
    setDraft("");
    if (inputRef.current) {
      inputRef.current.value = "";
      resize(inputRef.current);
    }
  }

  const editQueuedMessage = useCallback((message: import("@/stores").QueuedMessage) => {
    cancelQueuedMessage(message.clientId);
    setDraft(message.text);
    clearPendingImages();
    if (message.images?.length) addPendingImages(message.images);
    requestAnimationFrame(() => inputRef.current?.focus({ preventScroll: true }));
  }, [addPendingImages, clearPendingImages, setDraft]);

  async function attachFiles(fileList: FileList | File[] | null) {
    if (!fileList) return;
    const files = Array.from(fileList).filter((f) => f.type.startsWith("image/"));
    if (files.length === 0) return;
    const blocks = await Promise.all(files.map(fileToImageBlock));
    addPendingImages(blocks);
  }

  return (
    <div className="board-composer-stack">
      <AnimatePresence initial={false}>
        {pendingGoalProposal && (
          <GoalProposalCard key="goal-proposal" objective={pendingGoalProposal.objective} />
        )}
      </AnimatePresence>
      {/* Wrapper exists so the CommandPicker can sit as a sibling of
          the form rather than a child and avoid being clipped by the
          composer panel. */}
      <div className="board-composer-wrap relative max-w-[760px] mx-auto">
        <QueueCard onEdit={editQueuedMessage} />
        {pickerOpen && query !== null && (
          <CommandPicker query={query} onSelect={applyPickerSelection} />
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          onDragOver={(e) => {
            if (!Array.from(e.dataTransfer.types).includes("Files")) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
            setDragOver(true);
          }}
          onDragLeave={(e) => {
            // dragleave fires when moving onto a child; only clear when the
            // pointer actually left the form.
            if (e.relatedTarget instanceof Node && e.currentTarget.contains(e.relatedTarget)) return;
            setDragOver(false);
          }}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            void attachFiles(Array.from(e.dataTransfer.files));
          }}
          className={clsx(
            "board-composer surface-panel surface-radius-md relative flex flex-col",
            showWorking && "is-working",
            dragOver && "is-drop-target",
          )}
          data-thinking-intensity={thinkingIntensity}
          data-thinking-state={showWorking ? "waiting" : "idle"}
        >
          <WorkingStrip active={showWorking} label={stripLabel} />
          {/* Always-mounted live region — a region that mounts together with
              its content is not reliably announced, so the span stays and
              only its text toggles. Covers the submit → first-token window;
              ActivityHeader's aria-live takes over once tool activity exists. */}
          <span role="status" className="sr-only">
            {showWorking ? "Agent is working" : ""}
          </span>
          <AnimatePresence initial={false}>
            {editingId && <ComposerEditingBanner key="editing-banner" onCancel={cancelEdit} />}
          </AnimatePresence>
          <AnimatePresence initial={false}>
            {pendingImages.length > 0 && (
              <ComposerImageStrip key="pending-images" images={pendingImages} onRemove={removePendingImage} />
            )}
          </AnimatePresence>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            onChange={(e) => {
              void attachFiles(e.target.files);
              e.target.value = ""; // allow picking the same file again later
            }}
          />
          <div className="board-composer__input-row flex min-h-[66px] items-start gap-2 px-4 pt-[13px] pb-1">
            <AnimatePresence initial={false}>
              {selectedSkill && (
                <motion.button
                  key="skill-pill"
                  type="button"
                  initial={RISE_IN}
                  animate={RISE_SETTLED}
                  exit={SECTION_EXIT}
                  transition={SECTION_ENTER}
                  onClick={() => void viewSkill(selectedSkill.name)}
                  title={`${selectedSkill.path ?? selectedSkill.name} - Backspace on empty input detaches`}
                  className="mt-[1px] inline-flex max-w-[240px] shrink-0 items-baseline gap-1.5 truncate text-md leading-[1.5] text-info hover:text-accent-strong transition-colors"
                >
                  <Box size={ICON.MD} strokeWidth={2} className="relative top-[1px] shrink-0" />
                  <span className="truncate capitalize">{selectedSkill.name.replace(/[_-]/g, " ")}</span>
                </motion.button>
              )}
            </AnimatePresence>
            <textarea
              ref={inputRef}
              id="message-input"
              aria-label="Message arden"
              value={draft}
              onChange={(e) => {
                // Real typing exits history mode (recall sets the draft
                // programmatically, which doesn't fire onChange).
                setHistoryIndex(null);
                setDraft(e.target.value);
              }}
              onKeyDown={(e) => {
                // Enter (or any key) during IME composition confirms the
                // conversion — it must never submit or hit shortcut branches.
                if (e.nativeEvent.isComposing) return;
                // Backspace on empty draft + attached skill → detach the skill.
                if (
                  e.key === "Backspace" &&
                  !pickerOpen &&
                  selectedSkill &&
                  draft.length === 0
                ) {
                  e.preventDefault();
                  setSelectedSkill(null);
                  return;
                }
                // Esc cancels an in-flight run when the picker isn't open.
                if (e.key === "Escape" && !pickerOpen && running) {
                  e.preventDefault();
                  void stopRun();
                  return;
                }
                if (pickerOpen && filteredCommands.length > 0) {
                  if (e.key === "Tab") {
                    e.preventDefault();
                    applyPickerSelection(filteredCommands[pickerIndex]);
                    return;
                  }
                  if (e.key === "Escape") {
                    e.preventDefault();
                    dismissedQueryRef.current = query;
                    setPickerOpen(false);
                    return;
                  }
                  if (
                    e.key === "ArrowDown" ||
                    e.key === "ArrowUp" ||
                    (
                      e.key === "Enter"
                      && !e.shiftKey
                      && !e.metaKey
                      && !e.ctrlKey
                      && !e.altKey
                    )
                  ) {
                    pickerNav.onKeyDown(e);
                    return;
                  }
                }
                // Readline-style history. Picker nav takes precedence (handled
                // above); here the picker is closed. Plain ArrowUp/ArrowDown
                // only (no modifiers), and only when the caret sits on the
                // first/last line so multi-line editing still works.
                if (
                  !pickerOpen &&
                  (e.key === "ArrowUp" || e.key === "ArrowDown") &&
                  !e.shiftKey &&
                  !e.altKey &&
                  !e.metaKey &&
                  !e.ctrlKey &&
                  inputRef.current
                ) {
                  const el = inputRef.current;
                  const caretStart = el.selectionStart ?? 0;
                  const caretEnd = el.selectionEnd ?? caretStart;
                  const onFirstLine = !el.value.slice(0, caretStart).includes("\n");
                  const onLastLine = !el.value.slice(caretEnd).includes("\n");
                  const direction = e.key === "ArrowUp" ? "up" : "down";
                  const atEdge = direction === "up" ? onFirstLine : onLastLine;
                  const inHistory = historyIndex != null;
                  if (atEdge && (direction === "up" || inHistory)) {
                    const result = recallHistory(
                      { historyIndex, draft, stashedDraft: stashedDraftRef.current },
                      direction,
                      sentMessages,
                    );
                    if (result.value !== draft || result.historyIndex !== historyIndex) {
                      e.preventDefault();
                      stashedDraftRef.current = result.stashedDraft;
                      setHistoryIndex(result.historyIndex);
                      setDraft(result.value);
                      requestAnimationFrame(() => {
                        const node = inputRef.current;
                        if (node) node.setSelectionRange(node.value.length, node.value.length);
                      });
                      return;
                    }
                  }
                }
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit((e.metaKey || e.ctrlKey) && running ? "steer" : "queue");
                }
              }}
              onPaste={(e) => {
                const files = Array.from(e.clipboardData?.files ?? []).filter((f) =>
                  f.type.startsWith("image/"),
                );
                if (files.length > 0) {
                  e.preventDefault();
                  void attachFiles(files);
                }
              }}
              rows={1}
              placeholder={
                dragOver ? "Drop images here" : selectedSkill ? "if needed" : "Message arden…"
              }
              className="board-composer__input min-h-[44px] max-h-[220px] min-w-0 flex-1 resize-none border-0 bg-transparent p-0 text-md leading-[1.5] text-ink outline-none tracking-[-0.005em] placeholder:text-muted"
            />
          </div>
          <ComposerToolbar
            onAttach={() => fileInputRef.current?.click()}
            skipApprovals={skipApprovals}
            onToggleAuto={() => void toggleAuto(!skipApprovals)}
            action={composerAction}
            sendDisabled={disabled || (composerAction === "queue" && queueFull)}
            queueFull={queueFull}
            working={showWorking}
            onStop={() => void stopRun()}
          />
        </form>
      </div>
    </div>
  );
}
