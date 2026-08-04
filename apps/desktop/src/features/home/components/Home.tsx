import { useRef } from "react";
import { Settings, Sparkles } from "@/components/icons";
import { useStore } from "@/stores";
import { useAreasData } from "@/features/home/hooks/useAreasData";
import { HeroInput } from "@/features/home/components/HeroInput";
import { WorkBrief } from "@/features/home/components/WorkBrief";
import { Button } from "@/components/ui/Button";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { Callout } from "@/components/ui/Callout";
import { ICON } from "@/lib/icons";
import {
  useHomeKeyboard,
  type HomeDeckShortcutActions,
  type HomeOmniboxShortcutActions,
} from "@/features/home/hooks/useHomeKeyboard";
import "./Home.css";

/* Editorial numbers: words through ten, numerals beyond. */
const COUNT_WORDS = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];

interface Greeting {
  text: string;
  /** The count token inside `text`, rendered in the attention ink. */
  countWord: string | null;
}

function greetingPhrase(
  focusCount: number,
  stale: boolean,
  hasAmbientActivity: boolean,
): Greeting {
  if (focusCount === 0 && !hasAmbientActivity) {
    return { text: stale ? "Last check was all clear" : "That’s it for today", countWord: null };
  }
  if (focusCount === 0) {
    return { text: stale ? "Last check was all clear" : "All clear", countWord: null };
  }
  if (focusCount === 1) {
    return stale
      ? { text: "Last check found one thing needs you", countWord: "one" }
      : { text: "One thing needs you", countWord: "One" };
  }
  const count = COUNT_WORDS[focusCount] ?? String(focusCount);
  return stale
    ? { text: `${count} things needed you at the last check`, countWord: count }
    : { text: `${count} things need you`, countWord: count };
}

function answerContent({ text, countWord }: Greeting) {
  if (!countWord) return text;
  const at = text.indexOf(countWord);
  return (
    <>
      {text.slice(0, at)}
      <span className="mission-control__answer-count">{countWord}</span>
      {text.slice(at + countWord.length)}
    </>
  );
}

/** Mission Control is an attention desk, not a dashboard: one focused ask,
 *  a universal capture door, and the rest of the system at ambient depth. */
export function Home() {
  const { overview, phase, reload } = useAreasData();
  const captureInputRef = useRef<HTMLInputElement>(null);
  const deckActionsRef = useRef<HomeDeckShortcutActions | null>(null);
  const omniboxActionsRef = useRef<HomeOmniboxShortcutActions | null>(null);
  useHomeKeyboard({ captureInput: captureInputRef, deckActions: deckActionsRef, omnibox: omniboxActionsRef });
  const connected = useStore((s) => s.connected);
  const openSettings = useStore((s) => s.openSettings);
  const automations = useStore((s) => s.automations);
  const stale = overview !== null && phase === "error";

  if (!connected) {
    // Mirrors the retired HomeHero's disconnected state: Home's hero input
    // is a dead end with no server behind it, so a connect CTA replaces it
    // entirely rather than showing an input that can't send.
    return (
      <div className="mission-control mission-control--disconnected">
        <div className="mission-control__connection" role="status">
          <span aria-hidden className="mission-control__connection-mark">
          <Sparkles size={ICON.HERO} />
          </span>
          <div>
            <h1>Connect to get started</h1>
            <p>Open settings to point Arden at your server.</p>
          </div>
          <Button variant="primary" size="md" leadingIcon={Settings} onClick={() => openSettings("connection")}>
            Open settings
          </Button>
        </div>
      </div>
    );
  }

  if (!overview) {
    const failed = phase === "error";
    return (
      <div className="mission-control mission-control--disconnected">
        <div
          className="mission-control__connection"
          role={failed ? "alert" : "status"}
          aria-live="polite"
        >
          <span aria-hidden className="mission-control__connection-mark">
            <Sparkles size={ICON.HERO} />
          </span>
          <div>
            <h1>{failed ? "Couldn’t load your desk" : "Checking what needs you…"}</h1>
            <p>
              {failed
                ? "Arden couldn’t read the current Areas overview."
                : "Arden is reading the latest asks and activity."}
            </p>
          </div>
          {failed && (
            <Button variant="primary" size="md" onClick={() => void reload()}>
              Retry
            </Button>
          )}
        </div>
      </div>
    );
  }

  const brief = overview.brief;
  // Running agents and handled work live in the Agent activity strips below
  // (with links); the headline only needs to know whether anything is moving.
  const hasAmbientActivity =
    (automations ?? []).some((a) => a.running_since != null) ||
    brief.done.length > 0 ||
    brief.in_progress.length > 0;
  const answer = greetingPhrase(brief.needs_you.length, stale, hasAmbientActivity);

  return (
    <div className="mission-control">
      {/* iA-style calm: the room defocuses while the router field is awake.
          Activation is pure CSS (:has focus-within) — see Home.css. */}
      <div className="mission-control__veil arden-focus-veil" aria-hidden />
      <div className="mission-control__room">
        {stale && (
          <Callout
            tone="warn"
            title="Desk data is stale"
            action={<Button size="sm" onClick={() => void reload()}>Retry</Button>}
          >
            Showing the last successful overview. Current asks and activity may have changed.
          </Callout>
        )}
        <header className="mission-control__answer" data-page-enter-item>
          <h1>
            <BlurSwap swapKey={answer.text}>
              <span className="mission-control__answer-copy">{answerContent(answer)}</span>
            </BlurSwap>
          </h1>
        </header>
        <HeroInput inputRef={captureInputRef} omniboxActionsRef={omniboxActionsRef} />
        <WorkBrief
          brief={brief}
          automations={automations ?? []}
          stale={stale}
          keyboardActionsRef={deckActionsRef}
        />
      </div>
    </div>
  );
}
