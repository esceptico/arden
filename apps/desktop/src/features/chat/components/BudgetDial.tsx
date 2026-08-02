import clsx from "clsx";
import { useStore } from "@/stores";
import { RollingToken } from "@/components/ui/RollingToken";
import { HoverPopover } from "@/components/ui/HoverPopover";

/** Compact "two scales on one ring" budget meter. Outer arc = token
 *  pressure (parent context only — subagent internals don't count, per
 *  the compactor logic). Inner arc = message pressure. Token pressure
 *  near the configured limit or message pressure at the cap triggers
 *  auto-compaction on the server. Hover or click for the
 *  breakdown. */

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

  const modelCeiling = serverConfig?.chat_model_max_context ?? 0;
  const compressionPct = serverConfig
    ? Math.round(serverConfig.compression_threshold * 100)
    : 80;
  const tokenLimit =
    serverConfig?.compaction_token_limit
      ?? (modelCeiling > 0 ? Math.floor(modelCeiling * 0.8) : 0);
  const tokenTrigger = serverConfig?.compaction_token_trigger ?? 0;
  const messageLimit = serverConfig?.max_messages ?? 0;
  const tokenRatio = tokenLimit > 0 ? Math.min(1, usage.lastPrompt / tokenLimit) : 0;
  const messageRatio = messageLimit > 0 ? Math.min(1, usage.messageCount / messageLimit) : 0;
  const maxRatio = Math.max(tokenRatio, messageRatio);
  const hasAnyData = usage.lastPrompt > 0 || usage.messageCount > 0 || usage.totalCost > 0;

  // Visible compact label: prefer cost when present (most useful at a
  // glance), else the last prompt's tokens, else a tiny "—" placeholder
  // so the dial always has a hover target with text in it.
  const compactLabel = usage.totalCost > 0
    ? formatCost(usage.totalCost)
    : usage.lastPrompt > 0
      ? `${formatTokens(usage.lastPrompt)}`
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
                ? `${formatTokens(usage.lastPrompt)} / ${formatTokens(tokenLimit)} tokens · ${usage.messageCount} / ${messageLimit} msgs`
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
          {serverConfig?.chat_model && (
            <span
              className="text-2xs text-faint truncate max-w-[170px]"
              title={serverConfig.chat_model}
            >
              {serverConfig.chat_model}
            </span>
          )}
        </div>
        <Row
          label="Tokens"
          value={`${formatTokens(usage.lastPrompt)} / ${formatTokens(tokenLimit)}`}
          hint={tokenLimit > 0 ? formatPct(tokenRatio) : "—"}
          color={ratioColor(tokenRatio)}
          detail={
            modelCeiling > 0
              ? `${formatTokens(modelCeiling)} ceiling · budget ${compressionPct}%`
              : undefined
          }
        />
        <Row
          label="Messages"
          value={`${usage.messageCount} / ${messageLimit}`}
          hint={messageLimit > 0 ? formatPct(messageRatio) : "—"}
          color={ratioColor(messageRatio)}
        />
        <div className="mt-2 pt-2 border-t border-line-soft grid grid-cols-2 gap-y-1 gap-x-3">
          {/* Spend row hidden when zero — for OAuth-backed providers
              (openai-codex, claude-pro, etc.) the server has no
              pricing data and "$0" is misleading. The provider just
              doesn't meter per-token from us. */}
          {usage.totalCost > 0 && (
            <>
              <span className="text-muted">Session spend</span>
              <span className="tabular-nums text-ink-soft text-right">
                {formatCost(usage.totalCost)}
              </span>
            </>
          )}
          {usage.totalTokens > 0 && (
            <>
              <span className="text-muted">Total tokens</span>
              <span className="tabular-nums text-ink-soft text-right">
                {formatTokens(usage.totalTokens)}
              </span>
            </>
          )}
        </div>
        <div className="mt-2 text-2xs text-muted leading-snug">
          Auto-compacts at {formatTokens(tokenTrigger)} tokens or when messages hit 100%. Tool-agent tokens count toward session totals, not context pressure.
        </div>
      </HoverPopover>
    </span>
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
