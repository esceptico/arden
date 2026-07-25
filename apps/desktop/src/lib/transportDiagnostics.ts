import type { ConnectionPhase } from "@/stores/domains";

export interface TransportDiagnosticsSnapshot {
  connectionPhase: ConnectionPhase;
  lastSeq?: number;
  lastKeepaliveSeq?: number;
  connectAfterSeq?: number | null;
  lastClosedReason?: string | null;
  lastError?: string | null;
  updatedAt: number;
}
