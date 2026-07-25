export type HistoryPhase =
  | "idle"
  | "cached-preview"
  | "loading-history"
  | "live-tail"
  | "replay-gap";

export type ConnectionPhase =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected"
  | "failed";
