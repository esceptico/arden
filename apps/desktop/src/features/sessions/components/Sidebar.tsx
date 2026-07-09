import { Brain, House, Settings as SettingsIcon, Zap } from "lucide-react";
import { originFromEvent } from "@/lib/tokens/motion";
import { useStore } from "@/stores";
import { goToNewSessionHome } from "@/actions/sessions";
import { fetchAutomations } from "@/actions/automations";
import { ICON } from "@/lib/icons";
import { useVisibilityPoll } from "@/lib/hooks";
import { NavRow } from "@/features/sessions/components/NavRow";
import { SessionList } from "@/features/sessions/components/SessionList";
import { ThemeToggle } from "@/components/ui/ThemeToggle";

export function Sidebar() {
  const openSettings = useStore((s) => s.openSettings);
  const openAutomations = useStore((s) => s.openAutomations);
  const openMemory = useStore((s) => s.openMemory);
  useVisibilityPoll(fetchAutomations, 20_000);

  return (
    <aside className="sidebar flex flex-col h-full">
      {/* Drag region. Height tuned so nav rows start just below the
          macOS traffic-lights zone, not below a 38px chrome ribbon. */}
      <div className="drag-spacer shrink-0 h-[22px]" />
      <nav className="flex flex-col gap-0 px-2.5 pt-2">
        {/* Home doubles as "new session": the hero input starts a chat, so
            one destination covers both (⌘N lands here too). */}
        <NavRow
          icon={<House size={ICON.MD} strokeWidth={2} />}
          label="Home"
          onClick={() => goToNewSessionHome()}
        />
        <NavRow
          icon={<Zap size={ICON.MD} strokeWidth={2} />}
          label="Automations"
          onClick={(e) => openAutomations(originFromEvent(e.currentTarget))}
        />
        <NavRow
          icon={<Brain size={ICON.MD} strokeWidth={2} />}
          label="Memory"
          onClick={(e) => openMemory(originFromEvent(e.currentTarget))}
        />
      </nav>
      <SessionList />
      <nav className="flex items-center gap-1 px-2.5 pt-1.5 pb-3">
        <div className="min-w-0 flex-1">
          <NavRow
            icon={<SettingsIcon size={ICON.MD} strokeWidth={2} />}
            label="Settings"
            onClick={(e) => openSettings(originFromEvent(e.currentTarget))}
          />
        </div>
        <ThemeToggle />
      </nav>
    </aside>
  );
}
