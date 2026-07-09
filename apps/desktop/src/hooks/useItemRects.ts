import { useRef, useState, useCallback, useEffect, type RefObject } from "react";

/**
 * Layout boxes of a row-control's items, self-registered by the rows.
 * Drives the absolutely-positioned selection/focus layers in RadioGroup and
 * CheckboxGroup (the traveling selected fill, merge/split blocks, focus ring).
 */

export interface ItemRect {
  top: number;
  height: number;
  left: number;
  width: number;
}

export function useItemRects() {
  const itemsRef = useRef(new Map<number, HTMLElement>());
  const [itemRects, setItemRects] = useState<ItemRect[]>([]);
  const pendingRef = useRef(false);

  const measureItems = useCallback(() => {
    const rects: ItemRect[] = [];
    itemsRef.current.forEach((element, index) => {
      // offset* (not getBoundingClientRect) so measurements are unaffected by
      // CSS transforms on an ancestor (e.g. a parent scale animation), and
      // match the coordinate space of `position: absolute` children.
      rects[index] = {
        top: element.offsetTop,
        height: element.offsetHeight,
        left: element.offsetLeft,
        width: element.offsetWidth,
      };
    });
    setItemRects(rects);
  }, []);

  const registerItem = useCallback(
    (index: number, element: HTMLElement | null) => {
      if (element) {
        itemsRef.current.set(index, element);
      } else {
        itemsRef.current.delete(index);
      }
      // Coalesce a mount-batch of register/unregister calls into one measure.
      // A microtask (not rAF): it runs after the commit that registered the
      // rows, and keeps working in hidden/background windows where rAF is
      // frozen.
      if (pendingRef.current) return;
      pendingRef.current = true;
      queueMicrotask(() => {
        pendingRef.current = false;
        measureItems();
      });
    },
    [measureItems],
  );

  return { itemRects, registerItem, measureItems };
}

/** Child-item self-registration: call in an effect with the item ref + index. */
export function useRegisterItem(
  registerItem: (index: number, element: HTMLElement | null) => void,
  index: number,
  ref: RefObject<HTMLElement | null>,
) {
  useEffect(() => {
    registerItem(index, ref.current);
    return () => registerItem(index, null);
  }, [index, registerItem, ref]);
}
