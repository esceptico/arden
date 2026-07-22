import { useEffect, useMemo, useRef } from "react";
import {
  CornerDownLeft,
  DollarSign,
  Edit3,
  GitBranch,
  HelpCircle,
  Layers,
  RotateCcw,
  Sparkles,
  Target,
  Trash2,
  type NtrpIcon,
} from "@/components/icons";
import clsx from "clsx";
import { motion } from "motion/react";
import { DURATION_POPOVER, EASE_DECELERATE } from "@/lib/tokens/motion";
import { useStore } from "@/stores";
import { ICON } from "@/lib/icons";
import { filterCommands, useCommandList, type CommandEntry } from "@/features/chat/lib/commands";
import { PickerRow } from "@/components/ui/PickerRow";
import { Caption } from "@/components/ui/Caption";

const BUILTIN_ICONS: Record<string, NtrpIcon> = {
  help: HelpCircle,
  clear: Trash2,
  compact: Layers,
  revert: RotateCcw,
  rename: Edit3,
  branch: GitBranch,
  cost: DollarSign,
  goal: Target,
};

function iconFor(entry: CommandEntry): NtrpIcon {
  if (entry.kind === "builtin") return BUILTIN_ICONS[entry.name] ?? HelpCircle;
  return Sparkles;
}

export function CommandPicker({
  query,
  onSelect,
}: {
  query: string;
  onSelect: (entry: CommandEntry) => void;
}) {
  const open = useStore((s) => s.commandPickerOpen);
  const index = useStore((s) => s.commandPickerIndex);
  const setIndex = useStore((s) => s.setCommandPickerIndex);
  const all = useCommandList();
  const filtered = useMemo(() => filterCommands(all, query), [all, query]);
  const containerRef = useRef<HTMLDivElement>(null);

  // Split into sections so we can render group headers and a divider, while
  // keeping a single linear `index` aligned with the flat `filtered` array.
  const builtins = useMemo(() => filtered.filter((c) => c.kind === "builtin"), [filtered]);
  const skills = useMemo(() => filtered.filter((c) => c.kind === "skill"), [filtered]);

  useEffect(() => {
    if (index >= filtered.length && filtered.length > 0) setIndex(filtered.length - 1);
    if (index < 0) setIndex(0);
  }, [filtered.length, index, setIndex]);

  useEffect(() => {
    const el = containerRef.current?.querySelector<HTMLElement>(`[data-cmd-idx="${index}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [index]);

  if (!open || filtered.length === 0) return null;

  const activeName = filtered[index]?.name;

  return (
    <motion.div
      className="surface-panel surface-popover absolute left-0 right-0 bottom-full mb-2 overflow-hidden"
      initial={{ opacity: 0, y: 4, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: DURATION_POPOVER, ease: EASE_DECELERATE }}
      style={{ transformOrigin: "bottom left" }}
    >
      <div ref={containerRef} className="max-h-[320px] overflow-y-auto scroll-thin">
        {builtins.length > 0 && (
          <Section title="Commands" entries={builtins} startIndex={0} activeIndex={index} onSelect={onSelect} setIndex={setIndex} />
        )}
        {skills.length > 0 && (
          <Section
            title="Skills"
            entries={skills}
            startIndex={builtins.length}
            activeIndex={index}
            onSelect={onSelect}
            setIndex={setIndex}
            withTopDivider={builtins.length > 0}
          />
        )}
      </div>
      <Footer activeName={activeName} />
    </motion.div>
  );
}

function Section({
  title,
  entries,
  startIndex,
  activeIndex,
  onSelect,
  setIndex,
  withTopDivider = false,
}: {
  title: string;
  entries: CommandEntry[];
  startIndex: number;
  activeIndex: number;
  onSelect: (entry: CommandEntry) => void;
  setIndex: (i: number) => void;
  withTopDivider?: boolean;
}) {
  return (
    <>
      {withTopDivider && <div className="h-px bg-line-soft mx-1.5" />}
      <div className="py-1.5 px-1.5">
        <Caption as="div" className="px-3 pb-1 pt-0.5 select-none">
          {title}
        </Caption>
        {entries.map((entry, i) => {
          const idx = startIndex + i;
          const Icon = iconFor(entry);
          const active = idx === activeIndex;
          return (
            <PickerRow
              key={`${entry.kind}:${entry.name}`}
              active={active}
              data-cmd-idx={idx}
              onMouseDown={(e) => {
                e.preventDefault();
                onSelect(entry);
              }}
              onMouseMove={() => setIndex(idx)}
              className="app-row group/cmd flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-ink-soft"
            >
              <span
                className={clsx(
                  "shrink-0 grid place-items-center w-5 h-5 rounded-md transition-colors duration-check ease-out",
                  active ? "text-ink-soft" : "text-faint",
                )}
              >
                <Icon size={ICON.XS} strokeWidth={2} />
              </span>
              <span className="font-mono text-sm font-medium text-ink shrink-0">
                /{entry.name}
              </span>
              <span className="text-sm text-muted truncate flex-1 min-w-0 text-right">
                {entry.description}
              </span>
            </PickerRow>
          );
        })}
      </div>
    </>
  );
}

function Footer({ activeName }: { activeName?: string }) {
  if (!activeName) return null;
  return (
    <div className="flex items-center gap-3 px-3 py-1.5 bg-surface-soft/60 text-2xs text-faint select-none">
      <Hint icon={<span className="font-mono">↑↓</span>} label="navigate" />
      <Hint icon={<CornerDownLeft size={ICON.XS} strokeWidth={2} />} label={`run /${activeName}`} />
      <span className="ml-auto">
        <Hint icon={<span className="font-mono">esc</span>} label="dismiss" />
      </span>
    </div>
  );
}

function Hint({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 rounded-[4px] bg-surface border border-line text-2xs text-muted">
        {icon}
      </span>
      <span>{label}</span>
    </span>
  );
}
