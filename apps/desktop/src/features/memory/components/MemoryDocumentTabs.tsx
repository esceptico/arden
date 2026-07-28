import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import clsx from "clsx";
import { useReducedMotion } from "motion/react";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";
import type { ContextMenuPosition } from "@/components/ui/ContextMenu";
import { MenuItem } from "@/components/ui/MenuItem";
import { getBoardMotion } from "@/lib/boardMotion";

interface MemoryDocumentTabsProps {
  paths: readonly string[];
  activeIndex: number;
  titleForPath: (path: string) => string;
  disabled?: boolean;
  onSelect: (index: number) => void;
  onClose: (index: number) => void;
  onOpenContextMenu: (index: number, path: string, trigger: HTMLElement, source: ContextMenuPosition["source"], x: number, y: number) => void;
}

interface TabOverflowState {
  overflowing: boolean;
  hiddenCount: number;
  edgeLeft: boolean;
  edgeRight: boolean;
}

const EMPTY_OVERFLOW: TabOverflowState = {
  overflowing: false,
  hiddenCount: 0,
  edgeLeft: false,
  edgeRight: false,
};

export function countFullyHiddenTabs(stripRect: Pick<DOMRect, "left" | "right">, tabRects: readonly Pick<DOMRect, "left" | "right">[]) {
  return tabRects.filter((rect) => rect.right <= stripRect.left + 0.5 || rect.left >= stripRect.right - 0.5).length;
}

/** The mock's complete open-page tab contract: shared moving indicator,
 * append/activate state supplied by the workspace, roving keyboard focus,
 * active-only close focus, edge fades, ensure-visible, and the +N menu. */
/** Strip edge fade (10px mask) plus a little air. */
const TAB_EDGE_INSET = 14;

