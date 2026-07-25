export const QUEUE_MAX_ITEMS = 10;
export const QUEUE_ITEM_HEIGHT_PX = 44;
export const QUEUE_COMPOSER_GAP_PX = 8;
export const QUEUE_STACK_PEEK_PX = 12;
export const QUEUE_STACK_SCALE_STEP = 0.05;
export const QUEUE_STACK_GAP_PX = 8;
export const QUEUE_STACK_MAX_PEEKS = 2;
export const QUEUE_SPRING = {
  type: "spring",
  duration: 0.16,
  bounce: 0,
} as const;

/** Exact collapsed height of the Fluid Functionalism queue stack. */
export function queueStackExtentPx(total: number): number {
  if (total <= 0) return 0;
  return (
    QUEUE_ITEM_HEIGHT_PX
    + Math.min(total - 1, QUEUE_STACK_MAX_PEEKS) * QUEUE_STACK_PEEK_PX
    + QUEUE_COMPOSER_GAP_PX
  );
}
