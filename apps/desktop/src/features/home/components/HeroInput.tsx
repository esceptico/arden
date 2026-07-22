import { useEffect, useMemo, useState } from "react";
import { CornerDownLeft } from "@/components/icons";
import { useStore } from "@/stores";
import { sendMessage } from "@/actions/messages";
import { runAutomation } from "@/actions/automations";
import { switchSession, createSession } from "@/actions/sessions";
import { routeHeroInput, type HeroSuggestion } from "@/features/home/lib/heroRouting";
import { PickerRow } from "@/components/ui/PickerRow";
import { ICON } from "@/lib/icons";

// Stable references for "not loaded yet" fallbacks — a selector that falls
// back to `?? []` inline returns a NEW array every render, which zustand
// treats as a changed value and forces an infinite re-render loop.
const NO_AREAS: { key: string; title: string }[] = [];
const NO_AUTOMATIONS: { task_id: string; name: string }[] = [];

const KIND_LABEL: Record<HeroSuggestion["kind"], string> = {
  chat: "Chat",
  area: "Ask in",
  session: "Session",
  automation: "Automation",
  skill: "Skill",
};

/** The hero input IS the composer promoted: same draft/setDraft/sendMessage
 *  machinery as the in-thread Composer. The suggestion list reuses the
 *  slash-picker idiom (PickerRow + surface-popover + key hints): the first
 *  row is always visibly selected, ↑↓ move, ↵ applies, esc dismisses. */
export function HeroInput() {
  const draft = useStore((s) => s.draft);
  const setDraft = useStore((s) => s.setDraft);
  const openArea = useStore((s) => s.openArea);
  const sessions = useStore((s) => s.sessions);
  const areas = useStore((s) => s.areas.overview?.areas ?? NO_AREAS);
  const automations = useStore((s) => s.automations ?? NO_AUTOMATIONS);
  const skills = useStore((s) => s.skills);

  const [activeIndex, setActiveIndex] = useState(0);
  const [dismissed, setDismissed] = useState(false);

  const suggestions = useMemo(
    () => routeHeroInput(draft, { sessions, areas, automations, skills }),
    [draft, sessions, areas, automations, skills],
  );
  const showSuggestions = draft.trim().length > 0 && suggestions.length > 1 && !dismissed;

  // The list re-filters as the user types; keep the selection inside it and
  // reset the esc-dismissal on new input.
  useEffect(() => {
    setActiveIndex(0);
    setDismissed(false);
  }, [draft]);

  const applySuggestion = (suggestion: HeroSuggestion) => {
    switch (suggestion.kind) {
      case "chat":
        void sendMessage(suggestion.label);
        setDraft("");
        break;
      case "area": {
        // Route the chat INTO the area: ref is the container's area_id,
        // so filing is a plain create-in-area (the session inherits the
        // area's page as context). A bare area name with nothing to ask
        // just opens the room to browse.
        const msg = draft.trim();
        const nameOnly = msg.toLowerCase() === suggestion.label.toLowerCase();
        if (nameOnly) {
          openArea(suggestion.ref);
        } else {
          void createSession(suggestion.ref).then(() => sendMessage(msg));
        }
        setDraft("");
        break;
      }
      case "session":
        void switchSession(suggestion.ref);
        setDraft("");
        break;
      case "automation":
        void runAutomation(suggestion.ref);
        setDraft("");
        break;
      case "skill":
        setDraft(`/${suggestion.ref} `);
        break;
    }
    setActiveIndex(0);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" && showSuggestions) {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % suggestions.length);
      return;
    }
    if (e.key === "ArrowUp" && showSuggestions) {
      e.preventDefault();
      setActiveIndex((i) => (i - 1 + suggestions.length) % suggestions.length);
      return;
    }
    if (e.key === "Escape" && showSuggestions) {
      e.preventDefault();
      setDismissed(true);
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const chosen = showSuggestions ? suggestions[activeIndex] : suggestions[0];
      if (chosen) applySuggestion(chosen);
    }
  };

  return (
    <div className="relative">
      {/* Elevation per theme rules: light lifts via shadow tier, dark via the
          additive surface climb (bg-surface-2 over the floor). */}
      <div className="flex h-14 items-center gap-2 rounded-xl border border-line bg-surface-2 px-4 shadow-md">
        <input
          id="message-input"
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask, search, or start a chat…"
          autoFocus
          className="min-w-0 flex-1 bg-transparent text-md text-ink placeholder:text-faint focus:outline-none"
        />
        <kbd className="shrink-0 rounded-md bg-surface-soft px-1.5 py-0.5 text-2xs font-medium text-faint">
          ⌘K
        </kbd>
      </div>
      {showSuggestions && (
        <div className="surface-panel surface-popover absolute inset-x-0 top-[calc(100%+8px)] z-10 overflow-hidden">
          <div className="p-1.5">
            {suggestions.map((suggestion, index) => (
              <PickerRow
                key={`${suggestion.kind}-${suggestion.ref}-${index}`}
                active={activeIndex === index}
                onMouseDown={(e) => {
                  e.preventDefault();
                  applySuggestion(suggestion);
                }}
                onMouseMove={() => setActiveIndex(index)}
                className="app-row flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-ink-soft"
              >
                <span className="w-24 shrink-0 text-xs text-faint">{KIND_LABEL[suggestion.kind]}</span>
                <span className="min-w-0 flex-1 truncate text-sm text-ink">
                  {suggestion.label || "Send message"}
                </span>
              </PickerRow>
            ))}
          </div>
          <div className="flex items-center gap-3 bg-surface-soft/60 px-3 py-1.5 text-2xs text-faint select-none">
            <span className="inline-flex items-center gap-1.5">
              <kbd className="inline-flex h-4 min-w-4 items-center justify-center rounded-[4px] border border-line bg-surface px-1 font-mono">↑↓</kbd>
              navigate
            </span>
            <span className="inline-flex items-center gap-1.5">
              <kbd className="inline-flex h-4 min-w-4 items-center justify-center rounded-[4px] border border-line bg-surface px-1">
                <CornerDownLeft size={ICON.XS} strokeWidth={2} />
              </kbd>
              {suggestions[activeIndex] ? KIND_LABEL[suggestions[activeIndex].kind].toLowerCase() : "select"}
            </span>
            <span className="ml-auto inline-flex items-center gap-1.5">
              <kbd className="inline-flex h-4 items-center justify-center rounded-[4px] border border-line bg-surface px-1 font-mono">esc</kbd>
              dismiss
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
