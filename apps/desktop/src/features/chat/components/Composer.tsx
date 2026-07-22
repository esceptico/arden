import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Box } from "@/components/icons";
import { useShallow } from "zustand/react/shallow";
import { selectSentUserMessages, useStore } from "@/stores";
import { viewSkill } from "@/actions/skills";
import { enqueueMessage, sendMessage, stopRun } from "@/actions/messages";
import { respondToAllApprovals } from "@/actions/approvals";
import { isBuiltin, runBuiltinCommand } from "@/actions/builtins";
import { toggleAuto } from "@/actions/loops";
import { QueueCard } from "@/features/chat/components/QueueCard";
import { CommandPicker } from "@/features/chat/components/CommandPicker";
import { GoalProposalCard } from "@/features/chat/components/GoalProposalCard";
import { ComposerEditingBanner } from "@/features/chat/components/ComposerEditingBanner";
import { ComposerImageStrip } from "@/features/chat/components/ComposerImageStrip";
import { ComposerToolbar } from "@/features/chat/components/ComposerToolbar";
import { useListNav, useTimeoutFlag } from "@/lib/hooks";
import { ICON } from "@/lib/icons";
import { RISE_IN, RISE_SETTLED } from "@/lib/tokens/motion";
import { awaitingFirstRunOutput } from "@/features/chat/lib/runIndicators";
import { filterCommands, useCommandList, type CommandEntry } from "@/features/chat/lib/commands";
import { SECTION_ENTER, SECTION_EXIT } from "@/features/chat/lib/composerMotion";
import { fileToImageBlock, pickerQuery, resize } from "@/features/chat/lib/composerHelpers";
import { recallHistory } from "@/features/chat/lib/composerHistory";

