import { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { createPortal } from "react-dom";
import { useOverlayLayer } from "@/lib/overlayStack";
import { useFocusTrap } from "@/lib/hooks";
import { Button } from "@/components/ui/Button";
import { DISTANCE, MOTION } from "@/lib/tokens/motion";
import { getBoardMotion } from "@/lib/boardMotion";
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

type DiffKind = "add" | "del" | "ctx" | "hunk";

interface DiffSegment {
  text: string;
  changed: boolean;
}

interface DiffLine {
  kind: DiffKind;
  marker: string;
  text: string;
  oldLine: number | null;
  newLine: number | null;
  segments?: DiffSegment[];
}

function tokenizeWords(value: string): string[] {
  return value.match(/\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]+/gu) ?? [];
}

function coalesceSegments(tokens: readonly string[], unchanged: ReadonlySet<number>): DiffSegment[] {
  const segments: DiffSegment[] = [];
  tokens.forEach((text, index) => {
    const changed = !unchanged.has(index);
    const previous = segments.at(-1);
    if (previous?.changed === changed) previous.text += text;
    else segments.push({ text, changed });
  });
  return segments;
}

/** GitHub-style intraline word matching for one removed/added line pair. */
export function wordDiff(before: string, after: string): {
  before: DiffSegment[];
  after: DiffSegment[];
} {
  const left = tokenizeWords(before);
  const right = tokenizeWords(after);
  const leftUnchanged = new Set<number>();
  const rightUnchanged = new Set<number>();

  // Keep pathological generated lines bounded; common edges still remain
  // readable while avoiding a quadratic matrix during render.
  if (left.length * right.length > 40_000) {
    let prefix = 0;
    while (prefix < left.length && prefix < right.length && left[prefix] === right[prefix]) {
      leftUnchanged.add(prefix);
      rightUnchanged.add(prefix);
      prefix += 1;
    }
    let leftIndex = left.length - 1;
    let rightIndex = right.length - 1;
    while (
      leftIndex >= prefix
      && rightIndex >= prefix
      && left[leftIndex] === right[rightIndex]
    ) {
      leftUnchanged.add(leftIndex);
      rightUnchanged.add(rightIndex);
      leftIndex -= 1;
      rightIndex -= 1;
    }
  } else {
    const matrix = Array.from(
      { length: left.length + 1 },
      () => new Uint16Array(right.length + 1),
    );
    for (let i = 1; i <= left.length; i += 1) {
      for (let j = 1; j <= right.length; j += 1) {
        matrix[i][j] = left[i - 1] === right[j - 1]
          ? matrix[i - 1][j - 1] + 1
          : Math.max(matrix[i - 1][j], matrix[i][j - 1]);
      }
    }
    let i = left.length;
    let j = right.length;
    while (i > 0 && j > 0) {
      if (left[i - 1] === right[j - 1]) {
        leftUnchanged.add(i - 1);
        rightUnchanged.add(j - 1);
        i -= 1;
        j -= 1;
      } else if (matrix[i - 1][j] >= matrix[i][j - 1]) {
        i -= 1;
      } else {
        j -= 1;
      }
    }
  }

  return {
    before: coalesceSegments(left, leftUnchanged),
    after: coalesceSegments(right, rightUnchanged),
  };
}

function pairingTokens(value: string): Set<string> {
  return new Set(
    value.toLocaleLowerCase().match(/[\p{L}\p{N}_]+/gu) ?? [],
  );
}

function lineSimilarity(left: string, right: string): number {
  if (left === right) return 1;
  const leftTokens = pairingTokens(left);
  const rightTokens = pairingTokens(right);
  if (leftTokens.size === 0 || rightTokens.size === 0) return 0;
  let shared = 0;
  leftTokens.forEach((token) => {
    if (rightTokens.has(token)) shared += 1;
  });
  return shared / Math.max(leftTokens.size, rightTokens.size);
}

/** Order-preserving modified-line pairing. Unrelated deletions/additions stay
 * whole-row changes instead of becoming hundreds of false word highlights. */