export function MemoryDocumentTabs({ paths, activeIndex, titleForPath, disabled = false, onSelect, onClose, onOpenContextMenu }: MemoryDocumentTabsProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const restoreTabFocus = useRef(false);
  const tabsController = useRef<ReturnType<NonNullable<ReturnType<typeof getBoardMotion>>["tabs"]["bind"]>>(null);
  const pathsRef = useRef(paths);
  const onSelectRef = useRef(onSelect);
  const reducedMotion = useReducedMotion() ?? false;
  const [overflow, setOverflow] = useState<TabOverflowState>(EMPTY_OVERFLOW);
  const [menuOpen, setMenuOpen] = useState(false);
  const pathsSignature = paths.join("\u0000");
  const activePath = paths[activeIndex] ?? "";
  pathsRef.current = paths;
  onSelectRef.current = onSelect;

  useLayoutEffect(() => {
    const list = listRef.current;
    const motion = getBoardMotion();
    if (!list || !motion) return;
    const controller = motion.tabs.bind(list, {
      tabSelector: ".tab",
      valueAttribute: "data-page",
      activeClass: "on",
      onChange: ({ value }) => {
        if (value == null) return;
        const index = pathsRef.current.indexOf(value);
        if (index >= 0) onSelectRef.current(index);
      },
    });
    tabsController.current = controller;
    return () => {
      controller?.destroy();
      if (tabsController.current === controller) tabsController.current = null;
    };
  }, [pathsSignature]);

  useLayoutEffect(() => {
    if (!activePath) return;
    tabsController.current?.select(activePath, {
      emit: false,
      animate: !reducedMotion,
    });
  }, [activePath, reducedMotion]);

  const updateOverflow = useCallback(() => {
    const root = rootRef.current;
    const list = listRef.current;
    if (!root || !list) return;
    const motion = getBoardMotion();
    if (!motion) return;
    const readLength = motion.geometry.read;
    const tabs = Array.from(list.querySelectorAll<HTMLElement>('[role="tab"]'));
    const natural =
      tabs.reduce((width, tab) => width + tab.offsetWidth, 0) +
      readLength("--tab-bar-gap") * Math.max(0, tabs.length - 1) +
      // root.style.width below sets the WIDTH OF .mw-tab-strip itself (the
      // padded outer box), not just the tab content — has to add the
      // strip's own left+right padding back in or the last tab overflows
      // it. Was --tab-strip-chrome (a flat 8px, imprecise); this is the
      // strip's actual padding, exactly.
      readLength("--tab-bar-padding") * 2;
    const instruments = root.closest(".memory-ws")?.querySelector<HTMLElement>(".mw-instruments");
    const available = Math.max(
      readLength("--tab-overflow-min-width"),
      (instruments?.getBoundingClientRect().left ?? window.innerWidth) - root.getBoundingClientRect().left - readLength("--instrument-layout-gap"),
    );
    const hasOverflow = natural > available;
    const width = `${Math.round(hasOverflow ? available : natural)}px`;
    if (root.style.width !== width) root.style.width = width;
    if (!hasOverflow) list.scrollLeft = 0;

    const listRect = list.getBoundingClientRect();
    const next: TabOverflowState = {
      overflowing: hasOverflow,
      hiddenCount: hasOverflow
        ? countFullyHiddenTabs(
            listRect,
            tabs.map((tab) => tab.getBoundingClientRect()),
          )
        : 0,
      edgeLeft: hasOverflow && list.scrollLeft > 1,
      edgeRight: hasOverflow && list.scrollLeft < list.scrollWidth - list.clientWidth - 1,
    };
    setOverflow((current) =>
      current.overflowing === next.overflowing && current.hiddenCount === next.hiddenCount && current.edgeLeft === next.edgeLeft && current.edgeRight === next.edgeRight
        ? current
        : next,
    );
    if (!hasOverflow || next.hiddenCount === 0) setMenuOpen(false);
  }, []);

  useLayoutEffect(() => {
    const root = rootRef.current;
    const list = listRef.current;
    if (!root || !list) return;
    const frame = window.requestAnimationFrame(updateOverflow);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updateOverflow);
    observer?.observe(root);
    observer?.observe(list);
    const instruments = root.closest(".memory-ws")?.querySelector<HTMLElement>(".mw-instruments");
    if (instruments) observer?.observe(instruments);
    list.addEventListener("scroll", updateOverflow, { passive: true });
    window.addEventListener("resize", updateOverflow);
    return () => {
      window.cancelAnimationFrame(frame);
      observer?.disconnect();
      list.removeEventListener("scroll", updateOverflow);
      window.removeEventListener("resize", updateOverflow);
    };
  }, [pathsSignature, updateOverflow]);

  useLayoutEffect(() => {
    const list = listRef.current;
    const active = list?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[activeIndex];
    if (!list || !active) return;
    const listRect = list.getBoundingClientRect();
    const activeRect = active.getBoundingClientRect();
    let next = list.scrollLeft;
    // Land the tab CLEAR of the strip's 10px edge fade (memory.css masks
    // both ends when the list overflows) — scrolling it flush to the edge
    // leaves a newly opened tab dissolving under the mask.
    if (activeRect.left < listRect.left + TAB_EDGE_INSET) {
      next -= listRect.left + TAB_EDGE_INSET - activeRect.left;
    } else if (activeRect.right > listRect.right - TAB_EDGE_INSET) {
      next += activeRect.right - (listRect.right - TAB_EDGE_INSET);
    }
    if (next !== list.scrollLeft) {
      if (typeof list.scrollTo === "function") {
        list.scrollTo({
          left: next,
          behavior: reducedMotion ? "auto" : "smooth",
        });
      } else {
        list.scrollLeft = next;
      }
    }
    const frame = window.requestAnimationFrame(updateOverflow);
    return () => window.cancelAnimationFrame(frame);
  }, [activeIndex, paths.length, reducedMotion, updateOverflow]);

  useLayoutEffect(() => {
    if (!restoreTabFocus.current) return;
    restoreTabFocus.current = false;
    const frame = window.requestAnimationFrame(() => {
      listRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[activeIndex]?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeIndex, paths.length]);

  useEffect(() => {
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.defaultPrevented || disabled || paths.length === 0) return;
      if ((!event.metaKey && !event.ctrlKey) || event.altKey || event.key.toLowerCase() !== "w") return;
      event.preventDefault();
      onClose(activeIndex);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activeIndex, disabled, onClose, paths.length]);

  const closeAt = (index: number, restoreFocus = false) => {
    if (disabled) return;
    restoreTabFocus.current = restoreFocus;
    onClose(index);
  };

  const openContextMenu = (event: MouseEvent<HTMLDivElement>, index: number, path: string) => {
    event.preventDefault();
    const trigger = event.currentTarget.querySelector<HTMLButtonElement>('[role="tab"]');
    if (!trigger) return;
    onOpenContextMenu(index, path, trigger, "pointer", event.clientX, event.clientY);
  };

  const onSlotKeyDown = (event: KeyboardEvent<HTMLDivElement>, index: number, path: string) => {
    const target = event.target instanceof HTMLElement ? event.target.closest('[role="tab"]') : null;
    if (event.key === "Delete" && target) {
      event.preventDefault();
      closeAt(index, true);
      return;
    }
    if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
    event.preventDefault();
    const trigger = event.currentTarget.querySelector<HTMLButtonElement>('[role="tab"]');
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    onOpenContextMenu(index, path, trigger, "keyboard", rect.left + 12, rect.bottom - 4);
  };

  return (
    <div ref={rootRef} className={clsx("tabs mw-tab-strip", overflow.edgeLeft && "edge-left", overflow.edgeRight && "edge-right")} data-page-enter-item="chrome">
      <div ref={listRef} className="tab-strip mw-doc-tabs" role="tablist" aria-label="Open pages">
        {paths.map((path, index) => (
          <div
            key={path}
            className="tab-slot mw-doc-tab-slot"
            onKeyDown={(event) => onSlotKeyDown(event, index, path)}
            onContextMenu={(event) => openContextMenu(event, index, path)}
          >
            <button
              type="button"
              role="tab"
              data-page={path}
              id={`memory-note-tab-${index}`}
              aria-controls="memory-note-panel"
              aria-selected={index === activeIndex}
              tabIndex={index === activeIndex ? 0 : -1}
              disabled={disabled}
              className={clsx("tab mw-doc-tab", index === activeIndex && "on")}
            >
              {titleForPath(path)}
            </button>
            <button
              type="button"
              className="tab-close mw-doc-tab-x"
              aria-label={`Close ${titleForPath(path)}`}
              tabIndex={index === activeIndex ? 0 : -1}
              disabled={disabled}
              onClick={() => closeAt(index, true)}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <use href="#dp-close" />
              </svg>
            </button>
          </div>
        ))}
      </div>

      {overflow.overflowing && (
        <button
          type="button"
          className="tab-more mw-tab-more"
          aria-label={`${overflow.hiddenCount} more open ${overflow.hiddenCount === 1 ? "page" : "pages"}`}
          aria-expanded={menuOpen}
          aria-controls="memory-tab-overflow-menu"
          onClick={() => setMenuOpen((open) => !open)}
        >
          +{overflow.hiddenCount}
        </button>
      )}

      <AnchoredPopover
        id="memory-tab-overflow-menu"
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        anchor={rootRef}
        align="end"
        gap={8}
        variant="menu"
        ariaLabel="Open pages"
        className="mw-tab-menu"
      >
        {paths.map((path, index) => (
          <MenuItem
            key={path}
            context
            role="menuitem"
            tabIndex={-1}
            data-active={index === activeIndex ? "true" : undefined}
            onClick={() => {
              onSelect(index);
              setMenuOpen(false);
            }}
          >
            {titleForPath(path)}
          </MenuItem>
        ))}
      </AnchoredPopover>
    </div>
  );
}
