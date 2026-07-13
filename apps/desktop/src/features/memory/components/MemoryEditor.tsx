import { useEffect, useRef } from "react";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";

export function MemoryEditor({
  path,
  title,
  baseRevision,
  baseContent,
  value,
  saving,
  error,
  onChange,
  onSave,
  onClose,
}: {
  path: string;
  title: string;
  baseRevision: string;
  baseContent: string;
  value: string;
  saving: boolean;
  error: string | null;
  onChange: (value: string) => void;
  onSave: () => void;
  onClose: () => void;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const dirty = value !== baseContent;
  const hintId = `memory-editor-hint-${path.replace(/[^a-zA-Z0-9_-]/g, "-")}`;

  useEffect(() => {
    textareaRef.current?.focus();
  }, [path, baseRevision]);

  return (
    <section
      data-memory-editor
      data-theme-ready="true"
      data-responsive="true"
      aria-label={`Edit ${path}`}
      className="flex h-full min-h-0 min-w-0 flex-col bg-bg-main"
    >
      <p role="status" aria-live="polite" className="sr-only">Editing Markdown source for {path}</p>
      <header className="flex min-w-0 flex-wrap items-center gap-2 border-b border-line-soft bg-surface px-4 py-3 sm:gap-3">
        <div className="min-w-0 flex-1 basis-48">
          <h1 className="truncate text-lg font-semibold text-ink">{title}</h1>
          <p className="truncate font-mono text-2xs text-faint">{path} · rev {baseRevision.slice(0, 12)}</p>
        </div>
        <span role="status" className="ml-auto text-xs text-muted">
          {dirty ? "Unsaved draft" : "No changes"}
        </span>
        <Button variant="secondary" size="sm" aria-label="Close editor and keep draft" onClick={onClose}>
          Close
        </Button>
        <Button size="sm" aria-label="Review memory edit" disabled={!dirty || saving} onClick={onSave}>
          {saving ? "Preparing…" : "Review"}
        </Button>
      </header>
      <div className="flex min-h-0 flex-1 flex-col p-4 sm:p-6">
        <p id={hintId} className="mb-2 text-xs text-faint">
          Markdown source. Press Cmd/Ctrl+S to review; closing keeps this revision-specific draft.
        </p>
        <Textarea
          ref={textareaRef}
          value={value}
          onChange={(event) => onChange(event.currentTarget.value)}
          aria-label={`Markdown source for ${path}`}
          aria-describedby={hintId}
          spellCheck
          className="min-h-0 min-w-0 flex-1 resize-none overflow-auto whitespace-pre font-mono text-sm leading-6"
        />
        {error && <p role="alert" className="mt-2 text-xs text-bad">{error}</p>}
      </div>
    </section>
  );
}
