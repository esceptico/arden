const { app, BrowserWindow, Menu, Tray, clipboard, globalShortcut, ipcMain, nativeImage, nativeTheme, net, safeStorage, screen, session, shell } = require("electron");
const crypto = require("node:crypto");
const { execFile } = require("node:child_process");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { fileURLToPath } = require("node:url");
const { parseApiResponseBody } = require("./api-response.cjs");
const { createExecutorClient } = require("./executor.cjs");
const { createMainProcessFetch } = require("./main-process-fetch.cjs");
// The parser is an ESM module (shared with the Vite renderer, which can't
// import CommonJS source). Kick off the import at load; await the cached
// promise where it's used. CJS can't `require` ESM or top-level await.
const sseFrameParserModule = import("./sse-frame-parser.js");
const mainProcessFetch = createMainProcessFetch(net);

const devServerUrl = process.env.ARDEN_DESKTOP_DEV_SERVER_URL;
const isDev = Boolean(devServerUrl);
const configFileName = "config.json";

// App icon. macOS dev: dock picks up the PNG via app.dock.setIcon below
// (Electron's dock.setIcon is more reliable with a high-res PNG than an
// .icns). Windows/Linux dev: BrowserWindow `icon` is enough. In a
// packaged build the bundle's own icon (set by electron-builder) takes
// over.
const ICON_DIR = path.join(__dirname, "icons");
const ICON_PATH = process.platform === "win32"
  ? path.join(ICON_DIR, "icon.ico")
  : path.join(ICON_DIR, "icon.png");

const defaultConfig = {
  serverUrl: "http://localhost:6877",
  apiKey: "",
};
const eventStreams = new Map();

function configPath() {
  return path.join(app.getPath("userData"), configFileName);
}

function rendererIndexPath() {
  return path.join(__dirname, "../dist/renderer/index.html");
}

function originOf(value) {
  try {
    return new URL(value).origin;
  } catch {
    return null;
  }
}

function isTrustedSender(event) {
  const frameUrl = event.senderFrame?.url ?? "";
  return isTrustedRendererUrl(frameUrl);
}

function isTrustedRendererUrl(frameUrl) {
  if (isDev) return originOf(frameUrl) === originOf(devServerUrl);
  if (!frameUrl.startsWith("file://")) return false;
  try {
    return path.normalize(fileURLToPath(frameUrl)) === path.normalize(rendererIndexPath());
  } catch {
    return false;
  }
}

// The render_html shell document (see src/public/widget-frame.html) — the one
// subframe navigation to a real URL the app performs itself.
function isWidgetFrameUrl(url) {
  if (isDev) {
    const devUrl = devServerUrl;
    return originOf(url) === originOf(devUrl) && new URL(url).pathname === "/widget-frame.html";
  }
  if (!url.startsWith("file://")) return false;
  try {
    const expected = path.join(path.dirname(rendererIndexPath()), "widget-frame.html");
    return path.normalize(fileURLToPath(url)) === path.normalize(expected);
  } catch {
    return false;
  }
}

function assertTrustedSender(event) {
  if (!isTrustedSender(event)) throw new Error("Untrusted IPC sender");
}

function normalizeConfig(input) {
  const serverUrl = typeof input?.serverUrl === "string" ? input.serverUrl.trim().replace(/\/$/, "") : "";
  return {
    serverUrl: serverUrl || defaultConfig.serverUrl,
    apiKey: typeof input?.apiKey === "string" ? input.apiKey.trim() : "",
  };
}

// Sync safeStorage ONLY. Electron 42.4.0's async variants are a minefield
// on macOS: decryptStringAsync resolves { shouldReEncrypt, result } instead
// of the string (which silently emptied the apiKey after the 39→42 upgrade
// — the "asks for the API key every launch" bug), its key store is
// incompatible with the sync path's, and encryptStringAsync sporadically
// SIGSEGVs the main process (V8 HandleScope fatal). The sync calls block
// for one tiny string at boot — irrelevant. Revisit if the sync API is
// actually deprecated.

function encryptSecret(value) {
  if (!value) return { encoding: "empty", value: "" };
  if (safeStorage.isEncryptionAvailable()) {
    const encrypted = safeStorage.encryptString(value);
    return { encoding: "safeStorage", value: encrypted.toString("base64") };
  }
  return { encoding: "plain", value };
}

