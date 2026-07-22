(() => {
  const query = new URLSearchParams(location.search);
  if (query.get("theme")) document.documentElement.dataset.theme = query.get("theme");

  const motion = window.BOARD_MOTION;
  const body = document.body;
  const sidebar = document.querySelector(".rail");
  const workspace = document.querySelector(".workspace");
  const sidebarToggle = document.getElementById("sidebar-toggle");
  const compactShell = motion?.geometry?.maxWidthQuery?.("--breakpoint-compact") || matchMedia("(max-width: 760px)");
  const inspector = document.getElementById("area-inspector");
  const inspectorToggle = document.getElementById("inspector-toggle");
  const inspectorClose = document.getElementById("inspector-close");
  const requestCard = document.querySelector("[data-request-card]");
  const requestRest = document.querySelector("[data-request-rest]");
  const requestPosition = document.querySelector("[data-request-position]");
  const inspectorTabs = document.querySelector(".dp-peek-tabs");
  const inspectorBody = document.querySelector(".inspector-body");

  const AREA_REQUESTS = [
    {
      id: "audience",
      kind: "Question",
      age: "52m",
      title: "Choose the launch brief audience",
      reason: "Developer-led teams show stronger intent; operations teams retain longer. Which signal should lead the first release?",
      primary: "Review",
      secondary: "Later",
    },
    {
      id: "publish",
      kind: "Approval",
      age: "18m",
      title: "Publish the evidence brief",
      reason: "One file update is ready. External announcements remain blocked until you approve it.",
      primary: "Approve",
      secondary: "Later",
    },
    {
      id: "notion",
      kind: "Sign-in",
      age: "31m",
      title: "Reconnect Notion",
      reason: "The publishing automation lost write access and needs you to reconnect Notion.",
      primary: "Sign in",
      secondary: "Later",
    },
  ];

  function requestRow(request) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "rest-row";
    row.dataset.requestId = request.id;
    const title = document.createElement("b");
    title.textContent = request.title;
    const meta = document.createElement("small");
    meta.className = "rest-meta";
    meta.dataset.columns = "2";
    const kind = document.createElement("span");
    kind.textContent = request.kind.toLowerCase();
    const separator = document.createElement("span");
    separator.className = "rest-meta-separator";
    separator.textContent = "·";
    const age = document.createElement("span");
    age.textContent = request.age;
    meta.append(kind, separator, age);
    row.append(title, meta);
    row.addEventListener("click", () => renderRequestQueue(request.id));
    return row;
  }

  function setRequestText(selector, next, animate) {
    const element = requestCard?.querySelector(selector);
    if (!element || element.textContent === next) return;
    if (animate && motion?.textSwap?.swap) motion.textSwap.swap(element, next);
    else element.textContent = next;
  }

  function renderRequestQueue(requestId, { animate = true } = {}) {
    const activeIndex = AREA_REQUESTS.findIndex(request => request.id === requestId);
    if (activeIndex < 0 || !requestCard || !requestRest) return;
    const active = AREA_REQUESTS[activeIndex];
    setRequestText("[data-request-kind]", active.kind, animate);
    setRequestText("[data-request-age]", `waiting for you · ${active.age}`, animate);
    setRequestText("[data-request-title]", active.title, animate);
    setRequestText("[data-request-reason]", active.reason, animate);
    setRequestText("[data-request-primary]", active.primary, animate);
    setRequestText("[data-request-secondary]", active.secondary, animate);
    if (requestPosition) {
      const nextPosition = `${activeIndex + 1} of ${AREA_REQUESTS.length}`;
      if (animate && motion?.textSwap?.swap) motion.textSwap.swap(requestPosition, nextPosition);
      else requestPosition.textContent = nextPosition;
    }
    requestRest.replaceChildren(...AREA_REQUESTS.filter(request => request.id !== active.id).map(requestRow));
  }

  motion?.theme?.bindToggle?.(document.querySelector(".theme-toggle"), { lightIcon: "#dp-sun", darkIcon: "#dp-moon" });
  motion?.sidebarResize?.bind?.(document.querySelector(".rail-resize"), { variable: "--sidebar-width", keyboard: true });

  const setSidebarHidden = (hidden, instant = false) => {
    if (motion?.sidebar && motion?.shellToggle) {
      motion.sidebar.sync({ root: body, className: "rail-hidden", hidden, sidebar, dependents: [workspace], instant });
      motion.shellToggle.sync(sidebarToggle, { expanded: !hidden, expandedIcon: "#dp-panel-left-close", collapsedIcon: "#dp-panel-left-open", animate: !instant });
    } else {
      body.classList.toggle("rail-hidden", hidden);
      sidebarToggle.setAttribute("aria-expanded", String(!hidden));
    }
  };
  sidebarToggle.addEventListener("click", () => setSidebarHidden(!body.classList.contains("rail-hidden")));
  const syncCompactShell = () => setSidebarHidden(compactShell.matches, true);
  compactShell.addEventListener?.("change", syncCompactShell);
  syncCompactShell();

  const inspectorController = motion.peek.bind(inspector, {
    triggers: [inspectorToggle],
    closeButtons: [inspectorClose],
    focusSelector: "#inspector-close",
  });

  renderRequestQueue("audience", { animate: false });
  motion.tabPanels.bind(inspectorTabs, {
    body: inspectorBody,
    variant: "line",
    tabSelector: ".dp-peek-tab",
    indicatorSelector: ".dp-peek-tab-indicator",
    indicatorClass: "dp-peek-tab-indicator",
    activeClass: "on",
  });

  if (query.get("details") === "open") inspectorController.open({ focus: false });
})();