function pairModifiedLines(
  removed: readonly DiffLine[],
  added: readonly DiffLine[],
): Array<readonly [number, number]> {
  const rows = removed.length + 1;
  const columns = added.length + 1;
  const scores = Array.from({ length: rows }, () => new Float32Array(columns));
  const choices = Array.from({ length: rows }, () => new Uint8Array(columns));

  for (let left = 1; left < rows; left += 1) {
    for (let right = 1; right < columns; right += 1) {
      const similarity = lineSimilarity(removed[left - 1].text, added[right - 1].text);
      const paired = similarity >= 0.34
        ? scores[left - 1][right - 1] + similarity
        : Number.NEGATIVE_INFINITY;
      const skipLeft = scores[left - 1][right];
      const skipRight = scores[left][right - 1];
      if (paired >= skipLeft && paired >= skipRight) {
        scores[left][right] = paired;
        choices[left][right] = 1;
      } else if (skipLeft >= skipRight) {
        scores[left][right] = skipLeft;
        choices[left][right] = 2;
      } else {
        scores[left][right] = skipRight;
        choices[left][right] = 3;
      }
    }
  }

  const pairs: Array<readonly [number, number]> = [];
  let left = removed.length;
  let right = added.length;
  while (left > 0 && right > 0) {
    const choice = choices[left][right];
    if (choice === 1) {
      pairs.push([left - 1, right - 1]);
      left -= 1;
      right -= 1;
    } else if (choice === 2) {
      left -= 1;
    } else {
      right -= 1;
    }
  }
  return pairs.reverse();
}

export function diffLines(patch: string): DiffLine[] {
  const lines: DiffLine[] = [];
  const raw = (patch.endsWith("\n") ? patch.slice(0, -1) : patch).split("\n");
  let oldLine = 1;
  let newLine = 1;
  let index = 0;

  while (index < raw.length) {
    const line = raw[index];
    if (line.startsWith("---") || line.startsWith("+++")) {
      index += 1;
      continue;
    }
    if (line.startsWith("@@")) {
      const match = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
      if (match) {
        oldLine = Number(match[1]);
        newLine = Number(match[2]);
      }
      lines.push({
        kind: "hunk",
        marker: "",
        text: line,
        oldLine: null,
        newLine: null,
      });
      index += 1;
      continue;
    }
    if (!line.startsWith("-")) {
      if (line.startsWith("+")) {
        lines.push({
          kind: "add",
          marker: "+",
          text: line.slice(1),
          oldLine: null,
          newLine,
        });
        newLine += 1;
      } else {
        const text = line.startsWith(" ") ? line.slice(1) : line;
        lines.push({
          kind: "ctx",
          marker: " ",
          text,
          oldLine,
          newLine,
        });
        oldLine += 1;
        newLine += 1;
      }
      index += 1;
      continue;
    }

    const removed: DiffLine[] = [];
    while (index < raw.length && raw[index].startsWith("-") && !raw[index].startsWith("---")) {
      removed.push({
        kind: "del",
        marker: "-",
        text: raw[index].slice(1),
        oldLine,
        newLine: null,
      });
      oldLine += 1;
      index += 1;
    }
    const added: DiffLine[] = [];
    while (index < raw.length && raw[index].startsWith("+") && !raw[index].startsWith("+++")) {
      added.push({
        kind: "add",
        marker: "+",
        text: raw[index].slice(1),
        oldLine: null,
        newLine,
      });
      newLine += 1;
      index += 1;
    }

    pairModifiedLines(removed, added).forEach(([removedIndex, addedIndex]) => {
      const paired = wordDiff(removed[removedIndex].text, added[addedIndex].text);
      removed[removedIndex].segments = paired.before;
      added[addedIndex].segments = paired.after;
    });
    lines.push(...removed, ...added);
  }
  return lines;
}

