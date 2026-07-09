import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type Ref,
} from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import clsx from "clsx";
import { Check } from "lucide-react";
import { useProximityHover, useRegisterProximityItem } from "@/hooks/useProximityHover";
import { SPRING_LAYOUT, SPRING_TAP, SPRING_PROXIMITY, EXIT_FAST, MOTION } from "@/lib/tokens/motion";

interface RadioGroupContextValue {
  value: string;
  onChange: (value: string) => void;
  reduced: boolean;
  dense: boolean;
  registerItem: (index: number, el: HTMLElement | null) => void;
  registerValue: (index: number, value: string) => void;
  activeIndex: number | null;
  selectedIndex: number;
}

const RadioGroupContext = createContext<RadioGroupContextValue | null>(null);

function useRadioGroupContext() {
  const ctx = useContext(RadioGroupContext);
  if (!ctx) throw new Error("RadioGroupItem must be used within a RadioGroup");
  return ctx;
}

// Translucent fills that read in both themes (FF's bg-active / bg-hover).
const SELECTED_FILL = "color-mix(in oklab, var(--color-ink) 7%, transparent)";
const HOVER_FILL = "color-mix(in oklab, var(--color-ink) 4%, transparent)";

/**
 * Roving keyboard for any `[role="radio"]` group: Arrow/Home/End move focus AND
 * auto-select via each radio's `data-value`. The single definition — used by
 * RadioGroup and by bespoke radio layouts that can't adopt the row-list shell
 * (e.g. the thinking-animation preview-card grid in AppearanceTab) so the
 * behaviour is never re-implemented at a call site.
 */
