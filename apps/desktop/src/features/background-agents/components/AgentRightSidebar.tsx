import { useMemo, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, Bot, MoreHorizontal, X } from "lucide-react";
import { isInternalAutomation, isIterationLoop } from "@/lib/automationFilters";
import {
  DURATION_RIGHT_PANEL_HIDE,
  EASE_EMPHASIZED,
  EASE_OUT,
  MOTION,
  originFromEvent,
} from "@/lib/tokens/motion";
import { ICON } from "@/lib/icons";
import { useStore } from "@/stores";
import { isActiveAgentStatus, isAgentSessionId, parentSessionIdOf } from "@/lib/agentRun";
import { useWorkflows } from "@/hooks/useWorkflows";
import { isActiveWorkflow, workflowKey } from "@/stores/workflow-domain";
import { ExpandableWorkflowCard } from "@/components/ui/WorkflowDetail";
import { ScrollFadeTop } from "@/components/ui/ScrollBlur";
import { EmptyState } from "@/components/ui/EmptyState";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { StatusDot } from "@/components/ui/StatusDot";
import { Collapse } from "@/components/ui/Collapse";
import {
  latestTodoListFromMessages,
  RIGHT_PANEL_WIDTH,
} from "@/features/background-agents/lib/panelConstants";
import { rosterRowMotion } from "@/features/background-agents/lib/rosterMotion";
import { useChildAgentsPoll } from "@/features/background-agents/hooks/useChildAgentsPoll";
import { useChildAgentResults } from "@/features/background-agents/hooks/useChildAgentResults";
import { RightPanelResizeHandle } from "@/features/background-agents/components/RightPanelResizeHandle";
import { SidebarAgentRow } from "@/features/background-agents/components/SidebarAgentRow";
import { SidebarAutomationRow } from "@/features/background-agents/components/SidebarAutomationRow";
import { SectionHeader } from "@/features/background-agents/components/SectionHeader";
import { ApprovalsRow } from "@/features/background-agents/components/ApprovalsRow";
import { ParentBreadcrumb } from "@/features/background-agents/components/ParentBreadcrumb";
import { TodoSidebarSection } from "@/features/background-agents/components/TodoSidebarSection";
import { ContextPanel } from "@/features/context/components/ContextPanel";

export { isActiveBackgroundAgent } from "@/stores/background-agent-domain";
export { StatusDot } from "@/components/ui/StatusDot";
export { latestTodoListFromMessages, RIGHT_PANEL_WIDTH } from "@/features/background-agents/lib/panelConstants";

const RIGHT_PANEL_GUTTER = 16;
// Short rightward drift on hide — enough to give the fade/blur a direction
// without it reading as a full slide-back.
const RIGHT_PANEL_HIDE_DRIFT = 48;

const RECENT_AGENT_LIMIT = 6;

