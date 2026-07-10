import { useLayoutEffect, useRef, type CSSProperties } from "react";
import clsx from "clsx";
import { ArrowLeft } from "lucide-react";
import { useStore } from "@/stores";
import { switchSession } from "@/actions/sessions";
import { Messages } from "@/features/chat/components/Messages";
import { Composer } from "@/features/chat/components/Composer";
import { ApprovalBanner } from "@/features/chat/components/ApprovalBanner";
import { TriageChip } from "@/features/chat/components/TriageChip";
import { useChatTriage } from "@/hooks/useChatTriage";
import { Button } from "@/components/ui/Button";
import { ICON } from "@/lib/icons";
import { DURATION_RIGHT_PANEL_HIDE, MOTION } from "@/lib/tokens/motion";
import { useHasTrafficLights } from "@/lib/platform";

function ChatHeader() {
  const sessionId = useStore((s) => s.currentSessionId);
  const sessions = useStore((s) => s.sessions);
  const sidebarHidden = useStore((s) => s.prefs.sidebarHidden);
  const openArea = useStore((s) => s.openArea);
  // Rooms are universal , so any filed
  // chat gets a breadcrumb back to its room — titled from the areas
  // overview when live, else the plain area name.
  const areaTitle = useStore((s) => {
    const pid = s.sessions.find((x) => x.session_id === s.currentSessionId)?.area_id;
    if (!pid) return null;
    return (
      s.areas.overview?.areas.find((sl) => sl.key === pid)?.title ??
      s.areas.recordsById[pid]?.name ??
      null
    );
  });
  const hasTrafficLights = useHasTrafficLights();
  const session = sessions.find((s) => s.session_id === sessionId);

  const title = session?.name || (sessionId ? "untitled" : "no session");
  const areaKey = session?.area_id ?? null;

  // A child agent session gets a breadcrumb back to its parent in the header —
  // the discoverable spot, mirroring the hub's "← parent" chip.
  const parentId = session?.parent_session_id ?? null;
  const isAgent = session?.session_type === "agent" || !!parentId;
  const parentName =
    (parentId ? sessions.find((s) => s.session_id === parentId)?.name : null)?.trim() || "parent session";

  return (
    <div className="chat-header flex items-center h-[52px] px-[18px]">
      {/* With the sidebar hidden, the title row clears the fixed toggle. The
          travel depends on where the toggle sits: 110px when the macOS
          traffic lights push it to left:84, but only 49px in the browser /
          fullscreen where it sits at left:23 (no native lights). The shift
          rides a GPU translate — the right padding snaps once (re-truncating
          the title) while the transform carries the visible travel. */}
      <div
        className={clsx(
          "flex min-w-0 flex-1 items-center gap-2 transition-transform duration-route ease-emphasized",
          sidebarHidden &&
            (hasTrafficLights ? "translate-x-[110px] pr-[110px]" : "translate-x-[49px] pr-[49px]"),
        )}
      >
        {isAgent && parentId && (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void switchSession(parentId)}
              title={`Back to ${parentName}`}
              className="group/back shrink-0 max-w-[180px] -ml-0.5"
            >
              <ArrowLeft
                size={ICON.SM}
                strokeWidth={2}
                className="shrink-0 text-faint transition-colors group-hover/back:text-ink"
              />
              <span className="truncate">{parentName}</span>
            </Button>
            <span className="shrink-0 text-faint select-none" aria-hidden>
              /
            </span>
          </>
        )}
        {/* Filed chats breadcrumb back to their room, mirroring the agent
            parent chip: the room is this conversation's context. */}
        {areaKey && areaTitle && (
          <>
            <button
              type="button"
              onClick={() => openArea(areaKey)}
              title={`Back to the ${areaTitle} area`}
              className="shrink-0 max-w-[160px] truncate rounded-md bg-surface-soft px-2 py-0.5 text-xs font-medium text-ink-soft hover:text-ink"
            >
              {areaTitle}
            </button>
            <span className="shrink-0 text-faint select-none" aria-hidden>
              /
            </span>
          </>
        )}
        <h1 className="m-0 min-w-0 flex-1 text-md font-semibold tracking-[-0.01em] text-ink truncate">
          {title}
        </h1>
      </div>
    </div>
  );
}