function decryptSecret(secret) {
  if (!secret || secret.encoding === "empty") return "";
  if (secret.encoding === "plain") return typeof secret.value === "string" ? secret.value : "";
  if (secret.encoding !== "safeStorage" || typeof secret.value !== "string") return "";
  const encrypted = Buffer.from(secret.value, "base64");
  if (safeStorage.isEncryptionAvailable()) return safeStorage.decryptString(encrypted);
  return "";
}

async function readConfig() {
  try {
    const raw = JSON.parse(await fs.readFile(configPath(), "utf8"));
    return normalizeConfig({
      serverUrl: raw.serverUrl,
      apiKey: decryptSecret(raw.apiKey),
    });
  } catch {
    return defaultConfig;
  }
}

async function writeConfig(config) {
  const normalized = normalizeConfig(config);
  await fs.mkdir(path.dirname(configPath()), { recursive: true });
  await fs.writeFile(
    configPath(),
    JSON.stringify(
      {
        serverUrl: normalized.serverUrl,
        apiKey: encryptSecret(normalized.apiKey),
      },
      null,
      2,
    ),
    "utf8",
  );
  return normalized;
}

function normalizeApiRequest(input) {
  const method = typeof input?.method === "string" ? input.method.toUpperCase() : "GET";
  const allowedMethods = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);
  if (!allowedMethods.has(method)) throw new Error(`Unsupported API method: ${method}`);

  const requestPath = typeof input?.path === "string" ? input.path : "";
  if (!requestPath.startsWith("/") || requestPath.startsWith("//")) {
    throw new Error("API path must be relative to the configured server");
  }

  return {
    method,
    path: requestPath,
    body: typeof input?.body === "string" ? input.body : undefined,
    timeout: Number.isFinite(input?.timeout) ? Number(input.timeout) : 30_000,
  };
}

function apiHeaders(config, body) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (config.apiKey) headers.Authorization = `Bearer ${config.apiKey}`;
  return headers;
}

function apiNetworkErrorResponse(error, request) {
  const cause = error && typeof error === "object" ? error.cause : null;
  const code = cause && typeof cause === "object" && "code" in cause ? cause.code : null;
  const suffix = code ? ` (${code})` : "";
  const aborted = error?.name === "AbortError" || error?.name === "TimeoutError";
  return {
    ok: false,
    status: 0,
    statusText: aborted ? "Request Timeout" : "Network Error",
    contentType: "text/plain",
    data: null,
    text: aborted
      ? `Request timed out for ${request.method} ${request.path}`
      : `Network error for ${request.method} ${request.path}${suffix}`,
  };
}

async function apiRequest(configInput, requestInput, signal) {
  const config = normalizeConfig(configInput);
  const request = normalizeApiRequest(requestInput);
  const controller = new AbortController();
  const timeoutId = request.timeout > 0 ? setTimeout(() => controller.abort(), request.timeout) : null;
  const signals = [controller.signal];
  if (signal) signals.push(signal);

  try {
    const response = await mainProcessFetch(new URL(request.path, config.serverUrl), {
      method: request.method,
      headers: apiHeaders(config, request.body),
      body: request.body,
      signal: AbortSignal.any(signals),
    });
    const contentType = response.headers.get("content-type") ?? "";
    const text = await response.text();
    const { data } = parseApiResponseBody(contentType, text);
    return {
      ok: response.ok,
      status: response.status,
      statusText: response.statusText,
      contentType,
      data,
      text,
    };
  } catch (error) {
    return apiNetworkErrorResponse(error, request);
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
  }
}

function eventStreamUrl(config, sessionId, afterSeq) {
  const params = new URLSearchParams({ stream: "true" });
  if (typeof afterSeq === "number" && Number.isFinite(afterSeq)) {
    params.set("after_seq", String(afterSeq));
  }
  return new URL(`/chat/events/${encodeURIComponent(sessionId)}?${params.toString()}`, config.serverUrl);
}

