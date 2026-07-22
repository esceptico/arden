import type { ReactNode } from "react";
import { Callout } from "@/components/ui/Callout";

export function SettingsConnectionHint({
  title = "Connect the desktop to arden first",
  detail = "Check the server URL and API key in the Connection tab, then refresh this view.",
}: {
  title?: string;
  detail?: string;
}) {
  return (
    <div className="rounded-[12px] border border-line-soft bg-surface px-3.5 py-3">
      <div className="text-base font-medium text-ink">{title}</div>
      <div className="mt-1 text-sm text-muted leading-[1.45]">{detail}</div>
    </div>
  );
}

export function SettingsInlineError({
  title,
  message,
  action,
}: {
  title: string;
  message: string;
  action?: ReactNode;
}) {
  return (
    <Callout tone="bad" title={title} action={action}>
      {message}
    </Callout>
  );
}