export function radioGroupKeyDown(
  e: KeyboardEvent<HTMLElement>,
  value: string,
  onChange: (v: string) => void,
) {
  if (!["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) return;
  const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('[role="radio"]'));
  if (items.length === 0) return;
  e.preventDefault();
  const current = items.indexOf(e.target as HTMLElement);
  const idx = current === -1 ? 0 : current;
  let next = idx;
  if (["ArrowDown", "ArrowRight"].includes(e.key)) next = (idx + 1) % items.length;
  else if (["ArrowUp", "ArrowLeft"].includes(e.key)) next = (idx - 1 + items.length) % items.length;
  else if (e.key === "Home") next = 0;
  else if (e.key === "End") next = items.length - 1;
  const target = items[next];
  target.focus();
  const v = target.getAttribute("data-value");
  if (v && v !== value) onChange(v);
}

interface RadioGroupProps {
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
  "aria-label"?: string;
  className?: string;
  /** Compact rows + instant highlight travel — for popovers/menus, where the
   *  settings-page density and layout spring read oversized and slow. */
  dense?: boolean;
  ref?: Ref<HTMLDivElement>;
}

/**
 * Vertical list where the whole row is the radio target. Absolutely-positioned
 * motion layers float over the rows: the selected-row background (springs
 * between rows, dims slightly while another row is hovered), a proximity hover
 * background (springs to the row under the pointer via useProximityHover), and
 * a focus ring. Ported from Fluid Functionalism's RadioGroup — visible row +
 * motion + hand-rolled a11y only.
 *
 * a11y: role="radiogroup" on the container, role="radio" + aria-checked per
 * row, roving tabIndex (only the checked row tabbable, first if none),
 * Arrow/Home/End move focus AND select (auto-activate), Space/Enter select.
 */
export function RadioGroup({
  value,
  onChange,
  children,
  "aria-label": ariaLabel,
  className,
  dense = false,
  ref,
}: RadioGroupProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const valuesRef = useRef<Map<number, string>>(new Map());
  const reduced = !!useReducedMotion();
  const { activeIndex, setActiveIndex, itemRects, sessionRef, handlers, registerItem } =
    useProximityHover(containerRef);
  const [focusedIndex, setFocusedIndex] = useState<number | null>(null);

  const registerValue = (index: number, v: string) => {
    valuesRef.current.set(index, v);
  };

  const selectedIndex = (() => {
    for (const [index, v] of valuesRef.current) if (v === value) return index;
    return -1;
  })();

  const selectedRect = selectedIndex >= 0 ? itemRects[selectedIndex] : null;
  const activeRect = activeIndex !== null ? itemRects[activeIndex] : null;
  const focusRect = focusedIndex !== null ? itemRects[focusedIndex] : null;
  const isHoveringOther = activeIndex !== null && activeIndex !== selectedIndex;

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => radioGroupKeyDown(e, value, onChange);

  const ctx: RadioGroupContextValue = {
    value,
    onChange,
    reduced,
    dense,
    registerItem,
    registerValue,
    activeIndex,
    selectedIndex,
  };

  return (
    <div
      ref={(node) => {
        containerRef.current = node;
        if (typeof ref === "function") ref(node);
        else if (ref) (ref as React.MutableRefObject<HTMLDivElement | null>).current = node;
      }}
      role="radiogroup"
      aria-label={ariaLabel}
      className={clsx("relative flex w-full max-w-full select-none flex-col", className)}
      onMouseEnter={handlers.onMouseEnter}
      onMouseMove={handlers.onMouseMove}
      onMouseLeave={handlers.onMouseLeave}
      onFocus={(e) => {
        const indexAttr = (e.target as HTMLElement)
          .closest("[data-proximity-index]")
          ?.getAttribute("data-proximity-index");
        if (indexAttr == null) return;
        const idx = Number(indexAttr);
        setActiveIndex(idx);
        setFocusedIndex((e.target as HTMLElement).matches(":focus-visible") ? idx : null);
      }}
      onBlur={(e) => {
        if (containerRef.current?.contains(e.relatedTarget as Node)) return;
        setFocusedIndex(null);
        setActiveIndex(null);
      }}
      onKeyDown={onKeyDown}
    >
      {selectedRect && (
        <motion.div
          aria-hidden
          className={clsx("pointer-events-none absolute", dense ? "rounded-md" : "rounded-lg")}
          style={{ background: SELECTED_FILL }}
          initial={false}
          animate={{
            top: selectedRect.top,
            left: selectedRect.left,
            width: selectedRect.width,
            height: selectedRect.height,
            opacity: isHoveringOther ? 0.7 : 1,
          }}
          transition={
            reduced
              ? { duration: 0 }
              : { ...(dense ? SPRING_PROXIMITY : SPRING_LAYOUT), opacity: { duration: MOTION.fast } }
          }
        />
      )}

      <AnimatePresence>
        {activeRect && (
          <motion.div
            key={sessionRef.current}
            aria-hidden
            className={clsx("pointer-events-none absolute", dense ? "rounded-md" : "rounded-lg")}
            style={{ background: HOVER_FILL }}
            initial={{
              opacity: 0,
              top: activeRect.top,
              left: activeRect.left,
              width: activeRect.width,
              height: activeRect.height,
            }}
            animate={{
              opacity: 1,
              top: activeRect.top,
              left: activeRect.left,
              width: activeRect.width,
              height: activeRect.height,
            }}
            exit={{ opacity: 0, transition: EXIT_FAST }}
            transition={
              reduced
                ? { duration: 0 }
                : { ...(dense ? SPRING_PROXIMITY : SPRING_TAP), opacity: { duration: MOTION.fast } }
            }
          />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {focusRect && (
          <motion.div
            aria-hidden
            className="pointer-events-none absolute z-20 rounded-lg ring-2 ring-accent"
            initial={false}
            animate={{
              left: focusRect.left - 2,
              top: focusRect.top - 2,
              width: focusRect.width + 4,
              height: focusRect.height + 4,
            }}
            exit={{ opacity: 0, transition: EXIT_FAST }}
            transition={reduced ? { duration: 0 } : dense ? SPRING_PROXIMITY : SPRING_TAP}
          />
        )}
      </AnimatePresence>

      <RadioGroupContext.Provider value={ctx}>{children}</RadioGroupContext.Provider>
    </div>
  );
}

interface RadioGroupItemProps {
  value: string;
  index: number;
  label?: string;
  description?: string;
  /** Selection glyph: the default radio circle, or a menu-style checkmark
   *  (no resting ring — unselected rows show an empty slot). */
  indicator?: "radio" | "check";
  children?: ReactNode;
}

export function RadioGroupItem({
  value,
  index,
  label,
  description,
  indicator = "radio",
  children,
}: RadioGroupItemProps) {
  const { value: selectedValue, onChange, reduced, dense, registerItem, registerValue, activeIndex } =
    useRadioGroupContext();
  const itemRef = useRef<HTMLDivElement | null>(null);

  useRegisterProximityItem(registerItem, index, itemRef);
  useEffect(() => {
    registerValue(index, value);
  }, [index, value, registerValue]);

  const isSelected = selectedValue === value;
  const isActive = activeIndex === index;

  return (
    <div
      ref={itemRef}
      data-proximity-index={index}
      data-value={value}
      role="radio"
      aria-checked={isSelected}
      aria-label={label}
      tabIndex={isSelected ? 0 : -1}
      onClick={() => onChange(value)}
      onKeyDown={(e) => {
        if (e.key === " " || e.key === "Enter") {
          e.preventDefault();
          onChange(value);
        }
      }}
      className={clsx(
        "relative z-10 flex items-center outline-none",
        dense ? "gap-2 rounded-md px-2 py-[5px]" : "gap-2.5 rounded-lg px-3 py-2",
      )}
    >
      <span className="relative h-[15px] w-[15px] shrink-0">
        {indicator === "radio" && (
          <span
            aria-hidden
            className={clsx(
              "absolute inset-0 rounded-full border-[1.5px] transition-colors duration-100",
              isSelected
                ? "border-transparent"
                : isActive
                  ? "border-line-strong"
                  : "border-line",
            )}
          />
        )}
        <AnimatePresence>
          {isSelected && (
            <motion.span
              aria-hidden
              className="absolute inset-0 flex items-center justify-center"
              initial={reduced ? false : { opacity: 0, scale: dense ? 0.6 : 0.3 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: dense ? 0.6 : 0.3, transition: { duration: MOTION.fast } }}
              transition={reduced ? { duration: 0 } : dense ? SPRING_PROXIMITY : SPRING_TAP}
            >
              {indicator === "check" ? (
                <Check size={13} strokeWidth={2.25} className="text-ink" />
              ) : (
                <span className="h-[8px] w-[8px] rounded-full bg-accent" />
              )}
            </motion.span>
          )}
        </AnimatePresence>
      </span>

      {children ?? (
        <span className="flex min-w-0 flex-col">
          <span
            className={clsx(
              "text-[13px] leading-snug transition-colors duration-100",
              isSelected ? "font-semibold text-ink" : isActive ? "text-ink" : "text-muted",
            )}
          >
            {label}
          </span>
          {description && (
            <span className="text-[12px] leading-snug text-faint">{description}</span>
          )}
        </span>
      )}
    </div>
  );
}

export default RadioGroup;
