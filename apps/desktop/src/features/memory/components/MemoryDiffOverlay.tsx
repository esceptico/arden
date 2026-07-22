import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "@/components/icons";
import type { PageEditEvent } from "@/features/memory/lib/notebookTypes";

export function patchStat(patch: string): string {
  let added = 0;
  let removed = 0;
  for (const line of patch.split("\n")) {
    if (line.startsWith("+") && !line.startsWith("+++")) added += 1;
    else if (line.startsWith("-") && !line.startsWith("---")) removed += 1;
  }
  return `+${added} −${removed}`;
}

function diffLines(patch: string): string[] {
  return patch
    .split("\n")
    .filter((line) => !line.startsWith("+++") && !line.startsWith("---") && !line.startsWith("@@"));
}

function lineClass(line: string): "add" | "del" | undefined {
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return undefined;
}

type SplitRow = [string | null, string | null];

/** Pairs runs of deletions with the additions that follow them; context
 *  (and hunk) lines duplicate into both columns. */
export function splitRows(lines: string[]): SplitRow[] {
  const rows: SplitRow[] = [];
  let i = 0;
  while (i < lines.length) {
    if (lines[i].startsWith("-")) {
      const dels: string[] = [];
      while (i < lines.length && lines[i].startsWith("-")) dels.push(lines[i++]);
      const adds: string[] = [];
      while (i < lines.length && lines[i].startsWith("+")) adds.push(lines[i++]);
      for (let k = 0; k < Math.max(dels.length, adds.length); k++) {
        rows.push([dels[k] ?? null, adds[k] ?? null]);
      }
    } else if (lines[i].startsWith("+")) {
      rows.push([null, lines[i++]]);
    } else {
      rows.push([lines[i], lines[i]]);
      i++;
    }
  }
  return rows;
}

export function MemoryDiffOverlay({
  event,
  path,
  onClose,
}: {
  event: PageEditEvent;
  path: string;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<"unified" | "split">("unified");
  const lines = useMemo(() => diffLines(event.patch), [event.patch]);

  useEffect(() => {
    const onKeyDown = (keyEvent: KeyboardEvent) => {
      if (keyEvent.key !== "Escape") return;
      keyEvent.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const when = event.occurredAt.slice(0, 16).replace("T", " ");
  const cell = (line: string | null, key: string) =>
    line === null
      ? <span key={key} />
      : <span key={key} className={lineClass(line)}>{line || " "}</span>;

  return createPortal(
    <>
      <div className="mw-dv-scrim" onClick={onClose} />
      <div className="mw-dv" role="dialog" aria-modal="true" aria-label="Page edit diff">
        <div className="mw-dv-head">
          <span className="mw-dv-title" title={path}>{path}</span>
          <span className="mw-dv-meta">{when} · {event.actor} · {patchStat(event.patch)}</span>
          <div className="mw-dv-switch" role="tablist">
            <button
              type="button"
              className={mode === "unified" ? "on" : undefined}
              onClick={() => setMode("unified")}
            >Unified</button>
            <button
              type="button"
              className={mode === "split" ? "on" : undefined}
              onClick={() => setMode("split")}
            >Split</button>
          </div>
          <button type="button" className="mw-icon-btn" aria-label="Close" onClick={onClose}>
            <X size={15} />
          </button>
        </div>
        <div className={mode === "split" ? "mw-dv-body split-view" : "mw-dv-body"}>
          {mode === "unified" ? (
            lines.map((line, index) => {
              const cls = lineClass(line);
              return cls
                ? <span key={index} className={cls}>{line || " "}</span>
                : <span key={index}>{(line || " ") + "\n"}</span>;
            })
          ) : (
            <div className="mw-dv-split">
              {splitRows(lines).flatMap((row, index) => [
                cell(row[0], `${index}:l`),
                cell(row[1], `${index}:r`),
              ])}
            </div>
          )}
        </div>
      </div>
    </>,
    document.body,
  );
}
