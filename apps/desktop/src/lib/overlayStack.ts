import { useEffect, useRef, useSyncExternalStore, type RefObject } from "react";

interface OverlayEntry {
  id: symbol;
  element: HTMLElement;
  dismiss: () => void;
  dismissDisabled: boolean;
  blocksInput: boolean;
  sharedScrim: boolean;
  scrimOpen: () => boolean;
}

const stack: OverlayEntry[] = [];
const originalInert = new Map<HTMLElement, boolean>();
const subscribers = new Set<() => void>();
let listening = false;

export function hasBlockingOverlay(): boolean {
  return stack.some((entry) => entry.blocksInput);
}

export function hasSharedScrimOpen(): boolean {
  return stack.some((entry) => entry.sharedScrim && entry.scrimOpen());
}

function notifyOverlayChange(): void {
  for (const subscriber of subscribers) subscriber();
}

function subscribe(listener: () => void): () => void {
  subscribers.add(listener);
  return () => subscribers.delete(listener);
}

/**
 * A portaled surface can use this instead of guessing from its feature or
 * parent. A popover above a live blocking layer belongs to the nested level;
 * otherwise it belongs to the normal page-popover level.
 */
export function useHasBlockingOverlay(): boolean {
  return useSyncExternalStore(subscribe, hasBlockingOverlay, () => false);
}

/** Whether Arden's one global modal scrim should be visible. */
export function useSharedScrimOpen(): boolean {
  return useSyncExternalStore(subscribe, hasSharedScrimOpen, () => false);
}

/** Whether the upper blocking surface would yield — asked before opening a
 *  replacement surface, so a question that cannot be dismissed still wins
 *  without closing anything that merely could be. */
export function canDismissTopBlockingOverlay(): boolean {
  for (let index = stack.length - 1; index >= 0; index -= 1) {
    const entry = stack[index];
    if (!entry.blocksInput) continue;
    return !entry.dismissDisabled;
  }
  return false;
}

/** Close the upper blocking surface so a replacement surface can take over. */
export function dismissTopBlockingOverlay(): boolean {
  for (let index = stack.length - 1; index >= 0; index -= 1) {
    const entry = stack[index];
    if (!entry.blocksInput) continue;
    if (entry.dismissDisabled) return false;
    entry.dismiss();
    return true;
  }
  return false;
}

/** Backdrop clicks belong to the upper open surface sharing the global scrim. */
export function dismissTopSharedScrimOverlay(): boolean {
  for (let index = stack.length - 1; index >= 0; index -= 1) {
    const entry = stack[index];
    if (!entry.sharedScrim || !entry.scrimOpen()) continue;
    if (entry.dismissDisabled) return false;
    entry.dismiss();
    return true;
  }
  return false;
}

function dismissTransientOverlays(): void {
  const transient = stack.filter((entry) => !entry.blocksInput);
  for (const entry of transient) {
    // A page popover can have a higher global z-index than a takeover because
    // the same token also serves popovers opened inside that takeover. Hide
    // the old page layer synchronously, then let its owner perform the normal
    // stateful dismissal/unmount.
    entry.element.style.visibility = "hidden";
    entry.element.setAttribute("aria-hidden", "true");
    entry.dismiss();
  }
}

function restoreInertState(): void {
  for (const [element, inert] of originalInert) {
    if (element.isConnected) element.inert = inert;
  }
  originalInert.clear();
}

