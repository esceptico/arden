import {
  Children,
  createContext,
  useCallback,
  useContext,
  useId,
  useLayoutEffect,
  useState,
  type ReactNode,
} from "react";
import clsx from "clsx";

const SettingsPageActionContext = createContext<((action: ReactNode | null) => void) | null>(null);

/**
 * Shared page grammar for the full-screen Settings workspace.  The page title
 * moves with TabPanels, while the rail and close affordance stay put.
 */
export function SettingsPage({
  section,
  title,
  intro,
  action,
  wide = false,
  children,
}: {
  section: string;
  title: string;
  intro: ReactNode;
  action?: ReactNode;
  wide?: boolean;
  children: ReactNode;
}) {
  const titleId = useId();
  const [registeredAction, setRegisteredAction] = useState<ReactNode | null>(null);
  const registerAction = useCallback((nextAction: ReactNode | null) => setRegisteredAction(nextAction), []);

  return (
    <SettingsPageActionContext.Provider value={registerAction}>
      <section className="settings-page" aria-labelledby={titleId}>
        <header className={clsx("settings-page-head", wide && "settings-page-head-wide")}>
          <div className="settings-crumb">
            settings / <strong>{section}</strong>
          </div>
          <div className="settings-page-title-row">
            <h1 id={titleId} className="settings-page-title">{title}</h1>
            {(registeredAction ?? action) && (
              <div className="settings-page-header-action">{registeredAction ?? action}</div>
            )}
          </div>
          <p className="settings-page-intro">{intro}</p>
        </header>
        <div className="settings-page-body">{children}</div>
      </section>
    </SettingsPageActionContext.Provider>
  );
}

/** Lets a tab contribute a real page-header action without lifting its data
 * loading/mutation ownership into SettingsModal. */
export function SettingsPageAction({ children }: { children: ReactNode }) {
  const registerAction = useContext(SettingsPageActionContext);
  useLayoutEffect(() => {
    if (!registerAction) return;
    registerAction(children);
    return () => registerAction(null);
  }, [children, registerAction]);
  return null;
}

export function SettingsSection({
  title,
  detail,
  action,
  className,
  children,
}: {
  title: string;
  detail?: ReactNode;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={clsx("settings-section", className)}>
      <div className="settings-section-head">
        <h2>{title}</h2>
        {detail && <span className="settings-section-detail">{detail}</span>}
        {action && <div className="settings-section-action">{action}</div>}
      </div>
      {children}
    </section>
  );
}

export function SettingsSurface({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={clsx("settings-surface", className)}>{children}</div>;
}

export function SettingsSettingRow({
  title,
  hint,
  control,
  className,
}: {
  title: string;
  hint: ReactNode;
  control: ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("settings-setting-row", className)}>
      <div className="min-w-0">
        <div className="settings-setting-title">{title}</div>
        <div className="settings-setting-hint">{hint}</div>
      </div>
      <div className="settings-setting-control">{control}</div>
    </div>
  );
}

export function SettingsDataSection({
  title,
  detail,
  empty,
  children,
}: {
  title: string;
  detail?: ReactNode;
  empty: ReactNode;
  children: ReactNode;
}) {
  const hasChildren = Children.count(children) > 0;
  return (
    <SettingsSection title={title} detail={detail}>
      {hasChildren ? (
        <SettingsSurface className="settings-data-surface">{children}</SettingsSurface>
      ) : (
        <div className="settings-empty-note">{empty}</div>
      )}
    </SettingsSection>
  );
}

export function SettingsSummary({
  label,
  detail,
  stats,
  className,
}: {
  label: ReactNode;
  detail: ReactNode;
  stats: Array<{ value: ReactNode; label: ReactNode; tone?: "ok" | "warn" }>;
  className?: string;
}) {
  return (
    <section className={clsx("settings-summary", className)} aria-label={`${label} summary`}>
      <div className="settings-summary-main">
        <div className="settings-summary-label">{label}</div>
        <div className="settings-summary-copy">{detail}</div>
      </div>
      {stats.map((stat, index) => (
        <div className="settings-summary-stat" key={index}>
          <div className={clsx("settings-summary-value", stat.tone && `is-${stat.tone}`)}>{stat.value}</div>
          <div className="settings-summary-meta">{stat.label}</div>
        </div>
      ))}
    </section>
  );
}
