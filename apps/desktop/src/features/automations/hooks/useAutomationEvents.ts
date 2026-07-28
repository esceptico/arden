import { useEffect, useState } from "react";
import { useStore } from "@/stores";
import { refreshLoops } from "@/actions/loops";
import { fetchAutomations } from "@/actions/automations";
import { fetchAreasOverview, fetchAreaDetail } from "@/actions/areas";
import { applyAppDestination } from "@/actions/navigation";
import { refreshAreas } from "@/actions/sessions";
import type { AppConfig } from "@/api/core";
import type { AppDestination } from "@/api/navigation";
import type { SessionListItem } from "@/api/types";
import { createStallWatchdog } from "@/lib/streamWatchdog";
import { openSseStream } from "@/lib/sseTransport";
import { automationToast } from "@/lib/taskToast";
import type { MemoryVaultChange } from "@/stores/types";

// The automation stream keepalives every 5s; treat ~3x silence as stalled.
const AUTOMATION_STALL_MS = 15_000;
const AUTOMATION_STALL_CHECK_MS = 5_000;

/** SSE events emitted on `/automations/events`. The backend multiplexes
 *  all automations onto this single stream (one channel for the whole
 *  system, not per-task). `status` is a short human-readable string the
 *  backend assembles from the latest tool call, e.g. "bash_exec..." or
 *  "read_file: tasks.md". `automation_finished` carries the final result
 *  string and signals that the row should drop out of the running list.
 *  `session_created` announces a channel session an automation just
 *  provisioned, so the sidebar adds the row live instead of after Cmd+R. */
type AutomationEvent =
  | { type: "automation_progress"; task_id: string; status: string; seq?: number }
  | { type: "automation_finished"; task_id: string; result: string | null; seq?: number }
  | { type: "session_created"; session: SessionListItem; seq?: number }
  | { type: "session_activity"; session: SessionListItem; seq?: number }
  | { type: "memory_changed"; paths: string[]; revision?: string | null; review_required?: boolean; seq?: number }
  | { type: "areas_changed"; keys: string[]; seq?: number }
  | { type: "navigation_requested"; origin_session_id: string; destination: AppDestination; label: string; seq?: number }
  | { type: "stream_keepalive"; latest_seq: number; seq?: number }
  | { type: "stream_reset"; reason: string; seq?: number };

function headersFor(config: AppConfig): Record<string, string> {
  return config.apiKey ? { Authorization: `Bearer ${config.apiKey}` } : {};
}

export function automationEventsUrl(serverUrl: string, afterSeq: number | undefined): string {
  const url = new URL(`${serverUrl}/automations/events`);
  if (afterSeq !== undefined) {
    url.searchParams.set("after_seq", String(afterSeq));
  }
  return url.toString();
}

export function memoryVaultChangeFromEvent(
  event: Extract<AutomationEvent, { type: "memory_changed" }>,
): MemoryVaultChange {
  return {
    paths: event.paths,
    revision: event.revision ?? null,
    reviewRequired: event.review_required ?? false,
    seq: event.seq ?? null,
  };
}

/** An agent asked the app to take the user somewhere. Only the session the
 *  user is actually looking at may move the app under them; anything else
 *  offers the move as a toast and waits for a click. */
export function applyNavigationRequest(
  event: Extract<AutomationEvent, { type: "navigation_requested" }>,
): void {
  const store = useStore.getState();
  const active = event.origin_session_id === store.currentSessionId;
  const result = active ? applyAppDestination(event.destination) : null;
  if (result?.ok) return;
  store.pushToast({
    id: `nav:${event.origin_session_id}:${event.seq ?? Date.now()}`,
    title: event.label,
    detail: result ? result.error : undefined,
    status: result ? "failed" : "info",
    target: { kind: "destination", destination: event.destination },
  });
}

/** Subscribe to `/automations/events` for the lifetime of the app. The
 *  hook owns transport only; automation stream status and per-task
 *  projections live in the automation domain. */
