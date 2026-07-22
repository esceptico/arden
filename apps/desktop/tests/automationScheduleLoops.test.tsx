import { expect, test } from "bun:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { AutomationScheduleLoops } from "@/features/automations/components/AutomationScheduleLoops";
import type { Schedule } from "@/features/automations/lib/schedule";

const schedule: Schedule = {
  kind: "every",
  at: "09:00",
  every: "2h",
  days: "weekdays",
  start: "09:00",
  end: "17:00",
  event: "approaching",
  lead: "15",
  channel: "",
  fromUser: "",
  keywords: "",
};

test("renders direct radial controls for days, cadence, and the time window", () => {
  const html = renderToStaticMarkup(<AutomationScheduleLoops value={schedule} onChange={() => {}} />);
  expect(html).toContain("ACTIVE DAYS");
  expect(html).toContain("CADENCE");
  expect(html).toContain("Weekdays");
  expect(html).toContain("9:00 AM–5:00 PM");
  expect(html).toContain("Every 2h (9:00 AM–5:00 PM) · Weekdays");
  expect(html).toContain('aria-label="Monday"');
  expect(html).toContain('aria-label="Every 4h"');
  expect(html).toContain('aria-label="Window start"');
  expect(html).toContain('aria-label="Window end"');
});

test("the selected weekdays and cadence expose their checked state", () => {
  const html = renderToStaticMarkup(<AutomationScheduleLoops value={schedule} onChange={() => {}} />);
  expect(html).toMatch(/aria-label="Monday" aria-checked="true"/);
  expect(html).toMatch(/aria-label="Saturday" aria-checked="false"/);
  expect(html).toMatch(/aria-label="Every 2h" aria-checked="true"/);
});

test("clicking a cadence label emits a real schedule change", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  let next: Schedule | null = null;
  await act(async () => {
    root.render(<AutomationScheduleLoops value={schedule} onChange={(value) => { next = value; }} />);
  });
  const option = host.querySelector<SVGGElement>('[aria-label="Every 4h"]');
  expect(option).not.toBeNull();
  await act(async () => option?.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  expect(next).toMatchObject({ kind: "every", every: "4h", days: "weekdays" });
  await act(async () => root.unmount());
  host.remove();
});
