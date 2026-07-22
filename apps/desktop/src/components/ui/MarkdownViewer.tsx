import { ExternalLink } from "@/components/icons";
import { useStore } from "@/stores";
import { Markdown } from "@/components/ui/Markdown";
import { PageModal } from "@/components/ui/PageModal";
import { IconButton } from "@/components/ui/IconButton";
import { ICON } from "@/lib/icons";
import { ScrollFadeTop } from "@/components/ui/ScrollBlur";

/** Generic markdown viewer modal. State lives in the store as `viewingMarkdown`
 *  so any code can pop the viewer with a `setViewingMarkdown({title, content, ...})`
 *  call. Used today for skill files; reusable for memory notes, area docs,
 *  anything else that's markdown. */
export function MarkdownViewer() {
  const view = useStore((s) => s.viewingMarkdown);
  const close = useStore((s) => s.setViewingMarkdown);

  const openExternal = () => {
    if (view?.sourcePath) void window.ntrpDesktop?.shell?.openPath(view.sourcePath);
  };

  return (
    <PageModal
      open={!!view}
      onClose={() => close(null)}
      size="w-[min(720px,calc(100vw-32px))] max-h-[calc(100vh-32px)] sm:w-[min(720px,calc(100vw-80px))] sm:max-h-[calc(100vh-80px)]"
      header={
        view
          ? {
              title: view.title,
              subtitle: view.subtitle,
              actions: view.sourcePath ? (
                <IconButton
                  onClick={openExternal}
                  aria-label="Open in default app"
                  title="Open in default app"
                >
                  <ExternalLink size={ICON.SM} strokeWidth={2} />
                </IconButton>
              ) : undefined,
            }
          : undefined
      }
    >
      {view && (
        <div className="overflow-y-auto scroll-thin px-5 py-4">
          <ScrollFadeTop />
          <Markdown content={view.content} className="text-md leading-[1.6] text-ink" />
        </div>
      )}
    </PageModal>
  );
}