export function AgentRightSidebar({ sourcesPanel }: { sourcesPanel: ReactNode }) {
  const currentSessionId = useStore((s) => s.currentSessionId);
  const sessions = useStore((s) => s.sessions);
  const automations = useStore((s) => s.automations);
  const automationStatuses = useStore((s) => s.automationStream.statuses);
  const backgroundAgentRows = useStore((s) => s.backgroundAgents.rows);
  const openAutomations = useStore((s) => s.openAutomations);
  const todo = useStore((s) => latestTodoListFromMessages(s.order, s.messages));
  // Shared (in prefs) so the chat area can reflow to dock the panel.
  const collapsed = useStore((s) => s.prefs.rightPanelCollapsed);
  const rightPanelWidth = useStore((s) => s.prefs.rightPanelWidth);
  const setPref = useStore((s) => s.setPref);
  const rightInspectorTab = useStore((s) => s.rightInspectorTab);
  const setRightInspectorTab = useStore((s) => s.setRightInspectorTab);

  const toggleCollapsed = () => setPref("rightPanelCollapsed", !collapsed);

  // When viewing a child agent session, the roster is the *parent's* agents
  // (so the hub shows siblings + a back breadcrumb). Prefer the server's
  // immediate `parent_session_id`; fall back to parsing the child id
  // (`${parentId}::${hex}`, nestable) when the record isn't loaded yet.
  const currentSession = sessions.find((s) => s.session_id === currentSessionId);
  const inAgentSession =
    currentSession?.session_type === "agent" || isAgentSessionId(currentSessionId);
  const parentId = inAgentSession
    ? currentSession?.parent_session_id ?? parentSessionIdOf(currentSessionId)
    : null;
  const rosterSessionId = inAgentSession ? parentId : currentSessionId;
  const parentName = sessions.find((s) => s.session_id === parentId)?.name ?? null;

  useChildAgentsPoll(rosterSessionId);

  const workflows = useWorkflows(rosterSessionId);
  const dismissWorkflow = useStore((s) => s.dismissWorkflow);
  // A workflow's leaf agents are spawned with parent_id = the workflow's tool
  // call, so they share its parentToolCallId. They belong INSIDE the workflow
  // (its drill-in), not the top-level roster.
  const workflowParentIds = useMemo(
    () => new Set(workflows.map((w) => w.parentToolCallId).filter(Boolean)),
    [workflows],
  );

  const agents = useMemo(() => {
    const all = Object.values(backgroundAgentRows).filter(
      (agent) =>
        agent.sessionId === rosterSessionId &&
        !(agent.parentToolCallId && workflowParentIds.has(agent.parentToolCallId)),
    );
    const active = all
      .filter((agent) => isActiveAgentStatus(agent.status))
      .sort((a, b) => b.updatedAt - a.updatedAt);
    const terminal = all
      .filter((agent) => !isActiveAgentStatus(agent.status))
      .sort((a, b) => b.updatedAt - a.updatedAt);
    const recent = terminal.slice(0, RECENT_AGENT_LIMIT);
    // Always include the agent whose session we're viewing, even past the
    // recent cap — hoist it to the top of recent so the highlighted row isn't
    // stranded at the bottom.
    if (inAgentSession) {
      const current = terminal.find((agent) => agent.childSessionId === currentSessionId);
      if (current && !recent.includes(current)) recent.unshift(current);
    }
    return [...active, ...recent];
  }, [backgroundAgentRows, rosterSessionId, inAgentSession, currentSessionId, workflowParentIds]);

  const resultSnippets = useChildAgentResults(rosterSessionId, agents);

  // Dismissed keys (persisted in prefs) hide cards from the hub only — the
  // domain rows live on so the chat-trace card keeps its data, and the
  // UNfiltered `workflows` above still contains leaf agents via
  // workflowParentIds (a dismissed workflow's agents must not resurface as
  // roster orphans).
  const dismissedWorkflows = useStore((s) => s.prefs.dismissedWorkflows);
  const visibleWorkflows = useMemo(() => {
    const dismissed = new Set(dismissedWorkflows);
    return workflows.filter((w) => !dismissed.has(workflowKey(w.sessionId, w.workflowId)));
  }, [workflows, dismissedWorkflows]);

  const sortedWorkflows = useMemo(() => {
    const active = visibleWorkflows.filter(isActiveWorkflow).sort((a, b) => b.updatedAt - a.updatedAt);
    const done = visibleWorkflows
      .filter((w) => !isActiveWorkflow(w))
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, RECENT_AGENT_LIMIT);
    return [...active, ...done];
  }, [visibleWorkflows]);
  const runningWorkflowCount = visibleWorkflows.filter(isActiveWorkflow).length;

  const runningAutomations = useMemo(
    () =>
      (automations ?? []).filter(
        (a) =>
          a.running_since != null &&
          !isInternalAutomation(a) &&
          !isIterationLoop(a),
      ),
    [automations],
  );

  const approvalCount = useStore((s) => s.pendingApprovals.length);

  const runningAgentCount = agents.filter((agent) =>
    isActiveAgentStatus(agent.status),
  ).length;
  const hasBreadcrumb = inAgentSession && !!parentId;
  const hasTodo = todo != null;
  const hasAgents = agents.length > 0;
  const hasWorkflows = sortedWorkflows.length > 0;
  const hasAutomations = runningAutomations.length > 0;
  const sectionCount = [hasTodo, hasAgents, hasWorkflows, hasAutomations].filter(Boolean).length;
  const visible = hasTodo || hasAgents || hasWorkflows || hasAutomations;

  const todoOpenCount = todo?.items.filter((item) => item.status !== "completed").length ?? 0;
  const totalCount =
    agents.length + sortedWorkflows.length + runningAutomations.length + (todo?.items.length ?? 0);
  const activeCount =
    runningAgentCount + runningWorkflowCount + runningAutomations.length + todoOpenCount;

  return (
    <>
      {/* Fixed-position toggle — the single open/close control for the
          agent hub (mirror of `.sidebar-toggle`). Stays in viewport-fixed
          coords regardless of panel state; when open it floats over the
          panel header's right edge (z-panel-overlay 45 > panel z-40). The glyph swaps
          from dots (open me) to a right arrow (close → push the panel
          off-edge) so the control reads as a direct action. A running-
          status dot rides alongside when the panel is collapsed and work
          is active. Aligned with the macOS traffic lights (center y=25). */}
      <button
        type="button"
        onClick={toggleCollapsed}
        title={collapsed ? "Show inspector" : "Hide inspector"}
        aria-label={collapsed ? `Show inspector${totalCount > 0 ? ` (${totalCount})` : ""}` : "Hide inspector"}
        className="right-sidebar-toggle inline-flex items-center gap-1.5 h-[22px] px-1 rounded-md text-muted hover:bg-surface-soft hover:text-ink transition-[background-color,color,scale] duration-row ease-out active:scale-[0.96]"
      >
        {collapsed && activeCount > 0 && <StatusDot status="running" pulse />}
        <BlurSwap swapKey={collapsed ? "open" : "close"} scaleFrom={0.25}>
          {collapsed ? (
            <MoreHorizontal size={ICON.MD} strokeWidth={2} />
          ) : (
            <ArrowRight size={ICON.MD} strokeWidth={2} />
          )}
        </BlurSwap>
      </button>

      {/* Panel — always rendered (preserves internal state). Asymmetric
          motion:
          • OPEN slides in from the right edge (x: off-edge → 0) with the
            same MOTION.route/EASE_EMPHASIZED recipe as App.tsx's left
            sidebar; opacity/blur are reset instantly while still off-edge so
            the entrance is a clean slide, not a fade.
          • HIDE keeps it an opaque card that fades + blurs out while drifting
            a short distance right (direction without a full slide-back). It
            uses EASE_OUT, which front-loads the opacity fade so the card is
            visually gone before the chat's expanding edge slides under it —
            that's what prevents the overlap. The chat's right-inset reflow is
            matched to the SAME duration AND the SAME EASE_OUT curve so the two
            read as one synchronized motion. */}
      <motion.aside
        initial={false}
        animate={
          collapsed
            ? { x: RIGHT_PANEL_HIDE_DRIFT, opacity: 0, filter: "blur(6px)" }
            : { x: [rightPanelWidth, 0], opacity: 1, filter: "blur(0px)" }
        }
        transition={
          collapsed
            ? { duration: DURATION_RIGHT_PANEL_HIDE, ease: EASE_OUT }
            : {
                x: { duration: MOTION.route, ease: EASE_EMPHASIZED },
                opacity: { duration: 0 },
                filter: { duration: 0 },
              }
        }
        style={{
          width: `calc(var(--right-panel-width, ${RIGHT_PANEL_WIDTH}px) - ${RIGHT_PANEL_GUTTER}px)`,
          pointerEvents: collapsed ? "none" : "auto",
        }}
        aria-hidden={collapsed}
        inert={collapsed ? true : undefined}
        className="surface-panel surface-radius-md absolute top-2 bottom-2 right-2 z-40 flex flex-col overflow-hidden"
      >
        {/* Drag region height tuned so the inspector tabs' vertical
            center sits at viewport y=25 — same eye-line as the fixed dots
            toggle and the macOS traffic-light center. (panel top-2 = 8px,
            label centered in h-[34px] → 8 + 17 = 25.) The dots toggle
            (z-panel-overlay 45, fixed right-14) floats over this header's right edge and
            is the single open/close control — no redundant in-panel X. */}
        <div className="drag-spacer flex items-center px-3 h-[34px] shrink-0">
          <Tabs
            value={rightInspectorTab}
            onChange={(value) => setRightInspectorTab(value as "activity" | "sources" | "context")}
            variant="underline"
            label="Inspector sections"
            className="h-full items-center gap-4"
          >
            <Tab
              id="right-inspector-activity-tab"
              value="activity"
              className="inline-flex h-[34px] items-center text-xs font-medium text-muted transition-colors hover:text-ink data-[active=true]:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Activity
            </Tab>
            <Tab
              id="right-inspector-sources-tab"
              value="sources"
              className="inline-flex h-[34px] items-center text-xs font-medium text-muted transition-colors hover:text-ink data-[active=true]:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Sources
            </Tab>
            <Tab
              id="right-inspector-context-tab"
              value="context"
              className="inline-flex h-[34px] items-center text-xs font-medium text-muted transition-colors hover:text-ink data-[active=true]:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Context
            </Tab>
          </Tabs>
        </div>
        <RightPanelResizeHandle />
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex-1 min-h-0 overflow-y-auto scroll-thin px-3 pb-3 pt-1">
            <ScrollFadeTop key={rightInspectorTab} />
            <div
              id="right-inspector-activity-panel"
              role="tabpanel"
              aria-labelledby="right-inspector-activity-tab"
              hidden={rightInspectorTab !== "activity"}
              className="space-y-3"
            >
              <Collapse open={hasBreadcrumb}>
                {parentId && <ParentBreadcrumb parentId={parentId} parentName={parentName} />}
              </Collapse>
              <ApprovalsRow />
              {todo && (
                <TodoSidebarSection
                  key={currentSessionId ?? "none"}
                  todo={todo}
                  sessionId={currentSessionId}
                />
              )}

              {hasAgents && (
                <section>
                  {(sectionCount > 1 || hasBreadcrumb) && (
                    <SectionHeader
                      label={hasBreadcrumb ? "Agents in this run" : "Agents"}
                      count={agents.length}
                    />
                  )}
                  <div className="relative">
                    <AnimatePresence initial={false} mode="popLayout">
                      {agents.map((agent) => (
                        <motion.div key={`${agent.sessionId}:${agent.taskId}`} {...rosterRowMotion}>
                          <SidebarAgentRow
                            agent={agent}
                            resultPreview={resultSnippets[agent.taskId]}
                            active={inAgentSession && agent.childSessionId === currentSessionId}
                          />
                        </motion.div>
                      ))}
                    </AnimatePresence>
                  </div>
                </section>
              )}

              {hasWorkflows && (
                <section>
                  {(sectionCount > 1 || hasBreadcrumb) && (
                    <SectionHeader label="Workflows" count={sortedWorkflows.length} />
                  )}
                  <div className="relative">
                    <AnimatePresence initial={false} mode="popLayout">
                      {sortedWorkflows.map((wf) => (
                        <motion.div key={wf.workflowId} {...rosterRowMotion}>
                          <div className="group/wfrow relative py-0.5">
                            <ExpandableWorkflowCard workflow={wf} />
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                dismissWorkflow(wf.sessionId, wf.workflowId);
                              }}
                              title="Dismiss"
                              className="absolute -right-1 -top-1 grid place-items-center w-5 h-5 rounded-full border border-line-soft bg-surface text-faint opacity-0 transition-[opacity,color,scale] duration-row ease-out hover:text-bad active:scale-[0.97] group-hover/wfrow:opacity-100"
                            >
                              <X size={ICON.XS} strokeWidth={2} />
                            </button>
                          </div>
                        </motion.div>
                      ))}
                    </AnimatePresence>
                  </div>
                </section>
              )}

              {hasBreadcrumb && !hasAgents && (
                <p className="px-3 py-2 text-center text-xs text-muted">
                  No other agents in this run.
                </p>
              )}

              {hasAutomations && (
                <section>
                  {sectionCount > 1 ? (
                    <button
                      type="button"
                      onClick={(e) => openAutomations(originFromEvent(e.currentTarget))}
                      className="block w-full text-left hover:text-ink transition-colors duration-row ease-out"
                      title="Open automations"
                    >
                      <SectionHeader
                        label="Automations"
                        count={runningAutomations.length}
                      />
                    </button>
                  ) : null}
                  <div className="relative">
                    <AnimatePresence initial={false} mode="popLayout">
                      {runningAutomations.map((automation) => (
                        <motion.div key={automation.task_id} {...rosterRowMotion}>
                          <SidebarAutomationRow
                            automation={automation}
                            streamStatus={automationStatuses[automation.task_id]}
                          />
                        </motion.div>
                      ))}
                    </AnimatePresence>
                  </div>
                </section>
              )}

              <AnimatePresence initial={false}>
                {!visible && approvalCount === 0 && !hasBreadcrumb && (
                  <motion.div
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, filter: "blur(3px)", transition: { duration: MOTION.fast, ease: EASE_OUT } }}
                    transition={{ duration: MOTION.panel, ease: EASE_EMPHASIZED }}
                    className="grid min-h-[120px] place-items-center"
                  >
                    <EmptyState size="sm" icon={Bot}>
                      No agents yet.
                      <br />
                      Background agents you start appear here.
                    </EmptyState>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
            {rightInspectorTab === "sources" && !collapsed && (
              <div
                id="right-inspector-sources-panel"
                role="tabpanel"
                aria-labelledby="right-inspector-sources-tab"
              >
                {sourcesPanel}
              </div>
            )}
            {rightInspectorTab === "context" && !collapsed && (
              <div
                id="right-inspector-context-panel"
                role="tabpanel"
                aria-labelledby="right-inspector-context-tab"
              >
                <ContextPanel />
              </div>
            )}
          </div>
        </div>
      </motion.aside>
    </>
  );
}