async function streamEvents(connectionId, webContents, configInput, sessionId, afterSeq, signal) {
  const config = normalizeConfig(configInput);
  let terminalSent = false;
  try {
    const response = await mainProcessFetch(eventStreamUrl(config, sessionId, afterSeq), {
      headers: apiHeaders(config),
      signal,
    });
    if (!response.ok || !response.body) {
      throw new Error(`event stream failed: ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const { createSseFrameParser } = await sseFrameParserModule;
    const parser = createSseFrameParser();

    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const event of parser.push(decoder.decode(value, { stream: true }))) {
        if (!webContents.isDestroyed()) {
          webContents.send("events:data", { connectionId, event });
        }
      }
    }
  } catch (error) {
    if (!signal.aborted && !webContents.isDestroyed()) {
      terminalSent = true;
      webContents.send("events:data", {
        connectionId,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  } finally {
    if (!terminalSent && !signal.aborted && !webContents.isDestroyed()) {
      webContents.send("events:data", {
        connectionId,
        closed: true,
        reason: "eof",
      });
    }
    eventStreams.delete(connectionId);
  }
}

// --- Client executor: outbound connection to the server's executor
// protocol, living in main so it survives window close. The tray mirrors
// its status; tool handlers are registered as they are implemented.
const executorStateFileName = "executor.json";
let executorClient = null;
let tray = null;
let cachedConfig = { ...defaultConfig };

function executorStatePath() {
  return path.join(app.getPath("userData"), executorStateFileName);
}

const executorStateStore = {
  deviceName: os.hostname(),
  async load() {
    try {
      const raw = JSON.parse(await fs.readFile(executorStatePath(), "utf8"));
      const token = decryptSecret(raw.token);
      if (!raw.executorId || !token) return null;
      return { executorId: raw.executorId, token, cursor: raw.cursor ?? 0 };
    } catch {
      return null;
    }
  },
  async save(state) {
    if (!state) {
      await fs.rm(executorStatePath(), { force: true });
      return;
    }
    await fs.mkdir(path.dirname(executorStatePath()), { recursive: true });
    await fs.writeFile(
      executorStatePath(),
      JSON.stringify({ executorId: state.executorId, token: encryptSecret(state.token), cursor: state.cursor }, null, 2),
      "utf8",
    );
  },
};

function startExecutor() {
  executorClient?.dispose();
  executorClient = createExecutorClient({
    getConfig: () => cachedConfig,
    stateStore: executorStateStore,
    fetchImpl: mainProcessFetch,
    // eslint-disable-next-line no-console
    log: message => console.log(`[arden executor] ${message}`),
  });
  executorClient.onStatusChange(() => updateTrayMenu());
  executorClient.start();
}

function trayStatusLabel() {
  const status = executorClient?.getStatus();
  if (!status) return "Executor: off";
  if (status.connected) return `Executor: connected (${status.executorId ?? "?"})`;
  if (status.state === "connecting") return "Executor: connecting…";
  return `Executor: offline${status.lastError ? ` — ${status.lastError}` : ""}`;
}

function updateTrayMenu() {
  if (!tray) return;
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: "Open Arden",
        click: () => {
          if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.show();
            mainWindow.focus();
          } else {
            createWindow({ show: true });
          }
        },
      },
      { type: "separator" },
      { label: trayStatusLabel(), enabled: false },
      { type: "separator" },
      { label: "Quit Arden", click: () => app.quit() },
    ]),
  );
}

function createTray() {
  const image = nativeImage.createFromPath(path.join(ICON_DIR, "icon.png")).resize({ height: 18 });
  image.setTemplateImage(false);
  tray = new Tray(image);
  tray.setToolTip("Arden");
  updateTrayMenu();
}

let mainWindow = null;
let quickWindow = null;
/** Currently registered accelerator string, or null if none. Tracked so
 *  we can unregister cleanly before re-registering a new chord, and so
 *  duplicate-set calls become no-ops. */
let registeredShortcut = null;

const DEFAULT_QUICK_SHORTCUT = "CommandOrControl+Shift+Space";

// The visible sheet is 608px (the system-overlay contract); the transparent
// window adds the renderer's 24px shadow gutters on both sides.
const QUICK_WIDTH = 656;
/** Base height: the resting Spotlight-class bar — 56px input row with
 * inline accessories, plus the renderer's 8px top and 36px bottom shadow
 * gutters. The renderer's ResizeObserver requests taller bounds as the
 * bar grows (draft lines, image chips, destination picker). */
const QUICK_BASE_HEIGHT = 102;
const QUICK_MAX_HEIGHT = 560;
const QUICK_VISIBLE_BOTTOM_GUTTER = 36;
/** The visible bar's bottom edge sits this fraction of the work area up
 * from the bottom (companion-bar placement, not Spotlight's top third). */
const QUICK_BOTTOM_FRACTION = 0.15;

/** Register the quick-capture global shortcut, replacing whatever was
 *  previously bound. Pass an empty string / nullish value to clear.
 *  Returns true on success (or successful clear), false if the OS
 *  refused the registration (another app owns the chord). */
function setQuickShortcut(accelerator) {
  const target = accelerator || "";
  // Idempotent: if the renderer pushes the same chord we already hold,
  // skip the unregister/register dance entirely. Avoids a momentary
  // window where the chord isn't bound at all.
  if (registeredShortcut === (target || null)) return true;
  if (registeredShortcut) {
    globalShortcut.unregister(registeredShortcut);
    registeredShortcut = null;
  }
  if (!target) {
    // eslint-disable-next-line no-console
    console.log("[arden] quick-capture shortcut: disabled");
    return true;
  }
  try {
    const ok = globalShortcut.register(target, showQuickWindow);
    if (ok) {
      registeredShortcut = target;
      // eslint-disable-next-line no-console
      console.log(`[arden] quick-capture shortcut: bound ${target}`);
      return true;
    }
  } catch (error) {
    // eslint-disable-next-line no-console
    console.warn(`[arden] quick-capture shortcut: bind threw for ${target}:`, error);
    return false;
  }
  // eslint-disable-next-line no-console
  console.warn(`[arden] quick-capture shortcut: OS refused ${target} (another app already owns it?)`);
  return false;
}

function createWindow({ show }) {
  mainWindow = new BrowserWindow({
    show,
    width: 1320,
    height: 880,
    minWidth: 980,
    minHeight: 660,
    title: "Arden",
    icon: ICON_PATH,
    backgroundColor: nativeTheme.shouldUseDarkColors ? "#100f0f" : "#ece9e0",
    titleBarStyle: "hiddenInset",
    trafficLightPosition: { x: 18, y: 18 },
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      // arden streams agent activity the user watches from OTHER windows. With
      // the default (true), Chromium throttles/pauses rAF + timers when this
      // window is backgrounded or occluded, freezing the SSE-driven UI until
      // refocus. Keep the renderer live in the background — the whole point of
      // a personal agent dashboard is updates you don't have to babysit.
      backgroundThrottling: false,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://") || url.startsWith("http://")) {
      void shell.openExternal(url);
    }
    return { action: "deny" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!isTrustedRendererUrl(url)) event.preventDefault();
  });

  // Sandboxed srcdoc widgets (render_html) can always navigate THEMSELVES —
  // neither sandbox flags nor CSP block self-navigation, so widget HTML could
  // exfiltrate data via location.href and load a remote page into the
  // renderer. Pin every subframe to its srcdoc/about:blank document, with
  // one exception: the app's own widget-frame.html shell.
  mainWindow.webContents.on("will-frame-navigate", (event) => {
    if (!event.isMainFrame && !event.url.startsWith("about:") && !isWidgetFrameUrl(event.url)) {
      event.preventDefault();
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  mainWindow.on("enter-full-screen", () => {
    mainWindow?.webContents.send("window:fullscreen-changed", true);
  });
  mainWindow.on("leave-full-screen", () => {
    mainWindow?.webContents.send("window:fullscreen-changed", false);
  });

  if (isDev) {
    mainWindow.loadURL(devServerUrl);
  } else {
    mainWindow.loadFile(rendererIndexPath());
  }
}

/** Quick-capture window: a frameless, always-on-top floating composer
 *  summoned by the global shortcut. Loads the same renderer bundle but
 *  with the `#quick-capture` hash so main.tsx mounts QuickCapture
 *  instead of the full App. Hidden (not destroyed) on blur/submit so
 *  re-summoning is instant. */
function createQuickWindow() {
  if (quickWindow && !quickWindow.isDestroyed()) return quickWindow;

  quickWindow = new BrowserWindow({
    // macOS: a non-activating NSPanel. The composer takes keyboard focus
    // WITHOUT activating arden — the user's current app stays active, its
    // menu bar stays up, and dismissal hands focus straight back. This is
    // what lets dismissQuickWindow be a plain hide() instead of the old
    // app.hide() hack that vanished the main window along with the panel.
    ...(process.platform === "darwin" ? { type: "panel" } : {}),
    // Sized for the card PLUS its drop shadow: the renderer pads the card
    // (24px sides / 36px below) so the shadow can render instead of being
    // clipped at the window edge.
    width: QUICK_WIDTH,
    height: QUICK_BASE_HEIGHT,
    frame: false,
    transparent: true,
    resizable: false,
    movable: true,
    skipTaskbar: true,
    show: false,
    alwaysOnTop: true,
    fullscreenable: false,
    // No vibrancy here — combining it with `transparent: true` produced
    // a visible dark frame around the card on macOS (the vibrancy layer
    // showed through wherever the renderer was transparent). We want
    // the card itself to be the entire visible surface.
    hasShadow: false,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });

  // Floats across all spaces / above fullscreen apps on macOS. Without
  // this the window is invisible if the user is in a fullscreen Chrome
  // tab when they hit the shortcut. skipTransformProcessType is load-
  // bearing: without it, visibleOnFullScreen flips the app's process
  // type to UIElementApplication — which REMOVES the arden icon from the
  // Dock. The panel window type already floats over fullscreen, so the
  // transformation is pure downside.
  quickWindow.setVisibleOnAllWorkspaces(true, {
    visibleOnFullScreen: true,
    skipTransformProcessType: true,
  });

  quickWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://") || url.startsWith("http://")) {
      void shell.openExternal(url);
    }
    return { action: "deny" };
  });

  // Auto-hide on blur — Spotlight feel. User can re-summon with the
  // shortcut without paying the load cost since the window persists.
  // The renderer keeps the draft, so an accidental blur loses nothing.
  quickWindow.on("blur", () => {
    dismissQuickWindow();
  });

  quickWindow.on("closed", () => {
    quickWindow = null;
  });

  if (isDev) {
    const devUrl = devServerUrl;
    quickWindow.loadURL(`${devUrl}#quick-capture`);
  } else {
    quickWindow.loadFile(rendererIndexPath(), { hash: "quick-capture" });
  }

  return quickWindow;
}

let quickSummonPending = false;

function showQuickWindow() {
  const win = createQuickWindow();
  if (win.isVisible()) {
    dismissQuickWindow();
    return;
  }
  // Never present a window whose renderer hasn't loaded: keyboard-focus
  // routing and the quick:summon signal would land in a void, leaving an
  // empty deaf panel on screen. The window is created eagerly at startup,
  // so this only triggers if the shortcut is hit during app boot.
  if (win.webContents.isLoading()) {
    if (quickSummonPending) return;
    quickSummonPending = true;
    win.webContents.once("did-finish-load", () => {
      quickSummonPending = false;
      presentQuickWindow(win);
    });
    return;
  }
  presentQuickWindow(win);
}

function presentQuickWindow(win) {
  // Horizontally centered on the display that currently owns the cursor,
  // riding LOW like a companion bar: the visible card's bottom edge sits
  // ~15% of the work area up from the bottom. Reset to base size first:
  // a prior summon may have grown the window for the chat picker.
  const cursorPoint = screen.getCursorScreenPoint();
  const display = screen.getDisplayNearestPoint(cursorPoint);
  const { x: dx, y: dy, width: dw, height: dh } = display.workArea;
  const x = Math.round(dx + (dw - QUICK_WIDTH) / 2);
  // The renderer's transparent bottom shadow gutter is subtracted so the
  // visible card — not its window — lands on the anchor.
  const visibleBottom = dy + Math.round(dh * (1 - QUICK_BOTTOM_FRACTION));
  const y = Math.max(dy, visibleBottom - (QUICK_BASE_HEIGHT - QUICK_VISIBLE_BOTTOM_GUTTER));
  win.setBounds({ x, y, width: QUICK_WIDTH, height: QUICK_BASE_HEIGHT });
  win.show();
  win.focus();
  // Non-activating panels become the key window without activating the
  // app, but Chromium doesn't route keyboard focus into the page on its
  // own in that state — without this, keystrokes reach the window and
  // die before the <input>.
  win.webContents.focus();
  // Esc NEVER reaches Chromium in a non-activating panel (AppKit consumes
  // it at the NSPanel layer — verified: before-input-event sees every key
  // EXCEPT Escape). Claim it as a global shortcut for exactly the lifetime
  // of the panel being visible; dismissQuickWindow releases it.
  globalShortcut.register("Escape", () => {
    if (quickWindow && !quickWindow.isDestroyed()) {
      quickWindow.webContents.send("quick:dismiss");
    }
  });
  // Per-summon signal for the renderer: re-present the card, replay the
  // entrance, focus + select the draft.
  win.webContents.send("quick:summon");
}

/** Hide the quick window. As a non-activating panel it never stole app
 *  activation, so macOS hands focus straight back to whatever app the
 *  user was in — no app.hide() (which would hide the main window too). */
function dismissQuickWindow() {
  // Release Esc unconditionally — it must never stay claimed while the
  // panel is hidden, whatever state the window is in.
  globalShortcut.unregister("Escape");
  if (!quickWindow || quickWindow.isDestroyed() || !quickWindow.isVisible()) return;
  quickWindow.hide();
}

app.whenReady().then(() => {
  // macOS shows the bundle icon for packaged apps; in `electron .` dev
  // mode the dock would otherwise show the generic Electron icon.
  if (process.platform === "darwin" && app.dock) {
    app.dock.setIcon(ICON_PATH);
  }

  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });

  ipcMain.handle("app:reload", event => {
    assertTrustedSender(event);
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win) win.reload();
  });
  ipcMain.handle("app:quit", event => {
    assertTrustedSender(event);
    app.quit();
  });
  ipcMain.handle("window:isFullScreen", event => {
    assertTrustedSender(event);
    const win = BrowserWindow.fromWebContents(event.sender);
    return win?.isFullScreen() ?? false;
  });
  ipcMain.handle("config:get", event => {
    assertTrustedSender(event);
    return readConfig();
  });
  ipcMain.handle("config:set", async (event, config) => {
    assertTrustedSender(event);
    const normalized = await writeConfig(config);
    cachedConfig = normalized;
    // Server or key changed — reconnect the executor against the new target.
    startExecutor();
    return normalized;
  });
  ipcMain.handle("executor:status", event => {
    assertTrustedSender(event);
    return executorClient?.getStatus() ?? { state: "idle", executorId: null, connected: false, lastError: null };
  });
  ipcMain.handle("api:request", (event, config, request) => {
    assertTrustedSender(event);
    return apiRequest(config, request);
  });
  ipcMain.handle("events:connect", (event, config, sessionId, afterSeq) => {
    assertTrustedSender(event);
    const connectionId = crypto.randomUUID();
    const controller = new AbortController();
    eventStreams.set(connectionId, controller);
    setImmediate(() => {
      if (!controller.signal.aborted) {
        void streamEvents(connectionId, event.sender, config, sessionId, afterSeq, controller.signal);
      }
    });
    return connectionId;
  });
  ipcMain.handle("events:disconnect", (event, connectionId) => {
    assertTrustedSender(event);
    eventStreams.get(connectionId)?.abort();
    eventStreams.delete(connectionId);
  });
  ipcMain.handle("shell:open-path", async (event, targetPath) => {
    assertTrustedSender(event);
    if (typeof targetPath !== "string" || targetPath.length === 0) return "Invalid path";
    // shell.openPath returns "" on success, an error string otherwise.
    return shell.openPath(targetPath);
  });
  ipcMain.handle("clipboard:write", (event, text) => {
    assertTrustedSender(event);
    if (typeof text !== "string") return false;
    clipboard.writeText(text);
    return true;
  });

  // Hide the floating composer (called from QuickCapture after Escape,
  // submit, or any explicit dismiss — the renderer owns the timing so
  // its exit animation finishes first). Hiding rather than destroying
  // keeps the next summon snappy.
  ipcMain.handle("quick:close", event => {
    assertTrustedSender(event);
    dismissQuickWindow();
  });

  // Submit from the quick-capture window: { message, images?, sessionId? }.
  // We forward it to the main window over IPC so the renderer can call
  // its existing switchSession/createSession + sendMessage actions (with
  // the store, the SSE subscription, etc. already wired). Capture is
  // silent: the main window is NOT brought forward — the point of the
  // quick composer is firing off a thought without leaving the current
  // app. The session is waiting in arden whenever the user next switches.
  ipcMain.handle("quick:submit", (event, payload) => {
    assertTrustedSender(event);
    if (typeof payload?.message !== "string") return false;
    const hasImages = Array.isArray(payload.images) && payload.images.length > 0;
    if (!payload.message.trim() && !hasImages) return false;
    if (!mainWindow || mainWindow.isDestroyed()) {
      // Main window was closed (app kept alive in the dock). Recreate it
      // hidden so its renderer can process the message; the dock-icon
      // activate handler reveals it on demand. Defer the dispatch until
      // the renderer has mounted and registered its listener, otherwise
      // the message gets sent into the void.
      createWindow({ show: false });
      mainWindow.webContents.once("did-finish-load", () => {
        mainWindow.webContents.send("quick:message", payload);
      });
    } else {
      mainWindow.webContents.send("quick:message", payload);
    }
    return true;
  });

  // Interactive macOS screen snip for the quick composer. The panel hides
  // so the selection overlay captures what's beneath it, then re-presents
  // (draft intact) with the captured image handed back to the renderer.
  // Returns null when the user cancels the snip.
  ipcMain.handle("quick:captureScreen", async event => {
    assertTrustedSender(event);
    if (process.platform !== "darwin") return null;
    if (quickWindow && !quickWindow.isDestroyed()) dismissQuickWindow();
    const file = path.join(os.tmpdir(), `arden-quick-capture-${Date.now()}.png`);
    await new Promise(resolve => {
      // -i interactive selection, -x no shutter sound. Exits non-zero /
      // writes nothing when the user cancels with Esc — not an error.
      execFile("screencapture", ["-i", "-x", file], () => resolve());
    });
    let data = null;
    try {
      data = (await fs.readFile(file)).toString("base64");
      await fs.unlink(file);
    } catch {
      /* user cancelled the snip — no file */
    }
    if (quickWindow && !quickWindow.isDestroyed()) presentQuickWindow(quickWindow);
    return data ? { media_type: "image/png", data } : null;
  });

  // The quick renderer grows/shrinks its window around the chat picker.
  // Top edge stays fixed; the panel expands downward.
  ipcMain.handle("quick:resize", (event, height) => {
    assertTrustedSender(event);
    if (typeof height !== "number" || !Number.isFinite(height)) return;
    if (!quickWindow || quickWindow.isDestroyed()) return;
    const clamped = Math.round(Math.min(Math.max(height, QUICK_BASE_HEIGHT), QUICK_MAX_HEIGHT));
    // Bottom-anchored bar: growth extends upward so the input row stays put.
    const { x, y, height: current } = quickWindow.getBounds();
    quickWindow.setBounds({ x, y: y + (current - clamped), width: QUICK_WIDTH, height: clamped });
  });

  createWindow({ show: true });
  // Executor + tray live independently of any window: the menubar item is
  // the app's face while every window is closed, and the executor keeps
  // its server connection alive in the background.
  void readConfig().then(config => {
    cachedConfig = config;
    createTray();
    startExecutor();
  });
  // Preload the quick-capture panel (hidden) so the first shortcut press
  // presents an already-loaded renderer — same instant behavior as every
  // later summon, no first-use race.
  createQuickWindow();

  // Allow the renderer to bind/unbind the global quick-capture chord.
  // Returns true on success so the Settings UI can surface "already in
  // use" errors without polling. The renderer is the source of truth
  // for the user's chosen accelerator (it lives in localStorage prefs);
  // we just hold the OS registration here.
  ipcMain.handle("quick:setShortcut", (event, accelerator) => {
    assertTrustedSender(event);
    if (accelerator != null && typeof accelerator !== "string") return false;
    return setQuickShortcut(accelerator);
  });

  // Start with the default chord so the shortcut works even before the
  // renderer mounts and pushes the user's preference. Renderer will
  // overwrite this on boot if they've customized it.
  if (!setQuickShortcut(DEFAULT_QUICK_SHORTCUT)) {
    // eslint-disable-next-line no-console
    console.warn(`[arden] failed to register default global shortcut ${DEFAULT_QUICK_SHORTCUT} — already in use?`);
  }

  app.on("activate", () => {
    // Count only the main window: the quick-capture panel persisting in
    // the background must not satisfy "a window exists", or clicking the
    // dock icon after closing the main window would do nothing.
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (!mainWindow.isVisible()) mainWindow.show();
    } else {
      createWindow({ show: true });
    }
  });
});

app.on("will-quit", () => {
  // Release the global shortcut so other apps can claim it after we exit.
  globalShortcut.unregisterAll();
  executorClient?.dispose();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

// In dev mode the parent script (scripts/dev.mjs) sends SIGTERM/SIGINT to
// kill the Electron child during reloads. Without an explicit handler, the
// process dies before Chromium flushes pending localStorage writes — so any
// settings the user just toggled are lost on the next launch. Routing the
// signal through `app.quit()` runs the proper shutdown sequence (graceful
// renderer teardown + storage flush) before exiting.
process.on("SIGTERM", () => app.quit());
process.on("SIGINT", () => app.quit());
