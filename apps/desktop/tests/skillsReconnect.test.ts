import { expect, test } from "bun:test";

import { reloadAllCollections } from "@/actions/bootstrap";
import { getState, setState } from "@/stores";

test("reconnect reloads skills after an initially empty fetch", async () => {
  const originalDesktop = window.ardenDesktop;
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        const data = request.path === "/skills"
          ? { skills: [{ name: "apple-design", description: "Apple design", location: "shared-global", path: "/tmp/SKILL.md" }] }
          : request.path.startsWith("/sessions?")
            ? { sessions: [] }
            : request.path === "/automations"
              ? { automations: [] }
              : {};
        return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
      },
    },
  } as typeof window.ardenDesktop;

  try {
    setState({
      config: { serverUrl: "http://localhost:6877", apiKey: "" },
      currentSessionId: null,
      skills: [],
    });

    reloadAllCollections(null);
    await new Promise((resolve) => setTimeout(resolve, 1_000));

    expect(getState().skills.map((skill) => skill.name)).toEqual(["apple-design"]);
  } finally {
    window.ardenDesktop = originalDesktop;
  }
});
