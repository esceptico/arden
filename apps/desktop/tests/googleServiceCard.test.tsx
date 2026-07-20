import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { GoogleServiceCard } from "@/features/settings/components/GoogleServiceCard";
import { googleServiceConnectionSummary } from "@/features/settings/lib/integrationConnection";

const accounts = [
  {
    id: "acct-1",
    email: "user@example.com",
    services: ["gmail" as const],
    scopes: ["gmail-scope"],
  },
];

test("offers an existing Google account when connecting another service", () => {
  const markup = renderToStaticMarkup(
    <GoogleServiceCard
      integrationId="google_drive"
      enabled={false}
      summary={googleServiceConnectionSummary("google_drive", false, accounts)}
      accounts={accounts}
      pendingId={null}
      onToggle={async () => {}}
      onConnect={async () => {}}
      onDisconnect={async () => {}}
    />,
  );

  expect(markup).toContain("Google Drive");
  expect(markup).toContain("Add for user@example.com");
  expect(markup).toContain("Read Docs and Sheets; changes require approval");
});
