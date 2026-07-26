import { motion, useReducedMotion } from "motion/react";
import { ICON } from "@/lib/icons";
import { EASE_OUT, MOTION } from "@/lib/tokens/motion";
import type { CommandEntry } from "@/lib/commandEntries/types";
import type { OmniboxGroup } from "@/features/home/hooks/useOmniboxResults";

export function omniboxOptionId(entryId: string): string {
  return `home-omnibox-opt-${entryId}`;
}

/** One tier quicker than the dissolve-tier entrance, matching the house
 *  "exit runs a tier faster than enter" convention. */
// One clock with the field's pill→card morph (same 250ms swap tier, see
// --motion-icon-swap in Home.css): the sheet UNFOLDS from under the field —
// no scale, it is an attached extension, not a detached popover.
const SHEET_ENTER_POSE = { opacity: 0, y: -6 } as const;
const SHEET_ENTER_TRANSITION = { duration: MOTION.iconSwap, ease: EASE_OUT } as const;
const SHEET_EXIT_TRANSITION = { duration: MOTION.feedback, ease: EASE_OUT } as const;

/**
 * The router's docked results — reuses the command palette's row/heading
 * classes (`command-palette__row*`) so the two doors read as one visual
 * system, but owns its own markup: the palette's `Row` is bound to cmdk's
 * `Command.Item`, and this sheet's selection lives in the field's own
 * up/down state instead of a listbox widget.
 */
export function HomeOmniboxSheet({
  listId,
  groups,
  selectedIndex,
  onHoverIndex,
  onActivate,
}: {
  listId: string;
  groups: OmniboxGroup[];
  selectedIndex: number;
  onHoverIndex: (index: number) => void;
  onActivate: (entry: CommandEntry) => void;
}) {
  const reducedMotion = useReducedMotion() ?? false;
  let cursor = 0;

  return (
    <motion.div
      className="mission-control__omnibox-sheet"
      initial={reducedMotion ? { opacity: 0 } : SHEET_ENTER_POSE}
      animate={reducedMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
      exit={
        reducedMotion
          ? { opacity: 0, transition: { duration: MOTION.reduced } }
          : { ...SHEET_ENTER_POSE, transition: SHEET_EXIT_TRANSITION }
      }
      transition={reducedMotion ? { duration: MOTION.reduced } : SHEET_ENTER_TRANSITION}
    >
      <div
        id={listId}
        role="listbox"
        aria-label="Ask, search, or start a chat — results"
        className="mission-control__omnibox-list scroll-thin scroll-fade"
      >
        {groups.map((group) => {
          const startIndex = cursor;
          cursor += group.items.length;
          return (
            <div key={group.key}>
              <span className="command-palette__heading">{group.label}</span>
              <div className="mission-control__omnibox-group-items">
                {group.items.map((entry, i) => {
                  const flatIndex = startIndex + i;
                  const isSelected = flatIndex === selectedIndex;
                  const Icon = entry.icon;
                  return (
                    <button
                      key={entry.id}
                      type="button"
                      id={omniboxOptionId(entry.id)}
                      role="option"
                      aria-selected={isSelected}
                      data-selected={isSelected ? "true" : undefined}
                      className="command-palette__row"
                      onMouseDown={(event) => event.preventDefault()}
                      onMouseEnter={() => onHoverIndex(flatIndex)}
                      onClick={() => onActivate(entry)}
                    >
                      <Icon size={ICON.MD} strokeWidth={2} className="command-palette__row-icon" />
                      <span className="command-palette__row-label">{entry.label}</span>
                      <span className="command-palette__row-trailing">
                        {entry.hint && <span className="command-palette__row-hint">{entry.hint}</span>}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </motion.div>
  );
}
