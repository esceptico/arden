import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Check, ChevronDown } from "lucide-react";
import clsx from "clsx";
import { ICON } from "@/lib/icons";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";

export interface SelectOption {
  value: string;
  label: string;
  /** Secondary line under the label (option description). */
  description?: string;
}

interface SelectProps {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  /** Accessible name for the control — required (mirrors a native <select>'s label). */
  "aria-label"?: string;
  /** Shown on the trigger when `value` matches no option. */
  placeholder?: string;
  /** Extra classes on the trigger button (width, etc.). */
  className?: string;
  disabled?: boolean;
}

/**
 * Unified select. A trigger button (role="combobox") shows the selected label +
 * a chevron and opens a portaled listbox of options. The portal, outside-click,
 * Escape and entrance/exit motion all come from AnchoredPopover — never
 * re-implemented here.
 *
 * The listbox a11y is hand-rolled on the visible rows (role="listbox" +
 * role="option", roving tabindex, Arrow/Home/End, Enter/Space select, type-ahead)
 * since AnchoredPopover's `menu` variant is the wrong (menuitem) keyboard model.
 *
 * Color language: the selected option carries a neutral ink overlay + ink text
 * (never an accent hue) and a trailing ink-coloured check; the focus ring is
 * accent-soft. Trigger matches `.input-field` so it sits with other controls.
 */
export function Select({
  value,
  onChange,
  options,
  "aria-label": ariaLabel,
  placeholder = "Select…",
  className,
  disabled = false,
}: SelectProps) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const typeahead = useRef<{ query: string; at: number }>({ query: "", at: 0 });

  const selected = options.find((o) => o.value === value);
  const selectedIndex = options.findIndex((o) => o.value === value);

  const close = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const select = (v: string) => {
    onChange(v);
    setOpen(false);
    requestAnimationFrame(() => triggerRef.current?.focus());
  };

  // Move focus onto the selected (or first) option once the listbox mounts, so
  // Arrow keys work immediately — AnchoredPopover's focus-into-panel only fires
  // for the `menu` variant, which we don't use.
  useEffect(() => {
    if (!open) return;
    const id = requestAnimationFrame(() => {
      const items = listRef.current?.querySelectorAll<HTMLElement>('[role="option"]');
      if (!items || items.length === 0) return;
      (items[selectedIndex >= 0 ? selectedIndex : 0] ?? items[0]).focus();
    });
    return () => cancelAnimationFrame(id);
  }, [open, selectedIndex]);

  const onTriggerKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      setOpen(true);
    }
  };

  const focusOption = (index: number) => {
    const items = listRef.current?.querySelectorAll<HTMLElement>('[role="option"]');
    items?.[index]?.focus();
  };

  const onListKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const count = options.length;
    if (count === 0) return;
    const current = options.findIndex(
      (o) => o.value === (e.target as HTMLElement).getAttribute("data-value"),
    );
    const idx = current === -1 ? 0 : current;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      focusOption((idx + 1) % count);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      focusOption((idx - 1 + count) % count);
    } else if (e.key === "Home") {
      e.preventDefault();
      focusOption(0);
    } else if (e.key === "End") {
      e.preventDefault();
      focusOption(count - 1);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (current !== -1) select(options[current].value);
    } else if (e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) {
      // Type-ahead: accumulate keystrokes within 500ms, jump to the first
      // option whose label starts with the typed prefix.
      const now = Date.now();
      const ta = typeahead.current;
      ta.query = now - ta.at > 500 ? e.key : ta.query + e.key;
      ta.at = now;
      const q = ta.query.toLowerCase();
      const match = options.findIndex((o) => o.label.toLowerCase().startsWith(q));
      if (match !== -1) focusOption(match);
    }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onTriggerKeyDown}
        className={clsx(
          "inline-flex h-8 items-center justify-between gap-2 rounded-md border bg-transparent px-2.5 text-base outline-none transition-[border-color,box-shadow] duration-palette ease-out",
          "border-line hover:border-line-strong focus-visible:border-accent focus-visible:shadow-[0_0_0_3px_var(--color-accent-soft)]",
          "disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-line",
          className,
        )}
      >
        <span className={clsx("min-w-0 flex-1 truncate text-left", selected ? "text-ink" : "text-faint")}>
          {selected?.label ?? placeholder}
        </span>
        <ChevronDown
          size={ICON.SM}
          strokeWidth={2}
          className={clsx(
            "shrink-0 text-faint transition-transform duration-palette ease-out",
            open && "rotate-180",
          )}
        />
      </button>

      <AnchoredPopover
        open={open}
        onClose={close}
        anchor={triggerRef}
        ariaLabel={ariaLabel}
        className="max-h-[min(20rem,60vh)] min-w-[12rem] overflow-y-auto p-1"
      >
        <div
          ref={listRef}
          role="listbox"
          aria-label={ariaLabel}
          onKeyDown={onListKeyDown}
          className="flex flex-col gap-0.5 outline-none"
        >
          {options.map((opt) => (
            <SelectItem
              key={opt.value}
              option={opt}
              selected={opt.value === value}
              tabbable={opt.value === value || (selectedIndex === -1 && opt === options[0])}
              onSelect={() => select(opt.value)}
            />
          ))}
        </div>
      </AnchoredPopover>
    </>
  );
}

const SELECTED_FILL = "color-mix(in oklab, var(--color-ink) 7%, transparent)";

function SelectItem({
  option,
  selected,
  tabbable,
  onSelect,
}: {
  option: SelectOption;
  selected: boolean;
  tabbable: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      data-value={option.value}
      tabIndex={tabbable ? 0 : -1}
      onClick={onSelect}
      className={clsx(
        "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm outline-none",
        "transition-[background-color,color,scale] duration-check ease-out active:scale-[0.98]",
        selected
          ? "text-ink"
          : "text-ink-soft hover:bg-ink/[0.06] hover:text-ink focus-visible:bg-ink/[0.06] focus-visible:text-ink",
      )}
      style={selected ? { background: SELECTED_FILL } : undefined}
    >
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate">{option.label}</span>
        {option.description && (
          <span className="truncate text-xs text-faint">{option.description}</span>
        )}
      </span>
      {selected && <Check size={ICON.SM} strokeWidth={2.5} className="shrink-0 text-ink" />}
    </button>
  );
}

export default Select;
