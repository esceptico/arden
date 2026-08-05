import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type MutableRefObject,
  type RefObject,
} from "react";
import { AnimatePresence } from "motion/react";
import { useStore } from "@/stores";
import { sendMessage } from "@/actions/messages";
import type { CommandEntry } from "@/lib/commandEntries/types";
import { useOmniboxResults } from "@/features/home/hooks/useOmniboxResults";
import { HomeOmniboxSheet, omniboxOptionId } from "@/features/home/components/HomeOmniboxSheet";
import type { HomeOmniboxShortcutActions } from "@/features/home/hooks/useHomeKeyboard";

const LIST_ID = "home-omnibox-listbox";

/**
 * The universal capture door. Empty, it does one thing — start a chat. Typed
 * into, it becomes a router: a docked results sheet (same entries + fuzzy
 * match as cmd+K, see useOmniboxResults) opens attached under the field, so
 * Enter can open a matching chat/area/automation instead of only ever
 * composing a new one. Cmd+Enter always starts a new chat regardless of
 * what's selected — the door never loses its original job.
 */
export function HeroInput({
  inputRef,
  omniboxActionsRef,
}: {
  inputRef?: RefObject<HTMLInputElement | null>;
  omniboxActionsRef?: MutableRefObject<HomeOmniboxShortcutActions | null>;
}) {
  const draft = useStore((s) => s.draft);
  const setDraft = useStore((s) => s.setDraft);
  const pendingAreaId = useStore((s) => s.pendingNewChatAreaId);
  const pendingAreaName = useStore((s) =>
    pendingAreaId ? s.areas.recordsById[pendingAreaId]?.name : null,
  );
  const [dismissed, setDismissed] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const draftRevision = useRef(0);

  const hasQuery = draft.trim().length > 0;
  const { groups, flat } = useOmniboxResults(draft);
  const sheetVisible = hasQuery && !dismissed && flat.length > 0;
  const safeIndex = flat.length === 0 ? 0 : Math.min(Math.max(selectedIndex, 0), flat.length - 1);
  const activeEntry = sheetVisible ? flat[safeIndex] : undefined;

  // Top result pre-selected on every query change, and clamped immediately
  // (not just corrected next tick) so a shrinking list never flashes a
  // dangling `data-selected`.
  useLayoutEffect(() => {
    setSelectedIndex(0);
  }, [draft]);

  const activateNewChat = useCallback(() => {
    const text = draft.trim();
    if (!text) return;
    const submittedDraft = draft;
    const submittedRevision = draftRevision.current;
    const submittedDraftId = useStore.getState().pendingNewChatDraftId;
    setDraft("");
    setDismissed(false);
    void sendMessage(text).then((sent) => {
      if (sent || draftRevision.current !== submittedRevision) return;
      const state = useStore.getState();
      const stillOnHome =
        state.pendingNewChatDraftId === submittedDraftId
        && state.currentSessionId === null
        && state.areas.openAreaKey === null
        && !state.memoryOpen
        && !state.automationsOpen
        && !state.settingsOpen;
      if (stillOnHome && state.draft === "") setDraft(submittedDraft);
    });
  }, [draft, setDraft]);

  const activateEntry = useCallback((entry: CommandEntry) => {
    setDraft("");
    setDismissed(false);
    void entry.run?.();
  }, [setDraft]);

  const onEscape = useCallback((): boolean => {
    if (!hasQuery) return false;
    if (!dismissed) {
      setDismissed(true);
      return true;
    }
    setDraft("");
    setDismissed(false);
    return true;
  }, [hasQuery, dismissed, setDraft]);

  useLayoutEffect(() => {
    if (!omniboxActionsRef) return;
    const actions: HomeOmniboxShortcutActions = { onEscape };
    omniboxActionsRef.current = actions;
    return () => {
      if (omniboxActionsRef.current === actions) omniboxActionsRef.current = null;
    };
  }, [omniboxActionsRef, onEscape]);

  return (
    <form
      className="mission-control__capture"
      data-region="universal-capture"
      data-page-enter-item
      onSubmit={(event) => {
        event.preventDefault();
        activateNewChat();
      }}
    >
      {pendingAreaId && (
        <div className="mission-control__capture-scope" aria-live="polite">
          New chat in <span>{pendingAreaName ?? "selected Area"}</span>
        </div>
      )}
      <div className="mission-control__capture-field" data-sheet-open={sheetVisible || undefined}>
        <input
          ref={inputRef}
          id="message-input"
          type="text"
          role="combobox"
          aria-expanded={sheetVisible}
          aria-controls={sheetVisible ? LIST_ID : undefined}
          aria-activedescendant={activeEntry ? omniboxOptionId(activeEntry.id) : undefined}
          aria-autocomplete="list"
          autoComplete="off"
          value={draft}
          onChange={(e) => {
            draftRevision.current += 1;
            setDraft(e.target.value);
            setDismissed(false);
          }}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              if (!sheetVisible) return;
              event.preventDefault();
              const delta = event.key === "ArrowDown" ? 1 : -1;
              setSelectedIndex((i) => Math.min(Math.max(i + delta, 0), flat.length - 1));
              return;
            }
            if (event.key !== "Enter") return;
            event.preventDefault();
            if (event.metaKey || event.ctrlKey) {
              activateNewChat();
              return;
            }
            if (activeEntry) activateEntry(activeEntry);
            else activateNewChat();
          }}
          placeholder="Ask, search, or start a chat…"
          aria-label="Ask, search, or start a chat"
        />
        {/* Both hint states stay mounted in one grid cell: the slot keeps the
            width of the wider state, so the swap is a pure crossfade with
            zero input-row reflow. DynamicIconSwap's .25-scale pop is a
            single-glyph recipe — on a word cluster it reads as a zoom. */}
        <span
          className="mission-control__capture-shortcut"
          aria-hidden="true"
          data-ask={sheetVisible || undefined}
        >
          <span className="mission-control__capture-hint" data-hint="ask">
            <kbd className="arden-kbd arden-kbd--chord">⌘↩</kbd>
            <span className="mission-control__capture-hint-label">ask</span>
          </span>
          <span className="mission-control__capture-hint" data-hint="default">
            <kbd className="arden-kbd arden-kbd--chord">⌘K</kbd>
          </span>
        </span>
        <AnimatePresence>
          {sheetVisible && (
            <HomeOmniboxSheet
              listId={LIST_ID}
              groups={groups}
              selectedIndex={safeIndex}
              onHoverIndex={setSelectedIndex}
              onActivate={activateEntry}
            />
          )}
        </AnimatePresence>
      </div>
    </form>
  );
}