export function MemoryDiffOverlay({
  event,
  path,
  open,
  onOpenChange,
  onExitComplete,
}: {
  event: PageEditEvent;
  path: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onExitComplete: () => void;
}) {
  const overlayRef = useRef<HTMLElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const motionIntentRef = useRef<boolean | null>(null);
  const onExitCompleteRef = useRef(onExitComplete);
  onExitCompleteRef.current = onExitComplete;
  const lines = useMemo(() => diffLines(event.patch), [event.patch]);
  const appRoot = document.querySelector("#app");
  const requestClose = useCallback(() => onOpenChange(false), [onOpenChange]);
  // The activity peek owns the command that opened this tier-three diff.
  // Keep it mounted (and let the blocking layer inert it) so the parent does
  // not erase the dialog's state before the portal paints.
  useOverlayLayer(overlayRef, appRoot != null, requestClose, !open, true, false);
  useFocusTrap(panelRef, open);

  // Canonical board-memory tier-three controller: the room is #app and the
  // blocking sheet is its body-level sibling.
  useLayoutEffect(() => {
    document.body.classList.add("memory-diff-stage");
    return () => {
      document.body.classList.remove("memory-diff-stage", "memory-diff-receded");
    };
  }, []);

  useLayoutEffect(() => {
    document.body.classList.toggle("memory-diff-receded", open);
  }, [open]);

  // Story/test mounts without the app root cannot join the production stack;
  // retain the same Escape contract for that isolated fallback only.
  useEffect(() => {
    if (appRoot) return;
    const onKeyDown = (keyEvent: KeyboardEvent) => {
      if (keyEvent.key !== "Escape") return;
      keyEvent.preventDefault();
      requestClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [appRoot, requestClose]);

  useLayoutEffect(() => {
    const panel = panelRef.current;
    const boardMotion = getBoardMotion();
    if (!panel || !boardMotion) {
      if (!open) onExitCompleteRef.current();
      return;
    }
    // React Strict Mode replays layout effects in development. The mockup
    // controller receives one intent per state change, so suppress the replay
    // instead of interrupting and restarting its spring.
    if (motionIntentRef.current === open) return;
    motionIntentRef.current = open;
    if (open) {
      void boardMotion.surface.show(panel, {
        axis: "y",
        distance: DISTANCE.sheetEnter,
        duration: MOTION.sheetCleanup * 1_000,
        spring: boardMotion.spring.sheet,
        beforeOpen: () => panel.classList.add("show"),
      });
    } else {
      void boardMotion.surface.hide(panel, {
        axis: "y",
        distance: DISTANCE.sheetExit,
        duration: MOTION.focus * 1_000,
        afterClose: () => {
          if (motionIntentRef.current !== false) return;
          panel.classList.remove("show");
          onExitCompleteRef.current();
        },
      });
    }
  }, [open]);

  useEffect(() => () => {
    const panel = panelRef.current;
    const boardMotion = getBoardMotion();
    if (panel && boardMotion) boardMotion.surface.cancel(panel);
  }, []);

  const when = event.occurredAt.slice(0, 16).replace("T", " ");
  const title = (path.split("/").at(-1)?.replace(/\.md$/i, "") || path).replace(/[-_]+/g, " ");

  return createPortal(
    <section
      ref={overlayRef}
      className="memory-diff-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Page edit diff"
      data-overlay-state={open ? "open" : "closing"}
      aria-hidden={open ? undefined : "true"}
      inert={open ? undefined : true}
    >
      <button
        type="button"
        className="mw-dv-scrim"
        aria-label="Close page edit diff"
        onClick={requestClose}
      />
      <aside ref={panelRef} className="sheet3 mw-dv" tabIndex={-1}>
        <div className="sheet3-head mw-dv-head">
          <span className="pc mw-dv-title" title={path}>{when} · {event.actor} / <b>{title}</b></span>
          <span className="s3d mw-dv-meta">{patchStat(event.patch)}</span>
        </div>
        <div className="sheet3-body mw-dv-body scroll-fade">
          {lines.map((line, index) => (
            <div key={index} className={`dl ${line.kind}`}>
              <span className="mw-dv-line-number mw-dv-line-old" aria-hidden>{line.oldLine ?? ""}</span>
              <span className="mw-dv-line-number mw-dv-line-new" aria-hidden>{line.newLine ?? ""}</span>
              <span className="mw-dv-marker" aria-hidden>{line.marker}</span>
              <code className="mw-dv-code">
                {line.segments?.map((segment, segmentIndex) => (
                  <span
                    key={segmentIndex}
                    className={segment.changed ? `mw-dv-word-${line.kind}` : undefined}
                  >
                    {segment.text}
                  </span>
                )) ?? (line.text || " ")}
              </code>
            </div>
          ))}
        </div>
        <footer className="mw-dv-foot">
          <Button variant="quiet" size="md" onClick={requestClose}>Close</Button>
        </footer>
      </aside>
    </section>,
    document.body,
  );
}
