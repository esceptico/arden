import { useStore } from "@/stores";
import { Markdown } from "@/components/ui/Markdown";
import { SystemSheet } from "@/components/ui/SystemSheet";

/** Generic markdown viewer modal. State lives in the store as `viewingMarkdown`
 *  so any code can pop the viewer with a `setViewingMarkdown({title, content, ...})`
 *  call. Used today for skill files; reusable for memory notes, area docs,
 *  anything else that's markdown. */
export function MarkdownViewer() {
  const view = useStore((s) => s.viewingMarkdown);
  const close = useStore((s) => s.setViewingMarkdown);

  return (
    <SystemSheet
      open={!!view}
      onClose={() => close(null)}
      title={view?.title ?? "Markdown viewer"}
      ariaLabel={view?.title ?? "Markdown viewer"}
      closeLabel="Close Markdown viewer"
    >
      {view && (
        <>
          <article className="mx-auto max-w-[46rem]">
            {view.subtitle && <p className="mb-4 text-xs font-mono text-faint">{view.subtitle}</p>}
            <Markdown content={view.content} typeset />
          </article>
        </>
      )}
    </SystemSheet>
  );
}
