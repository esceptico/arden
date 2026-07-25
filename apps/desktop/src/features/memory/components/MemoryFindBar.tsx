import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { ChevronDown, ChevronUp, X } from "@/components/icons";
import { IconButton } from "@/components/ui/IconButton";
import { ICON } from "@/lib/icons";

const FIND = "memory-find";
const FIND_ACTIVE = "memory-find-active";

function highlightable(): boolean {
  return typeof CSS !== "undefined" && "highlights" in CSS && typeof Highlight !== "undefined";
}

function collectRanges(root: HTMLElement, query: string): Range[] {
  const needle = query.toLowerCase();
  if (!needle) return [];
  const ranges: Range[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent ?? "";
    const haystack = text.toLowerCase();
    let index = haystack.indexOf(needle);
    while (index !== -1) {
      const range = document.createRange();
      range.setStart(node, index);
      range.setEnd(node, index + needle.length);
      ranges.push(range);
      index = haystack.indexOf(needle, index + needle.length);
    }
  }
  return ranges;
}

function paint(ranges: Range[], active: number) {
  if (!highlightable()) return;
  CSS.highlights.set(FIND, new Highlight(...ranges));
  const current = ranges[active];
  if (current) CSS.highlights.set(FIND_ACTIVE, new Highlight(current));
  else CSS.highlights.delete(FIND_ACTIVE);
}

function clearPaint() {
  if (!highlightable()) return;
  CSS.highlights.delete(FIND);
  CSS.highlights.delete(FIND_ACTIVE);
}

/** Cmd/Ctrl+F find within the open note — literal, case-insensitive, with
 *  CSS Custom Highlight marks and next/prev cycling. Escapes close only the
 *  bar; the hosting surface keeps its own Escape. */
export function MemoryFindBar({ scrollerRef }: { scrollerRef: RefObject<HTMLElement | null> }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [total, setTotal] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const rangesRef = useRef<Range[]>([]);

  const refresh = useCallback((value: string, nextActive: number) => {
    const root = scrollerRef.current;
    const ranges = root ? collectRanges(root, value.trim()) : [];
    rangesRef.current = ranges;
    const bounded = ranges.length ? ((nextActive % ranges.length) + ranges.length) % ranges.length : 0;
    setTotal(ranges.length);
    setActive(bounded);
    paint(ranges, bounded);
    const current = ranges[bounded];
    if (current) {
      const el = current.startContainer.parentElement;
      el?.scrollIntoView({ block: "center" });
    }
  }, [scrollerRef]);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setTotal(0);
    setActive(0);
    rangesRef.current = [];
    clearPaint();
  }, []);

  useEffect(() => () => clearPaint(), []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f") {
        event.preventDefault();
        setOpen(true);
        requestAnimationFrame(() => inputRef.current?.select());
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  if (!open) return null;

  const step = (delta: number) => refresh(query, active + delta);

  return (
    <div
      role="search"
      aria-label="Find in note"
      className="memory-find-bar"
    >
      <input
        ref={inputRef}
        autoFocus
        value={query}
        placeholder="Find in note…"
        aria-label="Find in note"
        spellCheck={false}
        onChange={(event) => {
          setQuery(event.target.value);
          refresh(event.target.value, 0);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            step(event.shiftKey ? -1 : 1);
          } else if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            close();
          }
        }}
        className="memory-find-bar__input"
      />
      <span className="memory-find-bar__count" aria-live="polite">
        {total ? `${active + 1} of ${total}` : query.trim() ? "0 of 0" : ""}
      </span>
      <IconButton size="sm" tone="faint" aria-label="Previous match" disabled={!total} onClick={() => step(-1)}>
        <ChevronUp size={ICON.XS} />
      </IconButton>
      <IconButton size="sm" tone="faint" aria-label="Next match" disabled={!total} onClick={() => step(1)}>
        <ChevronDown size={ICON.XS} />
      </IconButton>
      <IconButton size="sm" tone="faint" aria-label="Close find" onClick={close}>
        <X size={ICON.XS} />
      </IconButton>
    </div>
  );
}
