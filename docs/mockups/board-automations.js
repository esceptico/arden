(() => {
  const motion = window.BOARD_MOTION;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const compact = motion.geometry.maxWidthQuery("--breakpoint-compact");
  const DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];
  const MODEL_OPTIONS = Object.freeze(["session default", "claude-opus-4.6", "claude-sonnet-4.6", "gpt-5.6-sol"]);
  const DAY_RULER = Object.freeze({ width: 320, inset: 8, axisY: 34, snap: 5, intervalSnap: 15 });
  const DEFAULT_SCHEDULE = Object.freeze({ kind: "at", at: "09:00", every: "30m", days: "daily", windows: "", event: "approaching", lead: "15", channel: "", fromUser: "", keywords: "" });
  const CADENCE_STOPS = Object.freeze([5, 10, 15, 30, 60, 120, 180, 360, 720]);

  const automations = [
    {
      id: "inbox-brief", group: "Yours", name: "Inbox brief", state: "ready", stateLabel: "in 16h",
      crumb: "automations / yours", summary: "Sort overnight messages into decisions, replies, and quiet context.",
      prompt: "Read new Gmail and Slack messages since the previous run. Group only the items that need a decision or reply, explain why each matters, and save the brief to Memory.",
      schedule: { ...DEFAULT_SCHEDULE, kind: "at", label: "Weekdays at 8:30 AM", at: "08:30", days: "weekdays" },
      model: "session default", autoApprove: false, readOnly: false,
      runs: [
        { status: "ok", day: "today", time: "08:31", duration: "1m 42s", summary: "6 messages grouped · 2 need a reply", result: "<h2>Inbox brief</h2><p>Two messages need a reply. Four others were grouped as quiet context and saved to Memory.</p><ul><li>Ana — confirm the research outline.</li><li>Release channel — choose the final publish window.</li></ul>" },
        { status: "ok", day: "Mon", time: "08:30", duration: "1m 18s", summary: "4 messages grouped · nothing urgent", result: "<h2>Inbox brief</h2><p>Nothing urgent. Four messages were grouped for later review.</p>" },
        { status: "ok", day: "Fri", time: "08:30", duration: "1m 36s", summary: "8 messages grouped · 1 approval", result: "<h2>Inbox brief</h2><p>One approval and seven context updates.</p>" },
        { status: "failed", day: "Thu", time: "08:30", duration: "—", summary: "Gmail connection expired", result: "<h2>Run failed</h2><p>Gmail authorization expired before the brief could be assembled. Slack was not modified.</p>" },
      ],
    },
    {
      id: "release-publisher", group: "Yours", name: "Release note publisher", state: "failed", stateLabel: "failed",
      crumb: "automations / yours", summary: "Publish approved release notes and notify the release channel.",
      prompt: "When Tim posts an approved release note in #release, publish it to the shared workspace and send one confirmation message with the final link.",
      schedule: { ...DEFAULT_SCHEDULE, kind: "message", label: "#release from @tim", channel: "release", fromUser: "tim", keywords: "approved" },
      model: "claude-opus-4.6", autoApprove: true, readOnly: false,
      runs: [
        { status: "failed", day: "today", time: "11:04", duration: "38s", summary: "Notion permission was revoked", result: "<h2>Publish failed</h2><p>The shared page was not created because the Notion connection lost write access. No Slack message was sent.</p>" },
        { status: "ok", day: "Fri", time: "16:22", duration: "44s", summary: "Release 0.18 published and announced", result: "<h2>Release 0.18</h2><p>Published to the shared workspace and announced in #release.</p>" },
      ],
    },
    {
      id: "meeting-prep", group: "Yours", name: "Meeting prep", state: "ready", stateLabel: "in 2h",
      crumb: "automations / yours", summary: "Prepare the context and open questions before important calls.",
      prompt: "Fifteen minutes before an external meeting, assemble recent messages, related Memory pages, and the unresolved questions into one short brief.",
      schedule: { ...DEFAULT_SCHEDULE, kind: "event", label: "15 min before events", event: "approaching", lead: "15" },
      model: "session default", autoApprove: false, readOnly: false,
      runs: [
        { status: "ok", day: "Mon", time: "09:45", duration: "52s", summary: "MATS call brief saved to Memory", result: "<h2>MATS call brief</h2><p>Three recent decisions, two open questions, and the latest application context.</p>" },
      ],
    },
    {
      id: "market-steward", group: "Area agents", name: "Market launch steward", state: "running", stateLabel: "running",
      crumb: "automations / area agent", summary: "Keeps the Market launch area current and surfaces decisions that need you.",
      prompt: "Review new evidence in Market launch, update the area brief, and surface only decisions whose answer changes the next action.",
      schedule: { ...DEFAULT_SCHEDULE, kind: "every", label: "Every 2h · Daily", every: "2h", days: "daily" },
      model: "claude-opus-4.6", autoApprove: false, readOnly: false,
      runs: [
        { status: "running", day: "now", time: "", duration: "2m", summary: "Checking the latest launch evidence", result: "<h2>Still running</h2><p>Checking the latest launch evidence.</p>" },
        { status: "ok", day: "", time: "12:03", duration: "2m 14s", summary: "Audience decision surfaced", result: "<h2>Market launch</h2><p>The audience decision is the only item that needs you.</p>" },
        { status: "ok", day: "", time: "10:02", duration: "1m 58s", summary: "Area brief refreshed", result: "<h2>Market launch</h2><p>Area brief refreshed with two new evidence links.</p>" },
      ],
    },
    {
      id: "memory-reflection", group: "System", name: "Memory reflection", state: "ready", stateLabel: "tonight",
      crumb: "automations / system", summary: "Consolidates durable knowledge from recent work.",
      prompt: "Review recent sessions and durable records, consolidate repeated facts, and preserve provenance. Do not infer new personal facts without evidence.",
      schedule: { ...DEFAULT_SCHEDULE, kind: "at", label: "Daily at 2:00 AM", at: "02:00", days: "daily" },
      model: "claude-sonnet-4.6", autoApprove: true, readOnly: true,
      runs: [
        { status: "ok", day: "today", time: "02:01", duration: "3m 12s", summary: "12 records consolidated", result: "<h2>Memory reflection</h2><p>Twelve records consolidated. Three duplicates were linked rather than rewritten.</p>" },
        { status: "ok", day: "Mon", time: "02:01", duration: "2m 48s", summary: "8 records consolidated", result: "<h2>Memory reflection</h2><p>Eight records consolidated.</p>" },
      ],
    },
    {
      id: "memory-retention", group: "System", name: "Memory retention", state: "paused", stateLabel: "paused",
      crumb: "automations / system", summary: "Archives low-value derived memory after the retention window.",
      prompt: "Apply the configured retention policy to derived memory. Preserve sources, explicit user notes, and anything with unresolved references.",
      schedule: { ...DEFAULT_SCHEDULE, kind: "at", label: "Sundays at 3:00 AM", at: "03:00", days: "sun" },
      model: "session default", autoApprove: true, readOnly: true,
      runs: [
        { status: "ok", day: "Jul 12", time: "03:04", duration: "4m 18s", summary: "43 derived records archived", result: "<h2>Retention run</h2><p>Forty-three derived records were archived. Source records were preserved.</p>" },
      ],
    },
  ];

  const groupOrder = ["Yours", "Area agents", "System"];
  const rail = $(".automation-rail");
  const workspace = $(".workspace");
  const detail = $(".detail");
  const groupsRoot = $("[data-automation-groups]");
  const titleInput = $("[data-detail-title]");
  const promptInput = $("[data-prompt]");
  const pauseAction = $("[data-pause-action]");
  const runAction = $("[data-run-action]");
  const runLedger = $("[data-run-ledger]");
  const newTrigger = $(".new-trigger");
  const newMenu = $(".new-menu");
  const scheduleTrigger = $(".schedule-trigger");
  const triggerPeek = $(".trigger-peek");
  const modelTrigger = $(".model-trigger");
  const modelMenu = $(".model-menu");
  const resultPeek = $(".result-peek");
  const triggerPeekController = motion.peek.bind(triggerPeek, { closeButtons: [], escape: false });
  const resultPeekController = motion.peek.bind(resultPeek, { closeButtons: [], escape: false });
  const moreAction = $(".more-action");
  let selectedId = automations[0].id;
  let previousId = selectedId;
  let draft = null;
  let runningTimer = null;
  let stagedSchedule = null;
  let scheduleTab = "schedule";
  let lastScheduleKind = "at";
  let saveStateTimer = null;
  let scheduleTabsController = null;
  let scheduleSelectControllers = [];

  const selected = () => draft || automations.find(item => item.id === selectedId) || automations[0];
  function eventHits(event, selector) {
    return event.composedPath().some(node => node instanceof Element && node.matches(selector));
  }

  function renderRail(query = "") {
    const q = query.trim().toLowerCase();
    groupsRoot.replaceChildren(...groupOrder.map(label => {
      const items = automations.filter(item => item.group === label && (!q || `${item.name} ${item.summary}`.toLowerCase().includes(q)));
      if (!items.length) return document.createDocumentFragment();
      const section = document.createElement("section");
      section.className = "automation-group";
      section.innerHTML = `<div class="group-label"><span>${label}</span><span>${items.length}</span></div>`;
      const rows = document.createElement("div");
      rows.className = "automation-rows dp-row-stack";
      items.forEach(item => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "automation-row dp-dense-row";
        button.dataset.automationId = item.id;
        button.dataset.state = item.state;
        button.setAttribute("data-context-actions", `open:Open|run-now:Run now|duplicate:Duplicate|-|${item.state === "paused" ? "resume:Resume" : "pause:Pause"}`);
        button.dataset.contextLabel = item.name;
        button.setAttribute("aria-selected", String(!draft && item.id === selectedId));
        button.innerHTML = `<span><b>${item.name}</b><small>${item.schedule.label}</small></span><span class="row-state">${item.stateLabel}</span>`;
        button.addEventListener("click", () => selectAutomation(item.id));
        rows.append(button);
      });
      section.append(rows);
      return section;
    }));
  }

  function formatRunStamp(run) {
    return [run.day, run.time].filter(Boolean).join(" ");
  }

  function renderLedger(item) {
    if (!item.runs.length) {
      const empty = document.createElement("p");
      empty.className = "run-empty";
      empty.textContent = "No runs yet";
      runLedger.replaceChildren(empty);
      return;
    }
    runLedger.replaceChildren(...item.runs.slice(0, 5).map((run, index) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = `run-row ${run.status}`;
      row.dataset.runIndex = String(index);
      row.setAttribute("data-context-actions", "open:Open result|copy-link:Copy run link");
      row.dataset.contextLabel = run.summary;
      row.innerHTML = `<i class="run-status"></i><span class="run-day">${run.day}</span><time class="run-time">${run.time}</time><span class="run-summary">${run.summary}</span><span class="run-duration">${run.duration}</span><svg aria-hidden="true"><use href="#dp-chevron-down"/></svg>`;
      row.addEventListener("click", () => openResult(item, run));
      return row;
    }));
  }

  function renderDetail() {
    const item = selected();
    $("[data-detail-crumb]").textContent = draft ? "automations / new" : item.crumb;
    titleInput.value = item.name;
    titleInput.readOnly = Boolean(item.readOnly && !draft);
    $("[data-detail-summary]").textContent = item.summary;
    promptInput.value = item.prompt;
    promptInput.readOnly = Boolean(item.readOnly && !draft);
    $("[data-schedule-value]").textContent = item.schedule.label;
    $("[data-model-value]").textContent = item.model;
    $("[data-control-meta]").textContent = item.autoApprove ? "runs unattended" : "asks before writes";
    const autoSwitch = $(".approval-row .dp-switch");
    autoSwitch.setAttribute("aria-checked", String(item.autoApprove));
    $("[data-safety-warning]").hidden = !(item.schedule.kind === "message" && item.autoApprove && !item.schedule.fromUser);
    const paused = item.state === "paused";
    const running = item.state === "running";
    $(".t-text-swap", pauseAction).textContent = paused ? "Resume" : "Pause";
    runAction.disabled = paused || running || Boolean(draft);
    pauseAction.disabled = Boolean(draft);
    pauseAction.hidden = Boolean(draft);
    runAction.hidden = Boolean(draft);
    moreAction.hidden = Boolean(draft);
    $(".t-text-swap", runAction).textContent = running ? "Running" : "Run now";
    renderLedger(item);
    showSaveState(draft ? "Draft" : "", { persist: Boolean(draft) });
    $("[data-draft-cancel]").hidden = !draft;
    $("[data-draft-create]").hidden = !draft;
    $(".runs-section").hidden = Boolean(draft);
    syncModelOptions(item.model);
    renderRail($(".rail-search input").value);
  }

  async function selectAutomation(id) {
    if (id === selectedId && !draft) {
      if (compact.matches) document.body.classList.add("compact-detail");
      return;
    }
    const direction = automations.findIndex(item => item.id === id) >= automations.findIndex(item => item.id === selectedId) ? 1 : -1;
    previousId = selectedId;
    const commit = () => { draft = null; selectedId = id; renderDetail(); };
    if (compact.matches) {
      commit();
      requestAnimationFrame(() => document.body.classList.add("compact-detail"));
    } else {
      await motion.content.swap(detail, commit, { axis: "y", direction });
    }
  }

  function setPopover(root, trigger, panel, open, className) {
    closePopovers(panel);
    motion.popover.sync(root, trigger, panel, open, { className });
    if (!open) return;
    requestAnimationFrame(() => motion.popover.place(trigger, panel, { matchWidth: panel.classList.contains("schedule-select-menu") }));
  }

  function closePopovers(except = null) {
    if (newMenu !== except && newMenu.getAttribute("aria-hidden") === "false") motion.popover.sync(document.body, newTrigger, newMenu, false, { className: "new-open" });
    if (modelMenu !== except && modelMenu.getAttribute("aria-hidden") === "false") motion.popover.sync(document.body, modelTrigger, modelMenu, false, { className: "model-open" });
  }

  function draftFrom(kind) {
    const presets = {
      suggested: { name: "Prepare every meeting", summary: "Draft from a server-backed suggestion.", prompt: "Before an external meeting, gather recent messages, relevant Memory pages, and unresolved questions into one concise brief.", schedule: { kind: "event", label: "15 min before events", event: "approaching", lead: "15" } },
      standup: { name: "Daily standup", summary: "Draft from the Daily standup template.", prompt: "Summarize progress since yesterday, work planned today, and any blockers that need attention.", schedule: { ...DEFAULT_SCHEDULE, kind: "at", label: "Weekdays at 9:00 AM", at: "09:00", days: "weekdays" } },
      inbox: { name: "Inbox triage", summary: "Draft from the Inbox triage template.", prompt: "Group new messages into reply, decision, and quiet context. Surface only what needs attention.", schedule: { ...DEFAULT_SCHEDULE, kind: "at", label: "Daily at 8:30 AM", at: "08:30", days: "daily" } },
      scratch: { name: "Untitled automation", summary: "Define what should happen and when it should run.", prompt: "", schedule: { ...DEFAULT_SCHEDULE, kind: "at", label: "Daily at 9:00 AM", at: "09:00", days: "daily" } },
    };
    const preset = presets[kind];
    previousId = selectedId;
    draft = { ...preset, id: "draft", group: "Yours", state: "draft", stateLabel: "draft", crumb: "automations / new", model: "session default", autoApprove: false, readOnly: false, runs: [] };
    closePopovers();
    const commit = () => renderDetail();
    if (compact.matches) { commit(); requestAnimationFrame(() => document.body.classList.add("compact-detail")); }
    else void motion.content.swap(detail, commit, { axis: "x", direction: 1 });
  }

  function scheduleSelectField(label, name, value, options) {
    const selected = options.find(option => option.value === value) || options[0];
    return `<div class="schedule-field"><span>${label}</span><div class="schedule-select"><button class="schedule-select-trigger" type="button" role="combobox" aria-haspopup="listbox" aria-expanded="false" aria-label="${label}" data-schedule-field="${name}" data-value="${selected.value}"><span>${selected.label}</span><svg aria-hidden="true"><use href="#dp-chevron-down"/></svg></button><div class="schedule-select-menu dp-popover" role="listbox" aria-label="${label}" aria-hidden="true" inert>${options.map(option => `<button type="button" role="option" data-value="${option.value}" aria-selected="${option.value === selected.value}"><span>${option.label}</span><svg aria-hidden="true"><use href="#dp-check"/></svg></button>`).join("")}</div></div></div>`;
  }

  function closeScheduleSelects(except = null) {
    scheduleSelectControllers.forEach(({ root, trigger, menu }) => {
      if (menu !== except && menu.getAttribute("aria-hidden") === "false") {
        motion.popover.sync(root, trigger, menu, false, { className: "open" });
      }
    });
  }

  function disposeScheduleSelects() {
    closeScheduleSelects();
    scheduleSelectControllers.forEach(({ menu }) => menu.remove());
    scheduleSelectControllers = [];
  }

  function bindScheduleSelects(panel) {
    $$(".schedule-select", panel).forEach(root => {
      const trigger = $(".schedule-select-trigger", root);
      const menu = $(".schedule-select-menu", root);
      const options = $$('[role="option"]', menu);
      document.body.append(menu);
      scheduleSelectControllers.push({ root, trigger, menu });
      const open = () => {
        closeScheduleSelects(menu);
        setPopover(root, trigger, menu, true, "open");
        requestAnimationFrame(() => (options.find(option => option.getAttribute("aria-selected") === "true") || options[0])?.focus());
      };
      const close = () => motion.popover.sync(root, trigger, menu, false, { className: "open" });
      trigger.addEventListener("click", () => root.classList.contains("open") ? close() : open());
      trigger.addEventListener("keydown", event => {
        if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) { event.preventDefault(); open(); }
      });
      options.forEach(option => option.addEventListener("click", () => {
        const field = trigger.dataset.scheduleField;
        const value = option.dataset.value;
        trigger.dataset.value = option.dataset.value;
        $("span", trigger).textContent = $("span", option).textContent;
        options.forEach(item => item.setAttribute("aria-selected", String(item === option)));
        close();
        if (field === "repeat") {
          stagedSchedule.kind = value;
          lastScheduleKind = value;
          renderSchedulePanel();
          return;
        }
        if (field) stagedSchedule[field] = value;
        updateScheduleReceipt();
        trigger.focus();
      }));
      menu.addEventListener("keydown", event => {
        const index = options.indexOf(event.target);
        if (index < 0) return;
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          options[(index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length].focus();
        } else if (event.key === "Home" || event.key === "End") {
          event.preventDefault();
          options[event.key === "Home" ? 0 : options.length - 1].focus();
        } else if (event.key === "Escape") {
          event.preventDefault(); close(); trigger.focus();
        }
      });
    });
  }

  function timeToMinutes(value) {
    const match = String(value || "").match(/^(\d{1,2}):(\d{2})$/);
    if (!match) return null;
    const hours = Number(match[1]);
    const minutes = Number(match[2]);
    return hours <= 24 && minutes < 60 ? Math.min(1440, hours * 60 + minutes) : null;
  }

  function minutesToTime(value) {
    const minutes = Math.max(0, Math.min(1440, Math.round(value)));
    return `${String(Math.floor(minutes / 60) % 24).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
  }

  function intervalMinutes(value) {
    const match = String(value || "").trim().match(/^(\d+(?:\.\d+)?)\s*(m|min|h|d)$/i);
    if (!match) return null;
    const amount = Number(match[1]);
    const unit = match[2].toLowerCase();
    return unit === "h" ? amount * 60 : unit === "d" ? amount * 1440 : amount;
  }

  // windows live on a 24h circle: [a, b] with b < a wraps through midnight
  const modDay = minutes => ((minutes % 1440) + 1440) % 1440;
  const winDur = ([a, b]) => b > a ? b - a : 1440 - a + b;

  function parseWindows(value) {
    const out = [];
    String(value || "").split(",").forEach(part => {
      const [a, b] = part.split("-").map(timeToMinutes);
      if (a != null && b != null && modDay(a) !== modDay(b)) out.push([modDay(a), b >= 1440 ? 1440 : modDay(b)]);
    });
    return out.sort((a, b) => a[0] - b[0]);
  }

  function serializeWindows(list) {
    return list.map(([a, b]) => `${minutesToTime(a)}-${b >= 1440 ? "24:00" : minutesToTime(b)}`).join(",");
  }

  // circular coverage bitmap over 15-min slots, as in the radial reference:
  // overlaps merge, full coverage collapses to [] = "around the clock"
  function mergeWindows(list) {
    const cover = new Array(96).fill(false);
    for (const win of list) {
      const slots = Math.round(winDur(win) / 15);
      const first = Math.round(modDay(win[0]) / 15);
      for (let k = 0; k < slots; k++) cover[(first + k) % 96] = true;
    }
    const gap = cover.findIndex(slot => !slot);
    if (gap === -1) return [];
    const out = [];
    let runStart = -1;
    for (let k = 1; k <= 96; k++) {
      const i = (gap + k) % 96;
      if (cover[i] && runStart === -1) runStart = i;
      if (!cover[i] && runStart !== -1) {
        const end = i === 0 ? 1440 : i * 15;
        out.push([runStart * 15, end]);
        runStart = -1;
      }
    }
    return out.sort((a, b) => a[0] - b[0]);
  }

  function parseDays(value) {
    const normalized = String(value || "").trim().toLowerCase();
    if (!normalized || normalized === "daily" || normalized === "*") return new Set([0, 1, 2, 3, 4, 5, 6]);
    if (normalized === "weekdays") return new Set([0, 1, 2, 3, 4]);
    if (normalized === "weekends") return new Set([5, 6]);
    const days = new Set();
    normalized.split(",").forEach(part => {
      const index = DAY_NAMES.findIndex(day => part.trim().startsWith(day));
      if (index >= 0) days.add(index);
    });
    return days.size ? days : new Set([0, 1, 2, 3, 4, 5, 6]);
  }

  function serializeDays(days) {
    if (days.size === 7) return "daily";
    if (days.size === 5 && [0, 1, 2, 3, 4].every(day => days.has(day))) return "weekdays";
    if (days.size === 2 && days.has(5) && days.has(6)) return "weekends";
    return [...days].sort((a, b) => a - b).map(day => DAY_NAMES[day]).join(",");
  }

  function humanDays(value) {
    if (value === "daily" || value === "*") return "Daily";
    if (value === "weekdays") return "Weekdays";
    if (value === "weekends") return "Weekends";
    return value ? value.split(",").map(day => day[0].toUpperCase() + day.slice(1)).join(", ") : "Daily";
  }

  function compactHumanDays(value) {
    const days = [...parseDays(value)].sort((a, b) => a - b);
    if (days.length === 7) return "Daily";
    if (days.length === 5 && days.every((day, index) => day === index)) return "Weekdays";
    if (days.length === 2 && days[0] === 5 && days[1] === 6) return "Weekends";
    const labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const ranges = [];
    let start = days[0];
    let end = start;
    for (const day of days.slice(1)) {
      if (day === end + 1) end = day;
      else { ranges.push([start, end]); start = day; end = day; }
    }
    ranges.push([start, end]);
    return ranges.map(([first, last]) => first === last ? labels[first] : `${labels[first]}–${labels[last]}`).join(", ");
  }

  function formatTime12(value) {
    const minutes = timeToMinutes(value);
    if (minutes == null) return value;
    const hour = Math.floor(minutes / 60) % 24;
    const minute = String(minutes % 60).padStart(2, "0");
    return `${((hour + 11) % 12) + 1}:${minute} ${hour >= 12 ? "PM" : "AM"}`;
  }

  function dayControls(daysValue) {
    const days = parseDays(daysValue);
    const buttons = DAY_LETTERS.map((letter, index) => `<button type="button" data-day-index="${index}" aria-label="${DAY_NAMES[index]}" aria-pressed="${days.has(index)}">${letter}</button>`).join("");
    const weekdays = days.size === 5 && [0, 1, 2, 3, 4].every(day => days.has(day));
    return `<div class="schedule-days" role="group" aria-label="Days">${buttons}<span><button type="button" data-day-preset="daily" aria-pressed="${days.size === 7}">Daily</button><button type="button" data-day-preset="weekdays" aria-pressed="${weekdays}">Weekdays</button></span></div>`;
  }

  function runsPerDay(schedule) {
    const interval = intervalMinutes(schedule.every);
    if (!interval || interval <= 0) return "";
    const windows = parseWindows(schedule.windows);
    const total = windows.length
      ? windows.reduce((sum, win) => sum + Math.floor(winDur(win) / interval) + 1, 0)
      : Math.floor(1439 / interval) + 1;
    return `${total} runs / day`;
  }

  function cadenceLabel(minutes) {
    if (minutes < 60) return `${minutes}m`;
    if (minutes % 60 === 0) return `${minutes / 60}h`;
    return `${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, "0")}`;
  }

  function nextRunMinute(schedule) {
    const interval = intervalMinutes(schedule.every);
    if (!interval || interval <= 0) return null;
    const windows = parseWindows(schedule.windows);
    const spans = windows.length ? windows : [[0, 1440]];
    const now = new Date();
    const nowMin = now.getHours() * 60 + now.getMinutes();
    let best = null;
    for (const win of spans) {
      const duration = windows.length ? winDur(win) : 1439;
      for (let off = 0; off <= duration; off += interval) {
        const minute = modDay(win[0] + off);
        const wait = modDay(minute - nowMin) || 1440;
        if (!best || wait < best.wait) best = { minute, wait };
      }
    }
    return best ? best.minute : null;
  }


  function humanWindows(schedule) {
    const windows = parseWindows(schedule.windows);
    if (!windows.length) return "";
    return windows.map(([a, b]) => `${formatTime12(minutesToTime(a))}–${b >= 1440 ? "12:00 AM" : formatTime12(minutesToTime(b))}`).join(", ");
  }

  function rulerX(minutes) {
    return DAY_RULER.inset + Math.max(0, Math.min(1440, minutes)) / 1440 * (DAY_RULER.width - DAY_RULER.inset * 2);
  }

  function syncDayRuler(ruler) {
    const mode = stagedSchedule.kind === "every" ? "every" : "at";
    const at = timeToMinutes(stagedSchedule.at) ?? 540;
    ruler.dataset.mode = mode;
    const atMarker = ruler.querySelector(".day-ruler-at");
    atMarker?.style.setProperty("--ruler-x", `${rulerX(at) / DAY_RULER.width * 100}%`);
    const atLabel = ruler.querySelector(".day-ruler-at text");
    if (atLabel) {
      atLabel.textContent = minutesToTime(at);
      atLabel.setAttribute("text-anchor", at > 1260 ? "end" : "start");
      atLabel.setAttribute("x", at > 1260 ? "-9" : "9");
    }
    const windows = ruler._draft || parseWindows(stagedSchedule.windows);
    const fullDay = !windows.length;
    const spans = fullDay ? [[0, 1440]] : windows;
    const hot = ruler._hot;
    const interval = mode === "every" ? intervalMinutes(stagedSchedule.every) || 0 : 0;
    const windowsGroup = ruler.querySelector(".day-ruler-windows");
    if (windowsGroup) {
      windowsGroup.innerHTML = mode !== "every" ? "" : spans.map((win, index) => {
        const [a, b] = win;
        const wraps = b < a;
        const isHot = hot != null && hot.idx === index && !fullDay;
        // an overnight window is one window drawn as two band segments,
        // split at midnight; grips only at its true start and end
        const band = (x0, x1) => `<rect class="day-ruler-band" x="${x0}" y="28" width="${Math.max(0, x1 - x0)}" height="12"/>`;
        const bands = wraps ? band(rulerX(a), rulerX(1440)) + band(rulerX(0), rulerX(b)) : band(rulerX(a), rulerX(b));
        const grip = (x, part) => `<rect class="day-ruler-handle${isHot && hot.part === part ? " hot" : ""}" x="${x - 2}" y="26" width="4" height="16" rx="2"/>`;
        const handles = fullDay ? "" : grip(rulerX(a), "edge0") + grip(rulerX(b >= 1440 ? 1440 : b), "edge1");
        const labelMid = wraps
          ? (1440 - a > b ? (a + 1440) / 2 : b / 2)
          : (a + b) / 2;
        const label = isHot ? `<text x="${Math.max(24, Math.min(DAY_RULER.width - 24, rulerX(labelMid)))}" y="12" text-anchor="middle">${minutesToTime(a)}–${b >= 1440 ? "24:00" : minutesToTime(b)}${wraps ? " ↩" : ""}</text>` : "";
        return `<g class="day-ruler-win${isHot ? " hot" : ""}${fullDay ? " full" : ""}">${bands}${handles}${label}</g>`;
      }).join("");
    }
    const firings = ruler.querySelector(".day-ruler-firings");
    if (firings) {
      // a mark is a run, at its true minute — never resampled, no density
      // threshold. At tight cadences neighboring marks simply fuse into a
      // filled strip while the taller hour beats keep the rhythm readable
      const marks = [];
      if (interval > 0) {
        const next = nextRunMinute(stagedSchedule);
        for (const win of spans) {
          const duration = fullDay ? 1439 : winDur(win);
          for (let off = 0; off <= duration && marks.length < 400; off += interval) {
            const minute = fullDay ? off : modDay(win[0] + off);
            const isNext = minute === next;
            // skip marks that would stack under the edge grips
            if (!isNext && !fullDay && (off < 10 || duration - off < 10)) continue;
            const onHour = minute % 60 === 0;
            const cls = isNext ? ' class="next"' : onHour ? ' class="major"' : "";
            marks.push(`<line${cls} x1="${rulerX(minute)}" y1="${isNext ? 29 : onHour ? 30 : 31.5}" x2="${rulerX(minute)}" y2="${isNext ? 39 : onHour ? 38 : 36.5}"/>`);
          }
        }
      }
      firings.innerHTML = marks.join("");
    }
    ruler.setAttribute("aria-label", mode === "every"
      ? `Runs every ${stagedSchedule.every || "30m"}, ${fullDay ? "around the clock" : `during ${humanWindows(stagedSchedule)}`}. Drag on the ruler to add a window, drag edges to resize, double-click a window to remove it. Scroll or press up and down to change cadence.`
      : `Fires at ${minutesToTime(at)}. Click or drag to set the time, arrow keys to nudge.`);
  }

  function renderDayRuler(ruler) {
    if (!ruler.firstElementChild) {
      const hourTicks = Array.from({ length: 25 }, (_, hour) => {
        const x = rulerX(hour * 60);
        const major = hour % 6 === 0;
        return `<line class="day-ruler-tick${major ? " major" : ""}" x1="${x}" y1="38" x2="${x}" y2="${major ? 48 : 44}"/>${major ? `<text class="day-ruler-label" x="${x}" y="62" text-anchor="${hour === 0 ? "start" : hour === 24 ? "end" : "middle"}">${String(hour).padStart(2, "0")}</text>` : ""}`;
      }).join("");
      const now = new Date();
      const nowX = rulerX(now.getHours() * 60 + now.getMinutes());
      // "now" is context, not a control — a caret under the axis, never a
      // vertical stroke that could read as a window border
      ruler.innerHTML = `<svg class="day-ruler-svg" viewBox="0 0 ${DAY_RULER.width} 72" aria-hidden="true"><line class="day-ruler-axis" x1="${DAY_RULER.inset}" y1="${DAY_RULER.axisY}" x2="${DAY_RULER.width - DAY_RULER.inset}" y2="${DAY_RULER.axisY}"/>${hourTicks}<path class="day-ruler-now" d="M ${nowX} 46 l -3.5 5 h 7 z"/><g class="day-ruler-windows"></g><g class="day-ruler-firings"></g><g class="day-ruler-marker day-ruler-at day-ruler-motion"><line x1="0" y1="21" x2="0" y2="47"/><circle cy="34" r="4.5"/><text x="9" y="12"></text></g></svg>`;
    }
    ruler._hot = ruler._hot || null;
    ruler._draft = null;
    syncDayRuler(ruler);
    requestAnimationFrame(() => defaultCaption());
    const minuteAt = event => {
      const bounds = ruler.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
      return ratio * 1440;
    };
    const staged = () => parseWindows(stagedSchedule.windows);
    // ~10 CSS px of grab tolerance, expressed in minutes
    const tolerance = () => 1440 * 16 / Math.max(1, ruler.getBoundingClientRect().width);
    const viewY = event => {
      const bounds = ruler.getBoundingClientRect();
      return (event.clientY - bounds.top) / Math.max(1, bounds.height) * 72;
    };
    const caption = text => {
      const el = $("[data-ruler-caption]", ruler.closest(".day-ruler-editor"));
      if (el) el.textContent = text;
    };
    const defaultCaption = () => caption(stagedSchedule.kind === "every"
      ? "drag to add a window · scroll sets cadence"
      : "click or drag to set the time");
    const hitTest = (minute, y) => {
      if (stagedSchedule.kind !== "every") {
        const at = timeToMinutes(stagedSchedule.at) ?? 540;
        return Math.abs(minute - at) <= tolerance() ? { kind: "at" } : { kind: "track" };
      }
      const windows = staged();
      for (let index = 0; index < windows.length; index++) {
        for (const edge of [0, 1]) {
          if (Math.abs(minute - windows[index][edge]) <= tolerance()) return { kind: "edge", idx: index, edge };
        }
      }
      for (let index = 0; index < windows.length; index++) {
        if (modDay(minute - windows[index][0]) < winDur(windows[index])) return { kind: "body", idx: index };
      }
      return { kind: "track" };
    };
    const publish = () => syncScheduleTimeUI();
    const commitDraft = () => {
      const snap = DAY_RULER.intervalSnap;
      const cleaned = (ruler._draft || [])
        .map(([a, b]) => {
          const sa = modDay(Math.round(a / snap) * snap);
          const sb = Math.round(b / snap) * snap;
          return [sa, sb >= 1440 ? 1440 : modDay(sb)];
        })
        .filter(win => modDay(win[0]) !== modDay(win[1]) && winDur(win) >= snap);
      stagedSchedule.windows = serializeWindows(mergeWindows(cleaned));
      ruler._draft = null;
      publish();
    };
    let drag = null;
    let lastPress = { t: 0, idx: -1 };
    const apply = event => {
      const minute = minuteAt(event);
      const snap = stagedSchedule.kind === "every" ? DAY_RULER.intervalSnap : DAY_RULER.snap;
      const snapped = Math.round(minute / snap) * snap;
      if (!drag) return;
      if (drag.kind === "at") {
        stagedSchedule.at = minutesToTime(Math.min(snapped, 1435));
        publish();
        return;
      }
      const windows = ruler._draft;
      if (drag.kind === "create") {
        if (!drag.moved && Math.abs(minute - drag.downMin) < 6) return;
        drag.moved = true;
        const span = [Math.min(drag.downMin, minute), Math.max(drag.downMin, minute)];
        windows[drag.idx] = [Math.max(0, span[0]), Math.min(1440, span[1])];
        ruler._hot = { idx: drag.idx, part: "body" };
      } else if (drag.kind === "edge") {
        const win = windows[drag.idx];
        const clamped = Math.max(0, Math.min(1440, snapped));
        // an edge can cross midnight or the other edge freely — duration is
        // circular, so the window flips to overnight instead of dying
        win[drag.edge] = drag.edge === 0 ? modDay(clamped) : (clamped === 0 ? 1440 : clamped);
        ruler._hot = { idx: drag.idx, part: `edge${drag.edge}` };
        drag.moved = true;
      } else {
        // moving a window wraps through midnight instead of hitting a wall
        const start = modDay(Math.round((minute - drag.offset) / snap) * snap);
        const end = start + drag.span;
        windows[drag.idx] = [start, end > 1440 ? end - 1440 : end];
        ruler._hot = { idx: drag.idx, part: "body" };
        if (modDay(Math.abs(start - drag.startPos)) >= snap) drag.moved = true;
      }
      syncDayRuler(ruler);
    };
    ruler.onpointerdown = event => {
      const minute = minuteAt(event);
      const hit = hitTest(minute, viewY(event));
      if (stagedSchedule.kind !== "every") {
        drag = { kind: "at" };
      } else if (hit.kind === "body") {
        const t = performance.now();
        if (lastPress.idx === hit.idx && t - lastPress.t < 350) {
          lastPress = { t: 0, idx: -1 };
          const windows = staged();
          windows.splice(hit.idx, 1);
          stagedSchedule.windows = serializeWindows(windows);
          ruler._hot = null;
          publish();
          return;
        }
        lastPress = { t, idx: hit.idx };
        const windows = staged();
        ruler._draft = windows.map(win => [...win]);
        drag = { kind: "body", idx: hit.idx, offset: modDay(minute - windows[hit.idx][0]), span: winDur(windows[hit.idx]), startPos: windows[hit.idx][0], moved: false };
      } else if (hit.kind === "edge") {
        ruler._draft = staged().map(win => [...win]);
        drag = { kind: "edge", idx: hit.idx, edge: hit.edge, moved: false };
      } else {
        // empty track (or full-day coverage): drag sketches a new window
        const windows = stagedSchedule.windows ? staged() : [];
        ruler._draft = windows.map(win => [...win]);
        ruler._draft.push([minute, minute]);
        drag = { kind: "create", idx: ruler._draft.length - 1, downMin: minute, moved: false };
      }
      ruler.classList.add("is-dragging");
      try { ruler.setPointerCapture(event.pointerId); } catch { /* capture is an optimization, not a requirement */ }
      ruler.focus({ preventScroll: true });
      apply(event);
    };
    ruler.onpointermove = event => {
      if (drag && event.buttons === 0) { release(event); return; }
      if (drag) { apply(event); return; }
      const hit = hitTest(minuteAt(event), viewY(event));
      const hover = hit.kind === "edge" ? { idx: hit.idx, part: `edge${hit.edge}` }
        : hit.kind === "body" ? { idx: hit.idx, part: "body" }
        : null;
      if (JSON.stringify(hover) !== JSON.stringify(ruler._hot)) {
        ruler._hot = hover;
        syncDayRuler(ruler);
      }
      if (ruler.dataset.hover !== hit.kind) ruler.dataset.hover = hit.kind;
      if (stagedSchedule.kind === "every") {
        if (hit.kind === "body") caption("drag to move · double-click removes");
        else if (hit.kind === "edge") caption("drag to resize");
        else defaultCaption();
      }
    };
    ruler.onpointerleave = () => {
      delete ruler.dataset.hover;
      defaultCaption();
      if (ruler._hot) { ruler._hot = null; syncDayRuler(ruler); }
    };
    const release = event => {
      if (event?.pointerId != null) ruler.releasePointerCapture?.(event.pointerId);
      ruler.classList.remove("is-dragging");
      if (!drag) return;
      const wasCreateTap = drag.kind === "create" && !drag.moved;
      drag = null;
      ruler._hot = null;
      if (ruler._draft) {
        if (wasCreateTap) { ruler._draft = null; publish(); }
        else commitDraft();
      } else syncDayRuler(ruler);
    };
    ruler.onpointerup = release;
    ruler.onpointercancel = release;
    let wheelAcc = 0;
    ruler.onwheel = event => {
      if (stagedSchedule.kind !== "every") return;
      event.preventDefault();
      // trackpads stream tiny deltas — accumulate to one step per detent
      if (Math.sign(event.deltaY) !== Math.sign(wheelAcc)) wheelAcc = 0;
      wheelAcc += event.deltaY;
      if (Math.abs(wheelAcc) < 80) return;
      const direction = Math.sign(wheelAcc);
      wheelAcc = 0;
      const current = intervalMinutes(stagedSchedule.every) || 30;
      const next = direction > 0
        ? CADENCE_STOPS.find(stop => stop > current)
        : [...CADENCE_STOPS].reverse().find(stop => stop < current);
      if (!next) return;
      stagedSchedule.every = cadenceLabel(next);
      const input = $('[data-schedule-field="every"]', ruler.closest(".schedule-form"));
      if (input) input.value = stagedSchedule.every;
      publish();
    };
    ruler.onkeydown = event => {
      if ((event.key === "ArrowUp" || event.key === "ArrowDown") && stagedSchedule.kind === "every") {
        event.preventDefault();
        const current = intervalMinutes(stagedSchedule.every) || 30;
        const next = event.key === "ArrowUp"
          ? CADENCE_STOPS.find(stop => stop > current)
          : [...CADENCE_STOPS].reverse().find(stop => stop < current);
        if (!next) return;
        stagedSchedule.every = cadenceLabel(next);
        publish();
        return;
      }
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const step = (event.shiftKey ? 60 : DAY_RULER.snap) * (event.key === "ArrowLeft" ? -1 : 1);
      if (stagedSchedule.kind === "every") {
        const windows = staged();
        if (!windows.length) return;
        stagedSchedule.windows = serializeWindows(windows.map(win => {
          const a = modDay(win[0] + step);
          const b = a + winDur(win);
          return [a, b > 1440 ? b - 1440 : b];
        }));
      } else {
        const at = timeToMinutes(stagedSchedule.at) ?? 540;
        stagedSchedule.at = minutesToTime(Math.max(0, Math.min(1435, at + step)));
      }
      publish();
    };
  }

  function renderWindowList(panel) {
    const list = $("[data-window-list]", panel);
    if (!list || list.contains(document.activeElement)) return;
    const windows = parseWindows(stagedSchedule.windows);
    const markup = !windows.length
      ? `<p class="window-empty">Around the clock</p>`
      : windows.map(([a, b], index) => `<div class="window-row" role="listitem"><input value="${minutesToTime(a)}" data-window-index="${index}" data-window-edge="0" aria-label="Window ${index + 1} starts"><span aria-hidden="true">–</span><input value="${b >= 1440 ? "24:00" : minutesToTime(b)}" data-window-index="${index}" data-window-edge="1" aria-label="Window ${index + 1} ends"><button type="button" data-window-remove="${index}" aria-label="Remove window ${index + 1}"><svg aria-hidden="true"><use href="#dp-close"/></svg></button></div>`).join("");
    // rebuilding identical rows on every sync drops hover/focus and flickers
    if (list._markup === markup) return;
    list._markup = markup;
    list.innerHTML = markup;
    if (!windows.length) return;
    $$("input[data-window-edge]", list).forEach(input => input.addEventListener("change", () => {
      const current = parseWindows(stagedSchedule.windows);
      const win = current[Number(input.dataset.windowIndex)];
      const minute = timeToMinutes(input.value);
      if (!win || minute == null) { syncScheduleTimeUI(); return; }
      // end before start is a valid overnight window (e.g. 23:00–10:00)
      win[Number(input.dataset.windowEdge)] = minute;
      stagedSchedule.windows = serializeWindows(mergeWindows(current.filter(([a, b]) => modDay(a) !== modDay(b))));
      input.blur();
      syncScheduleTimeUI();
    }));
    $$("button[data-window-remove]", list).forEach(button => button.addEventListener("click", () => {
      const current = parseWindows(stagedSchedule.windows);
      current.splice(Number(button.dataset.windowRemove), 1);
      stagedSchedule.windows = serializeWindows(current);
      syncScheduleTimeUI();
    }));
  }

  function syncScheduleTimeUI() {
    const panel = $("[data-schedule-panel]");
    const ruler = $("[data-day-ruler]", panel);
    if (ruler?.firstElementChild) syncDayRuler(ruler);
    for (const field of ["at", "every"]) {
      const input = $(`[data-schedule-field="${field}"]`, panel);
      if (input && input !== document.activeElement) input.value = stagedSchedule[field];
    }
    const count = $(".schedule-mode-row em", panel);
    if (count) count.textContent = runsPerDay(stagedSchedule);
    renderWindowList(panel);
    updateScheduleReceipt();
  }

  function renderSchedulePanel() {
    const panel = $("[data-schedule-panel]");
    const s = stagedSchedule;
    disposeScheduleSelects();
    if (scheduleTab === "message") {
      panel.innerHTML = `<div class="schedule-form">
        <label><span>Channel</span><input data-schedule-field="channel" value="${s.channel || ""}" placeholder="release" aria-describedby="message-channel-hint"><small class="field-hint" id="message-channel-hint">Comma-separated · without # · e.g. release, launch</small></label>
        <label><span>From user</span><input data-schedule-field="fromUser" value="${s.fromUser || ""}" placeholder="anyone" aria-describedby="message-sender-hint"><small class="field-hint" id="message-sender-hint">Optional · without @ · e.g. tim</small></label>
        <label><span>Matching</span><input data-schedule-field="keywords" value="${s.keywords || ""}" placeholder="anything" aria-describedby="message-matching-hint"><small class="field-hint" id="message-matching-hint">Optional · comma-separated words or phrases</small></label>
      </div>`;
    } else if (scheduleTab === "event") {
      panel.innerHTML = `<div class="schedule-form">${scheduleSelectField("Calendar event", "event", s.event || "approaching", [{ value: "starts", label: "Starts" }, { value: "ends", label: "Ends" }, { value: "approaching", label: "Is approaching" }])}<label><span>Lead time</span><input data-schedule-field="lead" value="${s.lead || "15"}" inputmode="numeric"></label></div>`;
    } else {
      const mode = s.kind === "every" ? "every" : "at";
      const value = mode === "every" ? s.every : s.at;
      const preview = `<div class="day-ruler-editor"><div class="day-ruler" data-day-ruler tabindex="0" aria-label="24 hour schedule ruler"></div><div class="day-ruler-caption" data-ruler-caption aria-hidden="true"></div>${mode === "every" ? `<div class="ruler-window-fields"><span class="ruler-window-title">Active windows</span><div class="ruler-window-list" data-window-list role="list" aria-label="Active windows"></div></div>` : ""}</div>`;
      panel.innerHTML = `<div class="schedule-form schedule-time-form"><div class="schedule-mode-row"><span>Fires</span>${scheduleSelectField("Schedule mode", "repeat", mode, [{ value: "at", label: "at a time" }, { value: "every", label: "every" }])}<span>·</span><label class="schedule-token-field"><input data-schedule-field="${mode}" value="${value}" placeholder="${mode === "every" ? "30m" : "09:00"}" aria-label="${mode === "every" ? "Interval" : "Time"}"></label>${mode === "every" ? `<em>${runsPerDay(s)}</em>` : ""}</div>${preview}${dayControls(s.days)}</div>`;
    }
    bindScheduleSelects(panel);
    $$('input[data-schedule-field]', panel).forEach(field => field.addEventListener("input", () => {
      stagedSchedule[field.dataset.scheduleField] = field.value;
      syncScheduleTimeUI();
    }));
    // day toggles update state in place — a full panel re-render here makes
    // the whole editor flash and jump on every click
    const syncDayButtons = () => {
      const days = parseDays(stagedSchedule.days);
      $$('[data-day-index]', panel).forEach(button => button.setAttribute("aria-pressed", String(days.has(Number(button.dataset.dayIndex)))));
      const preset = $('[data-day-preset="daily"]', panel);
      if (preset) preset.setAttribute("aria-pressed", String(days.size === 7));
      const weekdaysBtn = $('[data-day-preset="weekdays"]', panel);
      if (weekdaysBtn) weekdaysBtn.setAttribute("aria-pressed", String(days.size === 5 && [0, 1, 2, 3, 4].every(day => days.has(day))));
      updateScheduleReceipt();
    };
    $$('[data-day-index]', panel).forEach(button => button.addEventListener("click", () => {
      const days = parseDays(stagedSchedule.days);
      const index = Number(button.dataset.dayIndex);
      if (days.has(index)) { if (days.size > 1) days.delete(index); } else days.add(index);
      stagedSchedule.days = serializeDays(days);
      syncDayButtons();
    }));
    $$('[data-day-preset]', panel).forEach(button => button.addEventListener("click", () => {
      stagedSchedule.days = button.dataset.dayPreset;
      syncDayButtons();
    }));
    const ruler = $("[data-day-ruler]", panel);
    if (ruler) renderDayRuler(ruler);
    renderWindowList(panel);
    updateScheduleReceipt();
  }

  function updateScheduleReceipt() {
    let label;
    let main;
    let meta;
    if (scheduleTab === "message") {
      label = stagedSchedule.channel ? `#${stagedSchedule.channel}${stagedSchedule.fromUser ? ` from @${stagedSchedule.fromUser}` : ""}` : "On Slack message";
      main = stagedSchedule.channel ? `#${stagedSchedule.channel}` : "Any Slack message";
      meta = stagedSchedule.fromUser ? `From @${stagedSchedule.fromUser}` : "Any sender";
    } else if (scheduleTab === "event") {
      label = `${stagedSchedule.lead || "15"} min before events`;
      main = `${stagedSchedule.lead || "15"} min before`;
      meta = "Calendar events";
    }
    else if (stagedSchedule.kind === "every") {
      const windows = humanWindows(stagedSchedule);
      label = `Every ${stagedSchedule.every || "30m"}${windows ? ` (${windows})` : ""} · ${humanDays(stagedSchedule.days)}`;
      main = `Every ${stagedSchedule.every || "30m"}`;
      meta = [windows, compactHumanDays(stagedSchedule.days)].filter(Boolean).join(" · ");
    } else {
      label = `${humanDays(stagedSchedule.days)} at ${formatTime12(stagedSchedule.at || "09:00")}`;
      main = formatTime12(stagedSchedule.at || "09:00");
      meta = compactHumanDays(stagedSchedule.days);
    }
    const receipt = $("[data-schedule-receipt]");
    receipt.dataset.label = label;
    $("[data-schedule-receipt-main]").textContent = main;
    $("[data-schedule-receipt-meta]").textContent = meta;
  }

  function openSchedule() {
    const item = selected();
    closePopovers();
    if (resultPeek.classList.contains("show")) closeResult();
    stagedSchedule = { ...DEFAULT_SCHEDULE, ...item.schedule };
    lastScheduleKind = stagedSchedule.kind === "every" ? "every" : "at";
    scheduleTab = item.schedule.kind === "message" ? "message" : item.schedule.kind === "event" ? "event" : "schedule";
    scheduleTabsController.select(scheduleTab, { emit: false, animate: false });
    renderSchedulePanel();
    scheduleTrigger.setAttribute("aria-expanded", "true");
    void triggerPeekController.open({ focus: false });
    requestAnimationFrame(() => {
      const ruler = $("[data-day-ruler]", triggerPeek);
      if (ruler) renderDayRuler(ruler);
    });
  }

  function closeSchedule() {
    closeScheduleSelects();
    stagedSchedule = null;
    scheduleTrigger.setAttribute("aria-expanded", "false");
    return triggerPeekController.close({ restoreFocus: false });
  }

  function saveSchedule() {
    const item = selected();
    const kind = scheduleTab === "message" ? "message" : scheduleTab === "event" ? "event" : stagedSchedule.kind;
    item.schedule = { ...stagedSchedule, kind, label: $("[data-schedule-receipt]").dataset.label };
    void closeSchedule();
    motion.textSwap.swap($("[data-schedule-value]"), item.schedule.label);
    renderRail($(".rail-search input").value);
    syncSafety();
    showSaveState("Saved");
  }

  function syncSafety() {
    const item = selected();
    $("[data-safety-warning]").hidden = !(item.schedule.kind === "message" && item.autoApprove && !item.schedule.fromUser);
    $("[data-control-meta]").textContent = item.autoApprove ? "runs unattended" : "asks before writes";
  }

  function openResult(item, run) {
    if (triggerPeek.classList.contains("show")) void closeSchedule();
    $("[data-result-title]").textContent = formatRunStamp(run);
    $("[data-result-body]").innerHTML = run.result;
    void resultPeekController.open({ focus: false });
  }

  function closeResult() {
    void resultPeekController.close({ restoreFocus: false });
  }

  function showToast(message) {
    window.BOARD_TOAST.show(message);
  }

  function showSaveState(message, { persist = false } = {}) {
    const state = $("[data-save-state]");
    clearTimeout(saveStateTimer);
    state.textContent = message;
    state.classList.toggle("show", Boolean(message));
    if (message && !persist) {
      saveStateTimer = setTimeout(() => state.classList.remove("show"), motion.duration.acknowledge);
    }
  }

  function syncModelOptions(model) {
    $$("[data-model-option]", modelMenu).forEach(option => {
      const selected = option.dataset.modelOption === model;
      option.setAttribute("aria-checked", String(selected));
      const check = $(".check", option);
      check?.toggleAttribute("hidden", !selected);
    });
  }

  function renderModelOptions() {
    const label = $(".menu-label", modelMenu);
    const options = MODEL_OPTIONS.map(model => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "dp-menu-item";
      button.setAttribute("role", "menuitemradio");
      button.dataset.modelOption = model;
      button.innerHTML = `<span>${model}</span><svg class="check" aria-hidden="true" hidden><use href="#dp-check"/></svg>`;
      return button;
    });
    modelMenu.replaceChildren(label, ...options);
  }

  renderModelOptions();

  newTrigger.addEventListener("click", event => {
    event.stopPropagation();
    const open = newMenu.getAttribute("aria-hidden") !== "false";
    setPopover(document.body, newTrigger, newMenu, open, "new-open");
  });
  $$('[data-new-preset]').forEach(button => button.addEventListener("click", () => draftFrom(button.dataset.newPreset)));
  scheduleTrigger.addEventListener("click", event => { event.stopPropagation(); openSchedule(); });
  $("[data-schedule-close]").addEventListener("click", closeSchedule);
  $("[data-schedule-cancel]").addEventListener("click", closeSchedule);
  $("[data-schedule-save]").addEventListener("click", saveSchedule);
  scheduleTabsController = motion.tabPanels.bind($(".schedule-tabs"), {
    body: $("[data-schedule-panel]"),
    tabSelector: "[data-tab-value]",
    indicatorSelector: ".dp-tab-indicator",
    indicatorClass: "dp-tab-indicator",
    activeClass: "on",
    render: value => {
      scheduleTab = value;
      if (value === "schedule" && !["at", "every"].includes(stagedSchedule.kind)) stagedSchedule.kind = lastScheduleKind;
      renderSchedulePanel();
    },
  });
  modelTrigger.addEventListener("click", event => {
    event.stopPropagation();
    const open = modelMenu.getAttribute("aria-hidden") !== "false";
    setPopover(document.body, modelTrigger, modelMenu, open, "model-open");
  });
  $$('[data-model-option]').forEach(option => option.addEventListener("click", () => {
    selected().model = option.dataset.modelOption;
    syncModelOptions(selected().model);
    closePopovers();
    motion.textSwap.swap($("[data-model-value]"), selected().model);
    showSaveState("Saved");
  }));
  $(".approval-row .dp-switch").addEventListener("click", event => {
    event.stopPropagation();
    const item = selected();
    item.autoApprove = !item.autoApprove;
    event.currentTarget.setAttribute("aria-checked", String(item.autoApprove));
    syncSafety();
    showSaveState("Saved");
  });

  pauseAction.addEventListener("click", () => {
    if (draft) return;
    const item = selected();
    item.state = item.state === "paused" ? "ready" : "paused";
    item.stateLabel = item.state === "paused" ? "paused" : item.schedule.kind === "message" ? "on msg" : "in 16h";
    motion.textSwap.swap($(".t-text-swap", pauseAction), item.state === "paused" ? "Resume" : "Pause");
    runAction.disabled = item.state === "paused";
    renderRail($(".rail-search input").value);
    showSaveState("Saved");
  });

  runAction.addEventListener("click", () => {
    const item = selected();
    if (draft || item.state === "paused" || item.state === "running" || runningTimer) return;
    item.state = "running"; item.stateLabel = "running";
    runAction.disabled = true;
    motion.iconSwap.swap(runAction, "#dp-stop");
    motion.textSwap.swap($(".t-text-swap", runAction), "Running");
    renderRail($(".rail-search input").value);
    runningTimer = setTimeout(async () => {
      const newRun = { status: "ok", day: "now", time: "", duration: "1m 06s", summary: "Brief updated · 2 decisions surfaced", result: "<h2>Run complete</h2><p>The brief was updated and two decisions were surfaced for review.</p>" };
      item.runs.unshift(newRun); item.state = "ready"; item.stateLabel = "in 16h";
      motion.iconSwap.swap(runAction, "#dp-check");
      motion.textSwap.swap($(".t-text-swap", runAction), "Completed");
      await motion.content.swap(runLedger, () => renderLedger(item), { axis: "y", direction: -1 });
      renderRail($(".rail-search input").value);
      showToast("Run complete · result added to the ledger");
      setTimeout(() => {
        motion.iconSwap.swap(runAction, "#dp-activity");
        motion.textSwap.swap($(".t-text-swap", runAction), "Run now");
        runAction.disabled = false;
      }, motion.duration.acknowledge);
      runningTimer = null;
    }, motion.duration.demoDelay * 2);
  });

  titleInput.addEventListener("input", () => { if (!titleInput.readOnly) selected().name = titleInput.value; });
  titleInput.addEventListener("blur", () => { renderRail($(".rail-search input").value); showSaveState(draft ? "Draft" : "Saved", { persist: Boolean(draft) }); });
  promptInput.addEventListener("input", () => { if (!promptInput.readOnly) selected().prompt = promptInput.value; });
  promptInput.addEventListener("blur", () => showSaveState(draft ? "Draft" : "Saved", { persist: Boolean(draft) }));
  $(".rail-search input").addEventListener("input", event => renderRail(event.currentTarget.value));

  $("[data-draft-cancel]").addEventListener("click", () => selectAutomation(previousId));
  $("[data-draft-create]").addEventListener("click", () => {
    const created = { ...draft, id: `custom-${automations.length + 1}`, state: "ready", stateLabel: "tomorrow", runs: [] };
    automations.splice(3, 0, created);
    draft = null; selectedId = created.id;
    renderDetail();
    showToast("Automation created");
  });

  $("[data-result-close]").addEventListener("click", closeResult);
  $(".compact-back").addEventListener("click", () => document.body.classList.remove("compact-detail"));
  $("#rail-toggle").addEventListener("click", () => {
    const hidden = !document.body.classList.contains("rail-hidden");
    motion.sidebar.sync({ root: document.body, className: "rail-hidden", hidden, sidebar: rail, dependents: [workspace] });
    motion.shellToggle.sync($("#rail-toggle"), { expanded: !hidden, expandedIcon: "#dp-panel-left-close", collapsedIcon: "#dp-panel-left-open", hideLabel: "Hide automation list", showLabel: "Show automation list", title: false });
  });
  motion.theme.bindToggle($(".theme-toggle"), { lightIcon: "#dp-sun", darkIcon: "#dp-moon" });

  document.addEventListener("pointerdown", event => {
    if (!eventHits(event, ".schedule-select,.schedule-select-menu")) closeScheduleSelects();
    if (eventHits(event, ".new-menu,.new-trigger,.trigger-peek,.schedule-trigger,.schedule-select-menu,.model-menu,.model-trigger,.result-peek")) return;
    closePopovers();
    if (triggerPeek.classList.contains("show")) void closeSchedule();
    if (resultPeek.classList.contains("show")) closeResult();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      if ($$(".schedule-select.open", triggerPeek).length) { closeScheduleSelects(); return; }
      closePopovers(); if (triggerPeek.classList.contains("show")) void closeSchedule(); if (resultPeek.classList.contains("show")) closeResult();
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f") { event.preventDefault(); $(".rail-search input").focus(); }
  });
  addEventListener("resize", closePopovers);

  renderRail();
  renderDetail();
})();
