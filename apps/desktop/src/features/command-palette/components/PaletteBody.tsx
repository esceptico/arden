import { useCallback, useEffect, useMemo, useRef } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Command } from "cmdk";
import { Search } from "@/components/icons";
import { ICON } from "@/lib/icons";
import { EASE_EMPHASIZED, MOTION } from "@/lib/tokens/motion";
import { Breadcrumbs } from "@/features/command-palette/components/Breadcrumbs";
import { Row } from "@/features/command-palette/components/Row";
import { filterEntries, groupBySection } from "@/features/command-palette/lib/filter";
import { useEntries } from "@/features/command-palette/hooks/useEntries";
import { SECTION_LABEL, type CommandEntry, type Crumb } from "@/features/command-palette/types";
import { ScrollFadeTop } from "@/components/ui/ScrollBlur";
import { SLIDE_PAGE_VARIANTS } from "@/components/ui/TabPanels";

const LIST_ID = "command-palette-listbox";
const optionId = (entryId: string) => `${LIST_ID}-opt-${entryId}`;

export function PaletteBody({
  query,
  setQuery,
  index,
  setIndex,
  crumbs,
  setCrumbs,
  onClose,
  onAgentSubmit,
  morph = false,
}: {
  query: string;
  setQuery: (q: string) => void;
  index: number;
  setIndex: React.Dispatch<React.SetStateAction<number>>;
  crumbs: Crumb[];
  setCrumbs: React.Dispatch<React.SetStateAction<Crumb[]>>;
  onClose: () => void;
  onAgentSubmit: (query: string) => void;
  morph?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const rootEntries = useEntries();

  // Resolve the active view by following the crumb path from root.
  // If any segment goes stale (e.g. server data refreshed and an entry
  // disappeared), we collapse back to root rather than show a dead view.
  const { view, staleCrumbs } = useMemo(() => {
    let entries = rootEntries;
    let placeholder = "Search commands, sessions, memory…";
    for (let i = 0; i < crumbs.length; i++) {
      const crumb = crumbs[i];
      const folder = entries.find((e) => e.id === crumb.id && e.children);
      if (!folder || !folder.children) {
        return {
          view: { placeholder, entries: rootEntries },
          staleCrumbs: true,
        };
      }
      const next = folder.children();
      entries = next.entries;
      placeholder = next.placeholder;
    }
    return {
      view: { placeholder, entries },
      staleCrumbs: false,
    };
  }, [rootEntries, crumbs]);

  // Drop stale path silently — caller never sees the inconsistency.
  useEffect(() => {
    if (staleCrumbs) setCrumbs([]);
  }, [staleCrumbs, setCrumbs]);

  const filtered = useMemo(() => filterEntries(view.entries, query), [view.entries, query]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Reset index when filter or path changes.
  useEffect(() => {
    setIndex(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, crumbs.length]);

  // Clear stale query when descending into a sub-view — otherwise the
  // user's "switch model" query immediately filters the provider list
  // to nothing.
  const pushCrumb = useCallback(
    (entry: CommandEntry) => {
      setCrumbs((prev) => [...prev, { id: entry.id, label: entry.label }]);
      setQuery("");
    },
    [setCrumbs, setQuery],
  );

  const popCrumb = useCallback(() => {
    setCrumbs((prev) => (prev.length === 0 ? prev : prev.slice(0, -1)));
    setQuery("");
  }, [setCrumbs, setQuery]);

  const popTo = useCallback(
    (depth: number) => {
      setCrumbs((prev) => (prev.length <= depth ? prev : prev.slice(0, depth)));
      setQuery("");
    },
    [setCrumbs, setQuery],
  );

  function activate(entry: CommandEntry) {
    if (entry.children) {
      pushCrumb(entry);
      return;
    }
    if (entry.run) {
      onClose();
      void entry.run();
    }
  }

  const safe = filtered.length === 0 ? 0 : Math.min(Math.max(index, 0), filtered.length - 1);
  const selectedValue = filtered[safe]?.id ?? "";

  const grouped = useMemo(() => groupBySection(filtered), [filtered]);

  // Page identity = the crumb path. Drives the AnimatePresence swap so each
  // hierarchy level mounts as a fresh panel. `depth` alone would be ambiguous
  // if two sibling sub-views ever shared a depth; the joined id chain is exact.
  const pageKey = crumbs.length === 0 ? "root" : crumbs.map((c) => c.id).join("/");
  const depth = crumbs.length;
  const prevDepth = useRef(depth);
  const direction = depth >= prevDepth.current ? 1 : -1;
  useEffect(() => {
    prevDepth.current = depth;
  }, [depth]);

  return (
    <Command
      value={selectedValue}
      onValueChange={(value) => {
        const nextIndex = filtered.findIndex((entry) => entry.id === value);
        if (nextIndex >= 0) setIndex(nextIndex);
      }}
      shouldFilter={false}
      loop
      className="contents"
      label="Command palette"
    >
      <motion.div layout={morph} className="relative px-4 pt-3 pb-2.5">
        <Search
          size={ICON.MD}
          strokeWidth={2}
          className="absolute left-4 top-[22px] text-faint pointer-events-none"
        />
        <div className="flex items-center gap-1.5 pl-6">
          <Breadcrumbs crumbs={crumbs} onJump={popTo} />
          <Command.Input
            ref={inputRef}
            value={query}
            onValueChange={setQuery}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                const command = query.trim();
                if (!command) return;
                e.preventDefault();
                onAgentSubmit(command);
                onClose();
                return;
              }
              if (e.key === "Backspace" && query.length === 0 && crumbs.length > 0) {
                e.preventDefault();
                popCrumb();
                return;
              }
            }}
            placeholder={view.placeholder}
            spellCheck={false}
            className="flex-1 min-w-0 h-8 bg-transparent text-md text-ink placeholder:text-muted outline-none"
          />
          {query.trim() && (
            <span className="shrink-0 text-2xs text-faint" aria-hidden>
              ⌘↵ ask agent
            </span>
          )}
        </div>
      </motion.div>

      {/* cmdk owns list semantics and selection; Arden keeps its ranked filter
          and nested-view direction. */}
      <Command.List
        ref={listRef}
        id={LIST_ID}
        className="overflow-y-auto overflow-x-hidden scroll-thin pb-2"
      >
        <ScrollFadeTop />
        <AnimatePresence mode="wait" custom={direction} initial={false}>
          <motion.div
            key={pageKey}
            custom={direction}
            variants={SLIDE_PAGE_VARIANTS}
            initial="enter"
            animate="center"
            exit="exit"
            transition={{ duration: MOTION.palette, ease: EASE_EMPHASIZED }}
          >
            {filtered.length === 0 ? (
              <Command.Empty className="grid place-items-center min-h-[120px] text-sm italic text-muted">
                Nothing matches.
              </Command.Empty>
            ) : (
              grouped.map(({ section, items }) => (
                <Command.Group
                  key={section}
                  heading={
                    <span className="block px-2.5 pt-3 pb-1 text-2xs font-medium uppercase tracking-[0.10em] text-faint">
                      {SECTION_LABEL[section]}
                    </span>
                  }
                  className="px-1.5"
                >
                    {items.map((entry) => {
                      const isActive = entry === filtered[safe];
                      return (
                        <Row
                          key={entry.id}
                          entry={entry}
                          active={isActive}
                          optionId={optionId(entry.id)}
                          onClick={() => activate(entry)}
                        />
                      );
                    })}
                </Command.Group>
              ))
            )}
          </motion.div>
        </AnimatePresence>
      </Command.List>
    </Command>
  );
}
