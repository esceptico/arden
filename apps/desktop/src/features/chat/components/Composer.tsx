import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence } from "motion/react";
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
import { workingLabel } from "@/features/chat/lib/workingLabel";
import { filterCommands, useCommandList, type CommandEntry } from "@/features/chat/lib/commands";
import { fileToImageBlock, mentionQueryAt } from "@/features/chat/lib/composerHelpers";
import {
  caretOffset,
  insertMention,
  placeCaretAtEnd,
  renderDraft,
  serializeEditor,
} from "@/features/chat/lib/mentionEditor";
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
  const pendingImages = useStore((s) => s.pendingImages);
  const addPendingImages = useStore((s) => s.addPendingImages);
  const removePendingImage = useStore((s) => s.removePendingImage);
  const clearPendingImages = useStore((s) => s.clearPendingImages);
  const pendingGoalProposal = useStore((s) => s.pendingGoalProposal);
  const queuedCount = useStore((s) => s.queuedMessages.length);
  const queueFull = queuedCount >= QUEUE_MAX_ITEMS;
  const inputRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Readline-style recall over the session's sent messages. `historyIndex` is
  // null when not browsing; `stashedDraft` keeps the in-progress text so
  // ArrowDown past the newest entry restores it. Resetting historyIndex (on
  // typing or send) re-stashes on the next ArrowUp.
  const sentMessages = useStore(useShallow(selectSentUserMessages));
  const [historyIndex, setHistoryIndex] = useState<number | null>(null);
  const stashedDraftRef = useRef("");

  // Caret tracking makes the picker work for mid-text mentions, not just a
  // leading slash. onChange + onSelect together cover typing, clicks, and
  // arrow-key movement.
  const [caret, setCaret] = useState(0);
  const mention = useMemo(() => mentionQueryAt(draft, caret), [draft, caret]);
  const query = mention?.query ?? null;
  const allCommands = useCommandList();
  const filteredCommands = useMemo(() => {
    if (query === null) return [];
    const matches = filterCommands(allCommands, query);
    // Builtins act on the whole message; mid-text completion is skills only.
    return mention !== null && mention.start > 0
      ? matches.filter((entry) => entry.kind !== "builtin")
      : matches;
  }, [allCommands, mention, query]);

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
  const hasContent = hasDraft || pendingImages.length > 0;
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

  // The editor DOM is browser-owned while the user types; the store's draft
  // string is the single source of truth everywhere else. Programmatic draft
  // changes (history recall, message edit, clear-on-send) re-render the DOM
  // from the string, tokenizing every known /skill mention into an atomic
  // inline token. Layout effect so a multi-line recall never paints one
  // frame of stale content.
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    if (serializeEditor(el) === draft) return;
    renderDraft(el, draft, skills);
    if (document.activeElement === el) placeCaretAtEnd(el);
  }, [draft, skills]);

  // Clicks and arrow keys move the caret without an input event; the picker's
  // mid-text mention query needs the live offset.
  useEffect(() => {
    const handler = () => {
      const el = inputRef.current;
      if (!el || document.activeElement !== el) return;
      setCaret(caretOffset(el));
    };
    document.addEventListener("selectionchange", handler);
    return () => document.removeEventListener("selectionchange", handler);
  }, []);

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
    const el = inputRef.current;

    if (entry.kind === "builtin") {
      // Builtins fire-and-forget; the draft-sync effect clears the editor.
      setDraft("");
      void runBuiltinCommand(entry.name, "");
      return;
    }

    // Complete the mention in place — start of text and mid-text are the
    // same mechanism: an atomic inline token wherever the mention sits.
    // Serialization turns it back into /name, which the server expands.
    const skill = skills.find((s) => s.name === entry.name);
    if (!skill || !el || mention === null) return;
    insertMention(el, mention.start, caret, skill);
    setDraft(serializeEditor(el));
    setCaret(caretOffset(el));
  }

  function submit(delivery: "queue" | "steer" = "queue") {
    const text = draft;
    const images = pendingImages;
    if (!text.trim() && images.length === 0) return;
    if (running && delivery === "queue" && queueFull) return;

    setHistoryIndex(null);
    setDraft("");
    clearPendingImages();
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

    // Pure builtin (no images) — route to the dispatcher.
    if (images.length === 0 && dispatchCommand(text)) return;

    if (running) {
      void (delivery === "steer" ? steerMessage(text, images) : enqueueMessage(text, images));
      inputRef.current?.focus({ preventScroll: true });
    } else {
      void sendMessage(text, images);
    }
  }

  function cancelEdit() {
    setEditingId(null);
    setDraft("");
  }

  const editQueuedMessage = useCallback((message: import("@/stores").QueuedMessage) => {
    cancelQueuedMessage(message.clientId);
    setDraft(message.text);
    clearPendingImages();
    if (message.images?.length) addPendingImages(message.images);
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus({ preventScroll: true });
      placeCaretAtEnd(el);
    });
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
      <div className="board-composer-wrap relative max-w-[var(--content-max-width)] mx-auto">
        <QueueCard onEdit={editQueuedMessage} />
        {pickerOpen && query !== null && (
          <CommandPicker query={query} onSelect={applyPickerSelection} skillsOnly={mention !== null && mention.start > 0} />
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
          <div className="board-composer__input-row">
            <div
                ref={inputRef}
                id="message-input"
                role="textbox"
                aria-multiline="true"
                aria-label="Message arden"
                contentEditable
                suppressContentEditableWarning
                data-empty={draft.length === 0 ? "true" : undefined}
                data-placeholder={dragOver ? "Drop images here" : "Message arden…"}
                onClick={(e) => {
                  // Tokens are links: clicking one opens the skill, same as
                  // SkillInlineToken in sent bubbles.
                  const token = (e.target as HTMLElement).closest<HTMLElement>("[data-mention]");
                  if (token?.dataset.mention) void viewSkill(token.dataset.mention);
                }}
                onInput={(e) => {
                  const el = e.currentTarget;
                  // Real typing exits history mode (recall sets the draft
                  // programmatically, which doesn't fire onInput).
                  setHistoryIndex(null);
                  const text = serializeEditor(el);
                  const at = caretOffset(el);
                  // Typing a boundary after a known /skill name tokenizes it
                  // in place — same result as picking it from the picker.
                  const native = e.nativeEvent as InputEvent;
                  if (native.inputType === "insertText" && native.data && /\s/.test(native.data)) {
                    const match = /\/([a-z][a-z0-9-]{0,47})\s$/.exec(text.slice(0, at));
                    if (match && (match.index === 0 || /\s/.test(text[match.index - 1]!))) {
                      const skill = skills.find((s) => s.name === match[1]);
                      if (skill) {
                        insertMention(el, match.index, at, skill);
                        setDraft(serializeEditor(el));
                        setCaret(caretOffset(el));
                        return;
                      }
                    }
                  }
                  setDraft(text);
                  setCaret(at);
                }}
                onBeforeInput={(e) => {
                  // Normalize paragraphs to line breaks so the document stays
                  // flat: text nodes, tokens, and <br>s — nothing else.
                  const native = e.nativeEvent as InputEvent;
                  if (native.inputType === "insertParagraph") {
                    e.preventDefault();
                    document.execCommand("insertLineBreak");
                  }
                }}
                onCopy={(e) => {
                  const sel = window.getSelection();
                  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;
                  e.preventDefault();
                  e.clipboardData.setData("text/plain", serializeEditor(sel.getRangeAt(0).cloneContents()));
                }}
                onCut={(e) => {
                  const sel = window.getSelection();
                  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;
                  e.preventDefault();
                  e.clipboardData.setData("text/plain", serializeEditor(sel.getRangeAt(0).cloneContents()));
                  document.execCommand("delete");
                }}
                onKeyDown={(e) => {
                  // Enter (or any key) during IME composition confirms the
                  // conversion — it must never submit or hit shortcut branches.
                  if (e.nativeEvent.isComposing) return;
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
                    const text = serializeEditor(el);
                    const at = caretOffset(el);
                    const onFirstLine = !text.slice(0, at).includes("\n");
                    const onLastLine = !text.slice(at).includes("\n");
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
                        // The draft-sync effect re-renders the editor and
                        // parks the caret at the end.
                        setDraft(result.value);
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
                    return;
                  }
                  // Plain text only — pasted markup must never enter the doc.
                  e.preventDefault();
                  const text = e.clipboardData.getData("text/plain");
                  if (text) document.execCommand("insertText", false, text);
                }}
                className="board-composer__input board-composer__editor max-h-[220px] min-w-0 flex-1 border-0 bg-transparent p-0 text-ink outline-none"
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
