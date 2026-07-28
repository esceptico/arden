import { useCallback, useEffect, useMemo, useRef } from "react";
import { Command } from "cmdk";
import { Breadcrumbs } from "@/features/command-palette/components/Breadcrumbs";
import { Row } from "@/features/command-palette/components/Row";
import { filterEntries, groupBySection } from "@/lib/commandEntries/filter";
import { dismissTopBlockingOverlay } from "@/lib/overlayStack";
import { useEntries } from "@/hooks/useEntries";
import { SECTION_LABEL, type CommandEntry, type Crumb } from "@/lib/commandEntries/types";

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
}: {
  query: string;
  setQuery: (q: string) => void;
  index: number;
  setIndex: React.Dispatch<React.SetStateAction<number>>;
  crumbs: Crumb[];
  setCrumbs: React.Dispatch<React.SetStateAction<Crumb[]>>;
  onClose: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const rootEntries = useEntries();

  // Resolve the active view by following the crumb path from root.
  // If any segment goes stale (e.g. server data refreshed and an entry
  // disappeared), we collapse back to root rather than show a dead view.
  const { view, staleCrumbs } = useMemo(() => {
    let entries = rootEntries;
    let placeholder = "Search chats or run a command";
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
      // Choosing an entry IS navigation — only now does a takeover underneath
      // the palette give way, so that opening the palette over Memory and
      // dismissing it leaves you exactly where you were.
      dismissTopBlockingOverlay();
      void entry.run();
    }
  }

  const safe = filtered.length === 0 ? 0 : Math.min(Math.max(index, 0), filtered.length - 1);
  const selectedValue = filtered[safe]?.id ?? "";

  const grouped = useMemo(() => groupBySection(filtered), [filtered]);

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
      <div className="command-palette__search">
        <div className="command-palette__search-inner">
          <Breadcrumbs crumbs={crumbs} onJump={popTo} />
          <Command.Input
            ref={inputRef}
            value={query}
            onValueChange={setQuery}
            onKeyDown={(e) => {
              if (e.key === "Backspace" && query.length === 0 && crumbs.length > 0) {
                e.preventDefault();
                popCrumb();
                return;
              }
            }}
            placeholder={view.placeholder}
            spellCheck={false}
            className="command-palette__input"
          />
        </div>
      </div>

      {/* cmdk owns list semantics and selection; Arden keeps its ranked filter
          and nested-view direction. */}
      <Command.List
        ref={listRef}
        id={LIST_ID}
        className="command-palette__results scroll-thin scroll-fade"
      >
          <div>
            {filtered.length === 0 ? (
              <Command.Empty className="command-palette__empty">
                Nothing matches.
              </Command.Empty>
            ) : (
              grouped.map(({ section, items }) => (
                <Command.Group
                  key={section}
                  heading={
                    <span className="command-palette__heading">
                      {SECTION_LABEL[section]}
                    </span>
                  }
                  className="command-palette__group"
                >
                    {items.map((entry) => {
                      return (
                        <Row
                          key={entry.id}
                          entry={entry}
                          optionId={optionId(entry.id)}
                          onClick={() => activate(entry)}
                        />
                      );
                    })}
                </Command.Group>
              ))
            )}
          </div>
      </Command.List>
      <footer className="command-palette__footer">
        <span><kbd className="arden-kbd">Enter</kbd> opens the selected result</span>
      </footer>
    </Command>
  );
}
