import type { ReactNode } from "react";

function classes(...names: Array<string | undefined>) {
  return names.filter(Boolean).join(" ");
}

export function WorkspaceRail({
  children,
  label = "Workspace sidebar",
  className,
}: {
  children: ReactNode;
  label?: string;
  className?: string;
}) {
  return (
    <aside className={classes("workspace-rail", className)} aria-label={label}>
      <div className="workspace-rail__drag-region" aria-hidden />
      {children}
    </aside>
  );
}

export function WorkspaceRailNav({
  children,
  label,
  className,
}: {
  children: ReactNode;
  label: string;
  className?: string;
}) {
  return (
    <nav className={classes("workspace-rail__nav", className)} aria-label={label}>
      {children}
    </nav>
  );
}

export function WorkspaceRailSectionHeader({
  title,
  actions,
}: {
  title: string;
  actions?: ReactNode;
}) {
  return (
    <div className="workspace-rail__section-header">
      <h2 className="workspace-rail__section-title">{title}</h2>
      {actions ? <div className="workspace-rail__section-actions">{actions}</div> : null}
    </div>
  );
}

export function WorkspaceRailStatus({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: "neutral" | "live" | "done" | "warning" | "danger";
  className?: string;
}) {
  return (
    <span
      className={classes("workspace-rail__status", className)}
      data-tone={tone === "neutral" ? undefined : tone}
    >
      {children}
    </span>
  );
}

export function WorkspaceRailFooter({
  children,
  label,
  className,
}: {
  children: ReactNode;
  label: string;
  className?: string;
}) {
  return (
    <nav className={classes("workspace-rail__footer", className)} aria-label={label}>
      {children}
    </nav>
  );
}
