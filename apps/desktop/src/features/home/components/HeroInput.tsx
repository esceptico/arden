import type { RefObject } from "react";
import { useStore } from "@/stores";
import { sendMessage } from "@/actions/messages";
/** The universal capture door intentionally does one thing: start a chat.
 *  Navigation and commands remain explicit elsewhere instead of guessing at
 *  keywords while someone is trying to think. */
export function HeroInput({ inputRef }: { inputRef?: RefObject<HTMLInputElement | null> }) {
  const draft = useStore((s) => s.draft);
  const setDraft = useStore((s) => s.setDraft);

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    void sendMessage(text);
  };

  return (
    <form
      className="mission-control__capture"
      data-region="universal-capture"
      data-page-enter-item
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <div className="mission-control__capture-field">
        <input
          ref={inputRef}
          id="message-input"
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask, search, or start a chat…"
          aria-label="Ask, search, or start a chat"
        />
        <span className="mission-control__capture-shortcut" aria-hidden="true">
          <kbd className="arden-kbd">⌘</kbd>
          <kbd className="arden-kbd">K</kbd>
        </span>
      </div>
    </form>
  );
}
