import { expect, test } from "bun:test";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { ScheduleEditor } from "@/features/automations/components/ScheduleEditor";
import type { Schedule } from "@/features/automations/lib/schedule";

const atSchedule: Schedule = {
  kind: "at",
  at: "09:00",
  every: "30m",
  days: "daily",
  start: "",
  end: "",
  event: "approaching",
  lead: "15",
  channel: "",
  fromUser: "",
  keywords: "",
};

function mount(): { element: HTMLElement; root: Root; restore: () => void } {
  const element = document.createElement("div");
  document.body.append(element);
  return { element, root: createRoot(element), restore: () => element.remove() };
}

function ControlledScheduleEditor({
  initial,
  onCommit,
}: {
  initial: Schedule;
  onCommit: (next: Schedule) => void;
}) {
  const [schedule, setSchedule] = useState(initial);
  return (
    <ScheduleEditor
      schedule={schedule}
      onChange={(next) => {
        setSchedule(next);
        onCommit(next);
      }}
    />
  );
}

test("ScheduleEditor preserves the mockup day-ruler geometry", () => {
  const markup = renderToStaticMarkup(<ScheduleEditor schedule={atSchedule} onChange={() => {}} />);
  expect(markup).toContain('viewBox="0 0 320 72"');
  expect((markup.match(/automation-trigger-editor__day-ruler-tick/g) ?? [])).toHaveLength(25);
  expect(markup).toContain("click or drag to set the time");
});

test("ScheduleEditor keyboard nudges a clock schedule by the canonical five-minute increment", async () => {
  const { element, root, restore } = mount();
  let next: Schedule | null = null;
  await act(async () => {
    root.render(<ScheduleEditor schedule={atSchedule} onChange={(value) => { next = value; }} />);
  });

  const ruler = element.querySelector<HTMLElement>(".automation-trigger-editor__day-ruler")!;
  await act(async () => {
    ruler.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true }));
  });
  expect(next?.at).toBe("09:05");

  await act(async () => root.unmount());
  restore();
});

test("ScheduleEditor moves the one server-backed interval window without client-only windows", async () => {
  const { element, root, restore } = mount();
  let next: Schedule | null = null;
  const schedule: Schedule = { ...atSchedule, kind: "every", every: "30m", start: "08:00", end: "10:00" };
  await act(async () => {
    root.render(<ScheduleEditor schedule={schedule} onChange={(value) => { next = value; }} />);
  });

  const ruler = element.querySelector<HTMLElement>(".automation-trigger-editor__day-ruler")!;
  await act(async () => {
    ruler.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true }));
  });
  expect(next?.start).toBe("08:05");
  expect(next?.end).toBe("10:05");

  await act(async () => root.unmount());
  restore();
});

test("ScheduleEditor ArrowUp and ArrowDown move the server-backed cadence ladder", async () => {
  const { element, root, restore } = mount();
  const commits: Schedule[] = [];
  const schedule: Schedule = { ...atSchedule, kind: "every", every: "30m", start: "08:00", end: "10:00" };
  await act(async () => {
    root.render(<ControlledScheduleEditor initial={schedule} onCommit={(next) => commits.push(next)} />);
  });

  const ruler = element.querySelector<HTMLElement>(".automation-trigger-editor__day-ruler")!;
  await act(async () => {
    ruler.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true, cancelable: true }));
  });
  expect(commits.at(-1)).toMatchObject({ every: "1h", start: "08:00", end: "10:00" });

  await act(async () => {
    ruler.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
  });
  expect(commits.at(-1)?.every).toBe("30m");

  await act(async () => root.unmount());
  restore();
});

test("ScheduleEditor wheel uses the mock's detent accumulation before committing cadence", async () => {
  const { element, root, restore } = mount();
  const commits: Schedule[] = [];
  const schedule: Schedule = { ...atSchedule, kind: "every", every: "30m" };
  await act(async () => {
    root.render(<ControlledScheduleEditor initial={schedule} onCommit={(next) => commits.push(next)} />);
  });

  const ruler = element.querySelector<HTMLElement>(".automation-trigger-editor__day-ruler")!;
  const first = new WheelEvent("wheel", { deltaY: 40, bubbles: true, cancelable: true });
  await act(async () => ruler.dispatchEvent(first));
  expect(first.defaultPrevented).toBe(true);
  expect(commits).toHaveLength(0);

  const second = new WheelEvent("wheel", { deltaY: 40, bubbles: true, cancelable: true });
  await act(async () => ruler.dispatchEvent(second));
  expect(commits.at(-1)?.every).toBe("1h");

  const reverse = new WheelEvent("wheel", { deltaY: -80, bubbles: true, cancelable: true });
  await act(async () => ruler.dispatchEvent(reverse));
  expect(commits.at(-1)?.every).toBe("30m");

  await act(async () => root.unmount());
  restore();
});
