import { expect, test } from "bun:test";

import { agentExceptionalStatus } from "@/features/areas/components/AreaControls";

test("Custodian status names paused, unavailable, and failed states honestly", () => {
  expect(agentExceptionalStatus(true, "ready", null)).toBe("Paused — the agent isn’t watching this area.");
  expect(agentExceptionalStatus(false, "unavailable", null)).toBe("Custodian unavailable — no check is scheduled.");
  expect(agentExceptionalStatus(false, "error", "provider unavailable")).toBe(
    "Last check failed — provider unavailable",
  );
  expect(agentExceptionalStatus(false, "ready", null)).toBeNull();
});
