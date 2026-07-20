import { expect, test } from "bun:test";
import {
  gmailAccountSummary,
  googleServiceConnectionSummary,
  googleConnectionSummary,
  serviceConnectionLabel,
  serviceConnectionPill,
  serviceActionLabel,
} from "@/features/settings/lib/integrationConnection";

test("summarizes Google accounts without exposing token filenames", () => {
  const accounts = [
    { email: "one@example.com", token_file: "gmail_token_one@example.com.json", has_send_scope: true },
    { email: "two@example.com", token_file: "gmail_token_two@example.com.json", has_send_scope: false },
  ];

  expect(gmailAccountSummary(accounts)).toBe("2 accounts");
});

test("summarizes each Google service from explicit account bindings", () => {
  const accounts = [
    { id: "acct-1", email: "one@example.com", services: ["gmail", "calendar"] as const, scopes: [] },
  ];

  expect(googleServiceConnectionSummary("gmail", true, accounts).label).toBe("Ready");
  expect(googleServiceConnectionSummary("google_drive", true, accounts).label).toBe("Connect");
  expect(googleServiceConnectionSummary("calendar", false, accounts).label).toBe("Disabled");
});

test("labels env-managed service tokens as read-only", () => {
  const service = {
    id: "slack_bot_token",
    name: "Slack bot token",
    connected: true,
    from_env: true,
    key_hint: "xoxb-...abcd",
  };

  expect(serviceConnectionLabel(service)).toBe("Connected via env");
  expect(serviceConnectionPill(service)).toBe("Connected via env");
  expect(serviceActionLabel(service)).toBe("Env-managed");
});

test("keeps disconnected service rows quiet and summarizes google readiness", () => {
  const service = {
    id: "slack_bot_token",
    name: "Slack bot token",
    connected: false,
    from_env: false,
    key_hint: null,
  };

  expect(serviceConnectionPill(service)).toBeNull();
  expect(googleConnectionSummary(false, [])).toEqual({
    label: "Connect Google",
    detail: "No Google accounts",
    tone: "setup",
  });
  expect(googleConnectionSummary(true, [{ email: "one@example.com", token_file: "token.json" }])).toEqual({
    label: "Ready",
    detail: "1 account enabled",
    tone: "ready",
  });
});
