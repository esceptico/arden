import type { GoogleAccountSummary, GoogleIntegrationId } from "@/api/settings";

export interface ServiceConnectionLike {
  id: string;
  name: string;
  connected: boolean;
  from_env: boolean;
  key_hint?: string | null;
}

export interface GmailAccountLike {
  email?: string | null;
  token_file: string;
  has_send_scope?: boolean;
  error?: string;
}

export function serviceConnectionLabel(service: ServiceConnectionLike): string {
  if (!service.connected) return "Not connected";
  if (service.from_env) return "Connected via env";
  if (service.key_hint) return service.key_hint;
  return "Connected";
}

export function serviceConnectionPill(service: ServiceConnectionLike): string | null {
  if (!service.connected) return null;
  return serviceConnectionLabel(service);
}

export function serviceActionLabel(service: ServiceConnectionLike): string {
  if (service.connected && service.from_env) return "Env-managed";
  if (service.connected) return "Disconnect";
  return "Connect";
}

export function gmailAccountSummary(accounts: readonly GmailAccountLike[]): string {
  const count = accounts.length;
  if (count === 0) return "No accounts";
  return `${count} ${count === 1 ? "account" : "accounts"}`;
}

export interface GoogleConnectionSummary {
  label: string;
  detail: string;
  tone: "ready" | "paused" | "setup";
}

export function googleConnectionSummary(
  enabled: boolean,
  accounts: readonly GmailAccountLike[],
): GoogleConnectionSummary {
  const count = accounts.length;
  if (count === 0) {
    return {
      label: "Connect Google",
      detail: "No Google accounts",
      tone: "setup",
    };
  }

  const accountLabel = `${count} ${count === 1 ? "account" : "accounts"}`;
  if (!enabled) {
    return {
      label: "Paused",
      detail: `${accountLabel} connected`,
      tone: "paused",
    };
  }

  return {
    label: "Ready",
    detail: `${accountLabel} enabled`,
    tone: "ready",
  };
}

export function googleServiceConnectionSummary(
  integrationId: GoogleIntegrationId,
  enabled: boolean,
  accounts: readonly GoogleAccountSummary[],
): GoogleConnectionSummary {
  const count = accounts.filter((account) => account.services.includes(integrationId)).length;
  if (count === 0) {
    return { label: "Connect", detail: "No connected accounts", tone: "setup" };
  }
  const detail = `${count} ${count === 1 ? "account" : "accounts"}`;
  if (!enabled) return { label: "Disabled", detail, tone: "paused" };
  return { label: "Ready", detail, tone: "ready" };
}