function syncInertState(): void {
  const app = document.querySelector<HTMLElement>("#app");
  if (!app) return;

  restoreInertState();
  let topBlockingIndex = -1;
  for (let index = stack.length - 1; index >= 0; index -= 1) {
    if (stack[index].blocksInput) {
      topBlockingIndex = index;
      break;
    }
  }
  if (topBlockingIndex < 0) {
    return;
  }

  const topBlocking = stack[topBlockingIndex];
  const allowedByParent = new Map<HTMLElement, Set<HTMLElement>>();
  for (const entry of stack.slice(topBlockingIndex)) {
    // A nonblocking peek/popover inside the active blocking surface is
    // already within that surface's allowed branch. Walking its descendants
    // would incorrectly inert its siblings (for example Memory's rail and
    // paper while Page Peek is open). Portaled transients still need their
    // own branch and therefore continue through this path.
    if (
      !entry.blocksInput
      && entry !== topBlocking
      && topBlocking.element.contains(entry.element)
    ) {
      continue;
    }
    let branch: HTMLElement = entry.element;
    let parent = branch.parentElement;
    while (parent && (parent === app || app.contains(parent))) {
      const allowed = allowedByParent.get(parent) ?? new Set<HTMLElement>();
      allowed.add(branch);
      allowedByParent.set(parent, allowed);
      if (parent === app) break;
      branch = parent;
      parent = parent.parentElement;
    }
  }

  if (!allowedByParent.has(app)) allowedByParent.set(app, new Set());
  for (const [parent, allowed] of allowedByParent) {
    for (const child of Array.from(parent.children)) {
      if (!(child instanceof HTMLElement) || allowed.has(child)) continue;
      if (!originalInert.has(child)) originalInert.set(child, child.inert);
      child.inert = true;
    }
  }
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.key !== "Escape") return;
  const top = stack.at(-1);
  if (!top) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  if (!top.dismissDisabled) top.dismiss();
}

function ensureListener(): void {
  if (listening) return;
  window.addEventListener("keydown", onKeyDown, true);
  listening = true;
}

function releaseListener(): void {
  if (!listening || stack.length > 0) return;
  window.removeEventListener("keydown", onKeyDown, true);
  listening = false;
}

/**
 * Registers a surface with Arden's single overlay stack. Only the top layer
 * receives Escape; blocking layers make the lower application inert. `active`
 * is the full interactive lifecycle, including a retained exit phase—not just
 * the owner's requested open state.
 */
export function useOverlayLayer(
  ref: RefObject<HTMLElement | null>,
  active: boolean,
  onDismiss: () => void,
  dismissDisabled = false,
  blocksInput = true,
  dismissTransientsOnOpen = true,
  sharedScrim = false,
  sharedScrimOpen = active,
): void {
  const id = useRef(Symbol("overlay-layer"));
  const dismiss = useRef(onDismiss);
  const scrimOpen = useRef(sharedScrimOpen);
  dismiss.current = onDismiss;
  scrimOpen.current = sharedScrimOpen;

  useEffect(() => {
    if (!active || !ref.current) return;
    ensureListener();
    // A tier-three surface can preserve its owning transient underneath it
    // (Memory's activity diff is the canonical case). The blocking layer
    // still makes that parent inert; it simply must not unmount it first.
    if (blocksInput && dismissTransientsOnOpen) dismissTransientOverlays();
    const entry: OverlayEntry = {
      id: id.current,
      element: ref.current,
      dismiss: () => dismiss.current(),
      dismissDisabled,
      blocksInput,
      sharedScrim,
      scrimOpen: () => scrimOpen.current,
    };
    stack.push(entry);
    syncInertState();
    notifyOverlayChange();
    return () => {
      const index = stack.findIndex((candidate) => candidate.id === id.current);
      if (index >= 0) stack.splice(index, 1);
      syncInertState();
      notifyOverlayChange();
      releaseListener();
    };
  }, [active, blocksInput, dismissDisabled, dismissTransientsOnOpen, ref, sharedScrim]);

  // Opening/closing a shared-scrim surface changes only its visual intent.
  // Keep the stack entry registered through exit so inertness and focus still
  // release after the sheet, while the one scrim fades concurrently.
  useEffect(() => {
    if (active && sharedScrim) notifyOverlayChange();
  }, [active, sharedScrim, sharedScrimOpen]);
}
