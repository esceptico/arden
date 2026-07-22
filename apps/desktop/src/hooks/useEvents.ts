import { useEffect, useState } from "react";
import type { AppConfig } from "@/api/core";
import type { ServerEvent } from "@/api/events";
import { reloadAllCollections } from "@/actions/bootstrap";
import { loadHistory } from "@/actions/history";
import { enqueueMessage } from "@/actions/messages";
import { createSseFrameParser } from "@/../electron/sse-frame-parser.js";
import { createAnimationFrameBatcher } from "@/lib/eventBatch";
import { createStallWatchdog } from "@/lib/streamWatchdog";
import { setState, useStore } from "@/stores";

// The server keepalives every 5s; treat ~3x silence as a stalled stream.
const STREAM_STALL_MS = 15_000;
const STREAM_STALL_CHECK_MS = 5_000;
import {
  eventStreamUrl,
  handleIncomingServerEvent,
  lastEventSeqForSession,
  markStreamConnected,
  markStreamConnecting,
  markStreamDisconnected,
  markStreamReconnecting,
  recordTransportEventForDiagnostics,
} from "@/stores/chat-stream";

export {
  clearReplayGapBlockForSession,
  eventStreamUrl,
  forgetEventSeqForSession,
  handleIncomingServerEvent,
  handleReplayServerEvent,
  handleServerEvent,
  lastEventSeqForSession,
  reloadHistoryAfterReplayGap,
  resetEventSeqStateForTest,
  resetReplayGapReloadStateForTest,
  resetStreamStateForTest,
  setEventCursorForSession,
  transportDiagnosticsForSession,
} from "@/stores/chat-stream";

function headersFor(config: AppConfig): HeadersInit {
  return config.apiKey ? { Authorization: `Bearer ${config.apiKey}` } : {};
}

type DesktopEventsBridge = NonNullable<NonNullable<Window["ardenDesktop"]>["events"]>;

type DesktopEventPayload = {
  connectionId: string;
  event?: unknown;
  error?: string;
  closed?: boolean;
  reason?: string;
};

interface DesktopEventStreamLoopOptions {
  desktopEvents: DesktopEventsBridge;
  config: AppConfig;
  sessionId: string;
  signal: AbortSignal;
  retryDelayMs?: number;
  getAfterSeq?: () => number | undefined;
  onEvent: (event: ServerEvent) => void | Promise<void>;
  onError: (message: string) => void;
  onConnect?: () => void;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  if (ms <= 0 || signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const finish = () => {
      clearTimeout(timeout);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timeout = setTimeout(finish, ms);
    signal.addEventListener("abort", finish, { once: true });
  });
}

export function reconnectDelayMs(
  attempt: number,
  {
    baseMs = 500,
    maxMs = 15_000,
    jitterRatio = 0.2,
    random = Math.random,
  }: { baseMs?: number; maxMs?: number; jitterRatio?: number; random?: () => number } = {},
): number {
  const exponential = Math.min(maxMs, baseMs * 2 ** Math.max(0, attempt));
  const jitter = exponential * jitterRatio * (random() * 2 - 1);
  return Math.max(0, Math.round(exponential + jitter));
}

export function eventStreamReadyForSession({
  sessionId,
  historyLoadedFor,
}: {
  sessionId: string | null;
  historyLoadedFor: string | null;
}): boolean {
  return sessionId !== null && historyLoadedFor === sessionId;
}

export async function runDesktopEventStreamLoop({
  desktopEvents,
  config,
  sessionId,
  signal,
  retryDelayMs = 500,
  getAfterSeq,
  onEvent,
  onError,
  onConnect,
}: DesktopEventStreamLoopOptions): Promise<void> {
  let connectionId: string | null = null;
  let resolveTerminal: ((reason: string) => void) | null = null;
  let reconnectAttempt = 0;

  const dispose = desktopEvents.onData((payload: DesktopEventPayload) => {
    if (!connectionId || payload.connectionId !== connectionId) return;
    if (payload.event) {
      reconnectAttempt = 0;
      const event = payload.event as ServerEvent;
      recordTransportEventForDiagnostics(event);
      void onEvent(event);
      return;
    }
    if (payload.error) {
      markStreamReconnecting(sessionId, payload.error, payload.error);
      onError(payload.error);
      resolveTerminal?.(payload.error);
      return;
    }
    if (payload.closed) {
      markStreamReconnecting(sessionId, payload.reason ?? "closed");
      resolveTerminal?.(payload.reason ?? "closed");
    }
  });

  try {
    while (!signal.aborted) {
      const afterSeq = getAfterSeq?.() ?? lastEventSeqForSession(sessionId);
      markStreamConnecting(sessionId, afterSeq);
      try {
        connectionId = await desktopEvents.connect(config, sessionId, afterSeq);
        if (signal.aborted) break;
        markStreamConnected(sessionId);
        onConnect?.();

        await new Promise<void>((resolve) => {
          const finish = () => {
            signal.removeEventListener("abort", finish);
            resolveTerminal = null;
            resolve();
          };
          resolveTerminal = finish;
          signal.addEventListener("abort", finish, { once: true });
        });
      } catch (error) {
        if (!signal.aborted) {
          const message = errorMessage(error);
          markStreamReconnecting(sessionId, message, message);
          onError(message);
        }
      } finally {
        const id = connectionId;
        connectionId = null;
        resolveTerminal = null;
        if (id) {
          try {
            await desktopEvents.disconnect(id);
          } catch {
            // Best effort cleanup; reconnect will create a fresh stream.
          }
        }
      }
      const delayMs = reconnectDelayMs(reconnectAttempt++, { baseMs: retryDelayMs });
      await sleep(delayMs, signal);
    }
  } finally {
    dispose();
    if (connectionId) {
      try {
        await desktopEvents.disconnect(connectionId);
      } catch {
        // Best effort cleanup on unmount/abort.
      }
    }
    markStreamDisconnected(sessionId, "aborted");
  }
}