export function Chat() {
  const sidebarHidden = useStore((s) => s.prefs.sidebarHidden);
  const rightPanelCollapsed = useStore((s) => s.prefs.rightPanelCollapsed);
  const sessionId = useStore((s) => s.currentSessionId);
  const hasApproval = useStore((s) => s.pendingApprovals.length > 0);
  useChatTriage();

  // Composer overlays the bottom of the message scroll area. The scroll
  // area needs padding-bottom equal to the bottom stack's actual height
  // so the last message clears the composer when scrolled to the end.
  // Height is dynamic
  // (textarea auto-resize, approval banner appears/disappears), so we
  // observe it and write to `--chat-bottom-h` consumed by
  // `.scroll-messages` padding-bottom + the jump-to-bottom pill offset.
  const bottomStackRef = useRef<HTMLDivElement>(null);
  // useLayoutEffect (not useEffect) so the measured height is written
  // BEFORE first paint — otherwise the scroll-padding briefly uses the
  // CSS fallback (96px) and the pill uses its inline fallback (96px),
  // causing a one-frame jump when the real composer is taller. Per
  // Codex review.
  useLayoutEffect(() => {
    const el = bottomStackRef.current;
    if (!el) return;
    const apply = (height: number) => {
      document.documentElement.style.setProperty("--chat-bottom-h", `${height}px`);
    };
    apply(el.getBoundingClientRect().height);
    const ro = new ResizeObserver((entries) => {
      const h = entries[0]?.contentRect.height ?? 0;
      apply(h);
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      document.documentElement.style.removeProperty("--chat-bottom-h");
    };
  }, []);

  return (
    <main
      data-sidebar-hidden={sidebarHidden ? "true" : "false"}
      data-has-approval={hasApproval ? "true" : "false"}
      data-right-open={rightPanelCollapsed ? "false" : "true"}
      className="absolute top-0 right-0 bottom-0 left-[var(--sidebar-width,272px)] data-[sidebar-hidden=true]:left-0 data-[right-open=true]:right-[var(--right-panel-width,320px)] bg-bg overflow-hidden"
      style={{
        // Per-property transitions so each edge matches its own panel. On
        // HIDE the inset borrows the panel's faster EASE_OUT +
        // DURATION_RIGHT_PANEL_HIDE so the fading card and the expanding edge
        // move as one (no overlap); on OPEN it uses route/emphasized to mirror
        // the panel's slide-in.
        transition: `left ${(sidebarHidden ? DURATION_RIGHT_PANEL_HIDE : MOTION.route) * 1000}ms ${
          sidebarHidden ? "var(--ease-out-soft)" : "var(--ease-emphasized)"
        }, right ${(rightPanelCollapsed ? DURATION_RIGHT_PANEL_HIDE : MOTION.route) * 1000}ms ${
          rightPanelCollapsed ? "var(--ease-out-soft)" : "var(--ease-emphasized)"
        }`,
      } as CSSProperties}
    >
      <div className="relative w-full h-full">
        <Messages key={sessionId ?? "none"} />
        <div
          aria-hidden
          className="chat-bottom-fade absolute left-0 right-0 bottom-0 pointer-events-none z-[5]"
          style={{ height: "calc(var(--chat-bottom-h, 96px) + 24px)" }}
        />
        <div className="absolute top-0 left-0 right-0 z-10">
          <ChatHeader />
        </div>
        <div
          ref={bottomStackRef}
          className="absolute bottom-0 left-0 right-0 z-10"
        >
          <TriageChip />
          <ApprovalBanner />
          <Composer />
        </div>
      </div>
    </main>
  );
}
