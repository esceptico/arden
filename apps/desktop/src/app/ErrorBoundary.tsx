import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "@/components/ui/Button";

interface Props {
  children: ReactNode;
  /** Optional override for the fallback UI. Receives the caught error
   *  and a `reset` callback that re-mounts the children. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface State {
  error: Error | null;
}

/** Last-resort safety net so a thrown render error doesn't unmount the
 *  entire app tree. Wrap subtrees that can recover independently
 *  (Chat/Messages, the Suspense'd modals, etc.) so one bad render
 *  doesn't black out the shell. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface to devtools console; production logging hook can attach
    // here later (Sentry/etc).
    console.error("[arden] render error caught by ErrorBoundary", error, info);
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);
    return <DefaultFallback error={error} onReset={this.reset} />;
  }
}

function DefaultFallback({ error, onReset }: { error: Error; onReset: () => void }) {
  return (
    <div className="absolute inset-0 grid place-items-center p-8">
      <div className="surface-panel surface-radius-md max-w-[480px] p-6">
        <div className="text-lg font-semibold tracking-[var(--tracking-heading)] text-ink">
          Something went wrong rendering this view.
        </div>
        <div className="mt-2 text-sm text-muted">
          The rest of the app is still running. You can try again, or reload
          the window if it keeps happening.
        </div>
        <pre className="scroll-thin mt-3 text-xs text-faint font-mono whitespace-pre-wrap break-words max-h-32 overflow-auto">
          {error.message}
        </pre>
        <div className="mt-4 flex items-center gap-2">
          <Button variant="primary" size="sm" onClick={onReset}>
            Try again
          </Button>
          <Button variant="ghost" size="sm" onClick={() => window.location.reload()}>
            Reload window
          </Button>
        </div>
      </div>
    </div>
  );
}
