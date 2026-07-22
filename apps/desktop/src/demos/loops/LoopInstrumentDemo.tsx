import { useState } from "react";
import { AutomationScheduleLoops } from "@/features/automations/components/AutomationScheduleLoops";
import type { Schedule } from "@/features/automations/lib/schedule";
import "./demo.css";

const INITIAL: Schedule = {
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

export function LoopInstrumentDemo() {
  const [schedule, setSchedule] = useState<Schedule>(INITIAL);

  return (
    <main className="loop-demo">
      <AutomationScheduleLoops value={schedule} onChange={setSchedule} />
    </main>
  );
}
