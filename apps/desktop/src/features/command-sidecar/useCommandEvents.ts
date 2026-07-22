import { useEffect } from "react";
import { createSseFrameParser } from "@/../electron/sse-frame-parser.js";
import type { ServerEvent } from "@/api/events";
import { headersForConfig } from "@/api/core";
import { useStore } from "@/stores";

function streamUrl(serverUrl: string, sessionId: string, afterSeq: number): string {
  const params = new URLSearchParams({ stream: "true", after_seq: String(afterSeq) });
  return `${serverUrl}/chat/events/${encodeURIComponent(sessionId)}?${params}`;
}

export function useCommandEvents(): void {
  const config = useStore((state) => state.config);
  const open = useStore((state) => state.commandSidecar.open);
  const sessionId = useStore((state) => state.commandSidecar.sessionId);
  const status = useStore((state) => state.commandSidecar.status);
  const applyEvent = useStore((state) => state.applyCommandEvent);

  useEffect(() => {
    if (!open || !sessionId || !["starting", "running"].includes(status)) return;
    const controller = new AbortController();
    let afterSeq = 0;
    const ingest = (event: ServerEvent) => {
      if (typeof event.seq === "number" && event.seq <= afterSeq) return;
      if (typeof event.seq === "number") afterSeq = event.seq;
      applyEvent(event);
    };
    const desktopEvents = window.ntrpDesktop?.events;

    if (desktopEvents) {
      let connectionId: string | null = null;
      const dispose = desktopEvents.onData((payload) => {
        if (!connectionId || payload.connectionId !== connectionId) return;
        if (payload.event) ingest(payload.event as ServerEvent);
      });
      void desktopEvents.connect(config, sessionId, afterSeq).then((id) => {
        connectionId = id;
        if (controller.signal.aborted) void desktopEvents.disconnect(id);
      }).catch((error) => {
        const state = useStore.getState().commandSidecar;
        if (state.clientId) useStore.getState().failCommandSidecar(state.clientId, String(error));
      });
      return () => {
        controller.abort();
        dispose();
        if (connectionId) void desktopEvents.disconnect(connectionId);
      };
    }

    void (async () => {
      try {
        const response = await fetch(streamUrl(config.serverUrl, sessionId, afterSeq), {
          headers: headersForConfig(config),
          signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error(`Command stream failed: ${response.status}`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        const parser = createSseFrameParser();
        while (!controller.signal.aborted) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const event of parser.push(decoder.decode(value, { stream: true }))) {
            ingest(event as ServerEvent);
          }
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        const state = useStore.getState().commandSidecar;
        if (state.clientId) useStore.getState().failCommandSidecar(state.clientId, String(error));
      }
    })();

    return () => controller.abort();
  }, [applyEvent, config, open, sessionId, status]);
}
