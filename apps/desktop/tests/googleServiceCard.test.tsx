import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { GoogleServiceCard } from "@/features/settings/components/GoogleServiceCard";

const accounts = [
  {
    id: "acct-1",
    email: "user@example.com",
    services: ["gmail" as const, "calendar" as const],
    scopes: ["gmail-scope", "calendar-scope"],
  },
  {
    id: "acct-2",
    email: "work@example.com",
    services: ["gmail" as const],
    scopes: ["gmail-scope"],
  },
];

const noop = async () => {};

test("bound accounts render with Disconnect and an Add account primary", () => {
  const markup = renderToStaticMarkup(
    <GoogleServiceCard
      integrationId="gmail"
      enabled
      accounts={accounts}
      pendingId={null}
      onToggle={noop}
      onConnect={noop}
      onDisconnect={noop}
    />,
  );

  expect(markup).toContain("Gmail");
  expect(markup).toContain("2 accounts ready");
  expect(markup).toContain("4 tools available");
  expect(markup).toContain("user@example.com");
  expect(markup).toContain("work@example.com");
  expect(markup).toContain(">Disconnect</button>");
  expect(markup).toContain('aria-label="Add account"');
  expect(markup).not.toContain(">Add account</button>");
  expect(markup).not.toContain("Use account");
});

test("only accounts bound to the service are listed", () => {
  const markup = renderToStaticMarkup(
    <GoogleServiceCard
      integrationId="calendar"
      enabled={false}
      accounts={accounts}
      pendingId={null}
      onToggle={noop}
      onConnect={noop}
      onDisconnect={noop}
    />,
  );

  expect(markup).toContain("Google Calendar");
  expect(markup).toContain("user@example.com");
  expect(markup).not.toContain("work@example.com");
  expect(markup).not.toContain("Use account");
});

test("an unconnected service leads with Connect and the sign-in detection note", () => {
  const markup = renderToStaticMarkup(
    <GoogleServiceCard
      integrationId="google_drive"
      enabled={false}
      accounts={[]}
      pendingId={null}
      onToggle={noop}
      onConnect={noop}
      onDisconnect={noop}
    />,
  );

  expect(markup).toContain("Google Drive");
  expect(markup).toContain("Not connected");
  expect(markup).toContain("Account detected at sign-in");
  expect(markup).toContain(">Connect</button>");
});
