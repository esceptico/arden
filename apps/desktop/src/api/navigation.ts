import type { SettingsTabId } from "@/stores/types";

/** Where an agent can send the user. Server contract:
 *  apps/server/arden/events/destinations.py. */
export type AppDestination =
  | { kind: "home" }
  | { kind: "session"; session_id: string }
  | { kind: "settings"; tab?: SettingsTabId | null }
  | { kind: "automation"; task_id?: string | null }
  | { kind: "memory"; path?: string | null }
  | { kind: "area"; area_id: string };