export function useAutomationEvents(): void {
  const config = useStore((s) => s.config);
  const [reconnectNonce, setReconnectNonce] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    const store = () => useStore.getState();

    // A half-open automation stream silently drops session_created / progress
    // events until reload; force a reconnect on prolonged silence. Keepalives
    // bump the watchdog, so real silence is a genuine stall.
    const watchdog = createStallWatchdog({
      stallMs: AUTOMATION_STALL_MS,
      checkMs: AUTOMATION_STALL_CHECK_MS,
      onStall: () => {
        if (!disposed) setReconnectNonce((n) => n + 1);
      },
    });

    const handle = (event: AutomationEvent) => {
      watchdog.bump();
      if (event.type === "automation_progress") {
        store().automationProgress(event.task_id, event.status);
        // "starting..." is the scheduler's fire signal — the server just bumped
        // next_run_at and claimed the running flag, so pull loops and the
        // automation list to reset the countdown chip and show the run
        // immediately instead of waiting out the next poll.
        if (event.status === "starting...") {
          void fetchAutomations();
          const sid = useStore.getState().currentSessionId;
          if (sid) void refreshLoops(sid);
        }
      } else if (event.type === "automation_finished") {
        store().automationFinished(event.task_id);
        const st = store();
        const auto = st.automations?.find((a) => a.task_id === event.task_id) ?? null;
        const toast = automationToast({
          taskId: event.task_id,
          name: auto?.name ?? null,
          result: event.result,
          automationsOpen: st.automationsOpen,
        });
        if (toast) st.pushToast(toast);
        // Drop the row from the sidebar card immediately rather than after the
        // next 20s poll catches up to running_since going null.
        void fetchAutomations();
        // A finished automation run may have written area asks/state —
        // refresh Home's focus set the same way areas_changed does.
        void fetchAreasOverview();
      } else if (event.type === "session_created") {
        // An automation just provisioned its channel session. Prepend it so the
        // sidebar row shows up live; the store dedupes an already-loaded id.
        store().prependSession(event.session);
      } else if (event.type === "memory_changed") {
        // The live memory vault absorbed on-disk edits (Obsidian, a feed run,
        // a maintenance pass) — bump the version so an open memory view
        // silently refetches what it's showing.
        store().memoryVaultChanged(memoryVaultChangeFromEvent(event));
      } else if (event.type === "session_activity") {
        // A channel the user may not be viewing got new content — bump its
        // sidebar row to the top with fresh metadata.
        store().patchSession(event.session);
      } else if (event.type === "areas_changed") {
        // An area's asks/state moved (server-side dream/consolidation or an
        // automation write) — refetch so the Home focus set and strip
        // reflect it without waiting for the next mount.
        void fetchAreasOverview();
        void refreshAreas();
        // If the changed area's room is currently open, refetch its detail
        // too — otherwise the open room goes stale until the user re-opens it.
        const openKey = useStore.getState().areas.openAreaKey;
        if (openKey && event.keys.includes(openKey)) {
          void fetchAreaDetail(openKey);
        }
      } else if (event.type === "navigation_requested") {
        applyNavigationRequest(event);
      } else if (event.type === "stream_reset") {
        void fetchAutomations();
        const sid = useStore.getState().currentSessionId;
        if (sid) void refreshLoops(sid);
      }
    };

    store().automationStreamConnecting();
    void openSseStream<AutomationEvent>({
      // Resume rides Last-Event-ID: the server stamps `id: {seq}` on every
      // record + keepalive and honors the header, so fetch-event-source
      // re-sends the last id on reconnect — no after_seq needed in the URL.
      url: automationEventsUrl(config.serverUrl, undefined),
      signal: controller.signal,
      headers: headersFor(config),
      // Keep receiving automation/notification updates while the window is
      // backgrounded (matches the prior always-open loop).
      openWhenHidden: true,
      parse: (data) => {
        try {
          return JSON.parse(data) as AutomationEvent;
        } catch {
          return null;
        }
      },
      onConnect: () => {
        if (!disposed) store().automationStreamConnected();
      },
      onError: (message, fatal) => {
        if (disposed) return;
        if (fatal) store().automationStreamFailed(message);
        else store().automationStreamStale();
      },
      onEvent: handle,
    }).catch(() => {
      /* aborted or fatal — already surfaced via onError */
    });

    return () => {
      disposed = true;
      controller.abort();
      watchdog.dispose();
      store().automationStreamIdle();
    };
  }, [config, reconnectNonce]);
}
