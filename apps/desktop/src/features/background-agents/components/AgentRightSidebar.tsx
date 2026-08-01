import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  AiChat02,
  Folder,
  Maximize2,
  Minimize2,
  X,
  type ArdenIcon,
} from "@/components/icons";
import type { AreaAutomation, AreaDetail, AreaWorkEvent } from "@/api/areas";
import { switchSession } from "@/actions/sessions";
import { isInternalAutomation, isIterationLoop } from "@/lib/automationFilters";
import { EASE_EMPHASIZED, EASE_OUT, MOTION } from "@/lib/tokens/motion";
import { ICON } from "@/lib/icons";
import { useStore } from "@/stores";
import { isActiveAgentStatus, isAgentSessionId, parentSessionIdOf } from "@/lib/agentRun";
import { formatRelativePast } from "@/lib/format";
import { isActiveWorkflow, workflowKey } from "@/stores/workflow-domain";
import { ExpandableWorkflowCard } from "@/components/ui/WorkflowDetail";
import { EmptyState } from "@/components/ui/EmptyState";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { TabPanels, useTabDirection } from "@/components/ui/TabPanels";
import { Collapse } from "@/components/ui/Collapse";
import { SidebarToggle } from "@/components/ui/SidebarToggle";
import { PeekSurface } from "@/components/workspace/PeekSurface";
import { RIGHT_PANEL_WIDTH } from "@/features/background-agents/lib/panelConstants";
import { rosterRowMotion } from "@/features/background-agents/lib/rosterMotion";
import { useChildAgentsPoll } from "@/features/background-agents/hooks/useChildAgentsPoll";
import { childAgentResultKey, useChildAgentResults } from "@/features/background-agents/hooks/useChildAgentResults";
import { RightPanelResizeHandle } from "@/features/background-agents/components/RightPanelResizeHandle";
import { SidebarAgentRow } from "@/features/background-agents/components/SidebarAgentRow";
import { SidebarAutomationRow } from "@/features/background-agents/components/SidebarAutomationRow";
import { SectionHeader } from "@/features/background-agents/components/SectionHeader";
import { ApprovalsRow } from "@/features/background-agents/components/ApprovalsRow";
import { ParentBreadcrumb } from "@/features/background-agents/components/ParentBreadcrumb";
import { TodoSidebarSection } from "@/features/background-agents/components/TodoSidebarSection";

export { isActiveBackgroundAgent } from "@/stores/background-agent-domain";
export { RIGHT_PANEL_WIDTH } from "@/features/background-agents/lib/panelConstants";

const RECENT_AGENT_LIMIT = 6;
const INSPECTOR_SECTION_ORDER = ["activity", "sources"] as const;

type InspectorSection = (typeof INSPECTOR_SECTION_ORDER)[number];
type AreaHubScope = Pick<AreaDetail, "sessions" | "automations" | "work">;

export type AreaHubSource = { ref: string; event: AreaWorkEvent };

/** The Area detail owns the hub's session boundary. Do not fall back to the
 * currently open chat when this snapshot has not loaded yet. */
export function areaHubSessionIds(scope: AreaHubScope | null | undefined): string[] {
  return scope?.sessions.map((session) => session.session_id) ?? [];
}

/** `pendingApprovals` is only the current-chat projection. An Area Hub may
 * display it only when that current chat is explicitly Area-owned. */
export function areaHubApprovals<T>(
  scope: AreaHubScope | null | undefined,
  currentSessionId: string | null,
  approvals: readonly T[],
): readonly T[] {
  return currentSessionId && areaHubSessionIds(scope).includes(currentSessionId)
    ? approvals
    : [];
}

/** Area work events are the only durable source ownership returned by the
 * Area API. Keep refs in that boundary; the current chat's source panel is
 * intentionally never reused for Area details. */
