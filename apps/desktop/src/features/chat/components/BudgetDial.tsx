import clsx from "clsx";
import { useStore } from "@/stores";
import { RollingToken } from "@/components/ui/RollingToken";
import { HoverPopover } from "@/components/ui/HoverPopover";

/** Compact two-scale budget meter. The outer arc is current model-visible
 * context against the model's hard window; the inner arc is transcript
 * messages against the compaction cap. */

const SIZE = 18;
const STROKE = 2.2;
const OUTER_R = SIZE / 2 - STROKE / 2 - 0.5;
const INNER_R = OUTER_R - STROKE - 1.2;
const OUTER_C = 2 * Math.PI * OUTER_R;
const INNER_C = 2 * Math.PI * INNER_R;

function ratioColor(ratio: number): string {
  if (ratio >= 0.9) return "var(--color-bad)";
  if (ratio >= 0.7) return "var(--color-warn)";
  return "var(--color-ink-soft)";
}

function formatTokens(n: number): string {
  if (n < 1000) return `${n}`;
  if (n < 10000) return `${(n / 1000).toFixed(1)}k`;
  return `${Math.round(n / 1000)}k`;
}

function formatCost(n: number): string {
  if (n === 0) return "$0";
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(3)}`;
}

function formatPct(n: number): string {
  return `${Math.round(n * 100)}%`;
}

export function BudgetDial() {
  const usage = useStore((s) => s.usage);
  const serverConfig = useStore((s) => s.serverConfig);
  const currentSessionId = useStore((s) => s.currentSessionId);

  // An unsaved Home composer uses the configured default. Real sessions only
  // use their history snapshot, so switching models cannot flash global limits.
  const defaultBudget = serverConfig
    ? {
        model: serverConfig.chat_model,
        hardLimit: serverConfig.chat_model_max_context,
        compactionTrigger: serverConfig.compaction_token_trigger,
        messageLimit: serverConfig.max_messages,
      }
    : null;
  const budget = currentSessionId === null ? defaultBudget : usage.contextBudget;
  const hardLimit = budget?.hardLimit ?? null;
  const tokenTrigger = budget?.compactionTrigger ?? null;
  const messageLimit = budget?.messageLimit ?? 0;
  const tokenRatio = hardLimit && hardLimit > 0
    ? Math.min(1, usage.contextInputTokens / hardLimit)
    : 0;
  const messageRatio = usage.messageCount !== null && messageLimit > 0
    ? Math.min(1, usage.messageCount / messageLimit)
    : 0;
  const maxRatio = Math.max(tokenRatio, messageRatio);
  const hasAnyData = usage.contextInputTokens > 0 || (usage.messageCount ?? 0) > 0 || usage.totalCost > 0;
  const messageCountLabel = usage.messageCount ?? "unknown";

  const compactLabel = usage.contextInputTokens > 0
    ? formatTokens(usage.contextInputTokens)
    : "—";

  return (
    <span className="inline-flex items-center">
      <HoverPopover
        anchor="right"
        dismissOnOutsideClick
        className="w-[300px] p-3 text-sm"
        trigger={({ ref, open, toggle, hoverProps }) => (
          <button
            ref={ref}
            type="button"
            {...hoverProps}
            onClick={toggle}
            aria-label="Context budget"
            aria-expanded={open}
            title={
              hasAnyData
                ? `${formatTokens(usage.contextInputTokens)} / ${hardLimit ? formatTokens(hardLimit) : "unknown"} context tokens · ${messageCountLabel} / ${messageLimit || "unknown"} msgs`
                : "Context budget"
            }
            className={clsx(
              "budget-trigger composer-toolbar-control inline-flex items-center gap-1.5 h-7 px-2 rounded-[var(--r-control)]",
              "text-[length:var(--text-label)] text-muted transition-[background-color,color,scale] duration-check ease-out active:scale-[0.97]",
              open && "text-ink",
            )}
          >
            <svg
              width={SIZE}
              height={SIZE}
              viewBox={`0 0 ${SIZE} ${SIZE}`}
              className={clsx("shrink-0", maxRatio >= 1 && "text-bad")}
              aria-hidden
            >
              <circle
                cx={SIZE / 2}
                cy={SIZE / 2}
                r={OUTER_R}
                fill="none"
                stroke="currentColor"
                strokeOpacity={0.22}
                strokeWidth={STROKE}
              />
              <circle
                cx={SIZE / 2}
                cy={SIZE / 2}
                r={INNER_R}
                fill="none"
                stroke="currentColor"
                strokeOpacity={0.22}
                strokeWidth={STROKE}
              />
              <circle
                cx={SIZE / 2}
                cy={SIZE / 2}
                r={OUTER_R}
                fill="none"
                stroke={ratioColor(tokenRatio)}
                strokeWidth={STROKE}
                strokeLinecap="round"
                strokeDasharray={OUTER_C}
                strokeDashoffset={OUTER_C * (1 - tokenRatio)}
                transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
                style={{ transition: "stroke-dashoffset var(--duration-panel) var(--ease-out-soft), stroke var(--duration-panel) var(--ease-out-soft)" }}
              />
              <circle
                cx={SIZE / 2}
                cy={SIZE / 2}
                r={INNER_R}
                fill="none"
                stroke={ratioColor(messageRatio)}
                strokeWidth={STROKE}
                strokeLinecap="round"
                strokeDasharray={INNER_C}
                strokeDashoffset={INNER_C * (1 - messageRatio)}
                transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
                style={{ transition: "stroke-dashoffset var(--duration-panel) var(--ease-out-soft), stroke var(--duration-panel) var(--ease-out-soft)" }}
              />
            </svg>
            <span className="tracking-[var(--tracking-tight)]">
              <RollingToken value={compactLabel} mono />
            </span>
          </button>
        )}
      >
        <div className="mb-2 flex items-baseline justify-between gap-2">
          <span className="text-xs font-medium text-muted">Context budget</span>
          {budget?.model && (
            <span
              className="text-2xs text-faint truncate max-w-[170px]"
              title={budget.model}
            >
              {budget.model}
            </span>
          )}
        </div>
        <Row
          label="Context"
          value={`${formatTokens(usage.contextInputTokens)} / ${hardLimit ? formatTokens(hardLimit) : "—"}`}
          hint={hardLimit ? formatPct(tokenRatio) : "—"}
          color={ratioColor(tokenRatio)}
          detail={tokenTrigger ? `Compacts at ${formatTokens(tokenTrigger)}` : undefined}
        />
        <Row
          label="Messages"
          value={`${messageCountLabel} / ${messageLimit || "—"}`}
          hint={usage.messageCount !== null && messageLimit > 0 ? formatPct(messageRatio) : "—"}
          color={ratioColor(messageRatio)}
        />
        <div className="mt-2 pt-2 border-t border-line-soft grid grid-cols-2 gap-y-1 gap-x-3">
          <span className="col-span-2 text-2xs font-medium text-faint">This app session</span>
          {/* Spend row hidden when zero — for OAuth-backed providers
              (openai-codex, claude-pro, etc.) the server has no
              pricing data and "$0" is misleading. The provider just
              doesn't meter per-token from us. */}
          {usage.totalCost > 0 && (
            <>
              <span className="text-muted">Observed cost</span>
              <span className="tabular-nums text-ink-soft text-right">
                {formatCost(usage.totalCost)}
              </span>
            </>
          )}
          {usage.totalTokens > 0 && (
            <>
              <span className="text-muted">Observed tokens</span>
              <span className="tabular-nums text-ink-soft text-right">
                {formatTokens(usage.totalTokens)}
              </span>
            </>
          )}
          <ObservedRow label="Prompt" value={usage.observedPromptTokens} />
          <ObservedRow label="Output" value={usage.observedCompletionTokens} />
          <ObservedRow label="Cache read" value={usage.observedCacheReadTokens} />
          <ObservedRow label="Cache write" value={usage.observedCacheWriteTokens} />
        </div>
        <div className="mt-2 text-2xs text-muted leading-snug">
          {tokenTrigger
            ? `Compaction starts at ${formatTokens(tokenTrigger)} context tokens or when messages hit 100%. `
            : "Compaction threshold is unavailable for this model. "}
          Tool-agent usage affects observed totals only.
        </div>
      </HoverPopover>
    </span>
  );
}

function ObservedRow({ label, value }: { label: string; value: number }) {
  return (
    <>
      <span className="text-muted">{label}</span>
      <span className="tabular-nums text-ink-soft text-right">{formatTokens(value)}</span>
    </>
  );
}

function Row({
  label,
  value,
  hint,
  color,
  detail,
}: {
  label: string;
  value: string;
  hint: string;
  color: string;
  detail?: string;
}) {
  return (
    <div className="py-0.5">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-muted">
          <span
            aria-hidden
            className="inline-block w-1.5 h-1.5 rounded-[var(--r-control)]"
            style={{ backgroundColor: color }}
          />
          {label}
        </span>
        <span className="tabular-nums text-ink-soft">
          {value}{" "}
          <span className="text-muted">· {hint}</span>
        </span>
      </div>
      {detail && (
        <div className="pl-3 text-2xs text-muted tabular-nums">{detail}</div>
      )}
    </div>
  );
}
