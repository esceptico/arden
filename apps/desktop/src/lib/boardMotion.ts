type PageEntranceRun = Readonly<{
  animations: Animation[];
  finished: Promise<boolean>;
}>;

type SurfaceMotionOptions = Readonly<{
  axis?: "x" | "y";
  distance?: number;
  duration?: number;
  spring?: Readonly<Record<string, number>>;
  animate?: boolean;
  beforeOpen?: () => void;
  afterClose?: () => void;
}>;

type BoardTabChange = Readonly<{
  value: string | null;
  previousValue: string | null;
  button: HTMLButtonElement;
  previousButton: HTMLButtonElement | null;
}>;

type BoardTabsController = Readonly<{
  select(
    valueOrButton: string | HTMLButtonElement,
    options?: { animate?: boolean; emit?: boolean; focus?: boolean },
  ): boolean;
  sync(options?: { animate?: boolean }): void;
  destroy(): void;
}>;

export type BoardMotion = Readonly<{
  surface: Readonly<{
    show(element: HTMLElement, options?: SurfaceMotionOptions): Promise<boolean>;
    hide(element: HTMLElement, options?: SurfaceMotionOptions): Promise<boolean>;
    cancel(element: HTMLElement): void;
  }>;
  spring: Readonly<{
    sheet: Readonly<Record<string, number>>;
  }>;
  pageEntrance: Readonly<{
    bind(root?: ParentNode): PageEntranceRun | null;
  }>;
  tabs: Readonly<{
    bind(
      container: HTMLElement,
      options?: {
        tabSelector?: string;
        valueAttribute?: string;
        activeClass?: string;
        variant?: string;
        onChange?: (change: BoardTabChange) => void;
      },
    ): BoardTabsController | null;
    /** Lower-level than bind() — positions/animates the indicator span for
     *  a single target without taking over click/keyboard handling. Pass a
     *  falsy target to hide the indicator (e.g. nothing selected). Used by
     *  bind() internally; also the way to drive a sliding indicator off of
     *  externally-owned selection state (see ArtifactMemoryView's
     *  instrument panel — links/records/activity keep their own onClick
     *  toggle-to-close logic, sync() just mirrors it visually). */
    sync(
      container: HTMLElement,
      target: HTMLElement | null | undefined,
      options?: { variant?: string; animate?: boolean },
    ): HTMLElement;
    ensureIndicator(container: HTMLElement, options?: { indicatorClass?: string; indicatorSelector?: string }): HTMLElement;
  }>;
  geometry: Readonly<{
    read(name: string): number;
  }>;
}>;

declare global {
  interface Window {
    BOARD_MOTION?: BoardMotion;
  }
}

/** Access the behavior engine (main.tsx's side-effect import of
 * lib/boardMotionEngine.js).
 * Non-browser renderers leave motion absent and retain the final state. */
export function getBoardMotion(): BoardMotion | null {
  return window.BOARD_MOTION ?? null;
}
