const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("ardenDesktop", {
  version: () => process.versions.electron,
  app: {
    reload: () => ipcRenderer.invoke("app:reload"),
    quit: () => ipcRenderer.invoke("app:quit"),
  },
  window: {
    isFullScreen: () => ipcRenderer.invoke("window:isFullScreen"),
    onFullScreenChange: callback => {
      const listener = (_event, isFullScreen) => callback(isFullScreen);
      ipcRenderer.on("window:fullscreen-changed", listener);
      return () => ipcRenderer.off("window:fullscreen-changed", listener);
    },
  },
  config: {
    get: () => ipcRenderer.invoke("config:get"),
    set: config => ipcRenderer.invoke("config:set", config),
  },
  api: {
    request: (config, request) => ipcRenderer.invoke("api:request", config, request),
  },
  events: {
    connect: (config, sessionId, afterSeq) => ipcRenderer.invoke("events:connect", config, sessionId, afterSeq),
    disconnect: connectionId => ipcRenderer.invoke("events:disconnect", connectionId),
    onData: callback => {
      const listener = (_event, payload) => callback(payload);
      ipcRenderer.on("events:data", listener);
      return () => ipcRenderer.off("events:data", listener);
    },
  },
  shell: {
    openPath: path => ipcRenderer.invoke("shell:open-path", path),
  },
  dialog: {
    selectDirectory: options => ipcRenderer.invoke("dialog:select-directory", options),
  },
  clipboard: {
    writeText: text => ipcRenderer.invoke("clipboard:write", text),
  },
  quickCapture: {
    submit: payload => ipcRenderer.invoke("quick:submit", payload),
    close: () => ipcRenderer.invoke("quick:close"),
    captureScreen: () => ipcRenderer.invoke("quick:captureScreen"),
    resize: height => ipcRenderer.invoke("quick:resize", height),
    setShortcut: accelerator => ipcRenderer.invoke("quick:setShortcut", accelerator),
    onMessage: callback => {
      const listener = (_event, message) => callback(message);
      ipcRenderer.on("quick:message", listener);
      return () => ipcRenderer.off("quick:message", listener);
    },
    onSummon: callback => {
      const listener = () => callback();
      ipcRenderer.on("quick:summon", listener);
      return () => ipcRenderer.off("quick:summon", listener);
    },
    onDismiss: callback => {
      const listener = () => callback();
      ipcRenderer.on("quick:dismiss", listener);
      return () => ipcRenderer.off("quick:dismiss", listener);
    },
  },
});
