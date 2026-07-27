import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Plus, X } from "@/components/icons";
import {
  createNotifierApi,
  deleteNotifierApi,
  listNotifierConfigsApi,
  listNotifierTypesApi,
  testNotifierApi,
  updateNotifierApi,
  type NotifierConfigItem,
  type NotifierTypes,
} from "@/api/notifiers";
import { useStore } from "@/stores";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { ConfirmDeleteButton } from "@/components/ui/ConfirmDeleteButton";
import { PeekSurface } from "@/components/workspace/PeekSurface";
import { ICON } from "@/lib/icons";
import { SettingsTabSkeleton } from "@/features/settings/components/SettingsTabSkeleton";
import {
  settingsErrorMessage,
  settingsErrorTitle,
} from "@/features/settings/lib/settingsLoadState";
import { SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import { SettingsPageAction, SettingsSection, SettingsSurface } from "@/features/settings/components/SettingsPage";
import { SettingsRefreshAction } from "@/features/settings/components/SettingsRefreshAction";

const TYPE_LABELS: Record<string, string> = {
  email: "Email",
  telegram: "Telegram",
  slack: "Slack",
  bash: "Shell command",
};

const FIELD_LABELS: Record<string, string> = {
  from_account: "From account",
  to_address: "To address",
  user_id: "User ID",
  channel: "Channel",
  command: "Command",
};

/** The one line that identifies a channel at a glance in its row. */
function configSummary(item: NotifierConfigItem): string {
  const values = Object.values(item.config).filter(Boolean);
  return values.join(" · ");
}

interface EditorState {
  original: string | null;
  name: string;
  type: string;
  config: Record<string, string>;
}

export function NotifiersTab() {
  const config = useStore((s) => s.config);
  const [items, setItems] = useState<NotifierConfigItem[]>([]);
  const [types, setTypes] = useState<NotifierTypes>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testState, setTestState] = useState<"sending" | "sent" | string | null>(null);
  // PeekSurface retains its exiting child; keep the last editor rendered
  // until the visual exit finishes instead of flashing empty.
  const retainedEditor = useRef<EditorState | null>(null);
  if (editor) retainedEditor.current = editor;
  const visibleEditor = editor ?? retainedEditor.current;

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextItems, nextTypes] = await Promise.all([
        listNotifierConfigsApi(config),
        listNotifierTypesApi(config),
      ]);
      setItems(nextItems);
      setTypes(nextTypes);
      setLoadedOnce(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [config]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const typeIds = useMemo(() => Object.keys(types), [types]);

  const openNew = () => {
    setSaveError(null);
    setTestState(null);
    setEditor({ original: null, name: "", type: typeIds[0] ?? "email", config: {} });
  };

  const openEdit = (item: NotifierConfigItem) => {
    setSaveError(null);
    setTestState(null);
    setEditor({ original: item.name, name: item.name, type: item.type, config: { ...item.config } });
  };

  const save = async () => {
    if (!editor || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      if (editor.original) {
        await updateNotifierApi(config, editor.original, {
          name: editor.name !== editor.original ? editor.name : undefined,
          config: editor.config,
        });
      } else {
        await createNotifierApi(config, { name: editor.name, type: editor.type, config: editor.config });
      }
      setEditor(null);
      await refresh();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (name: string) => {
    try {
      await deleteNotifierApi(config, name);
      setEditor(null);
      await refresh();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    }
  };

  const test = async (name: string) => {
    setTestState("sending");
    try {
      await testNotifierApi(config, name);
      setTestState("sent");
    } catch (err) {
      setTestState(err instanceof Error ? err.message : String(err));
    }
  };

  const editorFields = visibleEditor ? types[visibleEditor.type]?.fields ?? [] : [];
  const emailAccounts = visibleEditor?.type === "email" ? types.email?.accounts ?? [] : [];
  const editorComplete = !!editor
    && editor.name.trim().length > 0
    && editorFields.every((field) => (editor.config[field] ?? "").trim().length > 0);

  if (loading && !loadedOnce) {
    return <SettingsTabSkeleton label="Loading notifiers…" />;
  }

  return (
    <>
      <SettingsPageAction>
        <span className="flex items-center gap-2">
          <SettingsRefreshAction label="Refresh notifiers" loading={loading} onRefresh={refresh} />
          <IconButton tone="primary" shape="circle" onClick={openNew} aria-label="New channel" title="New channel">
            <Plus size={ICON.XS} />
          </IconButton>
        </span>
      </SettingsPageAction>

      {error && (
        <SettingsInlineError
          title={settingsErrorTitle("notifiers", loadedOnce)}
          message={settingsErrorMessage(error)}
        />
      )}

      <SettingsSection title="Channels">
        <SettingsSurface>
          {items.length === 0 ? (
            <p className="p-4 text-sm text-muted">
              No channels yet. The agent&apos;s notify tool and automations deliver through the
              channels you add here.
            </p>
          ) : (
            <ul className="m-0 min-w-0 list-none p-0">
              {items.map((item) => (
                <li key={item.name} className="border-t border-line-soft first:border-t-0">
                  <button
                    type="button"
                    data-notifier-row
                    onClick={() => openEdit(item)}
                    aria-label={`Manage ${item.name}`}
                    className="settings-data-row w-full text-left"
                  >
                    <div className="settings-data-row-main">
                      <div className="settings-data-row-title">
                        <span className="truncate">{item.name}</span>
                      </div>
                      <div className="settings-data-row-sub">{TYPE_LABELS[item.type] ?? item.type}</div>
                    </div>
                    <div className="settings-data-row-copy truncate font-mono">{configSummary(item)}</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </SettingsSurface>
      </SettingsSection>

      <PeekSurface
        open={editor !== null}
        onClose={() => setEditor(null)}
        className="notifier-peek surface-peek"
        ariaLabel={visibleEditor?.original ? `Manage ${visibleEditor.original}` : "New channel"}
        layer="notifier-peek"
        focusOnOpen
        closeOnOutsidePointerDown
      >
        {visibleEditor && (
          <>
            <header className="arden-peek-rule-below">
              <strong>{visibleEditor.original ?? "New channel"}</strong>
              <IconButton data-peek-close onClick={() => setEditor(null)} aria-label="Close" title="Close">
                <X size={ICON.SM} />
              </IconButton>
            </header>
            <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
              <label className="flex flex-col gap-1 text-xs text-muted">
                Name
                <Input
                  value={editor?.name ?? visibleEditor.name}
                  onChange={(e) => editor && setEditor({ ...editor, name: e.target.value })}
                  placeholder="my-channel"
                  aria-label="Channel name"
                  spellCheck={false}
                  autoComplete="off"
                />
              </label>
              {visibleEditor.original ? (
                <div className="flex flex-col gap-1 text-xs text-muted">
                  Type
                  <span className="text-sm text-ink">{TYPE_LABELS[visibleEditor.type] ?? visibleEditor.type}</span>
                </div>
              ) : (
                <label className="flex flex-col gap-1 text-xs text-muted">
                  Type
                  <Select
                    value={visibleEditor.type}
                    onChange={(type) => editor && setEditor({ ...editor, type, config: {} })}
                    options={typeIds.map((id) => ({ value: id, label: TYPE_LABELS[id] ?? id }))}
                    aria-label="Channel type"
                  />
                </label>
              )}
              {editorFields.map((field) => {
                const label = FIELD_LABELS[field] ?? field;
                const value = editor?.config[field] ?? visibleEditor.config[field] ?? "";
                const commit = (next: string) =>
                  editor && setEditor({ ...editor, config: { ...editor.config, [field]: next } });
                return (
                  <label key={field} className="flex flex-col gap-1 text-xs text-muted">
                    {label}
                    {field === "from_account" && emailAccounts.length > 0 ? (
                      <Select
                        value={value}
                        onChange={commit}
                        options={emailAccounts.map((account) => ({ value: account, label: account }))}
                        placeholder="Choose account"
                        aria-label={label}
                      />
                    ) : field === "command" ? (
                      <Textarea
                        value={value}
                        onChange={(e) => commit(e.target.value)}
                        rows={6}
                        aria-label={label}
                        spellCheck={false}
                        autoComplete="off"
                        className="w-full resize-y whitespace-pre-wrap break-all font-mono text-xs leading-relaxed"
                      />
                    ) : (
                      <Input
                        value={value}
                        onChange={(e) => commit(e.target.value)}
                        aria-label={label}
                        spellCheck={false}
                        autoComplete="off"
                        className="font-mono text-sm"
                      />
                    )}
                  </label>
                );
              })}
              {visibleEditor.original && (
                <div className="flex items-center gap-2">
                  <Button
                    variant="quiet"
                    size="sm"
                    onClick={() => visibleEditor.original && void test(visibleEditor.original)}
                    disabled={testState === "sending"}
                  >
                    Send test
                  </Button>
                  <span className="text-xs" role={testState && testState !== "sending" && testState !== "sent" ? "alert" : undefined}>
                    {testState === "sending" && <span className="text-faint">sending…</span>}
                    {testState === "sent" && <span className="text-ok">sent</span>}
                    {testState && testState !== "sending" && testState !== "sent" && (
                      <span className="text-bad">{testState}</span>
                    )}
                  </span>
                </div>
              )}
              {saveError && <p role="alert" className="text-xs text-bad">{saveError}</p>}
            </div>
            <footer className="arden-peek-rule-above flex shrink-0 items-center gap-2 p-3">
              {visibleEditor.original && (
                <ConfirmDeleteButton
                  label={`Delete ${visibleEditor.original}`}
                  onConfirm={() => visibleEditor.original && void remove(visibleEditor.original)}
                />
              )}
              <span className="ml-auto flex items-center gap-2">
                <Button variant="quiet" onClick={() => setEditor(null)}>Cancel</Button>
                <Button variant="primary" onClick={() => void save()} disabled={!editorComplete || saving}>
                  {visibleEditor.original ? "Save" : "Create"}
                </Button>
              </span>
            </footer>
          </>
        )}
      </PeekSurface>
    </>
  );
}
