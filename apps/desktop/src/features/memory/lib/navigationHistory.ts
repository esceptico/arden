import { NavigationHistory as GenericNavigationHistory } from "@/lib/navigationHistory";

export interface NavigationLocation {
  path: string;
  anchor: string | null;
  scrollTop: number;
  focusSelector: string | null;
}

/** Two pages are the same destination when path and anchor match; scroll and
 *  focus are restoration detail, not identity. */
function sameDestination(left: NavigationLocation, right: NavigationLocation) {
  return left.path === right.path && left.anchor === right.anchor;
}

/** Page history inside the Memory vault. Distinct from the shell's route
 *  history — while Memory is open it owns ⌘[ / ⌘], the same way a browser's
 *  innermost history wins. */
export class NavigationHistory extends GenericNavigationHistory<NavigationLocation> {
  constructor(limit = 100) {
    super(sameDestination, limit);
  }
}
