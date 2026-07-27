import type { AppConfig } from "@/api/core";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { SettingsInlineError } from "@/features/settings/components/SettingsNotice";
import {
  SettingsSection,
  SettingsSettingRow,
  SettingsSurface,
} from "@/features/settings/components/SettingsPage";

export function ConnectionTab({
  formRef,
  draft,
  error,
  saving,
  onUpdate,
  onSubmit,
}: {
  formRef: React.RefObject<HTMLFormElement | null>;
  draft: AppConfig;
  error: string | null;
  saving: boolean;
  onUpdate: (patch: Partial<AppConfig>) => void;
  onSubmit: (e: React.FormEvent) => void;
}) {
  return (
    <form ref={formRef} onSubmit={onSubmit} className="grid gap-5">
      <SettingsSection title="Server" detail="saved together">
        <SettingsSurface>
          <SettingsSettingRow
            title="Server URL"
            hint="The address where your Arden server is running."
            control={
              <div className="settings-control-wide">
                <Input
                  type="url"
                  value={draft.serverUrl}
                  onChange={(event) => onUpdate({ serverUrl: event.target.value })}
                  placeholder="http://localhost:6877"
                  aria-label="Server URL"
                  spellCheck={false}
                  autoComplete="off"
                />
              </div>
            }
          />
          <SettingsSettingRow
            title="API key"
            hint="From your server config. Used as a Bearer token."
            control={
              <div className="settings-control-wide">
                <Input
                  type="password"
                  value={draft.apiKey}
                  onChange={(event) => onUpdate({ apiKey: event.target.value })}
                  placeholder="arden_…"
                  aria-label="API key"
                  spellCheck={false}
                  autoComplete="off"
                />
              </div>
            }
          />
        </SettingsSurface>
        <div className="settings-scope-note">
          Save &amp; reconnect validates both fields as one connection.
        </div>
        {error && <SettingsInlineError title="Could not connect" message={error} />}
        <div className="settings-section-actions">
          <Button type="submit" variant="primary" disabled={saving}>
            <BlurSwap swapKey={saving ? "checking" : "save"} blur={2}>
              {saving ? "Checking…" : "Save & reconnect"}
            </BlurSwap>
          </Button>
        </div>
      </SettingsSection>
    </form>
  );
}
