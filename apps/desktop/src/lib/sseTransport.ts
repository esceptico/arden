import { EventStreamContentType, fetchEventSource } from "@microsoft/fetch-event-source";

/** Exponential backoff with jitter (ms). Mirrors reconnectDelayMs in useEvents
 *  so both transports retry on the same schedule. */
export function sseReconnectDelayMs(
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

export type SseOpenDecision = "ok" | "fatal" | "retry";

/** Classify the response to an SSE connection attempt. A 4xx (except 429) is a
 *  client error — stale token, deleted session — and is FATAL: retrying would
 *  hammer the server forever. Everything else (5xx, 429, a non-event-stream
 *  body) is a transient drop → reconnect with backoff. */
export function classifySseOpen(status: number, ok: boolean, contentType: string): SseOpenDecision {
  if (ok && contentType.includes(EventStreamContentType)) return "ok";
  if (status >= 400 && status < 500 && status !== 429) return "fatal";
  return "retry";
}

class FatalSseError extends Error {}

export function parseSseFrame<T>(data: string, parse: (data: string) => T): T {
  try {
    return parse(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new FatalSseError(`invalid stream event: ${message}`);
  }
}

export interface SseStreamOptions<T> {
  url: string;
  signal: AbortSignal;
  headers?: Record<string, string>;
  /** Keep the connection open when the document is hidden (background window).
   *  Default false → fetch-event-source closes + reopens on visibility change. */
  openWhenHidden?: boolean;
  /** Parse one non-blank frame's `data` payload into a typed event.
   *  Invalid payloads are fatal because skipping one corrupts projections. */
  parse: (data: string) => T;
  onEvent: (event: T) => void;
  onConnect?: () => void;
  /** Called on every drop with a reason. `fatal` streams stop; non-fatal ones
   *  reconnect automatically with backoff. */
  onError?: (message: string, fatal: boolean) => void;
  baseRetryMs?: number;
  maxRetryMs?: number;
}

/** Open a resumable SSE stream on top of @microsoft/fetch-event-source — the
 *  battle-tested client used across LLM chat frontends. It owns spec-compliant
 *  frame parsing, POST/header/auth support, auto-reconnect, and native
 *  Last-Event-ID resume from the server's `id:` lines, so reconnect/backoff/
 *  cursor logic lives here once instead of being hand-rolled per channel.
 *  Resolves when the stream is aborted (via `signal`) or hits a fatal error. */
export function openSseStream<T>(opts: SseStreamOptions<T>): Promise<void> {
  let attempt = 0;
  return fetchEventSource(opts.url, {
    signal: opts.signal,
    openWhenHidden: opts.openWhenHidden ?? false,
    headers: { Accept: EventStreamContentType, ...(opts.headers ?? {}) },
    async onopen(response) {
      const decision = classifySseOpen(
        response.status,
        response.ok,
        response.headers.get("content-type") ?? "",
      );
      if (decision === "ok") {
        attempt = 0;
        opts.onConnect?.();
        return;
      }
      if (decision === "fatal") throw new FatalSseError(`stream failed: ${response.status}`);
      throw new Error(`stream failed: ${response.status}`);
    },
    onmessage(msg) {
      if (!msg.data) return; // keepalive comment / blank frame
      opts.onEvent(parseSseFrame(msg.data, opts.parse));
    },
    onclose() {
      // The server closed the stream unexpectedly; treat as a retriable drop.
      throw new Error("stream closed");
    },
    onerror(err) {
      if (err instanceof FatalSseError) {
        opts.onError?.(err.message, true);
        throw err; // stop retrying
      }
      const message = err instanceof Error ? err.message : String(err);
      opts.onError?.(message, false);
      return sseReconnectDelayMs(attempt++, { baseMs: opts.baseRetryMs, maxMs: opts.maxRetryMs });
    },
  });
}
