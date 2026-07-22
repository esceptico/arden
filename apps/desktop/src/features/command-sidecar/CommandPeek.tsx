import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { AlertCircle, Check, Loader2, Square, X } from "@/components/icons";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { cancelRun, submitToolResult } from "@/api/chat";
import { runCommandSidecar } from "@/actions/commandSidecar";
import { connectAndResume, declineConnection, verifyAndResume } from "@/actions/connections";
import { applyCommandDestination } from "@/features/command-sidecar/navigation";
import { useCommandEvents } from "@/features/command-sidecar/useCommandEvents";
import { useStore, type PendingConnection } from "@/stores";
import { ICON } from "@/lib/icons";

export function CommandPeek() {
  useCommandEvents();
  const config = useStore((state) => state.config);
  const command = useStore((state) => state.commandSidecar);
  const close = useStore((state) => state.closeCommandSidecar);
  const clearApproval = useStore((state) => state.clearCommandApproval);
  const clearConnection = useStore((state) => state.clearCommandConnection);
  const fail = useStore((state) => state.failCommandSidecar);
  const [busy, setBusy] = useState(false);
  const appliedRuns = useRef(new Set<string>());

  useEffect(() => {
    if (!command.open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close, command.open]);

  useEffect(() => {
    const destination = command.outcome?.destination;
    const runId = command.runId;
    if (!destination || !runId || appliedRuns.current.has(runId)) return;
    appliedRuns.current.add(runId);
    const result = applyCommandDestination(destination);
    if (!result.ok && command.clientId) fail(command.clientId, result.error);
  }, [command.clientId, command.outcome, command.runId, fail]);

  const respond = async (approved: boolean) => {
    if (!command.approval || !command.runId) return;
    const approval = command.approval;
    setBusy(true);
    clearApproval(approval.toolId);
    try {
      await submitToolResult(config, {
        run_id: command.runId,
        tool_id: approval.toolId,
        result: "",
        approved,
      });
    } catch (error) {
      if (command.clientId) fail(command.clientId, error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const handleConnection = async (mode: "connect" | "verify" | "decline") => {
    if (!command.connection) return;
    const connection = command.connection as PendingConnection;
    setBusy(true);
    try {
      if (mode === "decline") await declineConnection(connection);
      else if (mode === "verify") await verifyAndResume(connection);
      else await connectAndResume(connection);
      clearConnection(connection.toolId);
    } catch (error) {
      if (command.clientId) fail(command.clientId, error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    if (!command.runId) return;
    setBusy(true);
    try {
      await cancelRun(config, command.runId, command.sessionId);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AnimatePresence>
      {command.open && (
        <motion.aside
          key="command-peek"
          role="dialog"
          aria-label="Command agent"
          initial={{ opacity: 0, x: 20, scale: 0.985 }}
          animate={{ opacity: 1, x: 0, scale: 1 }}
          exit={{ opacity: 0, x: 12, scale: 0.99 }}
          transition={{ duration: 0.16 }}
          className="surface-panel surface-radius-md fixed right-3 top-3 z-[var(--z-modal)] flex max-h-[calc(100vh-24px)] w-[min(380px,calc(100vw-24px))] flex-col overflow-hidden shadow-xl"
        >
          <header className="flex items-start gap-3 border-b border-line-soft px-4 py-3.5">
            <div className="min-w-0 flex-1">
              <div className="text-2xs font-medium uppercase tracking-[0.1em] text-faint">Command agent</div>
              <div className="mt-1 truncate text-sm font-medium text-ink">{command.query}</div>
            </div>
            <IconButton aria-label="Close command agent" onClick={close}>
              <X size={ICON.SM} />
            </IconButton>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3.5">
            {command.status === "starting" && <Status icon={Loader2} text="Starting…" spin />}
            {command.activities.map((activity) => (
              <div key={activity.id} className="flex items-start gap-2 py-1.5 text-sm">
                {activity.status === "running" ? (
                  <Loader2 className="mt-0.5 animate-spin text-muted" size={ICON.SM} />
                ) : activity.status === "failed" ? (
                  <AlertCircle className="mt-0.5 text-bad" size={ICON.SM} />
                ) : (
                  <Check className="mt-0.5 text-good" size={ICON.SM} />
                )}
                <div className="min-w-0">
                  <div className="text-ink-soft">{activity.name}</div>
                  {activity.preview && <div className="mt-0.5 line-clamp-2 text-xs text-faint">{activity.preview}</div>}
                </div>
              </div>
            ))}

            {command.approval && (
              <section className="mt-3 rounded-xl border border-line bg-surface-soft p-3">
                <div className="text-sm font-medium text-ink">Approve {command.approval.name}</div>
                {(command.approval.preview || command.approval.diff) && (
                  <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-xs text-muted">
                    {command.approval.preview || command.approval.diff}
                  </pre>
                )}
                <div className="mt-3 flex gap-2">
                  <Button size="sm" disabled={busy} onClick={() => void respond(true)}>Approve</Button>
                  <Button size="sm" variant="secondary" disabled={busy} onClick={() => void respond(false)}>Deny</Button>
                </div>
              </section>
            )}

            {command.connection && (
              <section className="mt-3 rounded-xl border border-line bg-surface-soft p-3">
                <div className="text-sm font-medium text-ink">{command.connection.label} needs attention</div>
                <p className="mt-1 text-xs text-muted">{command.connection.detail}</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button size="sm" disabled={busy} onClick={() => void handleConnection(
                    command.connection?.action === "credentials" || command.connection?.action === "settings"
                      ? "verify"
                      : "connect",
                  )}>
                    {command.connection.action === "credentials" || command.connection.action === "settings"
                      ? "Check connection"
                      : "Connect"}
                  </Button>
                  <Button size="sm" variant="quiet" disabled={busy} onClick={() => void handleConnection("decline")}>Not now</Button>
                </div>
              </section>
            )}

            {command.outcome && (
              <section className="mt-3">
                <p className={command.outcome.status === "failed" ? "text-sm text-bad" : "text-sm text-ink"}>
                  {command.outcome.summary}
                </p>
                {command.outcome.status === "needs_input" && (
                  <div className="mt-2 grid gap-2">
                    {command.outcome.prompt && <p className="text-xs text-muted">{command.outcome.prompt}</p>}
                    {command.outcome.choices?.map((choice) => (
                      <Button key={choice.query} size="sm" variant="secondary" onClick={() => void runCommandSidecar(choice.query)}>
                        {choice.label}
                      </Button>
                    ))}
                  </div>
                )}
              </section>
            )}
            {command.error && <p role="alert" className="mt-3 text-sm text-bad">{command.error}</p>}
          </div>

          <footer className="flex items-center justify-between border-t border-line-soft px-4 py-3">
            <span className="text-xs capitalize text-faint">{command.status}</span>
            <div className="flex gap-2">
              {command.status === "starting" || command.status === "running" ? (
                <Button size="sm" variant="secondary" disabled={busy || !command.runId} leadingIcon={Square} onClick={() => void stop()}>
                  Stop
                </Button>
              ) : null}
              <Button size="sm" variant="quiet" onClick={close}>Close</Button>
            </div>
          </footer>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

function Status({ icon: Icon, text, spin = false }: { icon: typeof Loader2; text: string; spin?: boolean }) {
  return (
    <div className="flex items-center gap-2 text-sm text-muted">
      <Icon size={ICON.SM} className={spin ? "animate-spin" : undefined} />
      {text}
    </div>
  );
}
