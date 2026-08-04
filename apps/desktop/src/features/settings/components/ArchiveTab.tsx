import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArchiveRestore, HelpCircle, Trash2 } from "@/components/icons";
import clsx from "clsx";
import { useStore } from "@/stores";
import { EASE_OUT, MOTION, ROW_EXIT, SPRING_LAYOUT } from "@/lib/tokens/motion";
import { fetchArchivedSessions, permanentlyDeleteSession, restoreArchivedSession } from "@/actions/sessions";
import type { ArchivedSession } from "@/api/sessions";
import { useMutationState } from "@/lib/hooks";
import { formatRelativePast } from "@/lib/format";
import { ICON } from "@/lib/icons";
import { Skeleton } from "@/components/ui/Skeleton";
import { SearchInput } from "@/components/ui/SearchInput";
import { SettingsSection, SettingsSurface } from "@/features/settings/components/SettingsPage";
import { Button } from "@/components/ui/Button";
import {
  executeStorageCleanupApi,
  getStorageStatusApi,
  listStorageBackupsApi,
  planStorageCleanupApi,
  setStorageBackupKeepApi,
} from "@/api/settings";
import type { StorageBackup, StoragePlan, StoragePlanRequest, StorageStatus } from "@/api/types";
import { updateServerConfig } from "@/actions/server";
import { Tooltip } from "@/components/ui/Tooltip";
import { SwitchControl } from "@/components/ui/SwitchControl";

