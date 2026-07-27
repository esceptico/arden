import { useEffect, useId, useState, type ReactNode } from "react";
import clsx from "clsx";
import { ChevronDown, type ArdenIcon } from "@/components/icons";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { SwitchControl } from "@/components/ui/SwitchControl";
import { ICON } from "@/lib/icons";

export interface IntegrationDetailRow {
  id: string;
  title: ReactNode;
  meta: ReactNode;
  actionLabel: string;
  onAction: () => void;
  actionDisabled?: boolean;
}

export interface IntegrationAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  /** Icon-only variant; the label becomes the accessible name. */
  Icon?: ArdenIcon;
}

export function IntegrationItem({
  title,
  subtitle,
  stateTitle,
  stateDetail,
  enabled,
  onToggle,
  action,
  rows,
  expandedByDefault = false,
  footer,
}: {
  title: string;
  subtitle: string;
  stateTitle: string;
  stateDetail: string;
  enabled: boolean;
  onToggle: (next: boolean) => void;
  action: IntegrationAction;
  rows: IntegrationDetailRow[];
  expandedByDefault?: boolean;
  footer?: {
    text: ReactNode;
    actionLabel: string;
    ActionIcon: ArdenIcon;
    onAction: () => void;
    disabled?: boolean;
  };
}) {
  const panelId = useId();
  const canExpand = rows.length > 0 || Boolean(footer);
  const [expanded, setExpanded] = useState(expandedByDefault && canExpand);

  useEffect(() => {
    if (!canExpand) setExpanded(false);
    else if (expandedByDefault) setExpanded(true);
  }, [canExpand, expandedByDefault]);

  return (
    <div className={clsx("settings-integration-item", expanded && "is-expanded")}>
      <div className="settings-integration-row">
        <div className="settings-integration-main">
          <div className="settings-integration-title">{title}</div>
          <div className="settings-integration-sub">{subtitle}</div>
        </div>
        <div className="settings-integration-state">
          <strong>{stateTitle}</strong>
          <span>{stateDetail}</span>
        </div>
        {action.Icon ? (
          <IconButton
            tone="secondary"
            shape="circle"
            className="settings-integration-action-icon"
            aria-label={action.label}
            disabled={action.disabled}
            onClick={action.onClick}
          >
            <action.Icon size={ICON.XS} />
          </IconButton>
        ) : (
          <Button
            size="sm"
            className="settings-integration-action"
            disabled={action.disabled}
            onClick={action.onClick}
          >
            {action.label}
          </Button>
        )}
        <SwitchControl
          checked={enabled}
          onChange={onToggle}
          aria-label={`Enable ${title}`}
        />
        <button
          type="button"
          className="settings-integration-disclosure"
          onClick={() => setExpanded((value) => !value)}
          disabled={!canExpand}
          aria-expanded={canExpand ? expanded : undefined}
          aria-controls={canExpand ? panelId : undefined}
          aria-label={`${expanded ? "Collapse" : "Expand"} ${title} connections`}
        >
          <ChevronDown size={ICON.SM} />
        </button>
      </div>
      <div
        id={panelId}
        className="settings-integration-panel"
        aria-hidden={!expanded}
        inert={expanded ? undefined : true}
      >
        <div className="settings-integration-panel-clip">
          <div className="settings-integration-detail">
            {rows.map((row) => (
              <div key={row.id} className="settings-integration-detail-row">
                <div className="settings-integration-detail-main">{row.title}</div>
                <div className="settings-integration-detail-meta">{row.meta}</div>
                <div className="settings-integration-detail-actions">
                  <Button
                    size="sm"
                    disabled={row.actionDisabled}
                    onClick={row.onAction}
                  >
                    {row.actionLabel}
                  </Button>
                </div>
              </div>
            ))}
            {footer && (
              <div className="settings-integration-detail-footer">
                <span>{footer.text}</span>
                <IconButton
                  tone="secondary"
                  shape="circle"
                  aria-label={footer.actionLabel}
                  disabled={footer.disabled}
                  onClick={footer.onAction}
                >
                  <footer.ActionIcon size={ICON.XS} />
                </IconButton>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
