import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ServerList } from "@/features/settings/components/mcp/ServerList";

let root: Root | null = null;

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  document.body.replaceChildren();
});

test("empty MCP state explains both setup paths without a decorative surface", async () => {
  const app = document.createElement("div");
  document.body.append(app);
  root = createRoot(app);

  await act(async () => {
    root?.render(
      <ServerList
        servers={[]}
        loadError={null}
        onAdd={() => {}}
        onEdit={() => {}}
        onChanged={async () => {}}
        onAssistant={() => {}}
      />,
    );
  });

  expect(app.querySelector("button")?.textContent).toContain("Guided setup");
  const add = app.querySelector<HTMLButtonElement>('button[aria-label="Add MCP server"]');
  expect(add).not.toBeNull();
  expect(add?.textContent).toBe("");
  expect(add?.getAttribute("data-tone")).toBe("primary");
  expect(add?.getAttribute("data-shape")).toBe("circle");
  expect(app.querySelector(".settings-empty-note")).toBeNull();
  expect(app.querySelector(".settings-mcp-empty")?.textContent).toContain("No servers configured");
  expect(app.querySelector(".settings-mcp-empty")?.textContent).toContain(
    "Guided setup starts from a URL, package, or command. Add server opens every field directly.",
  );
});