function formatBytes(bytes: number): string {
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

export function ArchiveTab() {
  const archived = useStore((s) => s.archivedSessions);
  const config = useStore((s) => s.config);
  const serverConfig = useStore((s) => s.serverConfig);
  const currentSessionId = useStore((s) => s.currentSessionId);
  const pinnedSessionIds = useStore((s) => s.prefs.pinnedSessionIds);
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(serverConfig?.max_space_gb?.toString() ?? "");
  const [backupDays, setBackupDays] = useState(serverConfig?.storage_backup_retention_days ?? 14);
  const [allowArchived, setAllowArchived] = useState(serverConfig?.storage_allow_archived_cleanup ?? false);
  const [deleteCold, setDeleteCold] = useState(false);
  const [allowCurrent, setAllowCurrent] = useState(serverConfig?.storage_allow_current_cleanup ?? false);
  const [inactiveDays, setInactiveDays] = useState(serverConfig?.storage_current_inactive_days ?? 90);
  const [minimumCurrent, setMinimumCurrent] = useState(serverConfig?.storage_current_minimum ?? 100);
  const [storage, setStorage] = useState<StorageStatus | null>(null);
  const [backups, setBackups] = useState<StorageBackup[]>([]);
  const [plan, setPlan] = useState<StoragePlan | null>(null);
  const [plannedRequest, setPlannedRequest] = useState<StoragePlanRequest | null>(null);
  const [storageBusy, setStorageBusy] = useState(false);
  const [storageError, setStorageError] = useState<string | null>(null);
  const [confirmingCleanup, setConfirmingCleanup] = useState(false);
  const cleanupTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    void fetchArchivedSessions();
  }, []);

  useEffect(() => {
    void Promise.all([getStorageStatusApi(config), listStorageBackupsApi(config)])
      .then(([status, listedBackups]) => {
        setStorage(status);
        setBackups(listedBackups);
      })
      .catch(() => {});
  }, [config]);

  useEffect(() => {
    setLimit(serverConfig?.max_space_gb?.toString() ?? "");
    setBackupDays(serverConfig?.storage_backup_retention_days ?? 14);
    setAllowArchived(serverConfig?.storage_allow_archived_cleanup ?? false);
    setAllowCurrent(serverConfig?.storage_allow_current_cleanup ?? false);
    setInactiveDays(serverConfig?.storage_current_inactive_days ?? 90);
    setMinimumCurrent(serverConfig?.storage_current_minimum ?? 100);
  }, [serverConfig]);

  useEffect(() => () => {
    if (cleanupTimer.current) clearTimeout(cleanupTimer.current);
  }, []);

  const saveAndPreview = async () => {
    const parsed = limit.trim() === "" ? null : Number(limit);
    if (parsed !== null && (!Number.isFinite(parsed) || parsed < 0.1)) {
      setStorageError("Enter at least 0.1 GB, or leave blank for no limit.");
      return;
    }
    if (backupDays < 1 || inactiveDays < 1 || minimumCurrent < 1) {
      setStorageError("Retention values must be positive whole numbers.");
      return;
    }
    setStorageBusy(true);
    setStorageError(null);
    try {
      await updateServerConfig({
        max_space_gb: parsed,
        storage_backup_retention_days: backupDays,
        storage_allow_archived_cleanup: allowArchived,
        storage_allow_current_cleanup: allowCurrent,
        storage_current_inactive_days: inactiveDays,
        storage_current_minimum: minimumCurrent,
      });
      const request: StoragePlanRequest = {
        target_gb: parsed,
        allow_archived_chats: allowArchived,
        allow_delete_cold_chats: deleteCold,
        allow_current_chats: allowCurrent,
        current_session_id: currentSessionId,
        pinned_session_ids: pinnedSessionIds,
      };
      setPlan(await planStorageCleanupApi(config, request));
      setPlannedRequest(request);
    } catch (cause) {
      setStorageError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setStorageBusy(false);
    }
  };

  const executePlan = async () => {
    if (!plan || !plannedRequest) return;
    if (plan.actions.some((action) => action.destructive) && !confirmingCleanup) {
      setConfirmingCleanup(true);
      cleanupTimer.current = setTimeout(() => setConfirmingCleanup(false), 4000);
      return;
    }
    if (cleanupTimer.current) clearTimeout(cleanupTimer.current);
    setConfirmingCleanup(false);
    setStorageBusy(true);
    setStorageError(null);
    try {
      const result = await executeStorageCleanupApi(config, {
        ...plannedRequest,
        plan_id: plan.plan_id,
        current_session_id: currentSessionId,
        pinned_session_ids: pinnedSessionIds,
      });
      setStorage(result.status);
      setPlan(null);
      setPlannedRequest(null);
      setBackups(await listStorageBackupsApi(config));
      await fetchArchivedSessions();
    } catch (cause) {
      setStorageError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setStorageBusy(false);
    }
  };

  const toggleBackupKeep = async (backup: StorageBackup) => {
    setStorageBusy(true);
    setStorageError(null);
    try {
      setBackups(await setStorageBackupKeepApi(config, backup.relative_path, !backup.kept));
      setPlan(null);
      setPlannedRequest(null);
    } catch (cause) {
      setStorageError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setStorageBusy(false);
    }
  };

  const categories = useMemo(
    () => [...(storage?.categories ?? [])].sort((a, b) => b.total_bytes - a.total_bytes),
    [storage?.categories],
  );

  const filtered = useMemo(() => {
    if (!archived) return null;
    const q = query.trim().toLowerCase();
    if (!q) return archived;
    return archived.filter((s) => (s.name ?? "untitled").toLowerCase().includes(q));
  }, [archived, query]);

  const archivedCount = archived?.length ?? 0;

  return (
    <>
      <SettingsSection title="Storage budget" detail={storage ? formatBytes(storage.total_bytes) : "measuring"}>
        <SettingsSurface className="p-3.5">
          <div className="flex items-end gap-3 pb-3">
            <label className="min-w-0 flex-1 text-xs text-muted">
              Maximum Arden space (GB)
              <input
                className="mt-1 h-9 w-full rounded-[var(--r-control)] border border-line-soft bg-surface px-2.5 text-sm text-ink outline-none focus:border-line-strong"
                type="number"
                min="0.1"
                step="0.1"
                value={limit}
                placeholder="No limit"
                onChange={(event) => setLimit(event.target.value)}
              />
            </label>
            <Button size="sm" disabled={storageBusy} onClick={() => void saveAndPreview()}>
              {storageBusy ? "Checking…" : "Save & preview"}
            </Button>
          </div>
          <div className="border-t border-line-soft">
            {categories.map((category) => (
              <div key={category.id} className="flex min-w-0 items-center gap-3 border-b border-line-soft py-2.5 last:border-b-0">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 text-xs font-medium text-ink">
                    <span className="truncate">{category.label}</span>
                    <Tooltip label={category.description} side="right">
                      <button
                        type="button"
                        aria-label={`About ${category.label}`}
                        className="shrink-0 text-faint transition-colors hover:text-muted focus-visible:text-ink"
                      >
                        <HelpCircle size={ICON.XS} />
                      </button>
                    </Tooltip>
                  </div>
                  <div className="mt-0.5 truncate text-xs text-faint">
                    {category.item_count.toLocaleString()} items
                    {category.reclaimable_bytes > 0
                      ? ` · ${formatBytes(category.reclaimable_bytes)} reclaimable`
                      : category.protection_reason
                        ? ` · ${category.protection_reason}`
                        : ""}
                  </div>
                </div>
                <span className="shrink-0 text-xs tabular-nums text-ink-soft">{formatBytes(category.total_bytes)}</span>
              </div>
            ))}
          </div>
          {storage && (
            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-faint">
              <span>{formatBytes(storage.reclaimable_bytes)} immediately reclaimable</span>
              <span>{formatBytes(storage.protected_bytes)} not currently reclaimable</span>
              {storage.database_reclaim_mode === "offline_migration_required" && (
                <span>Database shrink needs one offline compaction</span>
              )}
            </div>
          )}
          {storageError && <p role="alert" className="mt-2 text-xs text-bad">{storageError}</p>}
        </SettingsSurface>
      </SettingsSection>

      <SettingsSection title="Retention policy" detail="oldest eligible data first">
        <SettingsSurface className="divide-y divide-line-soft">
          <PolicyRow
            title="Backup archives"
            hint="Remove auto-created backups after this many days. Kept backups are excluded."
            control={
              <NumberField label="Backup retention days" value={backupDays} onChange={setBackupDays} suffix="days" />
            }
          />
          <PolicyRow
            title="Archived chats"
            hint="Convert old archived chats to full-fidelity compressed bundles before permanent deletion is considered."
            control={
              <SwitchControl checked={allowArchived} onChange={setAllowArchived} aria-label="Include archived chats" />
            }
          />
          {allowArchived && (
            <PolicyRow
              title="Cold archived chats"
              hint="Allow permanent deletion only if conversion and safer cleanup still cannot meet the budget."
              control={
                <SwitchControl checked={deleteCold} onChange={setDeleteCold} aria-label="Delete cold archived chats" />
              }
            />
          )}
          <PolicyRow
            title="Inactive current chats"
            hint="Off by default. The open chat, pinned chats, active runs, automations, and unfinished goals stay excluded."
            control={
              <SwitchControl checked={allowCurrent} onChange={setAllowCurrent} aria-label="Include inactive current chats" />
            }
          />
          {allowCurrent && (
            <div className="grid grid-cols-2 gap-3 px-3.5 py-3">
              <NumberField label="Inactive for" value={inactiveDays} onChange={setInactiveDays} suffix="days" />
              <NumberField label="Always keep newest" value={minimumCurrent} onChange={setMinimumCurrent} suffix="chats" />
            </div>
          )}
        </SettingsSurface>
      </SettingsSection>

      {plan && (
        <SettingsSection title="Cleanup preview" detail={plan.attainable ? "target reachable" : "protected floor remains"}>
          <SettingsSurface className="p-3.5">
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
              <p className="mt-2 text-xs text-faint">No cleanup is needed with these settings.</p>
            )}
            {plan.blockers.length > 0 && (
              <p className="mt-3 text-xs text-faint">{plan.blockers.join(" · ")}</p>
            )}
            {plan.actions.length > 0 && (
              <div className="mt-3 flex justify-end">
                <Button size="sm" disabled={storageBusy} onClick={() => void executePlan()}>
                  {storageBusy
                    ? "Cleaning…"
                    : confirmingCleanup
                      ? "Confirm permanent cleanup"
                      : `Clean up ${formatBytes(plan.estimated_reclaimable_bytes)}`}
                </Button>
              </div>
            )}
          </SettingsSurface>
        </SettingsSection>
      )}

      {backups.length > 0 && (
        <SettingsSection title="Backup exceptions" detail={`${backups.length} backups`}>
          <SettingsSurface>
            <ul className="divide-y divide-line-soft">
              {backups.slice(0, 20).map((backup) => (
                <li key={backup.relative_path} className="flex min-w-0 items-center gap-3 px-3.5 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs text-ink" title={backup.relative_path}>{backup.relative_path}</div>
                    <div className="mt-0.5 text-xs text-faint">
                      {formatBytes(backup.size_bytes)} · {formatRelativePast(backup.modified_at)} ago
                      {backup.expired ? " · expired" : ""}
                    </div>
                  </div>
                  <Button size="sm" variant="ghost" disabled={storageBusy} onClick={() => void toggleBackupKeep(backup)}>
                    {backup.kept ? "Release" : "Keep"}
                  </Button>
                </li>
              ))}
            </ul>
          </SettingsSurface>
        </SettingsSection>
      )}

      {archivedCount > 0 && (
        <div className="settings-list-toolbar">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search archived sessions"
            ariaLabel="Search archived sessions"
            showClear
            trailing={`${filtered?.length ?? archivedCount} items`}
            className="settings-list-search"
          />
        </div>
      )}

      <SettingsSection title="Sessions" detail="newest first">
        {filtered === null ? (
          <div className="flex flex-col gap-1" role="status" aria-label="Loading archived items…">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} height={52} radius={10} />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="settings-empty-note">
            {archived && archived.length > 0
              ? "No matches."
              : "Nothing here. Archived sessions will show up in this view."}
          </div>
        ) : (
          <SettingsSurface>
            {/* Keyed by query so filter keystrokes swap the list instantly; restore/delete under a stable query still animates. */}
            <ul key={query} className="settings-archive-list">
              <AnimatePresence mode="popLayout" initial={false}>
                {filtered.map((s) => (
                  <ArchivedRow key={s.session_id} session={s} />
                ))}
              </AnimatePresence>
            </ul>
          </SettingsSurface>
        )}
      </SettingsSection>
    </>
  );
}