export function areaHubSources(scope: AreaHubScope | null | undefined): AreaHubSource[] {
  const latestFirst = [...(scope?.work.events ?? [])].sort(
    (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
  );
  const sources = new Map<string, AreaHubSource>();
  for (const event of latestFirst) {
    for (const ref of event.source_refs) {
      if (ref && !sources.has(ref)) sources.set(ref, { ref, event });
    }
  }
  return [...sources.values()];
}

function areaAutomationStatus(automation: AreaAutomation): string {
  if (automation.running_since) return "running";
  if (automation.latest_run?.status) return automation.latest_run.status;
  return automation.enabled ? "scheduled" : "paused";
}

function areaAutomationDetail(automation: AreaAutomation): string {
  return automation.latest_run?.error
    ?? automation.latest_run?.result
    ?? automation.last_result
    ?? (automation.enabled ? "No run recorded." : "Paused.");
}

function AreaAutomationRow({
  automation,
  onOpen,
}: {
  automation: AreaAutomation;
  onOpen: () => void;
}) {
  const status = areaAutomationStatus(automation);
  return (
    <button
      type="button"
      className="board-inspector__hub-row"
      data-status={status}
      onClick={onOpen}
      title="Open automation"
    >
      <span className="board-inspector__hub-row-copy">
        <strong>{automation.name}</strong>
        <small>{areaAutomationDetail(automation)}</small>
      </span>
      <span className="board-inspector__hub-row-status">{status}</span>
    </button>
  );
}

function AreaSessionRows({ sessions }: { sessions: AreaHubScope["sessions"] }) {
  return (
    <div className="board-inspector__hub-list">
      {sessions.map((session) => (
        <button
          key={session.session_id}
          type="button"
          className="board-inspector__hub-row"
          onClick={() => void switchSession(session.session_id)}
        >
          <span className="board-inspector__hub-row-copy">
            <strong>{session.name || "Untitled session"}</strong>
            <small>Area session</small>
          </span>
          <span className="board-inspector__hub-row-status">open</span>
        </button>
      ))}
    </div>
  );
}

function AreaSourceRows({ sources }: { sources: readonly AreaHubSource[] }) {
  return (
    <div className="board-inspector__hub-list">
      {sources.map(({ ref, event }) => (
        <article key={ref} className="board-inspector__hub-row" data-area-source-ref={ref}>
          <Folder aria-hidden size={ICON.SM} className="shrink-0 text-faint" />
          <span className="board-inspector__hub-row-copy">
            <strong>{ref}</strong>
            <small>{event.summary}</small>
          </span>
          <time className="board-inspector__hub-row-status" dateTime={event.created_at}>
            {formatRelativePast(event.created_at)}
          </time>
        </article>
      ))}
    </div>
  );
}

function HubEmpty({ children, icon = AiChat02 }: { children: ReactNode; icon?: ArdenIcon }) {
  return (
    <div className="grid min-h-[120px] place-items-center">
      <EmptyState size="sm" icon={icon}>{children}</EmptyState>
    </div>
  );
}

export function AgentRightSidebar({
  mode,
  allowDocking,
  sourcesPanel,
  areaScope,
  compact = false,
}: {
  mode: "peek" | "docked" | "hub";
  allowDocking: boolean;
  sourcesPanel: ReactNode;
  areaScope?: AreaHubScope | null;
  compact?: boolean;
}) {
  const currentSessionId = useStore((s) => s.currentSessionId);
  const sessions = useStore((s) => s.sessions);
  const automations = useStore((s) => s.automations);
  const automationStatuses = useStore((s) => s.automationStream.statuses);
  const backgroundAgentRows = useStore((s) => s.backgroundAgents.rows);
  const workflowRows = useStore((s) => s.workflows.rows);
  const openAutomations = useStore((s) => s.openAutomations);
  // Session-level slot (hydrated on switch, updated by todo_updated events);
  // retired and dismissed lists are simply absent.
  const todo = useStore((s) =>
    s.currentSessionId ? s.sessionTodos[s.currentSessionId] ?? null : null,
  );
  const pendingApprovals = useStore((s) => s.pendingApprovals);
  // Shared (in prefs) so the chat area can reflow to dock the panel.
  const collapsed = useStore((s) =>
    mode === "hub" ? s.prefs.areaHubCollapsed : s.prefs.rightPanelCollapsed,
  );
  const setPref = useStore((s) => s.setPref);
  const rightInspectorTab = useStore((s) => s.rightInspectorTab);
  const setRightInspectorTab = useStore((s) => s.setRightInspectorTab);
  const dismissWorkflow = useStore((s) => s.dismissWorkflow);
  const [hubSection, setHubSection] = useState<InspectorSection>("activity");
  const [compactPanelOpen, setCompactPanelOpen] = useState(false);
  const effectiveCollapsed = compact ? !compactPanelOpen : collapsed;

  const toggleCollapsed = () => {
    if (compact) {
      setCompactPanelOpen((visible) => !visible);
    } else if (mode === "hub") setPref("areaHubCollapsed", !collapsed);
    else setPref("rightPanelCollapsed", !collapsed);
  };
  const closeInspector = useCallback(() => {
    if (compact) setCompactPanelOpen(false);
    else if (mode === "hub") setPref("areaHubCollapsed", true);
    else setPref("rightPanelCollapsed", true);
  }, [compact, mode, setPref]);
  const toggleDocked = () => setPref("rightPanelDocked", mode !== "docked");

  // When viewing a child agent session, the chat inspector shows the parent's
  // roster. Area Hub never uses this fallback: its open Area detail is the
  // only scope source.
  const currentSession = sessions.find((s) => s.session_id === currentSessionId);
  const inAgentSession =
    currentSession?.session_type === "agent" || isAgentSessionId(currentSessionId);
  const parentId = inAgentSession
    ? currentSession?.parent_session_id ?? parentSessionIdOf(currentSessionId)
    : null;
  const rosterSessionId = inAgentSession ? parentId : currentSessionId;
  const parentName = sessions.find((s) => s.session_id === parentId)?.name ?? null;

  const areaSessionIds = useMemo(() => areaHubSessionIds(areaScope), [areaScope]);
  const scopedSessionIds = mode === "hub"
    ? areaSessionIds
    : rosterSessionId ? [rosterSessionId] : [];
  const scopeKey = scopedSessionIds.join("\u0000");
  const scopedSessionSet = useMemo(() => new Set(scopedSessionIds), [scopeKey]);

  // Compact workspaces match the redesign's resting state: the inspector is
  // available from its viewport toggle, but never consumes the screen merely
  // because the persisted wide-layout preference was open.
  useEffect(() => {
    if (compact) setCompactPanelOpen(false);
  }, [compact, mode, scopeKey]);

  // Poll every Area-owned parent session while its hub is open. This keeps the
  // backing roster bounded to the Area rather than the last chat the user saw.
  useChildAgentsPoll(scopedSessionIds);

  const workflows = useMemo(
    () => Object.values(workflowRows).filter((workflow) => scopedSessionSet.has(workflow.sessionId)),
    [workflowRows, scopedSessionSet],
  );
  // A workflow's leaf agents belong inside its drill-in, not in the top-level
  // agent roster.
  const workflowParentIds = useMemo(
    () => new Set(workflows.map((workflow) => workflow.parentToolCallId).filter(Boolean)),
    [workflows],
  );

  const agents = useMemo(() => {
    const all = Object.values(backgroundAgentRows).filter(
      (agent) =>
        scopedSessionSet.has(agent.sessionId)
        && !(agent.parentToolCallId && workflowParentIds.has(agent.parentToolCallId)),
    );
    const active = all
      .filter((agent) => isActiveAgentStatus(agent.status))
      .sort((a, b) => b.updatedAt - a.updatedAt);
    const terminal = all
      .filter((agent) => !isActiveAgentStatus(agent.status))
      .sort((a, b) => b.updatedAt - a.updatedAt);
    const recent = terminal.slice(0, RECENT_AGENT_LIMIT);
    if (mode !== "hub" && inAgentSession) {
      const current = terminal.find((agent) => agent.childSessionId === currentSessionId);
      if (current && !recent.includes(current)) recent.unshift(current);
    }
    return [...active, ...recent];
  }, [backgroundAgentRows, currentSessionId, inAgentSession, mode, scopedSessionSet, workflowParentIds]);

  const resultSnippets = useChildAgentResults(scopeKey, agents);

  // Dismissed keys hide cards from this hub only. The domain rows stay alive
  // for their transcript surfaces, and workflow leaf agents do not resurface.
  const dismissedWorkflows = useStore((s) => s.prefs.dismissedWorkflows);
  const visibleWorkflows = useMemo(() => {
    const dismissed = new Set(dismissedWorkflows);
    return workflows.filter((workflow) => !dismissed.has(workflowKey(workflow.sessionId, workflow.workflowId)));
  }, [workflows, dismissedWorkflows]);
  const sortedWorkflows = useMemo(() => {
    const active = visibleWorkflows.filter(isActiveWorkflow).sort((a, b) => b.updatedAt - a.updatedAt);
    const done = visibleWorkflows
      .filter((workflow) => !isActiveWorkflow(workflow))
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, RECENT_AGENT_LIMIT);
    return [...active, ...done];
  }, [visibleWorkflows]);

  const runningAutomations = useMemo(
    () =>
      (automations ?? []).filter(
        (automation) =>
          automation.running_since != null
          && !isInternalAutomation(automation)
          && !isIterationLoop(automation),
      ),
    [automations],
  );
  const areaAutomations = areaScope?.automations ?? [];

  // Pending approvals are held by the active session projection. They are
  // actionable only when that session is one of the open Area's own sessions;
  // never leak a global/current-chat approval into the Area Hub.
  const scopedApprovals = mode === "hub"
    ? areaHubApprovals(areaScope, currentSessionId, pendingApprovals)
    : pendingApprovals;
  const currentSessionIsInArea = mode === "hub"
    && currentSessionId !== null
    && scopedSessionSet.has(currentSessionId);
  // Todos have the same current-session projection as approvals. Only retain
  // them when that session is explicitly owned by this Area.
  const areaTodo = currentSessionIsInArea ? todo : null;
  const areaSources = useMemo(() => areaHubSources(areaScope), [areaScope]);

  const hasBreadcrumb = mode !== "hub" && inAgentSession && !!parentId;
  const hasTodo = mode !== "hub" && todo != null;
  const hasAgents = agents.length > 0;
  const hasWorkflows = sortedWorkflows.length > 0;
  const hasAutomations = mode === "hub" ? areaAutomations.length > 0 : runningAutomations.length > 0;
  const sectionCount = [hasTodo, hasAgents, hasWorkflows, hasAutomations].filter(Boolean).length;
  const visible = hasTodo || hasAgents || hasWorkflows || hasAutomations;
  const hasAreaSessions = (areaScope?.sessions.length ?? 0) > 0;
  const hasAreaActivity = scopedApprovals.length > 0
    || areaTodo !== null
    || hasAreaSessions
    || hasAgents
    || hasWorkflows
    || hasAutomations;
  const totalCount = scopedApprovals.length
    + agents.length
    + sortedWorkflows.length
    + (mode === "hub" ? areaAutomations.length : runningAutomations.length)
    + (mode === "hub" ? areaTodo?.items.length ?? 0 : hasTodo ? todo?.items.length ?? 0 : 0);
  const inspectorDirection = useTabDirection(INSPECTOR_SECTION_ORDER, rightInspectorTab);
  const hubDirection = useTabDirection(INSPECTOR_SECTION_ORDER, hubSection);

  const agentsList = hasAgents && (
    <div className="relative">
      <AnimatePresence initial={false} mode="popLayout">
        {agents.map((agent) => (
          <motion.div key={`${agent.sessionId}:${agent.taskId}`} {...rosterRowMotion}>
            <SidebarAgentRow
              agent={agent}
              resultPreview={resultSnippets[childAgentResultKey(agent)]}
              active={mode !== "hub" && inAgentSession && agent.childSessionId === currentSessionId}
            />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );

  const workflowsList = hasWorkflows && (
    <div className="relative">
      <AnimatePresence initial={false} mode="popLayout">
        {sortedWorkflows.map((workflow) => (
          <motion.div key={`${workflow.sessionId}:${workflow.workflowId}`} {...rosterRowMotion}>
            <div className="group/wfrow relative py-0.5">
              <ExpandableWorkflowCard workflow={workflow} />
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  dismissWorkflow(workflow.sessionId, workflow.workflowId);
                }}
                title="Dismiss"
                className="absolute -right-1 -top-1 grid place-items-center w-5 h-5 rounded-[var(--r-control)] border border-line-soft bg-surface text-faint opacity-0 transition-[opacity,color,scale] duration-row ease-out hover:text-bad active:scale-[0.97] group-hover/wfrow:opacity-100"
              >
                <X size={ICON.XS} />
              </button>
            </div>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );

  return (
    <>
      {/* The inspector is a rail like any other, so it uses the shared control
          rather than a hand-rolled button: same geometry, same swap. Its glyph
          used to be the closing one in both states — telling the user to close
          a panel that was already closed. */}
      <SidebarToggle
        side="right"
        hidden={effectiveCollapsed}
        onToggle={toggleCollapsed}
        labels={{
          hide: "Hide inspector",
          show: totalCount > 0 ? `Show inspector (${totalCount})` : "Show inspector",
          shortcut: false,
        }}
        className="board-inspector-toggle"
        data-inspector-trigger
      >
        {/* No status on the collapsed toggle — the icon alone. The active
            count lives in the tooltip ("Show inspector (N)") and inside the
            panel; a corner chip crowded the glyph and read as noise. */}
      </SidebarToggle>

      <PeekSurface
        open={!effectiveCollapsed}
        onClose={closeInspector}
        closeOnOutsidePointerDown={mode === "peek"}
        outsidePointerDownIgnoreSelector="[data-inspector-trigger]"
        ariaLabel={mode === "hub" ? "Area details" : "Conversation details"}
        layer={mode === "hub" ? "area-agent-hub" : "chat-inspector"}
        dataMode={mode}
        style={{
          width:
            mode === "hub" || mode === "peek"
              ? "var(--peek-width)"
              : `var(--inspector-dock-width, var(--right-panel-width, ${RIGHT_PANEL_WIDTH}px))`,
        }}
        className="board-inspector surface-peek absolute z-[var(--z-peek)] flex flex-col overflow-hidden"
      >
        <div className="board-inspector__header arden-peek-rule-below drag-spacer flex items-center shrink-0">
          {mode === "hub" ? (
            <Tabs
              value={hubSection}
              onChange={(value) => setHubSection(value as InspectorSection)}
              variant="underline"
              label="Area detail sections"
              className="h-full items-center gap-4"
            >
              <Tab
                id="area-hub-activity-tab"
                value="activity"
                className="inline-flex h-[44px] items-center text-xs font-medium text-muted transition-colors hover:text-ink data-[active=true]:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                Activity
              </Tab>
              <Tab
                id="area-hub-sources-tab"
                value="sources"
                className="inline-flex h-[44px] items-center text-xs font-medium text-muted transition-colors hover:text-ink data-[active=true]:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                Sources
              </Tab>
            </Tabs>
          ) : (
            <Tabs
              value={rightInspectorTab}
              onChange={(value) => setRightInspectorTab(value as "activity" | "sources")}
              variant="underline"
              label="Inspector sections"
              className="h-full items-center gap-4"
            >
              <Tab
                id="right-inspector-activity-tab"
                value="activity"
                className="inline-flex h-[44px] items-center text-xs font-medium text-muted transition-colors hover:text-ink data-[active=true]:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                Activity
              </Tab>
              <Tab
                id="right-inspector-sources-tab"
                value="sources"
                className="inline-flex h-[44px] items-center text-xs font-medium text-muted transition-colors hover:text-ink data-[active=true]:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                Sources
              </Tab>
            </Tabs>
          )}
          <div className="board-inspector__actions ml-auto flex items-center gap-0.5">
            {allowDocking && (
              <button
                type="button"
                className="board-inspector__action board-inspector__dock-action"
                onClick={toggleDocked}
                aria-label={mode === "docked" ? "Float details" : "Dock details as sidebar"}
                title={mode === "docked" ? "Float details" : "Dock as sidebar"}
              >
                {mode === "docked" ? (
                  <Minimize2 size={ICON.SM} />
                ) : (
                  <Maximize2 size={ICON.SM} />
                )}
              </button>
            )}
            <button
              type="button"
              className="board-inspector__action"
              onClick={closeInspector}
              aria-label={mode === "hub" ? "Close Area details" : "Close details"}
              title={mode === "hub" ? "Close Area details" : "Close details"}
            >
              <X size={ICON.SM} />
            </button>
          </div>
        </div>
        {mode === "docked" && <RightPanelResizeHandle />}
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="board-inspector__content flex-1 min-h-0 overflow-y-auto scroll-thin scroll-fade">
            {mode === "hub" ? (
              <TabPanels value={hubSection} direction={hubDirection} className="board-inspector__panel">
                {hubSection === "activity" ? (
                  <section
                    id="area-hub-activity-panel"
                    role="tabpanel"
                    aria-labelledby="area-hub-activity-tab"
                    className="board-inspector__activity board-inspector__hub-activity space-y-5"
                  >
                    {scopedApprovals.length > 0 && (
                      <section data-area-inspector-group="approvals">
                        <SectionHeader label="Approval" count={scopedApprovals.length} />
                        <ApprovalsRow approvals={scopedApprovals} variant="hub" />
                      </section>
                    )}
                    {areaTodo && (
                      <TodoSidebarSection
                        key={currentSessionId ?? "none"}
                        todo={areaTodo}
                        sessionId={currentSessionId}
                        label="Todo"
                      />
                    )}
                    {(hasAgents || hasAreaSessions) && (
                      <section data-area-inspector-group="agents">
                        <SectionHeader label="Agents" count={agents.length} />
                        {hasAreaSessions && areaScope && <AreaSessionRows sessions={areaScope.sessions} />}
                        {agentsList}
                      </section>
                    )}
                    {hasWorkflows && (
                      <section data-area-inspector-group="workflows">
                        <SectionHeader label="Workflows" count={sortedWorkflows.length} />
                        {workflowsList}
                      </section>
                    )}
                    {hasAutomations && (
                      <section data-area-inspector-group="automations">
                        <SectionHeader label="Automations" count={areaAutomations.length} />
                      <div className="board-inspector__hub-list">
                        {areaAutomations.map((automation) => (
                          <AreaAutomationRow
                            key={automation.task_id}
                            automation={automation}
                            onOpen={() => openAutomations()}
                          />
                        ))}
                      </div>
                      </section>
                    )}
                    {!hasAreaActivity && <HubEmpty>Nothing active in this Area.</HubEmpty>}
                  </section>
                ) : (
                  <section
                    id="area-hub-sources-panel"
                    role="tabpanel"
                    aria-labelledby="area-hub-sources-tab"
                    className="board-inspector__hub-panel"
                  >
                    <SectionHeader label="Sources" count={areaSources.length} />
                    {areaSources.length > 0 ? (
                      <>
                        <AreaSourceRows sources={areaSources} />
                        {areaSources[0] && (
                          <section className="board-inspector__hub-provenance" data-area-inspector-group="provenance">
                            <SectionHeader label="Provenance" />
                            <dl>
                              <div><dt>latest event</dt><dd>{areaSources[0].event.summary}</dd></div>
                              <div><dt>recorded</dt><dd>{formatRelativePast(areaSources[0].event.created_at)} ago</dd></div>
                            </dl>
                          </section>
                        )}
                      </>
                    ) : (
                      <HubEmpty icon={Folder}>No retained sources in this Area.</HubEmpty>
                    )}
                  </section>
                )}
              </TabPanels>
            ) : (
              <TabPanels value={rightInspectorTab} direction={inspectorDirection} className="board-inspector__panel">
                {rightInspectorTab === "activity" ? (
                  <div
                    id="right-inspector-activity-panel"
                    role="tabpanel"
                    aria-labelledby="right-inspector-activity-tab"
                    className="board-inspector__activity space-y-3"
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
                        {agentsList}
                      </section>
                    )}
                    {hasWorkflows && (
                      <section>
                        {(sectionCount > 1 || hasBreadcrumb) && (
                          <SectionHeader label="Workflows" count={sortedWorkflows.length} />
                        )}
                        {workflowsList}
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
                            onClick={() => openAutomations()}
                            className="block w-full text-left hover:text-ink transition-colors duration-row ease-out"
                            title="Open automations"
                          >
                            <SectionHeader label="Automations" count={runningAutomations.length} />
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
                      {!visible && scopedApprovals.length === 0 && !hasBreadcrumb && (
                        <motion.div
                          initial={{ opacity: 0, y: 4 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0, filter: "blur(3px)", transition: { duration: MOTION.fast, ease: EASE_OUT } }}
                          transition={{ duration: MOTION.panel, ease: EASE_EMPHASIZED }}
                          className="grid min-h-[120px] place-items-center"
                        >
                          <EmptyState size="sm" icon={AiChat02}>
                            No agents yet.
                            <br />
                            Background agents you start appear here.
                          </EmptyState>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                ) : (
                  <div
                    id="right-inspector-sources-panel"
                    role="tabpanel"
                    aria-labelledby="right-inspector-sources-tab"
                    className="board-inspector__sources"
                  >
                    {sourcesPanel}
                  </div>
                )}
              </TabPanels>
            )}
          </div>
        </div>
      </PeekSurface>
    </>
  );
}
