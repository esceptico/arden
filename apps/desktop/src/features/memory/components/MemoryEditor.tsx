import { useEffect, useRef } from "react";
import { splitFrontmatter } from "@/features/memory/lib/format";
import { stem } from "@/features/memory/lib/workspaceTree";

/** Markdown-source editor for a memory page — the draft's in-page editor:
 *  same page container, filename title, autosized body-only textarea
 *  (frontmatter is held back and reattached on change). Cmd/Ctrl+S (bound
 *  in ArtifactMemoryView) sends the draft to the review flow; Esc closes. */
export function MemoryEditor({
  path,
  baseContent,
  value,
  saving,
  error,
  onChange,
  onClose,
}: {
  path: string;
  baseContent: string;
  value: string;
  saving: boolean;
  error: string | null;
  onChange: (value: string) => void;
  onClose: () => void;
}) {
  const sourceRef = useRef<HTMLTextAreaElement>(null);
  const { frontmatter, body } = splitFrontmatter(value);
  const dirty = value !== baseContent;
  const hintId = `memory-editor-hint-${path.replace(/[^a-zA-Z0-9_-]/g, "-")}`;

  const autosize = () => {
    const textarea = sourceRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;
  };

  useEffect(() => {
    autosize();
    sourceRef.current?.focus();
    sourceRef.current?.setSelectionRange(0, 0);
    // Reclaim only from body — if another surface owns focus, stealing it is
    // worse. Two stages: an exiting peer can hold focus through its whole
    // ~250ms teardown before the blur-to-body lands.
    const reclaim = () => {
      if (document.activeElement === document.body) sourceRef.current?.focus();
    };
    const retries = [window.setTimeout(reclaim, 240), window.setTimeout(reclaim, 460)];
    return () => retries.forEach((timer) => window.clearTimeout(timer));
  }, []);

  return (
    <article
      data-memory-editor
      data-memory-editor-mode="source"
      aria-label={`Edit ${path}`}
      className="mw-scroller scroll-thin h-full"
    >
      <p role="status" aria-live="polite" className="sr-only">
        {saving ? `Reviewing ${path}` : dirty ? `Editing ${path} — unsaved draft` : `Editing ${path}`}
      </p>
      <p id={hintId} className="sr-only">Press Cmd/Ctrl+S to review changes and Escape to close the editor.</p>
      <div className="mw-page">
        <p className="mw-note-crumb">{path.includes("/") ? path.split("/").slice(0, -1).join(" / ") : "memory"} / <b>{stem(path)}</b></p>
        <h1 className="mw-note-title">{stem(path)}</h1>
        <textarea
          ref={sourceRef}
          className="mw-editor-textarea"
          value={body}
          spellCheck
          aria-label={`Markdown source for ${path}`}
          aria-describedby={hintId}
          onChange={(event) => {
            onChange(frontmatter + event.currentTarget.value);
            autosize();
          }}
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            event.stopPropagation();
            onClose();
          }}
        />
        {error && <p role="alert" className="mt-4 text-xs text-bad">{error}</p>}
      </div>
    </article>
  );
}