function PolicyRow({
  title,
  hint,
  control,
}: {
  title: string;
  hint: string;
  control: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 items-center gap-4 px-3.5 py-3">
      <div className="min-w-0 flex-1">
        <div className="text-xs font-medium text-ink">{title}</div>
        <div className="mt-0.5 max-w-[70ch] text-xs leading-relaxed text-faint">{hint}</div>
      </div>
      <div className="shrink-0">{control}</div>
    </div>
  );
}

function NumberField({
  label,
  value,
  onChange,
  suffix,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  suffix: string;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const commit = () => {
    const parsed = Number(draft);
    if (Number.isInteger(parsed) && parsed > 0) onChange(parsed);
    else setDraft(String(value));
  };
  return (
    <label className="block text-xs text-muted">
      {label}
      <span className="mt-1 flex h-8 items-center rounded-[var(--r-control)] border border-line-soft bg-surface px-2 focus-within:border-line-strong">
        <input
          type="number"
          min="1"
          step="1"
          value={draft}
          aria-label={label}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === "Enter") event.currentTarget.blur();
          }}
          className="w-14 bg-transparent text-right text-xs tabular-nums text-ink outline-none"
        />
        <span className="ms-1 text-xs text-faint">{suffix}</span>
      </span>
    </label>
  );
}

