import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FileText } from "@/components/icons";
import clsx from "clsx";
import { useOverlayLayer } from "@/lib/overlayStack";
import { useFocusTrap } from "@/lib/hooks";
import type { MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";
import { stem } from "@/features/memory/lib/workspaceTree";

// Bounds the rendered list, not what's reachable — the listbox scrolls.
const RESULT_LIMIT = 100;

function isSubsequence(haystack: string, needle: string): boolean {
  let i = 0;
  for (const char of haystack) {
    if (i >= needle.length) break;
    if (char === needle[i]) i += 1;
  }
  return i === needle.length;
}

/** Client-side fuzzy match over already-loaded artifact summaries. Empty
 *  query surfaces recency (most-recently-visited first); a query ranks by
 *  match quality — exact substring in the title beats a word-prefix match
 *  beats a scattered subsequence — ties broken by shorter title.
 *  Operator tokens narrow before ranking (the draft's switcher grammar):
 *  `folder:topics` path prefix, `@2026-07` created/modified month,
 *  `#topic` artifact kind. */
export function rankSwitcherMatches(
  artifacts: MemoryArtifactSummary[],
  query: string,
  recentPaths: string[],
): MemoryArtifactSummary[] {
  const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const operators = tokens.filter((token) => token.startsWith("folder:") || token.startsWith("@") || token.startsWith("#"));
  if (operators.length > 0) {
    artifacts = artifacts.filter((artifact) => operators.every((token) => {
      if (token.startsWith("folder:")) return artifact.path.toLowerCase().startsWith(token.slice(7));
      if (token.startsWith("@")) {
        const date = token.slice(1);
        return (artifact.updatedAt ?? "").startsWith(date) || (artifact.createdAt ?? "").startsWith(date);
      }
      return artifact.kind.toLowerCase() === token.slice(1);
    }));
  }
  const trimmed = tokens.filter((token) => !operators.includes(token)).join(" ");
  if (!trimmed) {
    const byPath = new Map(artifacts.map((artifact) => [artifact.path, artifact] as const));
    const seen = new Set<string>();
    const recent: MemoryArtifactSummary[] = [];
    for (const path of recentPaths) {
      const artifact = byPath.get(path);
      if (!artifact || seen.has(path)) continue;
      recent.push(artifact);
      seen.add(path);
    }
    const rest = artifacts
      .filter((artifact) => !seen.has(artifact.path))
      .sort((a, b) => stem(a.path).localeCompare(stem(b.path)));
    return [...recent, ...rest].slice(0, RESULT_LIMIT);
  }

  const needle = trimmed;
  const scored: Array<{ artifact: MemoryArtifactSummary; tier: number }> = [];
  for (const artifact of artifacts) {
    const title = stem(artifact.path).toLowerCase();
    const haystack = `${title} ${artifact.path.toLowerCase()}`;
    let tier: number | null = null;
    if (title.includes(needle)) tier = 0;
    else if (title.split(/\s+/).some((word) => word.startsWith(needle))) tier = 1;
    else if (isSubsequence(haystack, needle)) tier = 2;
    if (tier !== null) scored.push({ artifact, tier });
  }
  scored.sort((a, b) => a.tier - b.tier
    || stem(a.artifact.path).length - stem(b.artifact.path).length
    || stem(a.artifact.path).localeCompare(stem(b.artifact.path)));
  return scored.slice(0, RESULT_LIMIT).map((entry) => entry.artifact);
}

export function MemoryQuickSwitcher({
  open,
  artifacts,
  recentPaths,
  onClose,
  onSelect,
}: {
  open: boolean;
  artifacts: MemoryArtifactSummary[];
  recentPaths: string[];
  onClose: () => void;
  onSelect: (path: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [highlighted, setHighlighted] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  useFocusTrap(panelRef, open);
  useOverlayLayer(panelRef, open, onClose);

  const results = rankSwitcherMatches(artifacts, query, recentPaths);
  const safeHighlighted = Math.min(highlighted, Math.max(0, results.length - 1));

  useEffect(() => {
    panelRef.current
      ?.querySelector('[role="option"][data-active="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [safeHighlighted]);

  // Reset state on open. Escape is owned by the shared overlay stack, so the
  // topmost surface is the only one allowed to dismiss.
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setHighlighted(0);
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [open]);

  const root = document.querySelector("#app");
  if (!root || !open) return null;

  // Open/close is intentionally instant — keyboard-frequency surface, same
  // precedent as CommandPalette.
  return createPortal(
    <div
      className="memory-switcher-scrim"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Quick switcher"
        tabIndex={-1}
        className="memory-switcher"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="memory-switcher__query">
          <input
            ref={inputRef}
            type="text"
            role="combobox"
            aria-expanded
            aria-controls="memory-quick-switcher-listbox"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setHighlighted(0);
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setHighlighted((index) => Math.min(index + 1, Math.max(0, results.length - 1)));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setHighlighted((index) => Math.max(index - 1, 0));
              } else if (event.key === "Enter") {
                event.preventDefault();
                const target = results[safeHighlighted];
                if (target) onSelect(target.path);
              }
            }}
            placeholder="Jump to note…"
            spellCheck={false}
            className="memory-switcher__input"
          />
        </div>
        <div
          role="listbox"
          id="memory-quick-switcher-listbox"
          aria-label="Notes"
          className="memory-switcher__results scroll-fade"
        >
          {results.length === 0 ? (
            <div className="memory-switcher__empty">
              Nothing matches.
            </div>
          ) : (
            results.map((artifact, index) => {
              const segments = artifact.path.split("/");
              const parent = segments.slice(0, -1).join(" › ");
              return (
                <button
                  key={artifact.path}
                  type="button"
                  role="option"
                  aria-selected={index === safeHighlighted}
                  onClick={() => onSelect(artifact.path)}
                  onMouseEnter={() => setHighlighted(index)}
                  title={artifact.path}
                  className="memory-switcher__row"
                  data-active={index === safeHighlighted}
                >
                  <FileText className={clsx("memory-switcher__row-icon", index === safeHighlighted && "active")} />
                  <span className="memory-switcher__row-copy">
                    <span className={clsx("memory-switcher__row-title", index === safeHighlighted && "active")}>
                      {stem(artifact.path)}
                    </span>
                    {parent && <span className="memory-switcher__row-path">{parent}</span>}
                  </span>
                </button>
              );
            })
          )}
        </div>
      </div>
    </div>,
    root,
  );
}
