(() => {
  const query = new URLSearchParams(location.search);
  if (query.get("theme")) document.documentElement.dataset.theme = query.get("theme");
  if (query.get("commandStates") === "true") document.body.dataset.commandStateDemo = "true";
  const motion = window.BOARD_MOTION;
  const room = document.getElementById("system-room");
  const scrim = document.getElementById("system-scrim");
  const surfaces = new Map([...document.querySelectorAll("[data-surface][role=dialog]")].map(element => [element.dataset.surface, element]));
  const openButtons = [...document.querySelectorAll("[data-open]")];
  const commandPalette = surfaces.get("command-palette");
  const commandInput = commandPalette.querySelector("[cmdk-input]");
  const commandItems = [...commandPalette.querySelectorAll("[cmdk-list] [cmdk-item]")];
  const commandEmpty = commandPalette.querySelector("[cmdk-empty]");
  const stateButtons = [...document.querySelectorAll("[data-command-state]")];
  const commandHelper = surfaces.get("command-helper");
  const helperStateButtons = [...document.querySelectorAll("[data-helper-state]")];
  const helperStatus = commandHelper.querySelector("[data-helper-status]");
  const helperComposer = commandHelper.querySelector(".helper-composer");
  const helperInput = helperComposer.querySelector("input");
  const helperAction = helperComposer.querySelector("[data-helper-action]");

  function syncHelperAction() {
    const stopsRun = commandHelper.dataset.running === "true" && !helperInput.value.trim();
    helperAction.dataset.mode = stopsRun ? "stop" : "send";
    helperAction.setAttribute("aria-label", stopsRun ? "Stop navigation helper" : "Send reply");
    motion.iconSwap.swap(helperAction, stopsRun ? "#dp-stop" : "#dp-arrow-up");
  }

  function setHelperStatus(label, running = false) {
    helperStatus.textContent = label;
    helperStatus.classList.toggle("dp-running-text", running);
  }

  function setHelperState(state) {
    commandHelper.querySelectorAll("[data-helper-panel]").forEach(panel => { panel.hidden = panel.dataset.helperPanel !== state; });
    helperStateButtons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.helperState === state)));
    const running = state === "running";
    setHelperStatus(state === "done" ? "Completed" : state === "approval" ? "Awaiting approval" : state === "choice" ? "Needs input" : "Running", running);
    commandHelper.dataset.running = String(running);
    syncHelperAction();
  }

  async function closeCommandHelper({ restoreFocus = true } = {}) {
    if (commandHelper.hidden) return;
    const trigger = commandHelper._trigger;
    await motion.surface.hide(commandHelper, {
      axis: "y",
      afterClose: () => {
        commandHelper.hidden = true;
        commandHelper.dataset.open = "false";
        document.body.dataset.helperDemo = "false";
        if (restoreFocus && trigger?.isConnected) trigger.focus();
      },
    });
  }

  async function openCommandHelper(trigger, state = "running") {
    if (motion.overlay.top()) await motion.overlay.closeTop({ restoreFocus: false });
    setHelperState(state);
    commandHelper._trigger = trigger;
    commandHelper.hidden = false;
    commandHelper.dataset.open = "true";
    document.body.dataset.helperDemo = "true";
    openButtons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.open === "command-helper")));
    syncScrim();
    await motion.surface.show(commandHelper, { axis: "y" });
  }

  function syncScrim() {
    const open = Boolean(motion.overlay.top());
    scrim.dataset.open = String(open);
    scrim.setAttribute("aria-hidden", String(!open));
  }

  async function openSurface(name, trigger, { stacked = false } = {}) {
    if (name === "command-helper") {
      await openCommandHelper(trigger);
      return;
    }
    await closeCommandHelper({ restoreFocus: false });
    if (!stacked && motion.overlay.top()) await motion.overlay.closeTop({ restoreFocus: false });
    const surface = surfaces.get(name);
    if (!surface) return;
    motion.overlay.open(surface, { trigger, background: room, axis: "y" });
    if (name === "command-palette") {
      setCommandState("default");
      commandInput.value = "";
      filterCommands();
    }
    openButtons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.open === name)));
    syncScrim();
  }

  openButtons.forEach(button => button.addEventListener("click", () => openSurface(button.dataset.open, button)));
  document.querySelectorAll("[data-stacked]").forEach(button => button.addEventListener("click", () => openSurface(button.dataset.stacked, button, { stacked: true })));
  document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", async () => { await motion.overlay.closeTop(); syncScrim(); }));
  document.querySelector("[data-helper-close]").addEventListener("click", () => closeCommandHelper());
  commandHelper.querySelectorAll("[data-helper-complete]").forEach(button => button.addEventListener("click", () => setHelperState("done")));
  commandHelper.querySelector("[data-helper-activity]").addEventListener("click", event => {
    const expanded = event.currentTarget.getAttribute("aria-expanded") === "true";
    event.currentTarget.setAttribute("aria-expanded", String(!expanded));
    commandHelper.querySelector("[data-helper-steps]").hidden = expanded;
  });
  helperComposer.addEventListener("submit", event => {
    event.preventDefault();
    const reply = helperInput.value.trim();
    if (!reply) {
      if (commandHelper.dataset.running !== "true") return;
      commandHelper.dataset.running = "false";
      setHelperStatus("Cancelled");
      syncHelperAction();
      return;
    }
    const panel = commandHelper.querySelector("[data-helper-panel]:not([hidden])");
    const bubble = document.createElement("p");
    bubble.className = "helper-user";
    bubble.textContent = reply;
    const response = document.createElement("div");
    response.className = "helper-assistant";
    response.innerHTML = "<p>Got it. Updating the route…</p>";
    panel.append(bubble, response);
    helperInput.value = "";
    setHelperStatus("Running", true);
    commandHelper.dataset.running = "true";
    syncHelperAction();
    motion.content.enter(bubble, { axis: "y" });
    motion.content.enter(response, { axis: "y" });
  });
  helperInput.addEventListener("keydown", event => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    helperComposer.requestSubmit();
  });
  helperInput.addEventListener("input", syncHelperAction);
  helperStateButtons.forEach(button => button.addEventListener("click", () => setHelperState(button.dataset.helperState)));
  scrim.addEventListener("click", async () => { await motion.overlay.closeTop(); syncScrim(); });
  document.addEventListener("keydown", event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (motion.overlay.top() === commandPalette) motion.overlay.closeTop().then(syncScrim);
      else openSurface("command-palette", document.querySelector('[data-open="command-palette"]'));
      return;
    }
    if (event.key === "Escape") {
      if (!motion.overlay.top() && !commandHelper.hidden) closeCommandHelper();
      requestAnimationFrame(syncScrim);
    }
  });

  function setCommandState(state) {
    stateButtons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.commandState === state)));
    commandPalette.querySelectorAll("[data-panel]").forEach(panel => { panel.hidden = panel.dataset.panel !== state; });
  }

  function visibleCommandItems() {
    return commandItems.filter(item => !item.hidden);
  }

  function selectCommand(item) {
    commandItems.forEach(command => command.toggleAttribute("data-selected", command === item));
    commandInput.setAttribute("aria-activedescendant", item?.id || "");
    item?.scrollIntoView({ block: "nearest" });
  }

  function filterCommands() {
    const term = commandInput.value.trim().toLowerCase();
    commandItems.forEach(item => { item.hidden = !item.dataset.value.includes(term); });
    commandPalette.querySelectorAll("[cmdk-group]").forEach(group => {
      group.hidden = !group.querySelector("[cmdk-item]:not([hidden])");
    });
    const visible = visibleCommandItems();
    commandEmpty.hidden = visible.length > 0;
    selectCommand(visible[0]);
  }

  commandInput.addEventListener("input", () => {
    setCommandState("default");
    filterCommands();
  });
  commandPalette.addEventListener("pointermove", event => {
    const item = event.target.closest("[cmdk-item]");
    if (item && !item.hidden) selectCommand(item);
  });
  commandPalette.addEventListener("keydown", event => {
    const items = visibleCommandItems();
    if (!items.length) return;
    const selected = commandPalette.querySelector("[cmdk-list] [cmdk-item][data-selected]");
    const index = Math.max(0, items.indexOf(selected));
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        selectCommand(items[(index + 1) % items.length]);
        break;
      case "ArrowUp":
        event.preventDefault();
        selectCommand(items[(index - 1 + items.length) % items.length]);
        break;
      case "Enter":
        event.preventDefault();
        if (event.metaKey || event.ctrlKey) openCommandHelper(commandInput);
        else selected?.click();
        break;
    }
  });

  stateButtons.forEach(button => button.addEventListener("click", () => setCommandState(button.dataset.commandState)));

  document.querySelector("[data-toast]").addEventListener("click", () =>
    window.BOARD_TOAST.show({
      title: "Changes approved",
      description: "The workflow can continue.",
      status: "done",
      tone: "success",
    }),
  );
  window.BOARD_MOTION.tooltip.bind(document);

  const initialSurface = surfaces.has(query.get("surface")) ? query.get("surface") : "command-palette";
  openSurface(initialSurface, document.querySelector(`[data-open="${initialSurface}"]`));
})();