export function useEvents(sessionId: string | null) {
  const config = useStore((s) => s.config);
  const historyLoadedFor = useStore((s) => s.sessionView.historyLoadedFor);
  // Bumped by the stall watchdog to force a full reconnect of whichever
  // transport is active; the cursor machinery makes the reconnect lossless.
  const [reconnectNonce, setReconnectNonce] = useState(0);
  const ready = eventStreamReadyForSession({
    sessionId,
    historyLoadedFor,
  });

  useEffect(() => {
    if (!sessionId || !ready) return;
    let disposed = false;
    const reloadHistory = (reloadSessionId: string) => loadHistory(reloadSessionId);
    // A half-open stream delivers neither events nor keepalives but never
    // errors; without this it stays frozen until a manual reload. bump() on
    // every received frame proves liveness; onStall forces a reconnect.
    const watchdog = createStallWatchdog({
      stallMs: STREAM_STALL_MS,
      checkMs: STREAM_STALL_CHECK_MS,
      onStall: () => {
        if (disposed) return;
        markStreamReconnecting(sessionId, "stalled");
        setReconnectNonce((n) => n + 1);
      },
    });
    // Coalesce the per-line SSE stream onto animation frames so a burst of
    // token/tool deltas produces one render per frame instead of one per
    // delta. Both transports feed this single batcher.
    const batcher = createAnimationFrameBatcher<ServerEvent>((events) => {
      for (const event of events) {
        void handleIncomingServerEvent(event, reloadHistory, {
          resendQueuedMessage: (text, images) => enqueueMessage(text, images ?? []),
        });
      }
    });
    const ingest = (event: ServerEvent) => {
      watchdog.bump();
      batcher.enqueue(event);
    };

    // Collections with no live delta feed (sessions list, automations, loops,
    // goal, config) are resynced once per successful (re)connect so a dropped
    // stream / server restart never leaves them stale until a manual reload.
    let connected = false;
    const onConnected = () => {
      connected = true;
      reloadAllCollections(sessionId);
    };
    const onDropped = () => {
      connected = false;
    };

    // Reconnect proactively on lifecycle signals instead of waiting out the
    // stall watchdog: regaining network or refocusing the window resumes the
    // cursor immediately. Guarded on `connected` so a healthy stream is never
    // torn down. Coalesced through the existing reconnectNonce.
    const requestReconnect = () => {
      if (!disposed && !connected) setReconnectNonce((n) => n + 1);
    };
    const onOnline = () => requestReconnect();
    const onOffline = () => {
      connected = false;
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") requestReconnect();
    };
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    document.addEventListener("visibilitychange", onVisibility);
    const removeLifecycleListeners = () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
      document.removeEventListener("visibilitychange", onVisibility);
    };

    const desktopEvents = window.ardenDesktop?.events;
    if (desktopEvents) {
      const controller = new AbortController();
      void runDesktopEventStreamLoop({
        desktopEvents,
        config,
        sessionId,
        signal: controller.signal,
        onEvent: (event) => ingest(event),
        onConnect: () => onConnected(),
        onError: (message) => {
          onDropped();
          if (!disposed) setState({ error: message });
        },
      });

      return () => {
        disposed = true;
        controller.abort();
        watchdog.dispose();
        batcher.dispose();
        removeLifecycleListeners();
      };
    }

    const controller = new AbortController();
    void (async () => {
      let reconnectAttempt = 0;
      while (!disposed && !controller.signal.aborted) {
        try {
          markStreamConnecting(sessionId, lastEventSeqForSession(sessionId));
          const response = await fetch(eventStreamUrl(config, sessionId), {
            headers: headersFor(config),
            signal: controller.signal,
          });
          if (!response.ok) {
            // 5xx/429 are transient → retry with backoff. Other 4xx (401 stale
            // token, 404 deleted session) are fatal → surface and stop looping
            // instead of hammering the server forever.
            if (response.status < 500 && response.status !== 429) {
              onDropped();
              markStreamDisconnected(sessionId, `http_${response.status}`);
              if (!disposed) setState({ error: `event stream failed: ${response.status}` });
              return;
            }
            throw new Error(`event stream failed: ${response.status}`);
          }
          if (!response.body) {
            throw new Error("event stream failed: no response body");
          }
          markStreamConnected(sessionId);
          onConnected();

          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          const parser = createSseFrameParser();

          while (!disposed && !controller.signal.aborted) {
            const { done, value } = await reader.read();
            if (done) {
              onDropped();
              markStreamReconnecting(sessionId, "eof");
              const delayMs = reconnectDelayMs(reconnectAttempt++);
              await sleep(delayMs, controller.signal);
              break;
            }
            for (const event of parser.push(decoder.decode(value, { stream: true }))) {
              reconnectAttempt = 0;
              ingest(event as ServerEvent);
            }
          }
        } catch (error) {
          if (controller.signal.aborted) return;
          onDropped();
          const message = errorMessage(error);
          markStreamReconnecting(sessionId, message, message);
          // Suppress the error toast while offline — the 'online' listener
          // will trigger a clean reconnect once the network is back.
          if (navigator.onLine) setState({ error: message });
          const delayMs = reconnectDelayMs(reconnectAttempt++);
          await sleep(delayMs, controller.signal);
        }
      }
    })();

    return () => {
      disposed = true;
      controller.abort();
      watchdog.dispose();
      batcher.dispose();
      removeLifecycleListeners();
      markStreamDisconnected(sessionId);
    };
  }, [sessionId, config, ready, reconnectNonce]);
}
