import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { SetupAssistant } from "@/features/settings/components/setup/SetupAssistant";

let root: Root | null = null;

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  document.body.replaceChildren();
});

test("MCP guided setup states exactly what happens next", async () => {
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);
  root = createRoot(app);

  await act(async () => {
    root?.render(
      <SetupAssistant
        open
        kind="mcp"
        onClose={() => {}}
        onDone={() => {}}
      />,
    );
  });

  const sheet = document.body.querySelector<HTMLElement>(".setup-sheet")!;
  expect(sheet.getAttribute("data-overlay-layer")).toBe("settings-setup");
  expect(sheet.getAttribute("aria-modal")).toBe("false");
  expect(sheet.textContent).toContain("Guided MCP setup");
  expect(sheet.textContent).toContain("GUIDED SETUP");
  expect(sheet.textContent).toContain("Connect an MCP server");
  expect(sheet.textContent).toContain(
    "Paste a server URL, package name, or command. Arden carries it into the server form for review; nothing is connected yet.",
  );
  expect(sheet.querySelector(".sheet-head.dp-sheet-header")).not.toBeNull();
  expect(sheet.querySelector(".sheet-body.dp-sheet-body")).not.toBeNull();
  expect(sheet.querySelector(".sheet-actions.dp-sheet-footer")?.textContent).toContain("Cancel");
  expect(sheet.querySelector('[aria-label="Close Guided MCP setup"]')?.getAttribute("title")).toBeNull();
  expect(sheet.querySelector(".sheet-cancel")?.getAttribute("data-variant")).toBe("secondary");
  expect(sheet.querySelector(".sheet-field label")?.textContent).toBe("Server or package");
  expect(sheet.querySelector<HTMLInputElement>(".sheet-field .dp-field")?.placeholder)
    .toBe("URL, package name, or command");
  expect(sheet.querySelector(".sheet-step-label")).toBeNull();
  expect(sheet.querySelector(".scope-list")?.textContent)
    .toContain("Review transport, authentication, discovered tools, and each approval policy.");
  expect(sheet.querySelector(".sheet-primary")?.textContent).toBe("Continue to server form");
});

test("integration sheets keep the mock's fields, steps, scopes, and primary", async () => {
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);
  root = createRoot(app);

  await act(async () => {
    root?.render(
      <SetupAssistant
        open
        kind="slack"
        onClose={() => {}}
        onDone={() => {}}
      />,
    );
  });

  const sheet = document.body.querySelector<HTMLElement>(".setup-sheet")!;
  expect(sheet.textContent).toContain("Connect Slack");
  expect(sheet.querySelectorAll(".sheet-step-label")).toHaveLength(2);
  expect(sheet.querySelector(".sheet-step-label")?.textContent).toBe("1Bot token");
  expect(sheet.querySelectorAll(".sheet-step-label")[1]?.textContent).toBe("2Review capability");
  expect(sheet.querySelector<HTMLInputElement>(".sheet-field .dp-field")?.type).toBe("password");
  expect(sheet.querySelector(".scope-list")?.textContent)
    .toContain("Channels + messages");
  expect(sheet.querySelector(".scope-list")?.textContent)
    .toContain("Post message — always ask");
  expect(sheet.querySelector(".sheet-primary")?.textContent).toBe("Save connection");
});

test("the MCP footer delegates a provided reference without turning the peek into a wizard", async () => {
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);
  root = createRoot(app);
  const calls: Array<[string, string]> = [];

  await act(async () => {
    root?.render(
      <SetupAssistant
        open
        kind="mcp"
        onClose={() => {}}
        onDone={() => {}}
        onPrimary={(kind, value) => calls.push([kind, value])}
      />,
    );
  });

  const sheet = document.body.querySelector<HTMLElement>(".setup-sheet")!;
  const field = sheet.querySelector<HTMLInputElement>(".sheet-field .dp-field")!;
  expect(sheet.querySelector<HTMLButtonElement>(".sheet-primary")?.disabled).toBe(true);

  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    setter?.call(field, "https://mcp.example.test");
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });

  expect(sheet.querySelector<HTMLButtonElement>(".sheet-primary")?.disabled).toBe(false);

  await act(async () => {
    sheet.querySelector<HTMLButtonElement>(".sheet-primary")?.click();
  });

  expect(calls).toEqual([["mcp", "https://mcp.example.test"]]);
  expect(sheet.querySelectorAll(".sheet-step")).toHaveLength(2);
});
