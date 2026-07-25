import { type ComponentPropsWithoutRef, type RefObject } from "react";
import { ScrollArea as ScrollAreaPrimitive } from "@base-ui/react/scroll-area";
import clsx from "clsx";

type Orientation = "vertical" | "horizontal" | "both";

interface ScrollAreaProps extends ComponentPropsWithoutRef<"div"> {
  /** Classes for the inner scrolling viewport — where `scroll-fade` goes. */
  viewportClassName?: string;
  /** Which axes get scrollbars. */
  orientation?: Orientation;
  /** Escape hatch for imperative scroll access (restore position, etc.). */
  viewportRef?: RefObject<HTMLDivElement | null>;
}

/**
 * Fluid-functionalism's scroll area (Base UI flavor; scrollbar machinery
 * adapted from Lina): native bars are hidden app-wide, and this paints the
 * one scrollbar the app owns — resting narrow and quiet, widening on hover,
 * fading out when idle (design/scroll.css). Constrain the height/width on
 * `className`; put `scroll-fade` on `viewportClassName`.
 *
 * FF's touch-primary native fallback is deliberately dropped: this renderer
 * ships only in the desktop app.
 */
export function ScrollArea({
  className,
  children,
  viewportClassName,
  viewportRef,
  orientation = "vertical",
  ...props
}: ScrollAreaProps) {
  return (
    <ScrollAreaPrimitive.Root className={clsx("relative overflow-hidden", className)} {...props}>
      <ScrollAreaPrimitive.Viewport
        ref={viewportRef}
        className={clsx("size-full rounded-[inherit] scroll-thin", viewportClassName)}
      >
        {/* Content gives Base UI an intrinsic size to measure horizontal
            overflow against. */}
        <ScrollAreaPrimitive.Content>{children}</ScrollAreaPrimitive.Content>
      </ScrollAreaPrimitive.Viewport>
      {orientation !== "horizontal" && <ScrollBar orientation="vertical" />}
      {orientation !== "vertical" && <ScrollBar orientation="horizontal" />}
      {orientation === "both" && <ScrollAreaPrimitive.Corner />}
    </ScrollAreaPrimitive.Root>
  );
}

function ScrollBar({ orientation }: { orientation: "vertical" | "horizontal" }) {
  return (
    <ScrollAreaPrimitive.Scrollbar orientation={orientation} className="arden-scrollbar">
      <ScrollAreaPrimitive.Thumb className="arden-scrollbar__thumb" />
    </ScrollAreaPrimitive.Scrollbar>
  );
}