// `ref` reaches the li so AnimatePresence popLayout can measure the row
// before popping it out of the layout on exit.
function ArchivedRow({
  session,
  ref,
}: {
  session: ArchivedSession;
  ref?: React.Ref<HTMLLIElement>;
}) {
  const { busy: anyBusy, error, run } = useMutationState();
  const [busyOp, setBusyOp] = useState<"restore" | "delete" | null>(null);
  // Inline two-click confirm replaces the native confirm() dialog: first
  // click arms ("Confirm delete"), second commits; auto-reverts after 3s.
  const [confirming, setConfirming] = useState(false);
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
  }, []);

  const trigger = async (op: "restore" | "delete", fn: () => Promise<void>) => {
    if (anyBusy) return;
    setBusyOp(op);
    await run(fn);
    setBusyOp(null);
  };

  const onRestore = () =>
    void trigger("restore", () => restoreArchivedSession(session.session_id));
  const onDelete = () => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
    if (confirming) {
      setConfirming(false);
      void trigger("delete", () => permanentlyDeleteSession(session.session_id));
      return;
    }
    setConfirming(true);
    confirmTimer.current = setTimeout(() => setConfirming(false), 3000);
  };

  return (
    <motion.li
      ref={ref}
      layout
      exit={{ ...ROW_EXIT, transition: { duration: MOTION.row, ease: EASE_OUT } }}
      transition={{ layout: SPRING_LAYOUT }}
      onMouseLeave={() => {
        if (confirmTimer.current) clearTimeout(confirmTimer.current);
        setConfirming(false);
      }}
      className="settings-archive-row group"
    >
      <div className="min-w-0 flex-1">
        <div className="settings-archive-title">
          {session.name || "untitled"}
        </div>
        <div className="settings-archive-meta">
          archived {formatRelativePast(session.archived_at)} ago · {session.message_count} msg
          {session.message_count === 1 ? "" : "s"}
          {session.storage_state === "cold" ? ` · cold · ${formatBytes(session.cold_bundle_bytes ?? 0)}` : ""}
        </div>
        {error && (
          <div aria-live="polite" className="mt-1 text-xs text-bad truncate" title={error}>
            {error}
          </div>
        )}
      </div>
      <div className="settings-archive-actions">
        <RowAction
          icon={<ArchiveRestore size={ICON.XS} />}
          label="Restore"
          onClick={onRestore}
          busy={busyOp === "restore"}
        />
        <RowAction
          icon={<Trash2 size={ICON.XS} />}
          label={confirming ? "Confirm delete" : "Delete"}
          onClick={onDelete}
          busy={busyOp === "delete"}
          danger
        />
      </div>
    </motion.li>
  );
}

function RowAction({
  icon,
  label,
  onClick,
  busy,
  danger,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  busy?: boolean;
  danger?: boolean;
}) {
  // Bespoke (not Button): a dense list-row action whose destructive form is
  // neutral-at-rest / red-on-hover (the app's destructive convention, cf.
  // ConfirmDeleteButton) — Button's `danger` variant is red-at-rest, which
  // doesn't fit. Kept hand-rolled at h-6/text-xs density.
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className={clsx(
        "inline-flex items-center gap-1.5 h-6 px-2 rounded-[var(--r-control)] text-xs font-medium tracking-[var(--tracking-tight)] transition-[color,background-color,scale] duration-check ease-out active:scale-[0.97]",
        busy
          ? "text-faint cursor-wait"
          : danger
            ? "text-ink-soft hover:bg-bad-soft hover:text-bad"
            : "text-ink-soft hover:bg-surface-soft hover:text-ink",
      )}
    >
      {icon}
      {label}
    </button>
  );
}
