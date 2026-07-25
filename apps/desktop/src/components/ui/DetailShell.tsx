import type { ReactNode } from "react";

export function DetailShell({
  header,
  body,
  meta,
  actions,
}: {
  header: ReactNode;
  body: ReactNode;
  meta: ReactNode;
  actions: ReactNode;
}) {
  return (
    <div className="flex flex-col h-full">
      <div className="px-7 pt-6 pb-3">{header}</div>
      <div className="flex-1 min-h-0 px-7 overflow-y-auto scroll-thin scroll-fade">
        {body}
        <div className="mt-7 mb-6">{meta}</div>
      </div>
      <div className="flex items-center justify-end gap-2 px-7 py-3">{actions}</div>
    </div>
  );
}
