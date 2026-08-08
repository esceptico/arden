import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "@/stores";
import { formatBytes, formatRelativePast } from "@/lib/format";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { SwitchControl } from "@/components/ui/SwitchControl";
import { NumberField } from "@/features/settings/components/Field";
import { SaveStatus } from "@/features/settings/components/SaveStatus";
import { SettingsTabSkeleton } from "@/features/settings/components/SettingsTabSkeleton";
import { SettingsConnectionHint, SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import {
  SettingsDataSection,
  SettingsSection,
  SettingsSettingRow,
  SettingsSummary,
  SettingsSurface,
} from "@/features/settings/components/SettingsPage";
import {
  executeStorageCleanupApi,
  getStorageStatusApi,
  listStorageBackupsApi,
  planStorageCleanupApi,
  setStorageBackupKeepApi,
  type ServerConfigPatch,
} from "@/api/settings";
import type { ServerConfig, StorageBackup, StoragePlan, StoragePlanRequest, StorageStatus } from "@/api/types";
import { fetchArchivedSessions } from "@/actions/sessions";
import { fetchServerConfig, updateServerConfig } from "@/actions/server";
import { useMutationState } from "@/lib/hooks";
import type { MutationState } from "@/lib/hooks";

/** Every numeric limit on this tab. Held as one draft so a single Save covers
 *  them; `null` means "no pending edit — follow the server". */
interface Limits {
  maxSpace: string;
  backupDays: number;
  inactiveDays: number;
  minimumCurrent: number;
}

function limitsOf(config: ServerConfig): Limits {
  return {
    maxSpace: config.max_space_gb === null ? "" : String(config.max_space_gb),
    backupDays: config.storage_backup_retention_days,
    inactiveDays: config.storage_current_inactive_days,
    minimumCurrent: config.storage_current_minimum,
  };
}

function sameLimits(a: Limits, b: Limits): boolean {
  return (
    a.maxSpace.trim() === b.maxSpace.trim() &&
    a.backupDays === b.backupDays &&
    a.inactiveDays === b.inactiveDays &&
    a.minimumCurrent === b.minimumCurrent
  );
}

export function StorageTab() {
  const config = useStore((s) => s.config);
  const serverConfig = useStore((s) => s.serverConfig);
  const connected = useStore((s) => s.connected);
  const currentSessionId = useStore((s) => s.currentSessionId);
  const pinnedSessionIds = useStore((s) => s.prefs.pinnedSessionIds);

  const [status, setStatus] = useState<StorageStatus | null>(null);
  const [backups, setBackups] = useState<StorageBackup[]>([]);
  const [draft, setDraft] = useState<Limits | null>(null);
  const [limitsError, setLimitsError] = useState<string | null>(null);
  const [plan, setPlan] = useState<StoragePlan | null>(null);
  const [planStale, setPlanStale] = useState(false);
  const [cleanupBusy, setCleanupBusy] = useState(false);
  const [cleanupError, setCleanupError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const policy = useMutationState();
  const limitSave = useMutationState();
  const backupKeep = useMutationState();

  useEffect(() => {
    void Promise.all([getStorageStatusApi(config), listStorageBackupsApi(config)])
      .then(([nextStatus, nextBackups]) => {
        setStatus(nextStatus);
        setBackups(nextBackups);
      })
      .catch(() => {});
  }, [config]);

  useEffect(() => () => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
  }, []);

  const categories = useMemo(
    () => [...(status?.categories ?? [])].sort((a, b) => b.total_bytes - a.total_bytes),
    [status?.categories],
  );

  if (!serverConfig) {
    if (!connected) return <SettingsConnectionHint />;
    return <SettingsTabSkeleton rows={5} label="Loading storage settings…" />;
  }

  const saved = limitsOf(serverConfig);
  const limits = draft ?? saved;
  const dirty = !sameLimits(limits, saved);

  const editLimits = (patch: Partial<Limits>) => {
    setLimitsError(null);
    setDraft({ ...limits, ...patch });
  };

  const dropPlan = () => {
    setPlan(null);
    setPlanStale(false);
  };

  /** Only settings the server already persisted feed a plan, so a saved change
   *  invalidates whatever preview is on screen. A failed write reverts to
   *  server truth; the switches read from it, so they snap back on their own. */
  const persist = (patch: ServerConfigPatch, mutation: MutationState, onSaved?: () => void) =>
    void mutation.run(async () => {
      try {
        await updateServerConfig(patch);
      } catch (cause) {
        await fetchServerConfig();
        throw cause;
      }
      onSaved?.();
      dropPlan();
    });

  const saveLimits = (event: React.FormEvent) => {
    event.preventDefault();
    if (!dirty || limitSave.busy) return;
    const trimmed = limits.maxSpace.trim();
    const maxSpace = trimmed === "" ? null : Number(trimmed);
    if (maxSpace !== null && (!Number.isFinite(maxSpace) || maxSpace < 0.1)) {
      setLimitsError("Enter at least 0.1 GB, or leave it blank for no limit.");
      return;
    }
    setLimitsError(null);
    persist(
      {
        max_space_gb: maxSpace,
        storage_backup_retention_days: limits.backupDays,
        storage_current_inactive_days: limits.inactiveDays,
        storage_current_minimum: limits.minimumCurrent,
      },
      limitSave,
      () => setDraft(null),
    );
  };

  const planRequest = (): StoragePlanRequest => ({
    current_session_id: currentSessionId,
    pinned_session_ids: pinnedSessionIds,
  });

  const previewCleanup = async () => {
    setCleanupBusy(true);
    setCleanupError(null);
    try {
      setPlan(await planStorageCleanupApi(config, planRequest()));
      setPlanStale(false);
    } catch (cause) {
      setPlan(null);
      setCleanupError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setCleanupBusy(false);
    }
  };

  const executeCleanup = async () => {
    if (!plan) return;
    if (plan.actions.some((action) => action.destructive) && !confirming) {
      setConfirming(true);
      confirmTimer.current = setTimeout(() => setConfirming(false), 4000);
      return;
    }
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
    setConfirming(false);
    setCleanupBusy(true);
    setCleanupError(null);
    try {
      const run = await executeStorageCleanupApi(config, { ...planRequest(), plan_id: plan.plan_id });
      setStatus(run.status);
      dropPlan();
      setBackups(await listStorageBackupsApi(config));
      await fetchArchivedSessions();
    } catch (cause) {
      setPlanStale(true);
      setCleanupError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setCleanupBusy(false);
    }
  };

  const toggleBackupKeep = (backup: StorageBackup) =>
    void backupKeep.run(async () => {
      setBackups(await setStorageBackupKeepApi(config, backup.relative_path, !backup.kept));
      dropPlan();
    });

  return (
    <>
      <SettingsSummary
        label={status ? `${formatBytes(status.total_bytes)} on disk` : "Measuring disk usage"}
        detail={
          status?.max_bytes
            ? `Budget ${formatBytes(status.max_bytes)}. Cleanup aims a little under it so the budget holds after the next write.`
            : "No budget set, so nothing is ever cleaned up automatically."
        }
        stats={[
          { value: status ? formatBytes(status.reclaimable_bytes) : "—", label: "reclaimable" },
          { value: status ? formatBytes(status.protected_bytes) : "—", label: "protected" },
        ]}
      />

      <SettingsSection
        title="What's using space"
        detail={status?.database_reclaim_mode === "offline_migration_required"
          ? "database shrink needs one offline compaction"
          : undefined}
      >
        <SettingsSurface>
          {categories.map((category) => (
            <SettingsSettingRow
              key={category.id}
              title={category.label}
              hint={
                <>
                  {category.description}
                  <br />
                  {category.item_count.toLocaleString()} items
                  {category.reclaimable_bytes > 0
                    ? ` · ${formatBytes(category.reclaimable_bytes)} reclaimable`
                    : category.protection_reason
                      ? ` · ${category.protection_reason}`
                      : ""}
                </>
              }
              control={<span className="text-sm tabular-nums text-ink-soft">{formatBytes(category.total_bytes)}</span>}
            />
          ))}
        </SettingsSurface>
      </SettingsSection>

      <SettingsSection
        title="Cleanup policy"
        detail="saved as you switch"
        action={<SaveStatus busy={policy.busy} saved={policy.saved} />}
      >
        <SettingsSurface>
          <SettingsSettingRow
            title="Archived chats"
            hint="Let cleanup convert old archived chats to full-fidelity compressed bundles. Nothing is lost — the chat just stops living in the database."
            control={
              <SwitchControl
                checked={serverConfig.storage_allow_archived_cleanup}
                disabled={policy.busy}
                onChange={(next) => persist({ storage_allow_archived_cleanup: next }, policy)}
                aria-label="Include archived chats in cleanup"
              />
            }
          />
          {serverConfig.storage_allow_archived_cleanup && (
            <SettingsSettingRow
              title="Cold archived chats"
              hint="Also permanently delete already-compressed archived chats, but only when conversion and every safer step still miss the budget."
              control={
                <SwitchControl
                  checked={serverConfig.storage_allow_delete_cold_chats}
                  disabled={policy.busy}
                  onChange={(next) => persist({ storage_allow_delete_cold_chats: next }, policy)}
                  aria-label="Delete cold archived chats"
                />
              }
            />
          )}
          <SettingsSettingRow
            title="Inactive current chats"
            hint="Off by default. The open chat, pinned chats, active runs, automations, and unfinished goals stay excluded whatever this says."
            control={
              <SwitchControl
                checked={serverConfig.storage_allow_current_cleanup}
                disabled={policy.busy}
                onChange={(next) => persist({ storage_allow_current_cleanup: next }, policy)}
                aria-label="Include inactive current chats in cleanup"
              />
            }
          />
        </SettingsSurface>
        {policy.error && <SettingsInlineError title="Couldn't save the policy" message={policy.error} />}
      </SettingsSection>

      <form onSubmit={saveLimits}>
        <SettingsSection
          title="Limits"
          detail={dirty ? "unsaved changes" : "saved"}
          action={<SaveStatus busy={limitSave.busy} saved={limitSave.saved} />}
        >
          <div className="settings-field-stack">
            <Input
              label="Maximum Arden space (GB)"
              help="Total disk budget for everything under ~/.arden. Blank means no limit and no cleanup."
              type="number"
              min="0.1"
              step="0.1"
              value={limits.maxSpace}
              placeholder="No limit"
              onChange={(event) => editLimits({ maxSpace: event.target.value })}
            />
            <NumberField
              label="Backup archive retention"
              suffix="days"
              help="How long an auto-created backup file stays eligible before cleanup may remove it. Kept backups never expire."
              value={limits.backupDays}
              min={15}
              max={180}
              step={15}
              onChange={(next) => editLimits({ backupDays: next })}
            />
            {serverConfig.storage_allow_current_cleanup && (
              <>
                <NumberField
                  label="Current chats count as inactive after"
                  suffix="days"
                  help="A current chat is only a cleanup candidate once it has been untouched this long."
                  value={limits.inactiveDays}
                  min={30}
                  max={360}
                  step={30}
                  onChange={(next) => editLimits({ inactiveDays: next })}
                />
                <NumberField
                  label="Always keep newest"
                  suffix="chats"
                  help="Floor on current chats. Cleanup stops before it would take you under this many."
                  value={limits.minimumCurrent}
                  min={100}
                  max={1000}
                  step={100}
                  onChange={(next) => editLimits({ minimumCurrent: next })}
                />
              </>
            )}
          </div>
          {limitsError && <SettingsInlineError title="Check the limit" message={limitsError} />}
          {limitSave.error && <SettingsInlineError title="Couldn't save the limits" message={limitSave.error} />}
          <div className="settings-field-actions">
            <Button type="submit" variant="primary" disabled={!dirty || limitSave.busy} aria-busy={limitSave.busy}>
              {limitSave.busy ? "Saving" : "Save changes"}
            </Button>
          </div>
        </SettingsSection>
      </form>

      <SettingsSection
        title="Cleanup"
        detail={
          planStale
            ? "stale — preview again"
            : plan
              ? plan.attainable ? "target reachable" : "protected floor remains"
              : "dry run first"
        }
      >
        <SettingsSurface className="p-3.5">
          {plan ? (
            <>
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-sm font-medium text-ink">
                  {formatBytes(plan.before_bytes)} → {formatBytes(plan.estimated_after_bytes)}
                </p>
                <span className="text-xs tabular-nums text-faint">target {formatBytes(plan.target_bytes)}</span>
              </div>
              {plan.actions.length > 0 ? (
                <ol className="mt-3 space-y-1.5">
                  {plan.actions.slice(0, 8).map((action, index) => (
                    <li key={`${action.kind}:${action.resource_id}`} className="flex min-w-0 gap-2 text-xs text-ink-soft">
                      <span className="shrink-0 tabular-nums text-faint">{index + 1}.</span>
                      <span className="min-w-0 flex-1 truncate">{action.description}</span>
                      <span className="shrink-0 tabular-nums text-faint">
                        {formatBytes(action.estimated_reclaimable_bytes)}
                      </span>
                    </li>
                  ))}
                  {plan.actions.length > 8 && (
                    <li className="text-xs text-faint">+ {plan.actions.length - 8} more actions</li>
                  )}
                </ol>
              ) : (
                <p className="mt-2 text-xs text-faint">No cleanup is needed with the saved limits and policy.</p>
              )}
              {plan.blockers.length > 0 && <p className="mt-3 text-xs text-faint">{plan.blockers.join(" · ")}</p>}
            </>
          ) : (
            <p className="text-sm text-muted leading-[var(--leading-body)]">
              Preview builds a dry run from the limits and policy above as they are saved on the server. Nothing is
              removed until you run the plan.
            </p>
          )}
        </SettingsSurface>
        {cleanupError && (
          <SettingsInlineError
            title={planStale ? "Cleanup didn't run" : "Couldn't build a cleanup plan"}
            message={
              planStale
                ? `${cleanupError} — the work on disk changed, so preview again before running it.`
                : cleanupError
            }
          />
        )}
        <div className="settings-field-actions gap-2">
          <Button variant="ghost" disabled={cleanupBusy} onClick={() => void previewCleanup()}>
            {cleanupBusy && !plan ? "Checking…" : plan ? "Preview again" : "Preview cleanup"}
          </Button>
          {plan && plan.actions.length > 0 && !planStale && (
            <Button
              tone={confirming ? "danger" : undefined}
              disabled={cleanupBusy}
              onClick={() => void executeCleanup()}
            >
              {cleanupBusy
                ? "Cleaning…"
                : confirming
                  ? "Confirm permanent cleanup"
                  : `Clean up ${formatBytes(plan.estimated_reclaimable_bytes)}`}
            </Button>
          )}
        </div>
      </SettingsSection>

      <SettingsDataSection
        title="Backup archives"
        detail={`${backups.length} files`}
        empty="No backup files yet. Arden writes one before each risky migration."
      >
        {backups.slice(0, 20).map((backup) => (
          <SettingsSettingRow
            key={backup.relative_path}
            title={backup.relative_path}
            hint={`${formatBytes(backup.size_bytes)} · written ${formatRelativePast(backup.modified_at)} ago${
              backup.expired ? " · past retention" : ""
            }`}
            control={
              <Button
                size="sm"
                variant="ghost"
                disabled={backupKeep.busy}
                onClick={() => toggleBackupKeep(backup)}
              >
                {backup.kept ? "Release" : "Keep"}
              </Button>
            }
          />
        ))}
      </SettingsDataSection>
      {backupKeep.error && <SettingsInlineError title="Couldn't update the backup" message={backupKeep.error} />}
    </>
  );
}
