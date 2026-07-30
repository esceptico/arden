declare module "*.css";
declare module "@fontsource-variable/geist";
declare module "@fontsource-variable/geist-mono";

/** What the quick-capture window submits and the main window receives. */
interface QuickCapturePayload {
  message: string;
  /** Base64 image blocks, same shape the chat API accepts. */
  images?: { media_type: string; data: string }[];
  /** Existing chat to send into; null/undefined → new Inbox chat. */
  sessionId?: string | null;
}

interface Window {
  ardenDesktop?: {
    version: () => string;
    app: {
      /** Reload the renderer (Cmd-R equivalent). */
      reload: () => Promise<void>;
      /** Quit the Electron app entirely. */
      quit: () => Promise<void>;
    };
    window: {
      /** Current native fullscreen state for the containing Electron window. */
      isFullScreen: () => Promise<boolean>;
      /** Subscribe to native fullscreen changes. */
      onFullScreenChange: (callback: (isFullScreen: boolean) => void) => () => void;
    };
    config: {
      get: () => Promise<{ serverUrl: string; apiKey: string }>;
      set: (config: { serverUrl: string; apiKey: string }) => Promise<{ serverUrl: string; apiKey: string }>;
    };
    api: {
      request: (
        config: { serverUrl: string; apiKey: string },
        request: { path: string; method?: string; body?: string; timeout?: number },
      ) => Promise<{
        ok: boolean;
        status: number;
        statusText: string;
        contentType: string;
        data: unknown;
        text: string;
      }>;
    };
    events: {
      connect: (
        config: { serverUrl: string; apiKey: string },
        sessionId: string,
        afterSeq?: number,
      ) => Promise<string>;
      disconnect: (connectionId: string) => Promise<void>;
      onData: (
        callback: (payload: {
          connectionId: string;
          event?: unknown;
          error?: string;
          closed?: boolean;
          reason?: string;
        }) => void,
      ) => () => void;
    };
    shell: {
      /** Opens a file or directory in the OS default application. Resolves
       *  to "" on success, an error message otherwise. */
      openPath: (path: string) => Promise<string>;
    };
    clipboard: {
      /** Writes text to the system clipboard via Electron's main process. */
      writeText: (text: string) => Promise<boolean>;
    };
    quickCapture: {
      /** Submit from the quick-capture window. Main process forwards it
       *  to the main window which calls its existing switchSession/
       *  createSession + sendMessage. Resolves true on success. */
      submit: (payload: QuickCapturePayload) => Promise<boolean>;
      /** Hide the quick-capture window without submitting. */
      close: () => Promise<void>;
      /** Interactive macOS screen snip (panel hides during selection,
       *  re-presents after). Resolves null when the user cancels. */
      captureScreen: () => Promise<{ media_type: string; data: string } | null>;
      /** Grow/shrink the quick window (e.g. around the chat picker).
       *  Height is clamped in the main process; top edge stays fixed. */
      resize: (height: number) => Promise<void>;
      /** Register a new global shortcut for summoning the quick-capture
       *  window. Pass an empty string to disable. Resolves true on
       *  success, false if the OS refused (another app owns the chord). */
      setShortcut: (accelerator: string) => Promise<boolean>;
      /** Subscribe to forwarded quick-capture submissions on the MAIN
       *  window. Returns an unsubscribe function. */
      onMessage: (callback: (payload: QuickCapturePayload) => void) => () => void;
      /** Fires in the QUICK window each time the global shortcut summons
       *  it — the signal to re-present the card and focus the input.
       *  Returns an unsubscribe function. */
      onSummon: (callback: () => void) => () => void;
      /** Fires in the QUICK window when main catches Esc (AppKit consumes
       *  the key at the NSPanel layer, so main claims it as a global
       *  shortcut while the panel is visible) — the signal to run the
       *  cancel exit. Returns an unsubscribe function. */
      onDismiss: (callback: () => void) => () => void;
    };
  };
}
