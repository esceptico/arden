import { useEffect, useMemo, useState } from "react";
import { FolderOpen } from "lucide-react";
import type { Area } from "@/api/types";
import { archiveArea, saveArea } from "@/actions/sessions";
import { detachAreaPage, updateAreaAutonomy } from "@/actions/areas";
import { selectDirectory } from "@/features/sessions/lib/directoryPicker";
import { PageModal } from "@/components/ui/PageModal";
import { Button } from "@/components/ui/Button";
import { ConfirmDeleteButton } from "@/components/ui/ConfirmDeleteButton";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Textarea";
import { LabeledField } from "@/components/ui/LabeledField";

interface AreaSettingsModalProps {
  area: Area | null;
  onClose: () => void;
}

export function AreaSettingsModal({ area, onClose }: AreaSettingsModalProps) {
  const [name, setName] = useState("");
  const [cwd, setCwd] = useState("");
  const [instructions, setInstructions] = useState("");
  const [saving, setSaving] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [pickingCwd, setPickingCwd] = useState(false);
  const [capabilityBusy, setCapabilityBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!area) return;
    setName(area.name);
    setCwd(area.default_cwd ?? "");
    setInstructions(area.instructions ?? "");
    setError(null);
    setSaving(false);
    setArchiving(false);
    setPickingCwd(false);
  }, [area]);

  const busy = saving || archiving || capabilityBusy;
  const canSave = useMemo(() => Boolean(area && name.trim() && !busy), [area, name, busy]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!area || !canSave) return;
    setSaving(true);
    setError(null);
    try {
      await saveArea(area.area_id, {
        name: name.trim(),
        default_cwd: cwd.trim() || null,
        instructions: instructions.trim() || null,
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function pickCwd() {
    if (busy || pickingCwd) return;
    setPickingCwd(true);
    setError(null);
    try {
      const selected = await selectDirectory({ defaultPath: cwd.trim() || undefined });
      if (selected) setCwd(selected);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPickingCwd(false);
    }
  }

  async function archive() {
    if (!area || archiving) return;
    setArchiving(true);
    setError(null);
    try {
      await archiveArea(area.area_id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setArchiving(false);
    }
  }

  async function disableCustodian() {
    if (!area || capabilityBusy) return;
    setCapabilityBusy(true);
    setError(null);
    try {
      await updateAreaAutonomy(area.area_id, null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCapabilityBusy(false);
    }
  }

  async function detachPage() {
    if (!area || capabilityBusy) return;
    setCapabilityBusy(true);
    setError(null);
    try {
      await detachAreaPage(area.area_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCapabilityBusy(false);
    }
  }

  return (
    <PageModal
      open={Boolean(area)}
      onClose={busy ? () => {} : onClose}
      disableEscape={busy}
      size="w-[min(640px,calc(100vw-32px))] h-[min(520px,calc(100vh-32px))]"
      header={{ title: area?.name ?? "Area" }}
    >
      <form onSubmit={submit} className="min-h-0 grid grid-rows-[minmax(0,1fr)_auto]">
        <div className="min-h-0 overflow-y-auto scroll-thin px-5 pb-4 space-y-4">
          <Input
            label="Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            autoFocus
          />
          <LabeledField label="Default cwd">
            <div className="flex gap-2">
              <Input
                className="min-w-0 flex-1 font-mono"
                value={cwd}
                onChange={(event) => setCwd(event.target.value)}
                spellCheck={false}
              />
              <Button
                variant="secondary"
                disabled={busy || pickingCwd}
                onClick={pickCwd}
                leadingIcon={FolderOpen}
              >
                Choose
              </Button>
            </div>
          </LabeledField>
          <Textarea
            label="Instructions"
            className="w-full min-h-[150px] resize-none"
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
          />
          {area?.page_path && (
            <LabeledField label="Area page">
              <div className="flex items-center gap-2 rounded-lg bg-surface-soft px-3 py-2">
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-soft">
                  {area.page_path}
                </span>
                {area.autonomy !== null ? (
                  <Button variant="ghost" size="sm" disabled={busy} onClick={() => void disableCustodian()}>
                    Disable custodian
                  </Button>
                ) : (
                  <Button variant="ghost" size="sm" disabled={busy} onClick={() => void detachPage()}>
                    Detach
                  </Button>
                )}
              </div>
            </LabeledField>
          )}
          {error && <div className="text-sm text-bad">{error}</div>}
        </div>
        <footer className="flex items-center justify-between gap-2 px-5 py-4 border-t border-line-soft">
          <ConfirmDeleteButton
            size="md"
            label={`Archive ${area?.name ?? "area"}`}
            busy={busy}
            onConfirm={() => void archive()}
          />
          <div className="flex items-center justify-end gap-2">
            <Button variant="ghost" disabled={busy} onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" disabled={!canSave}>
              Save
            </Button>
          </div>
        </footer>
      </form>
    </PageModal>
  );
}
