/** A route with unsaved work can block navigation away from itself.
 *
 *  Exactly one stage route is mounted at a time (the route host runs
 *  AnimatePresence in mode="wait"), so a module-level slot is the whole
 *  mechanism — no stack, no keys.
 *
 *  The guard receives the navigation it interrupted. Returning false blocks
 *  the move and hands the route responsibility for asking the user; calling
 *  `resume()` afterwards replays it. Returning true lets it through. */
type NavigationGuard = (resume: () => void) => boolean;

let guard: NavigationGuard | null = null;

export function setNavigationGuard(next: NavigationGuard | null): void {
  guard = next;
}

/** Run `navigate` unless the mounted route claims unsaved work. */
export function withNavigationGuard(navigate: () => void): void {
  if (guard && !guard(navigate)) return;
  navigate();
}