export function Composer() {
  const draft = useStore((s) => s.draft);
  const setDraft = useStore((s) => s.setDraft);
  const running = useStore((s) => s.running);
  const connected = useStore((s) => s.connected);
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

  // Composer shows a "thinking" indicator while we're waiting for the
  // agent's first token (running but no assistant turn streaming yet).
  // Replaces the standalone Thinking row — status lives on the surface
  // that produced the action. The visual variant is user-configurable
  // via Settings → Appearance.
  const order = useStore((s) => s.order);
  const messages = useStore((s) => s.messages);
  const currentRunId = useStore((s) => s.currentRunId);
  const thinkingRunId = useStore((s) => s.thinkingRunId);
  const indicatorMessages = useMemo(
    () =>
      order
        .map((id) => messages.get(id))
        .filter((message): message is NonNullable<typeof message> => message !== undefined),
    [order, messages],
  );
  const serverThinking = Boolean(thinkingRunId && (!currentRunId || thinkingRunId === currentRunId));
  const awaitingFirstToken = serverThinking || awaitingFirstRunOutput(running, indicatorMessages);
  // 350ms threshold — fast replies (cached, small models, short tool
  // chains) shouldn't briefly flash the indicator. If the agent starts
  // emitting within the threshold, awaitingFirstToken flips false before
  // the timer fires and the indicator never appears. This is the
  // "spinner only when actually slow" pattern from ChatGPT/Cursor.
  const [showThinking, setShowThinking] = useState(false);
  // When the rim's been visible and the agent's first token arrives,
  // hold the rim mounted for ~250ms in a "leaving" state so its exit
  // can fade rather than hard-cut. Beat 1 of the thinking → streaming
  // transition pass.
  const [thinkingLeaving, setThinkingLeaving] = useState(false);
  useEffect(() => {
    if (!awaitingFirstToken) {
      if (showThinking) {
        setThinkingLeaving(true);
        const id = window.setTimeout(() => {
          setShowThinking(false);
          setThinkingLeaving(false);
        }, 250);
        return () => window.clearTimeout(id);
      }
      return;
    }
    setThinkingLeaving(false);
    const id = window.setTimeout(() => setShowThinking(true), 350);
    return () => window.clearTimeout(id);
  }, [awaitingFirstToken, showThinking]);
  const thinkingStyle = useStore((s) => s.prefs.thinkingAnimation);
  const thinkingIntensity = useStore((s) => s.prefs.thinkingIntensity);
  // Brief programmatic "press" on the send button. The button's :active
  // pseudo doesn't fire when Enter submits the form (no actual click).
  // This gives keyboard submits the same tactile feedback as a mouse
  // click — the button shrinks for ~140ms each time submit() runs.
  const [sendPressing, flashSendPress] = useTimeoutFlag(140);
  // Composer-level send acknowledgement — the panel fill brightens for
  // a single beat (280ms) on submit. It composes alongside the
  // thinking-rim ::before since this lives on a separate ::after
  // pseudo-element in CSS.
  const [justSent, flashJustSent] = useTimeoutFlag(280);

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

  function submit() {
    const text = draft;
    const skill = selectedSkill;
    const images = pendingImages;
    if (!text.trim() && !skill && images.length === 0) return;

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
      void enqueueMessage(trimmed, images);
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
      void enqueueMessage(fullText, images);
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

  async function attachFiles(fileList: FileList | File[] | null) {
    if (!fileList) return;
    const files = Array.from(fileList).filter((f) => f.type.startsWith("image/"));
    if (files.length === 0) return;
    const blocks = await Promise.all(files.map(fileToImageBlock));
    addPendingImages(blocks);
  }

  return (
    <div className="px-7 pb-2">
      <div className="max-w-[760px] mx-auto">
        <QueueCard />
      </div>
      <AnimatePresence initial={false}>
        {pendingGoalProposal && (
          <GoalProposalCard key="goal-proposal" objective={pendingGoalProposal.objective} />
        )}
      </AnimatePresence>
      {/* Wrapper exists so the CommandPicker can sit as a sibling of
          the form rather than a child and avoid being clipped by the
          composer panel. */}
      <div className="composer-wrap relative max-w-[760px] mx-auto">
        {pickerOpen && query !== null && (
          <CommandPicker query={query} onSelect={applyPickerSelection} />
        )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          flashSendPress();
          flashJustSent();
          submit();
        }}
        data-thinking={showThinking ? "true" : undefined}
        data-thinking-leaving={thinkingLeaving ? "true" : undefined}
        data-thinking-style={thinkingStyle}
        data-thinking-intensity={thinkingIntensity}
        data-just-sent={justSent ? "true" : undefined}
        data-drag-over={dragOver ? "true" : undefined}
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
        className="composer-card surface-panel surface-radius-md relative flex flex-col"
      >
        {/* Always-mounted live region — a region that mounts together with
            its content is not reliably announced, so the span stays and
            only its text toggles. Covers the submit → first-token window;
            ActivityHeader's aria-live takes over once tool activity exists. */}
        <span role="status" className="sr-only">
          {showThinking ? "Agent is thinking" : ""}
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
        <div className="flex min-h-[64px] items-start gap-2 px-4 pt-[13px] pb-1">
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
            aria-label="Message ntrp"
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
                  (e.key === "Enter" && !e.shiftKey)
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
                submit();
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
              dragOver ? "Drop images here" : selectedSkill ? "if needed" : "Message ntrp…"
            }
            className="min-h-[44px] max-h-[220px] min-w-0 flex-1 resize-none border-0 bg-transparent p-0 text-md leading-[1.5] text-ink outline-none tracking-[-0.005em] placeholder:text-muted"
          />
        </div>
        <ComposerToolbar
          onAttach={() => fileInputRef.current?.click()}
          skipApprovals={skipApprovals}
          onToggleAuto={() => void toggleAuto(!skipApprovals)}
          running={running}
          sendDisabled={disabled}
          sendPressing={sendPressing}
          onStop={() => void stopRun()}
        />
      </form>
      </div>
    </div>
  );
}
