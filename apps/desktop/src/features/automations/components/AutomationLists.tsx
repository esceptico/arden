import { useMemo } from "react";
import { AnimatePresence } from "motion/react";
import { CalendarClock } from "lucide-react";
import { useStore } from "@/stores";
import type { Automation, AutomationSuggestion } from "@/api/types";
import { templatesByCategory, type AutomationTemplate } from "@/features/automations/lib/templates";
import { Button } from "@/components/ui/Button";
import { Empty } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Tab as TabItem } from "@/components/ui/Tabs";
import { AutomationCard } from "@/features/automations/components/AutomationCard";
import { TemplateCard } from "@/features/automations/components/TemplateCard";
import { SuggestionsSection } from "@/features/automations/components/SuggestionsSection";

/** Designed loading placeholder for the automation lists — card-shaped skeletons
 *  in the same grid as the real cards, instead of a bare "Loading…" string. */
function ListLoadingSkeleton() {
  return (
    <div
      className="grid grid-cols-[repeat(auto-fit,minmax(280px,1fr))] gap-2.5"
      role="status"
      aria-label="Loading automations…"
    >
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} height={84} radius={12} />
      ))}
    </div>
  );
}

export function AutomationTab({
  value,
  label,
  count,
}: {
  value: string;
  label: string;
  count?: number;
}) {
  return (
    <TabItem
      value={value}
      className="inline-flex h-9 items-center gap-1.5 text-base font-medium tracking-[-0.005em] text-muted transition-colors hover:text-ink data-[active=true]:text-ink"
    >
      {label}
      {count != null && count > 0 && (
        <span className="inline-flex h-[18px] min-w-[20px] items-center justify-center rounded-full px-1.5 text-2xs font-medium tabular-nums bg-surface-soft text-muted group-data-[active=true]:bg-ink group-data-[active=true]:text-on-ink">
          {count}
        </span>
      )}
    </TabItem>
  );
}

// ─── Active list ────────────────────────────────────────────────────

export function ActiveList({
  automations,
  onEdit,
  onPickTemplate,
  onCreate,
}: {
  automations: Automation[] | null;
  onEdit: (a: Automation) => void;
  onPickTemplate: () => void;
  onCreate: () => void;
}) {
  if (automations === null) {
    return <ListLoadingSkeleton />;
  }
  if (automations.length === 0) {
    return (
      <Empty
        icon={CalendarClock}
        hint="Start from a template, or write a prompt and a schedule from scratch."
        action={
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="md" onClick={onPickTemplate}>
              Browse templates
            </Button>
            <Button variant="quiet" size="md" onClick={onCreate}>
              Start from scratch
            </Button>
          </div>
        }
      >
        No automations yet.
      </Empty>
    );
  }
  // Area agents are ordinary automations (edit, pause, reschedule like any
  // other) — the section split only keeps the user's own from being
  // interleaved with the seeded per-area set.
  const areaAgents = automations.filter((a) => a.task_id.startsWith("area:"));
  const own = automations.filter((a) => !a.task_id.startsWith("area:"));

  return (
    <div className="grid content-start gap-6">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(280px,1fr))] gap-2.5">
        <AnimatePresence mode="popLayout" initial={false}>
          {own.map((automation) => (
            <AutomationCard
              key={automation.task_id}
              automation={automation}
              onEdit={() => onEdit(automation)}
            />
          ))}
        </AnimatePresence>
      </div>
      {areaAgents.length > 0 && (
        <section className="grid gap-2">
          <h3 className="m-0 text-xs font-medium uppercase tracking-[0.08em] text-muted">
            Area agents
          </h3>
          <div className="grid grid-cols-[repeat(auto-fit,minmax(280px,1fr))] gap-2.5">
            <AnimatePresence mode="popLayout" initial={false}>
              {areaAgents.map((automation) => (
                <AutomationCard
                  key={automation.task_id}
                  automation={automation}
                  onEdit={() => onEdit(automation)}
                />
              ))}
            </AnimatePresence>
          </div>
        </section>
      )}
    </div>
  );
}

export function SystemList({
  automations,
  onEdit,
}: {
  automations: Automation[] | null;
  onEdit: (a: Automation) => void;
}) {
  if (automations === null) {
    return <ListLoadingSkeleton />;
  }
  if (automations.length === 0) {
    return (
      <Empty
        icon={CalendarClock}
        hint="Knowledge reflection, retention, and health checks are seeded by the server when memory is enabled."
      >
        No system automations.
      </Empty>
    );
  }
  return (
    <div className="grid gap-3">
      <div className="text-sm text-muted leading-[1.5] max-w-[720px]">
        Background knowledge work is automatic. What it does is fixed; when it runs is yours —
        adjust the schedule, pause, or run now.
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(280px,1fr))] gap-2.5">
        <AnimatePresence mode="popLayout" initial={false}>
          {automations.map((automation) => (
            <AutomationCard
              key={automation.task_id}
              automation={automation}
              onEdit={() => onEdit(automation)}
            />
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}

// ─── Templates list ─────────────────────────────────────────────────

export function TemplatesList({
  onPick,
  onPickSuggestion,
}: {
  onPick: (template: AutomationTemplate) => void;
  onPickSuggestion: (suggestion: AutomationSuggestion) => void;
}) {
  const groups = useMemo(() => templatesByCategory(), []);
  const suggestions = useStore((s) => s.automationSuggestions);
  return (
    <div className="grid gap-6 content-start">
      <SuggestionsSection suggestions={suggestions} onPick={onPickSuggestion} />
      {groups.map(({ category, items }) => (
        <section key={category} className="grid gap-2">
          <h3 className="m-0 text-xs font-medium uppercase tracking-[0.08em] text-muted">
            {category}
          </h3>
          <div className="grid grid-cols-[repeat(auto-fit,minmax(280px,1fr))] gap-2.5">
            {items.map((template) => (
              <TemplateCard key={template.id} template={template} onPick={() => onPick(template)} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
