// The Board motion engine — originally ported verbatim from the retired
// docs/mockups/board-motion.js exploration. This is the canonical copy
// production runs; edit it directly.
(() => {
  const duration = Object.freeze({
    reduced: 1,
    instant: 10,
    press: 120,
    feedback: 150,
    exit: 160,
    popover: 180,
    focus: 180,
    dissolve: 220,
    peekEnter: 450,
    peekExit: 160,
    tabsLayout: 300,
    tabsSliding: 250,
    traceRow: 360,
    toolCallCadence: 1600,
    textSwap: 150,
    iconSwap: 250,
    counterSpin: 420,
    skeletonPulse: 1000,
    skeletonReveal: 400,
    skeletonSummaryDelay: 560,
    tabShift: 220,
    tabExit: 100,
    tabEnter: 120,
    iconMorph: 240,
    contentBlur: 260,
    instrument: 260,
    instrumentHideDelay: 250,
    furniture: 300,
    sidebarShow: 300,
    sidebarHide: 400,
    titleCleanup: 520,
    sheetCleanup: 260,
    refreshMorph: 220,
    refreshLoop: 700,
    refreshDoneHold: 650,
    holdArm: 650,
    deckExit: 160,
    deckEnter: 450,
    deckHandoff: 60,
    refreshRequest: 1050,
    refreshSettleMin: 80,
    refreshSettleMax: 320,
    loadingShowDelay: 250,
    loadingMinVisible: 450,
    tooltipDelay: 500,
    tooltipWarm: 300,
    previewShow: 300,
    previewHide: 200,
    acknowledge: 420,
    copyFeedback: 900,
    shimmer: 1800,
    demoDelay: 700,
    notificationDemoStart: 500,
    notificationDemoInterval: 800,
    toastLifetime: 4200,
    pageEnter: 240,
    pageChrome: 180,
    pageReduced: 160,
    pageStagger: 36,
    pageSkeletonHold: 180,
    pageSkeletonReveal: 220,
  });

  const distance = Object.freeze({
    swap: 8,
    traceRow: 4,
    deckExit: 72,
    deckDrop: 56,
    peekEnter: 16,
    peekExit: 12,
    sheetEnter: 24,
    sheetExit: 28,
    sidebarHide: 48,
    dissolve: 4,
    textSwap: 4,
    pageEnter: 6,
  });

  const blur = Object.freeze({
    swap: 2,
    traceRow: 2,
    title: 1.4,
    sidebar: 6,
    dissolve: 2,
    furniture: 1,
    iconSwap: 2,
    counter: 2,
    skeleton: 2,
  });

  const scale = Object.freeze({ iconSwapStart: .25 });
  const limits = Object.freeze({ traceTail: 3 });
  const progressiveBlurDefaults = Object.freeze({ layers: 8, intensity: .25 });

  const spring = Object.freeze({
    peek: Object.freeze({ type: "spring", stiffness: 210, damping: 26 }),
    sheet: Object.freeze({ type: "spring", stiffness: 360, damping: 32, mass: 0.75 }),
    layout: Object.freeze({ type: "spring", stiffness: 380, damping: 34, mass: 1 }),
    traceRow: Object.freeze({ type: "spring", stiffness: 350, damping: 40, mass: 0.8 }),
  });

  function springEasing({ stiffness, damping, mass = 1 }, durationMs, samples = 30) {
    const frequency = Math.sqrt(stiffness / mass);
    const ratio = damping / (2 * Math.sqrt(stiffness * mass));
    const values = Array.from({ length: samples + 1 }, (_, index) => {
      if (index === samples) return 1;
      const time = (index / samples) * durationMs / 1000;
      if (ratio < 1) {
        const damped = frequency * Math.sqrt(1 - ratio * ratio);
        const decay = Math.exp(-ratio * frequency * time);
        return 1 - decay * (
          Math.cos(damped * time) +
          (ratio * frequency / damped) * Math.sin(damped * time)
        );
      }
      if (Math.abs(ratio - 1) < 0.0001) {
        const decay = Math.exp(-frequency * time);
        return 1 - decay * (1 + frequency * time);
      }
      const root = frequency * Math.sqrt(ratio * ratio - 1);
      const slow = -ratio * frequency + root;
      const fast = -ratio * frequency - root;
      const response = (
        fast * Math.exp(slow * time) - slow * Math.exp(fast * time)
      ) / (fast - slow);
      return 1 - response;
    });
    return `linear(${values.map(value => value.toFixed(5)).join(", ")})`;
  }

  const surfaceRuns = new WeakMap();
  const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const surfaceTransform = (axis, value) => axis === "y" ? `translateY(${value}px)` : `translateX(${value}px)`;
  const hiddenSurfaceFrame = (distance, axis = "x") => ({
    opacity: "0",
    transform: surfaceTransform(axis, distance),
  });
  const visibleSurfaceFrame = axis => ({
    opacity: "1",
    transform: surfaceTransform(axis, 0),
  });

  function readSurfaceFrame(element) {
    const style = getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden") return null;
    return {
      opacity: style.opacity,
      transform: style.transform === "none" ? "translateX(0px)" : style.transform,
    };
  }

  function clearSurfacePresentation(element) {
    element.style.removeProperty("opacity");
    element.style.removeProperty("transform");
    element.style.removeProperty("filter");
    element.style.removeProperty("pointer-events");
  }

  function moveSurface(element, opening, options = {}) {
    if (!element) return Promise.resolve(false);

    const previous = surfaceRuns.get(element);
    const current = previous ? readSurfaceFrame(element) : null;
    previous?.animation.cancel();

    const token = Symbol("surface-motion");
    const axis = options.axis || "x";
    const offset = options.distance ?? (opening ? distance.peekEnter : distance.peekExit);
    const hidden = hiddenSurfaceFrame(offset, axis);
    const visible = visibleSurfaceFrame(axis);
    const run = { token, animation: null };
    surfaceRuns.set(element, run);

    if (opening) {
      options.beforeOpen?.();
      element.removeAttribute("inert");
      element.setAttribute("aria-hidden", "false");
    } else {
      options.beforeClose?.();
      element.style.pointerEvents = "none";
    }

    if (reducedMotion() || options.animate === false) {
      if (!opening) {
        options.afterClose?.();
        element.setAttribute("aria-hidden", "true");
        element.setAttribute("inert", "");
      }
      clearSurfacePresentation(element);
      surfaceRuns.delete(element);
      return Promise.resolve(true);
    }

    // Force the opening state to participate in layout before the first frame.
    element.getBoundingClientRect();
    const from = current || (opening ? hidden : visible);
    const to = opening ? visible : hidden;
    const runDuration = options.duration ?? (opening ? duration.peekEnter : duration.peekExit);
    const animation = element.animate([from, to], {
      duration: runDuration,
      easing: opening
        ? springEasing(options.spring || spring.peek, runDuration)
        : options.easing || "cubic-bezier(0.23, 1, 0.32, 1)",
      fill: "both",
    });
    run.animation = animation;

    return animation.finished.then(() => {
      if (surfaceRuns.get(element)?.token !== token) return false;
      if (!opening) {
        options.afterClose?.();
        element.setAttribute("aria-hidden", "true");
        element.setAttribute("inert", "");
      }
      animation.cancel();
      clearSurfacePresentation(element);
      surfaceRuns.delete(element);
      return true;
    }).catch(() => false);
  }

  const surface = Object.freeze({
    show: (element, options) => moveSurface(element, true, options),
    hide: (element, options) => moveSurface(element, false, options),
    cancel(element) {
      surfaceRuns.get(element)?.animation.cancel();
      surfaceRuns.delete(element);
      clearSurfacePresentation(element);
    },
  });

  const tabControllers = new WeakMap();

  function ensureTabIndicator(container, options = {}) {
    const selector = options.indicatorSelector || ":scope > .tab-indicator";
    let indicator = container.querySelector(selector);
    if (!indicator) {
      indicator = document.createElement("span");
      indicator.className = options.indicatorClass || "tab-indicator";
      indicator.setAttribute("aria-hidden", "true");
      container.prepend(indicator);
    }
    indicator.classList.add("dp-tab-indicator");
    return indicator;
  }

  function syncTabIndicator(container, target, options = {}) {
    const indicator = ensureTabIndicator(container, options);
    if (!target || !container.getClientRects().length || !target.getClientRects().length) {
      indicator.hidden = true;
      delete indicator.dataset.ready;
      return indicator;
    }
    const containerRect = container.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const x = targetRect.left - containerRect.left + container.scrollLeft;
    const y = targetRect.top - containerRect.top + container.scrollTop;
    const variant = options.variant || container.dataset.tabsVariant;
    const line = variant === "line";
    const lineSize = readCssLength("--tab-line-size", { element: container });
    const settle = reducedMotion() || options.animate === false || !indicator.dataset.ready;
    if (settle) indicator.style.transition = "none";
    indicator.hidden = false;
    indicator.style.width = `${targetRect.width}px`;
    indicator.style.height = `${line ? lineSize : targetRect.height}px`;
    indicator.style.transform = `translate(${x}px, ${line ? container.scrollTop + containerRect.height - lineSize : y}px)`;
    if (settle) {
      void indicator.offsetWidth;
      indicator.style.transition = "";
    }
    indicator.dataset.ready = "true";
    return indicator;
  }

  function syncExpandingTabLayout(container, target, options = {}) {
    const config = options.expanding || {};
    const indicator = ensureTabIndicator(container, options);
    const items = [...container.querySelectorAll(options.tabSelector || '[role="tab"]')];
    if (!target || !items.length || !container.getClientRects().length) {
      indicator.hidden = true;
      delete indicator.dataset.ready;
      return indicator;
    }
    const length = (option, token) => option ?? readCssLength(token, { element: container });
    const collapsedWidth = length(config.collapsedWidth, "--tab-control-size");
    const activeGap = length(config.activeGap, "--tab-active-gap");
    const horizontalPadding = length(config.horizontalPadding, "--tab-inline-padding");
    const expandedWidth = button => {
      const label = button.querySelector(config.labelSelector);
      const icon = button.querySelector(config.iconSelector);
      const range = label ? document.createRange() : null;
      if (range) range.selectNodeContents(label);
      const labelWidth = range?.getBoundingClientRect().width || label?.scrollWidth || 0;
      const iconWidth = icon?.getBoundingClientRect().width || icon?.width?.baseVal?.value || 0;
      range?.detach?.();
      return Math.ceil(labelWidth + iconWidth + activeGap + horizontalPadding * 2);
    };
    const expandedWidths = items.map(expandedWidth);
    const widths = items.map((button, index) => button === target ? expandedWidths[index] : collapsedWidth);
    const computed = getComputedStyle(container);
    const paddingLeft = parseFloat(computed.paddingLeft) || 0;
    const paddingRight = parseFloat(computed.paddingRight) || 0;
    const paddingTop = parseFloat(computed.paddingTop) || 0;
    const maxExpandedWidth = Math.max(...expandedWidths);
    const firstAnchor = paddingLeft + maxExpandedWidth / 2;
    const lastAnchor = container.clientWidth - paddingRight - maxExpandedWidth / 2;
    const anchorStep = items.length > 1 ? (lastAnchor - firstAnchor) / (items.length - 1) : 0;
    const anchors = items.map((_, index) => items.length > 1 ? firstAnchor + anchorStep * index : container.clientWidth / 2);
    const targetIndex = items.indexOf(target);
    const targetX = anchors[targetIndex] - widths[targetIndex] / 2;
    const settle = reducedMotion() || options.animate === false || !indicator.dataset.ready;
    const animated = [indicator, ...items];
    const transitions = settle ? animated.map(element => element.style.transition) : [];
    if (settle) animated.forEach(element => { element.style.transition = "none"; });
    items.forEach((button, index) => {
      button.style.left = `${anchors[index] - widths[index] / 2}px`;
      button.style.width = `${widths[index]}px`;
      const label = button.querySelector(config.labelSelector);
      if (label) label.style.width = button === target ? `${label.scrollWidth}px` : "0px";
    });
    indicator.hidden = false;
    indicator.style.width = `${widths[targetIndex]}px`;
    indicator.style.height = `${target.getBoundingClientRect().height}px`;
    indicator.style.transform = `translate(${targetX}px, ${paddingTop}px)`;
    if (settle) {
      void indicator.offsetWidth;
      animated.forEach((element, index) => { element.style.transition = transitions[index]; });
    }
    indicator.dataset.ready = "true";
    return indicator;
  }

  function bindTabs(container, options = {}) {
    if (!container) return null;
    tabControllers.get(container)?.destroy();
    container.classList.add("dp-tabs");
    if (options.variant) container.dataset.tabsVariant = options.variant;
    if (options.expanding) container.dataset.tabsExpanding = "";
    const tabSelector = options.tabSelector || '[role="tab"]';
    const activeClass = options.activeClass || "on";
    const valueAttribute = options.valueAttribute || "data-tab-value";
    const buttons = () => [...container.querySelectorAll(tabSelector)].map(button => {
      if (options.expanding?.contentClass && !button.querySelector(`:scope > .${options.expanding.contentClass}`)) {
        const content = document.createElement("span");
        content.className = options.expanding.contentClass;
        content.append(...button.children);
        button.append(content);
      }
      button.classList.add("dp-tab");
      if (options.expanding) {
        const label = button.querySelector(options.expanding.labelSelector);
        const icon = button.querySelector(options.expanding.iconSelector);
        label?.classList.add("dp-tab-label");
        icon?.classList.add("dp-tab-icon");
        label?.parentElement?.classList.add("dp-tab-content");
      }
      return button;
    });
    const valueOf = button => button?.getAttribute(valueAttribute);
    const selected = () => buttons().find(button => button.getAttribute("aria-selected") === "true" || button.classList.contains(activeClass));

    const select = (valueOrButton, selection = {}) => {
      const target = typeof valueOrButton === "string"
        ? buttons().find(button => valueOf(button) === valueOrButton)
        : valueOrButton;
      if (!target) return false;
      const previous = selected();
      buttons().forEach(button => {
        const active = button === target;
        button.classList.toggle(activeClass, active);
        button.setAttribute("aria-selected", String(active));
        button.tabIndex = active ? 0 : -1;
      });
      const syncIndicator = options.expanding ? syncExpandingTabLayout : syncTabIndicator;
      syncIndicator(container, target, { ...options, animate: selection.animate !== false });
      if (selection.focus) target.focus();
      if (selection.emit !== false && previous !== target) {
        options.onChange?.({
          value: valueOf(target),
          previousValue: valueOf(previous),
          button: target,
          previousButton: previous || null,
        });
      }
      return true;
    };

    const onClick = event => {
      const target = event.target.closest("button");
      if (!target || !buttons().includes(target)) return;
      select(target);
    };
    const onKeyDown = event => {
      const current = event.target.closest("button");
      if (!current || !buttons().includes(current)) return;
      const vertical = container.getAttribute("aria-orientation") === "vertical";
      const nextKey = vertical ? "ArrowDown" : "ArrowRight";
      const previousKey = vertical ? "ArrowUp" : "ArrowLeft";
      if (![nextKey, previousKey, "Home", "End"].includes(event.key)) return;
      const items = buttons().filter(button => !button.disabled);
      const index = items.indexOf(current);
      if (index < 0) return;
      event.preventDefault();
      const nextIndex = event.key === "Home" ? 0
        : event.key === "End" ? items.length - 1
        : event.key === nextKey ? (index + 1) % items.length
        : (index - 1 + items.length) % items.length;
      select(items[nextIndex], { focus: true });
    };
    const sync = ({ animate = false } = {}) => {
      const active = selected();
      if (active) {
        const syncIndicator = options.expanding ? syncExpandingTabLayout : syncTabIndicator;
        syncIndicator(container, active, { ...options, animate });
      }
    };

    container.addEventListener("click", onClick);
    container.addEventListener("keydown", onKeyDown);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => sync());
    observer?.observe(container);
    if (!options.expanding) buttons().forEach(button => observer?.observe(button));
    const controller = {
      select,
      sync,
      destroy() {
        container.removeEventListener("click", onClick);
        container.removeEventListener("keydown", onKeyDown);
        observer?.disconnect();
        if (tabControllers.get(container) === controller) tabControllers.delete(container);
      },
    };
    tabControllers.set(container, controller);
    document.fonts?.ready.then(() => {
      if (tabControllers.get(container) === controller) sync();
    });
    requestAnimationFrame(() => {
      const initial = selected() || buttons()[0];
      if (initial) select(initial, { emit: false, animate: false });
    });
    return controller;
  }

  const tabs = Object.freeze({
    bind: bindTabs,
    ensureIndicator: ensureTabIndicator,
    sync: syncTabIndicator,
    syncExpanding: syncExpandingTabLayout,
  });

  function iconHref(svg) {
    return svg?.querySelector("use")?.getAttribute("href") || "";
  }

  function iconSvg(target) {
    if (!target) return null;
    if (target.matches?.(".t-icon-swap")) {
      const state = target.dataset.state || "a";
      return target.querySelector(`.t-icon[data-icon="${state}"]`);
    }
    const source = target.matches?.("svg")
      ? target
      : target.matches?.("use")
        ? target.ownerSVGElement
        : target.querySelector?.("svg");
    const wrapper = source?.closest(".t-icon-swap");
    if (!wrapper) return source || null;
    const state = wrapper.dataset.state || "a";
    return wrapper.querySelector(`.t-icon[data-icon="${state}"]`);
  }

  function ensureIconSwap(target) {
    const source = iconSvg(target);
    if (!source) return null;
    const existing = source.closest(".t-icon-swap");
    if (existing) return existing;

    const wrapper = document.createElement("span");
    wrapper.className = "t-icon-swap";
    wrapper.dataset.state = "a";
    wrapper.setAttribute("aria-hidden", "true");
    source.before(wrapper);
    source.classList.add("t-icon");
    source.dataset.icon = "a";
    const alternate = source.cloneNode(true);
    alternate.dataset.icon = "b";
    wrapper.append(source, alternate);
    return wrapper;
  }

  function swapIcon(target, href, options = {}) {
    const source = iconSvg(target);
    if (!source) return false;
    const wrapper = ensureIconSwap(source);
    if (!wrapper) return false;
    const currentState = wrapper.dataset.state || "a";
    const currentSvg = wrapper.querySelector(`.t-icon[data-icon="${currentState}"]`);
    if (iconHref(currentSvg) === href) return false;
    const nextState = currentState === "a" ? "b" : "a";
    const nextSvg = wrapper.querySelector(`.t-icon[data-icon="${nextState}"]`);
    nextSvg?.querySelector("use")?.setAttribute("href", href);

    const commit = () => { wrapper.dataset.state = nextState; };
    if (reducedMotion() || options.animate === false) {
      wrapper.classList.add("is-instant");
      commit();
      void wrapper.offsetWidth;
      wrapper.classList.remove("is-instant");
    } else {
      void wrapper.offsetWidth;
      requestAnimationFrame(commit);
    }
    return true;
  }

  const iconSwap = Object.freeze({
    prepare: ensureIconSwap,
    set(element, state) {
      if (!element) return false;
      element.dataset.state = state;
      return true;
    },
    swap: swapIcon,
  });

  function enhanceIconButton(button, options = {}) {
    if (!button) return null;
    button.classList.add(options.shell ? "dp-shell-toggle" : "dp-icon-button");
    if (options.label) button.setAttribute("aria-label", options.label);
    if (options.icon) {
      let svg = iconSvg(button);
      if (!svg) {
        svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("aria-hidden", "true");
        button.replaceChildren(svg);
      }
      let use = svg.querySelector("use");
      if (!use) {
        use = document.createElementNS("http://www.w3.org/2000/svg", "use");
        svg.replaceChildren(use);
      }
      use.setAttribute("href", options.icon);
    }
    iconSwap.prepare(button);
    return button;
  }

  function bindIconButtons(root = document) {
    root.querySelectorAll(".dp-icon-button, .dp-shell-toggle").forEach(button => enhanceIconButton(button, {
      shell: button.classList.contains("dp-shell-toggle"),
    }));
  }

  const iconButton = Object.freeze({ bind: bindIconButtons, enhance: enhanceIconButton });

  function syncShellToggle(button, options = {}) {
    if (!button) return false;
    const expanded = Boolean(options.expanded);
    button.setAttribute("aria-expanded", String(expanded));
    button.setAttribute("aria-label", expanded ? options.hideLabel || "Hide sidebar" : options.showLabel || "Show sidebar");
    if (options.title !== false) button.title = expanded ? options.hideTitle || button.getAttribute("aria-label") : options.showTitle || button.getAttribute("aria-label");
    return iconSwap.swap(button, expanded ? options.expandedIcon : options.collapsedIcon, { animate: options.animate !== false });
  }

  const shellToggle = Object.freeze({ sync: syncShellToggle });

  function themeIsDark(root = document.documentElement, media = matchMedia("(prefers-color-scheme: dark)")) {
    return root.dataset.theme === "dark" || (!root.dataset.theme && media.matches);
  }

  function setTheme(value, options = {}) {
    const root = options.root || document.documentElement;
    const next = ["light", "dark"].includes(value) ? value : "system";
    if (next === "system") delete root.dataset.theme;
    else root.dataset.theme = next;
    return next;
  }

  function bindThemeToggle(button, options = {}) {
    if (!button) return null;
    const root = options.root || document.documentElement;
    const media = matchMedia("(prefers-color-scheme: dark)");
    const sync = ({ animate = false } = {}) => {
      const dark = themeIsDark(root, media);
      button.setAttribute("aria-pressed", String(dark));
      button.setAttribute("aria-label", dark ? options.lightLabel || "Use light appearance" : options.darkLabel || "Use dark appearance");
      iconSwap.swap(button, dark ? options.darkIcon : options.lightIcon, { animate });
    };
    const onClick = () => {
      setTheme(themeIsDark(root, media) ? "light" : "dark", { root });
      sync({ animate: true });
    };
    const onSystemChange = () => { if (!root.dataset.theme) sync({ animate: true }); };
    button.addEventListener("click", onClick);
    media.addEventListener?.("change", onSystemChange);
    sync({ animate: false });
    return Object.freeze({
      sync,
      destroy() {
        button.removeEventListener("click", onClick);
        media.removeEventListener?.("change", onSystemChange);
      },
    });
  }

  const theme = Object.freeze({ bindToggle: bindThemeToggle, isDark: themeIsDark, set: setTheme });

  function syncPopover(root, trigger, panel, open, options = {}) {
    if (!root || !trigger || !panel) return false;
    root.classList.toggle(options.className || "open", open);
    trigger.setAttribute("aria-expanded", String(open));
    panel.setAttribute("aria-hidden", String(!open));
    panel.inert = !open;
    if (!open) options.afterClose?.();
    else options.afterOpen?.();
    return open;
  }

  function placePopover(trigger, panel, options = {}) {
    if (!trigger?.getClientRects().length || !panel?.getClientRects().length) return false;
    if (options.matchWidth) panel.style.width = `${trigger.getBoundingClientRect().width}px`;
    const triggerRect = trigger.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const gap = options.gap ?? readCssLength("--popover-anchor-gap");
    const edge = options.edge ?? readCssLength("--floating-edge");
    const left = clamp(triggerRect.left, edge, innerWidth - panelRect.width - edge);
    const above = shouldPlaceAbove(trigger, panel, { gap });
    panel.style.left = `${left}px`;
    panel.style.top = `${above ? triggerRect.top - panelRect.height - gap : triggerRect.bottom + gap}px`;
    panel.style.transformOrigin = `${clamp(triggerRect.left + triggerRect.width / 2 - left, 0, panelRect.width)}px ${above ? "100%" : "0"}`;
    return true;
  }

  const popover = Object.freeze({ sync: syncPopover, place: placePopover });

  function centeredStart(containerStart, containerSize, itemSize) {
    return containerStart + (containerSize - itemSize) / 2;
  }

  function placeAfter(rect, gap = 0) {
    return rect.right + gap;
  }

  function mirroredInset(rect) {
    return rect.left;
  }

  function syncShellControlGeometry(root = document) {
    const traffic = root.querySelector(".dp-traffic");
    if (!traffic?.getClientRects().length) return false;
    const trafficRect = traffic.getBoundingClientRect();
    const gap = readCssLength("--chrome-control-gap");
    const verticalPosition = button => {
      const size = button.getBoundingClientRect().height || readCssLength("--shell-control-size");
      return centeredStart(trafficRect.top, trafficRect.height, size);
    };
    root.querySelectorAll(".dp-shell-toggle-left").forEach(button => {
      button.style.left = `${placeAfter(trafficRect, gap)}px`;
      button.style.top = `${verticalPosition(button)}px`;
    });
    root.querySelectorAll(".dp-shell-toggle-right").forEach(button => {
      button.style.top = `${verticalPosition(button)}px`;
      button.style.right = `${mirroredInset(trafficRect)}px`;
    });
    return true;
  }

  function bindShellControlGeometry(root = document) {
    const sync = () => syncShellControlGeometry(root);
    const observed = [root.querySelector(".dp-traffic"), ...root.querySelectorAll(".dp-shell-toggle")].filter(Boolean);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(sync);
    observed.forEach(element => observer?.observe(element));
    window.addEventListener?.("resize", sync);
    requestAnimationFrame(sync);
    return Object.freeze({
      sync,
      destroy() {
        observer?.disconnect();
        window.removeEventListener?.("resize", sync);
      },
    });
  }

  const shellControls = Object.freeze({ bind: bindShellControlGeometry, sync: syncShellControlGeometry });

  function syncSidebarState(options = {}) {
    const root = options.root || document.body;
    const className = options.className || "sidebar-hidden";
    const hidden = Boolean(options.hidden);
    const elements = [options.sidebar, ...(options.dependents || [])].filter(Boolean);
    const transitions = options.instant ? elements.map(element => element.style.transition) : [];
    if (options.instant) elements.forEach(element => { element.style.transition = "none"; });
    root.classList.toggle(className, hidden);
    if (options.instant) {
      root.offsetWidth;
      requestAnimationFrame(() => elements.forEach((element, index) => {
        element.style.transition = transitions[index];
      }));
    }
    return hidden;
  }

  const sidebar = Object.freeze({ sync: syncSidebarState });

  function readCssLength(name, options = {}) {
    const element = options.element || document.documentElement;
    const raw = getComputedStyle(element).getPropertyValue(name).trim();
    if (/^-?\d*\.?\d+px$/.test(raw)) return parseFloat(raw);
    const host = element === document.documentElement ? (document.body || document.documentElement) : element;
    const probe = document.createElement("i");
    probe.setAttribute("aria-hidden", "true");
    probe.style.cssText = `display:block;position:absolute;visibility:hidden;pointer-events:none;width:var(${name});height:0;padding:0;border:0;`;
    host.append(probe);
    const value = probe.getBoundingClientRect().width;
    probe.remove();
    return Number.isFinite(value) && value ? value : (options.fallback || 0);
  }

  function readCssNumber(name, options = {}) {
    const element = options.element || document.documentElement;
    const value = Number.parseFloat(getComputedStyle(element).getPropertyValue(name));
    return Number.isFinite(value) ? value : (options.fallback ?? 0);
  }

  function maxWidthQuery(name) {
    return matchMedia(`(max-width: ${readCssLength(name)}px)`);
  }

  function shouldPlaceAbove(trigger, panel, options = {}) {
    if (!trigger || !panel) return false;
    const gap = options.gap ?? readCssLength("--popover-anchor-gap");
    const rect = trigger.getBoundingClientRect();
    const required = panel.scrollHeight + gap;
    return innerHeight - rect.bottom < required && rect.top > required;
  }

  const clampUnit = value => Math.max(0, Math.min(1, value));
  function smoothstep(value) {
    const unit = clampUnit(value);
    return unit * unit * (3 - 2 * unit);
  }

  function gaussianField({ dx = 0, dy = 0, sigmaX, sigmaY, envelope = 1 }) {
    if (!(sigmaX > 0) || !(sigmaY > 0)) return 0;
    return envelope * Math.exp(
      -(dy * dy) / (2 * sigmaY * sigmaY) -
      (dx * dx) / (2 * sigmaX * sigmaX)
    );
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function sidebarWidthFromPointer(clientX, options = {}) {
    const inset = options.inset ?? readCssLength("--sidebar-edge");
    const min = options.min ?? readCssLength("--sidebar-min-width");
    const max = options.max ?? readCssLength("--sidebar-max-width");
    return clamp(clientX - inset, min, max);
  }

  const geometry = Object.freeze({
    clamp,
    centeredStart,
    placeAfter,
    mirroredInset,
    read: readCssLength,
    number: readCssNumber,
    maxWidthQuery,
    shouldPlaceAbove,
    smoothstep,
    gaussianField,
    sidebarWidthFromPointer,
  });

  function bindSidebarResize(handle, options = {}) {
    if (!handle) return null;
    const root = options.root || document.documentElement;
    const sidebarElement = options.sidebar || handle.closest(".dp-sidebar, .rail");
    const variable = options.variable || "--sidebar-width";
    const bodyClass = options.bodyClass || "sidebar-resizing";
    const activeClass = options.activeClass || "dragging";
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", "vertical");
    handle.setAttribute("aria-valuemin", String(Math.round(options.min ?? readCssLength("--sidebar-min-width"))));
    handle.setAttribute("aria-valuemax", String(Math.round(options.max ?? readCssLength("--sidebar-max-width"))));
    if (options.keyboard && !handle.hasAttribute("tabindex")) handle.tabIndex = 0;
    const current = () => options.getValue?.() ?? sidebarElement?.getBoundingClientRect().width ?? readCssLength("--sidebar-width");
    const normalize = value => clamp(
      value,
      options.min ?? readCssLength("--sidebar-min-width"),
      options.max ?? readCssLength("--sidebar-max-width"),
    );
    const apply = (value, detail = {}) => {
      const next = normalize(value);
      if (options.onChange) options.onChange(next, detail);
      else root.style.setProperty(variable, `${next}px`);
      handle.setAttribute("aria-valuenow", String(Math.round(next)));
      return next;
    };
    let pointerId = null;
    const finish = (event, cancelled = false) => {
      if (pointerId == null) return;
      if (handle.hasPointerCapture?.(pointerId)) handle.releasePointerCapture(pointerId);
      pointerId = null;
      document.body.classList.remove(bodyClass);
      handle.classList.remove(activeClass);
      options.onCommit?.(current(), { source: "pointer", cancelled, event });
    };
    const onPointerDown = event => {
      if (event.button !== 0) return;
      event.preventDefault();
      pointerId = event.pointerId;
      handle.setPointerCapture?.(pointerId);
      document.body.classList.add(bodyClass);
      handle.classList.add(activeClass);
    };
    const onPointerMove = event => {
      if (event.pointerId !== pointerId) return;
      apply(sidebarWidthFromPointer(event.clientX), { source: "pointer", event });
    };
    const onPointerUp = event => finish(event, false);
    const onPointerCancel = event => finish(event, true);
    const onDoubleClick = event => {
      const value = options.resetValue ?? readCssLength("--sidebar-width");
      apply(value, { source: "reset", event });
      options.onCommit?.(value, { source: "reset", event });
    };
    const onKeyDown = event => {
      if (!options.keyboard || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      const step = options.step ?? readCssLength("--sidebar-resize-step");
      const value = apply(current() + (event.key === "ArrowRight" ? step : -step), { source: "keyboard", event });
      options.onCommit?.(value, { source: "keyboard", event });
    };
    handle.addEventListener("pointerdown", onPointerDown);
    handle.addEventListener("pointermove", onPointerMove);
    handle.addEventListener("pointerup", onPointerUp);
    handle.addEventListener("pointercancel", onPointerCancel);
    handle.addEventListener("dblclick", onDoubleClick);
    handle.addEventListener("keydown", onKeyDown);
    handle.setAttribute("aria-valuenow", String(Math.round(current())));
    return Object.freeze({
      apply,
      destroy() {
        handle.removeEventListener("pointerdown", onPointerDown);
        handle.removeEventListener("pointermove", onPointerMove);
        handle.removeEventListener("pointerup", onPointerUp);
        handle.removeEventListener("pointercancel", onPointerCancel);
        handle.removeEventListener("dblclick", onDoubleClick);
        handle.removeEventListener("keydown", onKeyDown);
      },
    });
  }

  const sidebarResize = Object.freeze({ bind: bindSidebarResize });

  const contentRuns = new WeakMap();

  function contentTargets(target) {
    const values = Array.isArray(target) ? target : [target];
    return values.filter(element => element?.animate);
  }

  function contentTransform(axis, offset) {
    return axis === "x" ? `translateX(${offset}px)` : `translateY(${offset}px)`;
  }

  function cancelContentTargets(targets) {
    targets.forEach(element => {
      contentRuns.get(element)?.cancel();
      contentRuns.delete(element);
      // Spare scroll-driven animations (scroll-fade edge masks): cancelling a
      // CSS animation kills it until its animation-name is re-applied.
      element.getAnimations()
        .filter(animation => animation.timeline === document.timeline)
        .forEach(animation => animation.cancel());
    });
  }

  async function enterContent(target, options = {}) {
    const targets = contentTargets(target);
    if (!targets.length || reducedMotion() || options.animate === false) return true;
    cancelContentTargets(targets);
    const direction = options.direction ?? 1;
    const offset = (options.distance ?? distance.swap) * direction;
    const axis = options.axis || "y";
    const blurRadius = options.blur ?? blur.swap;
    const animations = targets.map(element => {
      const animation = element.animate([
        { opacity: 0, filter: `blur(${blurRadius}px)`, transform: contentTransform(axis, offset) },
        { opacity: 1, filter: "blur(0)", transform: contentTransform(axis, 0) },
      ], {
        duration: options.duration ?? duration.contentBlur,
        easing: options.easing || "cubic-bezier(0.22, 1, 0.36, 1)",
      });
      contentRuns.set(element, animation);
      return animation;
    });
    await Promise.all(animations.map(animation => animation.finished.catch(() => false)));
    targets.forEach((element, index) => {
      if (contentRuns.get(element) !== animations[index]) return;
      animations[index].cancel();
      contentRuns.delete(element);
    });
    return true;
  }

  async function swapContent(target, update, options = {}) {
    const targets = contentTargets(target);
    if (!targets.length || reducedMotion() || options.animate === false) {
      update?.();
      return true;
    }
    cancelContentTargets(targets);
    const token = Symbol("content-swap");
    const direction = options.direction ?? 1;
    const offset = (options.distance ?? distance.swap) * direction;
    const axis = options.axis || "y";
    const blurRadius = options.blur ?? blur.swap;
    const exits = targets.map(element => {
      const animation = element.animate([
        { opacity: 1, filter: "blur(0)", transform: contentTransform(axis, 0) },
        { opacity: 0, filter: `blur(${blurRadius}px)`, transform: contentTransform(axis, -offset) },
      ], {
        duration: options.exitDuration ?? duration.exit,
        easing: options.exitEasing || motion.curve.accelerateCss,
        fill: "forwards",
      });
      animation.__boardToken = token;
      contentRuns.set(element, animation);
      return animation;
    });
    await Promise.all(exits.map(animation => animation.finished.catch(() => false)));
    if (targets.some(element => contentRuns.get(element)?.__boardToken !== token)) return false;
    update?.();
    const enters = targets.map((element, index) => {
      exits[index].cancel();
      const animation = element.animate([
        { opacity: 0, filter: `blur(${blurRadius}px)`, transform: contentTransform(axis, offset) },
        { opacity: 1, filter: "blur(0)", transform: contentTransform(axis, 0) },
      ], {
        duration: options.enterDuration ?? duration.contentBlur,
        easing: options.enterEasing || motion.curve.smoothCss,
        fill: "forwards",
      });
      animation.__boardToken = token;
      contentRuns.set(element, animation);
      return animation;
    });
    await Promise.all(enters.map(animation => animation.finished.catch(() => false)));
    if (targets.some(element => contentRuns.get(element)?.__boardToken !== token)) return false;
    enters.forEach(animation => animation.cancel());
    targets.forEach(element => contentRuns.delete(element));
    return true;
  }

  async function overlapContent(target, update, options = {}) {
    if (!target) return false;
    if (reducedMotion() || options.animate === false) {
      update?.();
      return true;
    }
    const previousPosition = target.style.position;
    const ghost = document.createElement("span");
    ghost.className = "dp-overlap-ghost";
    ghost.innerHTML = target.innerHTML;
    Object.assign(ghost.style, {
      position: "absolute",
      inset: "0",
      pointerEvents: "none",
    });
    target.style.position = "relative";
    update?.();
    const entering = [...target.children];
    target.append(ghost);
    const blurRadius = options.blur ?? blur.swap;
    const exit = ghost.animate([
      { opacity: 1, filter: "blur(0)" },
      { opacity: 0, filter: `blur(${blurRadius}px)` },
    ], {
      duration: options.exitDuration ?? duration.textSwap,
      easing: options.exitEasing ?? motion.curve.blurCss,
      fill: "forwards",
    });
    const enters = entering.map(element => element.animate([
      { opacity: 0, filter: `blur(${blurRadius}px)` },
      { opacity: 1, filter: "blur(0)" },
    ], {
      duration: options.enterDuration ?? duration.dissolve,
      easing: options.enterEasing ?? motion.curve.smoothCss,
      fill: "backwards",
    }));
    await Promise.all([exit, ...enters].map(animation => animation.finished.catch(() => false)));
    ghost.remove();
    if (previousPosition) target.style.position = previousPosition;
    else target.style.removeProperty("position");
    return true;
  }

  const content = Object.freeze({ enter: enterContent, swap: swapContent, overlap: overlapContent });

  function bindTabPanels(container, options = {}) {
    if (!container) return null;
    const body = () => typeof options.body === "function"
      ? options.body()
      : options.body || container.parentElement?.querySelector("[data-tab-panels]");
    const tabSelector = options.tabSelector || '[role="tab"]';
    const panelSelector = options.panelSelector || "[data-tab-panel]";
    const valueAttribute = options.valueAttribute || "data-tab-value";
    const panelValueAttribute = options.panelValueAttribute || "data-tab-panel";
    const values = () => [...container.querySelectorAll(tabSelector)]
      .map(tab => tab.getAttribute(valueAttribute))
      .filter(Boolean);
    let intent = container.querySelector('[aria-selected="true"]')?.getAttribute(valueAttribute) || values()[0];

    const commit = (value, previousValue) => {
      if (options.render) options.render(value, previousValue);
      else body()?.querySelectorAll(panelSelector).forEach(panel => {
        panel.hidden = panel.getAttribute(panelValueAttribute) !== value;
      });
      body()?.setAttribute("data-tab", value);
      options.onChange?.({ value, previousValue });
    };

    const tabsController = tabs.bind(container, {
      ...options,
      valueAttribute,
      onChange: ({ value, previousValue }) => {
        if (!value || value === intent) return;
        const previousIntent = intent;
        intent = value;
        const order = values();
        const direction = order.indexOf(value) >= order.indexOf(previousIntent) ? 1 : -1;
        const update = () => commit(value, previousValue || previousIntent);
        const target = body();
        if (!target || options.animate === false) update();
        else void content.swap(target, update, { axis: options.axis || "x", direction });
      },
    });

    return Object.freeze({
      select: tabsController.select,
      sync: tabsController.sync,
      destroy: tabsController.destroy,
      get value() { return intent; },
    });
  }

  const tabPanels = Object.freeze({ bind: bindTabPanels });

  function bindPeek(root, options = {}) {
    if (!root) return null;
    const triggers = [...(options.triggers || [])].filter(Boolean);
    const closes = [...(options.closeButtons || root.querySelectorAll("[data-peek-close]"))].filter(Boolean);
    const openClass = options.openClass || "show";
    let restoreTarget = null;
    let openIntent = !root.hidden && root.getAttribute("aria-hidden") !== "true";

    const syncTriggers = open => triggers.forEach(trigger => trigger.setAttribute("aria-expanded", String(open)));
    const focusTarget = () => root.querySelector(options.focusSelector || "[data-peek-close], [role=tab], button, a[href], [tabindex]:not([tabindex='-1'])");
    const setOpen = async (open, change = {}) => {
      if (open === openIntent && change.force !== true) return open;
      openIntent = open;
      if (open) {
        restoreTarget = change.trigger || document.activeElement;
        const beforeOpen = () => {
          root.hidden = false;
          root.inert = false;
          root.classList.add(openClass);
          root.setAttribute("aria-hidden", "false");
          syncTriggers(true);
          options.beforeOpen?.(change);
        };
        const shown = await surface.show(root, { axis: options.axis || "x", animate: change.animate, beforeOpen });
        if (shown && change.focus !== false) focusTarget()?.focus();
        options.afterOpen?.();
        return shown;
      }
      const afterClose = () => {
        root.hidden = true;
        root.inert = true;
        root.classList.remove(openClass);
        root.setAttribute("aria-hidden", "true");
        syncTriggers(false);
        options.afterClose?.();
        if (change.restoreFocus !== false && restoreTarget?.isConnected) restoreTarget.focus();
      };
      return surface.hide(root, {
        axis: options.axis || "x",
        animate: change.animate,
        beforeClose: () => options.beforeClose?.(change),
        afterClose,
      });
    };
    const open = change => setOpen(true, change);
    const close = change => setOpen(false, change);
    const toggle = change => setOpen(!openIntent, change);
    const onTrigger = event => toggle({ trigger: event.currentTarget });
    const onClose = () => close();
    const onKeyDown = event => { if (event.key === "Escape" && openIntent) close(); };
    triggers.forEach(trigger => trigger.addEventListener("click", onTrigger));
    closes.forEach(button => button.addEventListener("click", onClose));
    if (options.escape !== false) document.addEventListener("keydown", onKeyDown);
    root.classList.toggle(openClass, openIntent);
    syncTriggers(openIntent);
    return Object.freeze({
      open,
      close,
      toggle,
      setOpen,
      destroy() {
        triggers.forEach(trigger => trigger.removeEventListener("click", onTrigger));
        closes.forEach(button => button.removeEventListener("click", onClose));
        if (options.escape !== false) document.removeEventListener("keydown", onKeyDown);
      },
      get isOpen() { return openIntent; },
    });
  }

  const peek = Object.freeze({ bind: bindPeek });

  const overlayStack = [];
  const overlayBackgrounds = new Set();
  const overlayFocusSelector = [
    "button:not(:disabled)",
    "input:not(:disabled)",
    "textarea:not(:disabled)",
    "select:not(:disabled)",
    "a[href]",
    '[tabindex]:not([tabindex="-1"])',
  ].join(",");

  function overlayFocusables(element) {
    return [...element.querySelectorAll(overlayFocusSelector)].filter(item => !item.hidden && !item.closest("[inert]"));
  }

  function syncOverlayStack() {
    const top = overlayStack.at(-1);
    overlayStack.forEach(item => {
      const active = item === top;
      item.element.toggleAttribute("inert", !active);
      item.element.setAttribute("aria-hidden", String(!active));
    });
    overlayStack.forEach(item => { if (item.background) overlayBackgrounds.add(item.background); });
    overlayBackgrounds.forEach(background => {
      const blocked = overlayStack.some(item => item.background === background);
      background.toggleAttribute("inert", blocked);
      if (!blocked) overlayBackgrounds.delete(background);
    });
  }

  function focusOverlay(item) {
    const target = item.options.initialFocus?.() || overlayFocusables(item.element)[0] || item.element;
    if (target === item.element && !target.hasAttribute("tabindex")) target.tabIndex = -1;
    target.focus({ preventScroll: true });
  }

  function openOverlay(element, options = {}) {
    if (!element || overlayStack.some(item => item.element === element)) return false;
    const item = {
      element,
      options,
      background: options.background || null,
      restoreFocus: options.trigger || document.activeElement,
    };
    overlayStack.push(item);
    element.hidden = false;
    element.dataset.open = "true";
    syncOverlayStack();
    surface.show(element, {
      axis: options.axis || "y",
      distance: options.distance ?? distance.sheetEnter,
      spring: options.spring || spring.sheet,
    });
    requestAnimationFrame(() => focusOverlay(item));
    return true;
  }

  async function closeOverlay(element, options = {}) {
    const index = overlayStack.findIndex(item => item.element === element);
    if (index < 0) return false;
    const item = overlayStack[index];
    if (item.closing) return false;
    item.closing = true;
    await surface.hide(element, {
      axis: item.options.axis || "y",
      distance: item.options.distance ?? distance.sheetExit,
    });
    overlayStack.splice(index, 1);
    element.dataset.open = "false";
    element.hidden = true;
    syncOverlayStack();
    const next = overlayStack.at(-1);
    if (next) {
      if (item.restoreFocus?.isConnected && next.element.contains(item.restoreFocus)) item.restoreFocus.focus({ preventScroll: true });
      else focusOverlay(next);
    }
    else if (options.restoreFocus !== false && item.options.restoreFocus !== false && item.restoreFocus?.isConnected) item.restoreFocus.focus({ preventScroll: true });
    return true;
  }

  function closeTopOverlay(options = {}) {
    const top = overlayStack.at(-1);
    if (!top || top.options.dismissible === false) return false;
    return closeOverlay(top.element, options);
  }

  function trapOverlayFocus(event) {
    const top = overlayStack.at(-1);
    if (!top) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeTopOverlay();
      return;
    }
    if (event.key !== "Tab") return;
    const focusables = overlayFocusables(top.element);
    if (!focusables.length) {
      event.preventDefault();
      focusOverlay(top);
      return;
    }
    const first = focusables[0];
    const last = focusables.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  document.addEventListener("keydown", trapOverlayFocus);
  const overlay = Object.freeze({
    open: openOverlay,
    close: closeOverlay,
    closeTop: closeTopOverlay,
    top: () => overlayStack.at(-1)?.element || null,
  });

  function animateListChange(options = {}) {
    const before = options.before || new Map();
    const staying = options.staying || [];
    const entering = options.entering ? [options.entering].flat() : [];
    const leaving = options.leaving || [];
    const runDuration = reducedMotion() ? 1 : options.duration ?? duration.traceRow;
    const easing = options.easing ?? motion.curve.traceRowCss;
    const offset = options.distance ?? distance.traceRow;
    const blurRadius = options.blur ?? blur.traceRow;
    const animations = [];
    staying.forEach(element => {
      if (!before.has(element) || reducedMotion()) return;
      const deltaY = before.get(element).top - element.getBoundingClientRect().top;
      if (deltaY) animations.push(element.animate(
        [{ transform: `translateY(${deltaY}px)` }, { transform: "translateY(0px)" }],
        { duration: runDuration, easing },
      ));
    });
    entering.forEach(element => animations.push(element.animate(
      reducedMotion()
        ? [{ opacity: 1 }, { opacity: 1 }]
        : [{ opacity: 0, filter: `blur(${blurRadius}px)`, transform: `translateY(${offset}px)` }, { opacity: 1, filter: "blur(0)", transform: "translateY(0px)" }],
      { duration: runDuration, easing },
    )));
    leaving.forEach(element => animations.push(element.animate(
      reducedMotion()
        ? [{ opacity: 0 }, { opacity: 0 }]
        : [{ opacity: 1, filter: "blur(0)", transform: "translateY(0px)" }, { opacity: 0, filter: `blur(${blurRadius}px)`, transform: `translateY(${-offset}px)` }],
      { duration: runDuration, easing, fill: "both" },
    )));
    return Promise.allSettled(animations.map(animation => animation.finished));
  }

  function transitionCentered(element, update, options = {}) {
    if (!element || reducedMotion() || options.animate === false) {
      update?.();
      return 0;
    }
    const anchor = () => options.anchor?.() || element;
    const before = anchor()?.getBoundingClientRect();
    element.classList.add(options.measuringClass || "shell-measuring");
    element.style.setProperty(options.property || "--shell-shift", "0px");
    update?.();
    const after = anchor()?.getBoundingClientRect();
    if (!before || !after) {
      element.classList.remove(options.measuringClass || "shell-measuring");
      return 0;
    }
    const delta = before.left + before.width / 2 - (after.left + after.width / 2);
    element.style.setProperty(options.property || "--shell-shift", `${delta}px`);
    void element.offsetWidth;
    element.classList.remove(options.measuringClass || "shell-measuring");
    requestAnimationFrame(() => element.style.setProperty(options.property || "--shell-shift", "0px"));
    return delta;
  }

  function positionIndicator(container, indicator, target, options = {}) {
    if (!container || !indicator || !target) return false;
    const containerRect = container.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const x = targetRect.left - containerRect.left + container.scrollLeft;
    const y = targetRect.top - containerRect.top + container.scrollTop;
    if (options.instant || reducedMotion()) indicator.style.transition = "none";
    indicator.style.transform = options.axis === "y" ? `translateY(${y}px)` : `translate(${x}px, ${y}px)`;
    if (options.size) {
      indicator.style.width = `${targetRect.width}px`;
      indicator.style.height = `${targetRect.height}px`;
    }
    if (options.instant || reducedMotion()) {
      void indicator.offsetWidth;
      indicator.style.transition = "";
    }
    return true;
  }

  function bindIntrinsicWidth(element, options = {}) {
    if (!element) return () => {};
    const property = options.property || element.dataset.intrinsicWidth || "--intrinsic-width";
    const measure = () => {
      const width = Math.ceil(element.scrollWidth);
      if (width > 0) element.style.setProperty(property, `${width}px`);
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    return () => observer?.disconnect();
  }

  function bindIntrinsicWidths(root = document, selector = "[data-intrinsic-width]", options = {}) {
    const cleanups = [...root.querySelectorAll(selector)].map(element => bindIntrinsicWidth(element, options));
    return () => cleanups.forEach(cleanup => cleanup());
  }

  // FLIP for whole blocks: when a mutation reflows the room (the deck head
  // changes height, a queue row leaves), the blocks below glide to their new
  // resting place on the trace-row spring instead of snapping there.
  function flipBlocks(blocks, mutate, options = {}) {
    const targets = (blocks || []).filter(Boolean);
    if (!targets.length || reducedMotion() || options.animate === false) {
      mutate?.();
      return;
    }
    const before = targets.map(element => element.getBoundingClientRect().top);
    mutate?.();
    targets.forEach((element, index) => {
      const delta = before[index] - element.getBoundingClientRect().top;
      if (Math.abs(delta) < 0.5) return;
      element.animate([
        { transform: `translateY(${delta}px)` },
        { transform: "translateY(0)" },
      ], { duration: duration.traceRow, easing: motion.curve.traceRowCss });
    });
  }

  const layout = Object.freeze({
    animateListChange,
    transitionCentered,
    positionIndicator,
    bindIntrinsicWidth,
    bindIntrinsicWidths,
    flipBlocks,
  });

  /* deck — a horizontal swipe on the peek spring. Resolving an ask reads as one
     stream of travel: the old card accelerates straight out along the verb's
     direction (affirmative right, decline left) through swap blur, and after one
     deckHandoff beat the next card follows from the opposite side, resolving on
     the same 210/26 spring that opens peeks. Set-aside (direction 0) drops
     behind instead, and its successor rises in place. Vectors stay strictly
     horizontal for directional verbs — no diagonal drift, no scale wobble. */
  const deckRuns = new WeakMap();

  function deckVector(direction) {
    if (direction === 0) return { x: 0, y: 4, scale: 0.96 };
    if (direction < 0) return { x: -32, y: 0, scale: 1 };
    return { x: 32, y: 0, scale: 1 };
  }

  const deckPose = vector => `translate(${vector.x}px, ${vector.y}px) scale(${vector.scale})`;

  async function settleDeckRun(card, animation) {
    deckRuns.set(card, animation);
    await animation.finished.catch(() => {});
    return deckRuns.get(card) === animation;
  }

  // direction: 1 = affirmative (exits right), -1 = decline (left), 0 = set aside (drops behind)
  function deckExit(card, options = {}) {
    if (!card?.animate) return Promise.resolve(true);
    deckRuns.get(card)?.cancel();
    if (reducedMotion() || options.animate === false) {
      return settleDeckRun(card, card.animate([{ opacity: 1 }, { opacity: 0 }], { duration: duration.reduced, fill: "forwards" }));
    }
    const vector = deckVector(options.direction ?? 1);
    return settleDeckRun(card, card.animate([
      { opacity: 1, filter: "blur(0)", transform: "translate(0, 0) scale(1)" },
      { opacity: 0, filter: `blur(${blur.swap}px)`, transform: deckPose(vector) },
    ], { duration: options.duration ?? duration.deckExit, easing: motion.curve.accelerateCss, fill: "forwards" }));
  }

  // entry: one movement on the peek spring — opacity, blur, and travel together.
  // Directional verbs continue the swipe (entry trails in from the opposite side);
  // handoff waits one beat so the entry never renders under a still-legible exit.
  function deckPromote(card, options = {}) {
    if (!card?.animate || reducedMotion() || options.animate === false) return Promise.resolve(true);
    deckRuns.get(card)?.cancel();
    const delay = options.handoff ? duration.deckHandoff : 0;
    const direction = options.direction ?? 0;
    const fromPose = direction === 0
      ? `translateY(${distance.swap}px)`
      : `translateX(${direction > 0 ? -distance.peekEnter : distance.peekEnter}px)`;
    return settleDeckRun(card, card.animate([
      { opacity: 0, filter: `blur(${blur.swap}px)`, transform: fromPose },
      { opacity: 1, filter: "blur(0)", transform: "translate(0, 0)" },
    ], { duration: options.duration ?? duration.deckEnter, delay, easing: motion.curve.deckEnterCss, fill: "backwards" }));
  }

  // undo: the departed card physically comes back along its exit path on the stack spring
  function deckReturn(card, options = {}) {
    if (!card?.animate) return Promise.resolve(true);
    deckRuns.get(card)?.cancel();
    if (reducedMotion() || options.animate === false) {
      return settleDeckRun(card, card.animate([{ opacity: 0 }, { opacity: 1 }], { duration: duration.reduced }));
    }
    const vector = deckVector(options.direction ?? 1);
    const delay = options.handoff ? duration.deckHandoff : 0;
    return settleDeckRun(card, card.animate([
      { opacity: 0, filter: `blur(${blur.swap}px)`, transform: deckPose(vector) },
      { opacity: 1, filter: "blur(0)", transform: "translate(0, 0) scale(1)" },
    ], { duration: options.duration ?? duration.deckEnter, delay, easing: motion.curve.deckEnterCss, fill: "backwards" }));
  }

  const deck = Object.freeze({ exit: deckExit, promote: deckPromote, return: deckReturn });

  function startSpinner(element, options = {}) {
    const runDuration = options.duration ?? duration.refreshLoop;
    return reducedMotion()
      ? element.animate([{ opacity: 1 }, { opacity: .45 }, { opacity: 1 }], { duration: runDuration, easing: "ease-in-out", iterations: Infinity })
      : element.animate([{ transform: "rotate(0deg)" }, { transform: "rotate(360deg)" }], { duration: runDuration, easing: "linear", iterations: Infinity });
  }

  async function settleSpinner(element, active, options = {}) {
    if (!active) return;
    if (reducedMotion()) {
      active.cancel();
      return;
    }
    const loop = options.duration ?? duration.refreshLoop;
    const progress = (Number(active.currentTime ?? 0) % loop) / loop;
    active.cancel();
    const animation = element.animate(
      [{ transform: `rotate(${progress * 360}deg)` }, { transform: "rotate(360deg)" }],
      {
        duration: Math.max(
          options.minDuration ?? duration.refreshSettleMin,
          Math.min(options.maxDuration ?? duration.refreshSettleMax, loop * (1 - progress)),
        ),
        easing: options.easing ?? motion.curve.smoothCss,
      },
    );
    await animation.finished.catch(() => {});
  }

  // loading discipline: nothing spins for fast work (show delay), and once shown
  // the indicator stays long enough to read as a state, not a flicker (min visible)
  async function runSpinner(element, task, options = {}) {
    const showDelay = options.showDelay ?? duration.loadingShowDelay;
    const minVisible = options.minVisible ?? duration.loadingMinVisible;
    let active = null;
    let shownAt = 0;
    const timer = setTimeout(() => {
      shownAt = performance.now();
      active = startSpinner(element, options);
    }, reducedMotion() ? 0 : showDelay);
    try {
      return await task;
    } finally {
      clearTimeout(timer);
      if (active) {
        const remaining = minVisible - (performance.now() - shownAt);
        if (remaining > 0 && !reducedMotion()) await new Promise(resolve => setTimeout(resolve, remaining));
        await settleSpinner(element, active, options);
      }
    }
  }

  const spinner = Object.freeze({ start: startSpinner, settle: settleSpinner, run: runSpinner });

  /* tooltip — group warm-up: the first tooltip waits tooltipDelay; while the group
     is warm (a tooltip was visible within tooltipWarm) peers show immediately */
  const tooltip = (() => {
    let warmUntil = 0;
    let showTimer = 0;
    let bubble = null;

    const ensureBubble = () => {
      if (bubble?.isConnected) return bubble;
      bubble = document.createElement("span");
      bubble.className = "dp-tooltip";
      bubble.setAttribute("role", "tooltip");
      bubble.hidden = true;
      document.body.append(bubble);
      return bubble;
    };

    function show(trigger) {
      const tip = ensureBubble();
      tip.textContent = trigger.getAttribute("data-tooltip");
      tip.hidden = false;
      const rect = trigger.getBoundingClientRect();
      const tipRect = tip.getBoundingClientRect();
      const left = Math.max(8, Math.min(window.innerWidth - tipRect.width - 8, rect.left + rect.width / 2 - tipRect.width / 2));
      const top = rect.bottom + 6 + tipRect.height > window.innerHeight ? rect.top - tipRect.height - 6 : rect.bottom + 6;
      tip.style.left = `${left}px`;
      tip.style.top = `${top}px`;
    }

    function hide() {
      clearTimeout(showTimer);
      showTimer = 0;
      if (bubble && !bubble.hidden) {
        bubble.hidden = true;
        warmUntil = performance.now() + duration.tooltipWarm;
      }
    }

    function bind(root = document) {
      root.addEventListener("pointerenter", event => {
        const trigger = event.target?.closest?.("[data-tooltip]");
        if (!trigger) return;
        clearTimeout(showTimer);
        const warm = performance.now() < warmUntil;
        if (warm) show(trigger);
        else showTimer = setTimeout(() => show(trigger), duration.tooltipDelay);
      }, true);
      root.addEventListener("pointerleave", event => {
        if (event.target?.closest?.("[data-tooltip]")) hide();
      }, true);
      root.addEventListener("focusin", event => {
        const trigger = event.target?.closest?.("[data-tooltip]");
        if (trigger?.matches(":focus-visible")) show(trigger);
      });
      root.addEventListener("focusout", () => hide());
      window.addEventListener("scroll", hide, true);
      return { hide };
    }

    return Object.freeze({ bind, hide });
  })();

  const disclosureRuns = new WeakMap();
  function syncDisclosure(root, trigger, panel, open, options = {}) {
    if (!root || !trigger || !panel) return false;
    root.classList.toggle(options.expandedClass || "expanded", open);
    trigger.setAttribute("aria-expanded", String(open));
    panel.setAttribute("aria-hidden", String(!open));
    if (options.inert !== false) panel.inert = !open;
    if (options.hidden) panel.hidden = !open;
    return open;
  }

  async function setDisclosure(root, open, options = {}) {
    const trigger = options.trigger || root?.querySelector(options.triggerSelector || '[aria-expanded]');
    const panel = options.panel || root?.querySelector(options.panelSelector || '[data-disclosure-panel]');
    if (!root || !trigger || !panel) return false;
    const token = Symbol("disclosure");
    disclosureRuns.set(root, token);
    const siblings = [...root.parentElement.children];
    const followers = options.followers || siblings.slice(siblings.indexOf(root) + 1).filter(element => !element.hidden);
    [panel, ...followers].forEach(element => element.getAnimations().forEach(animation => animation.cancel()));
    trigger.setAttribute("aria-expanded", String(open));
    if (reducedMotion() || options.animate === false) {
      panel.hidden = !open;
      root.classList.toggle(options.collapsedClass || "collapsed", !open);
      disclosureRuns.delete(root);
      options.onLayout?.();
      return true;
    }
    if (!open) {
      await panel.animate([
        { opacity: 1, filter: "blur(0)", transform: "translateY(0)" },
        { opacity: 0, filter: `blur(${motion.blur.dissolve}px)`, transform: `translateY(-${distance.dissolve}px)` },
      ], { duration: duration.dissolve * .46, easing: motion.curve.accelerateCss }).finished.catch(() => false);
      if (disclosureRuns.get(root) !== token) return false;
    }
    const before = new Map(followers.map(element => [element, element.getBoundingClientRect().top]));
    panel.hidden = !open;
    root.classList.toggle(options.collapsedClass || "collapsed", !open);
    const flows = followers.map(element => {
      const delta = (before.get(element) || 0) - element.getBoundingClientRect().top;
      if (Math.abs(delta) < .5) return null;
      return element.animate([{ transform: `translateY(${delta}px)` }, { transform: "translateY(0)" }], {
        duration: duration.dissolve * (open ? 1 : .54),
        easing: motion.curve.dissolveCss,
      });
    }).filter(Boolean);
    const reveal = open ? panel.animate([
      { opacity: 0, filter: `blur(${motion.blur.dissolve}px)`, transform: `translateY(-${distance.dissolve}px)` },
      { opacity: 1, filter: "blur(0)", transform: "translateY(0)" },
    ], { duration: duration.dissolve, easing: motion.curve.dissolveCss }) : null;
    await Promise.all([...flows, reveal].filter(Boolean).map(animation => animation.finished.catch(() => false)));
    if (disclosureRuns.get(root) !== token) return false;
    disclosureRuns.delete(root);
    options.onLayout?.();
    return true;
  }

  const disclosure = Object.freeze({ set: setDisclosure, sync: syncDisclosure });

  const textSwapRuns = new WeakMap();

  function swapText(element, nextText, options = {}) {
    if (!element) return false;
    const next = String(nextText);
    const active = textSwapRuns.get(element);
    if (active?.phase === "exit") {
      active.next = next;
      active.label = options.label;
      return true;
    }
    if (!active && element.textContent === next) return false;
    if (active) {
      clearTimeout(active.exitTimer);
      clearTimeout(active.cleanupTimer);
      textSwapRuns.delete(element);
      element.classList.remove("is-exit", "is-enter-start");
    }

    element.classList.add("t-text-swap");
    if (reducedMotion() || options.animate === false) {
      element.textContent = next;
      if (options.label != null) element.setAttribute("aria-label", String(options.label));
      return true;
    }

    const run = {
      next,
      label: options.label,
      phase: "exit",
      exitTimer: 0,
      cleanupTimer: 0,
    };
    textSwapRuns.set(element, run);
    void element.offsetWidth;
    element.classList.add("is-exit");
    run.exitTimer = setTimeout(() => {
      if (textSwapRuns.get(element) !== run) return;
      element.textContent = run.next;
      if (run.label != null) element.setAttribute("aria-label", String(run.label));
      element.classList.remove("is-exit");
      element.classList.add("is-enter-start");
      void element.offsetHeight;
      element.classList.remove("is-enter-start");
      run.phase = "enter";
      run.cleanupTimer = setTimeout(() => {
        if (textSwapRuns.get(element) !== run) return;
        element.classList.remove("is-exit", "is-enter-start");
        textSwapRuns.delete(element);
      }, duration.textSwap);
    }, duration.textSwap);
    return true;
  }

  const textSwap = Object.freeze({ swap: swapText });

  const counterRuns = new WeakMap();

  function setSpinningCounter(element, value, options = {}) {
    if (!element) return false;
    const next = String(value);
    const previous = element.dataset.value ?? element.textContent.trim();
    if (previous === next) return false;
    clearTimeout(counterRuns.get(element));
    element.dataset.value = next;
    element.setAttribute("aria-label", options.label?.(next) || next);

    if (reducedMotion() || options.animate === false) {
      element.textContent = next;
      return true;
    }

    const length = Math.max(previous.length, next.length);
    const oldChars = previous.padStart(length, " ").split("");
    const nextChars = next.padStart(length, " ").split("");
    const fragment = document.createDocumentFragment();
    oldChars.forEach((oldChar, index) => {
      const nextChar = nextChars[index];
      const cell = document.createElement("span");
      cell.className = oldChar === nextChar ? "t-spin-cell" : "t-spin-cell is-spinning";
      cell.style.setProperty("--spin-delay", `${Math.min(length - index - 1, 3) * 32}ms`);
      cell.setAttribute("aria-hidden", "true");
      if (oldChar === nextChar) {
        cell.textContent = nextChar;
      } else {
        const oldDigit = document.createElement("span");
        oldDigit.className = "t-spin-old";
        oldDigit.textContent = oldChar;
        const newDigit = document.createElement("span");
        newDigit.className = "t-spin-new";
        newDigit.textContent = nextChar;
        cell.append(oldDigit, newDigit);
      }
      fragment.append(cell);
    });
    element.replaceChildren(fragment);
    const cleanup = setTimeout(() => {
      if (element.dataset.value === next) element.textContent = next;
      counterRuns.delete(element);
    }, duration.counterSpin + 120);
    counterRuns.set(element, cleanup);
    return true;
  }

  const spinningCounter = Object.freeze({ set: setSpinningCounter });

  const skeletonRuns = new WeakMap();

  function replaySkeleton(element, options = {}) {
    if (!element) return false;
    clearTimeout(skeletonRuns.get(element));
    const skeleton = element.querySelector(".t-skel-skeleton");
    element.classList.add("is-resetting");
    element.classList.remove("is-revealed");
    skeleton?.classList.remove("is-pulsing");
    void element.offsetWidth;
    element.classList.remove("is-resetting");
    skeleton?.classList.add("is-pulsing");
    if (reducedMotion() || options.animate === false) {
      element.classList.add("is-revealed");
      return true;
    }
    const delay = options.delay ?? Math.min(duration.skeletonPulse, 620);
    const timer = setTimeout(() => {
      element.classList.add("is-revealed");
      skeletonRuns.delete(element);
    }, delay);
    skeletonRuns.set(element, timer);
    return true;
  }

  const skeletonReveal = Object.freeze({
    reveal(element) { element?.classList.add("is-revealed"); },
    replay: replaySkeleton,
  });

  function collectPageSkeletonRects(target) {
    const targetRect = target.getBoundingClientRect();
    const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
    const rects = [];
    let node = walker.nextNode();
    while (node && rects.length < 96) {
      const parent = node.parentElement;
      const text = node.textContent?.trim();
      if (parent && text && !parent.closest(".dp-page-skeleton, [hidden]")) {
        const style = getComputedStyle(parent);
        if (style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) !== 0) {
          const range = document.createRange();
          range.selectNodeContents(node);
          Array.from(range.getClientRects()).forEach(rect => {
            const x = rect.left - targetRect.left + target.scrollLeft;
            const y = rect.top - targetRect.top + target.scrollTop;
            const width = Math.min(rect.width, target.clientWidth - x);
            const height = Math.max(4, Math.min(12, rect.height * .55));
            if (width > 3 && x < target.clientWidth && y + rect.height > 0 && y < target.clientHeight) {
              rects.push({ x, y: y + (rect.height - height) / 2, width, height });
            }
          });
          range.detach();
        }
      }
      node = walker.nextNode();
    }
    return rects;
  }

  function createPageSkeleton(target) {
    const skeleton = document.createElement("div");
    skeleton.className = "dp-page-skeleton";
    skeleton.setAttribute("aria-hidden", "true");
    collectPageSkeletonRects(target).forEach(rect => {
      const line = document.createElement("i");
      line.className = "dp-page-skeleton-line";
      line.style.setProperty("--dp-skeleton-x", `${rect.x}px`);
      line.style.setProperty("--dp-skeleton-y", `${rect.y}px`);
      line.style.setProperty("--dp-skeleton-width", `${rect.width}px`);
      line.style.setProperty("--dp-skeleton-height", `${rect.height}px`);
      skeleton.append(line);
    });
    target.append(skeleton);
    return skeleton;
  }

  function bindPageEntrance(root = document) {
    const host = root.querySelector?.("[data-page-enter]");
    if (!host || host.dataset.pageEnterBound === "true") return null;
    host.dataset.pageEnterBound = "true";
    host.dataset.pageEnterState = "running";

    const isReduced = reducedMotion();
    const reducedFrames = [{ opacity: 0 }, { opacity: 1 }];
    const items = Array.from(host.querySelectorAll("[data-page-enter-item]"));
    const skeletonTargets = Array.from(host.querySelectorAll("[data-page-skeleton]"));
    const skeletons = skeletonTargets.map(createPageSkeleton);
    const plainItems = items.filter(element => !element.matches("[data-page-skeleton]"));
    const skeletonContent = skeletonTargets.flatMap(target =>
      Array.from(target.children)
        .filter(element => !element.matches(".dp-page-skeleton"))
        .map(element => ({ element, index: items.indexOf(target), skeletonBridge: true })),
    );
    const entries = [
      ...plainItems.map(element => ({ element, index: items.indexOf(element), skeletonBridge: false })),
      ...skeletonContent,
    ];
    const animations = entries.map(({ element, index, skeletonBridge }) => {
      const chrome = element.dataset.pageEnterItem === "chrome";
      const frames = isReduced || chrome
        ? reducedFrames
        : [
            { opacity: 0, filter: `blur(${blur.skeleton}px)`, transform: `translateY(${distance.pageEnter}px)` },
            { opacity: 1, filter: "blur(0)", transform: "translateY(0)" },
          ];
      const delay = chrome
        ? 0
        : Math.min(index, 3) * duration.pageStagger + (skeletonBridge ? duration.pageSkeletonHold : 0);
      return element.animate(frames, {
        duration: isReduced ? duration.pageReduced : chrome ? duration.pageChrome : duration.pageEnter,
        delay,
        easing: "cubic-bezier(0.23, 1, 0.32, 1)",
        fill: "backwards",
      });
    });

    const skeletonAnimations = skeletons.map(element => element.animate(
      isReduced
        ? [{ opacity: 1 }, { opacity: 0 }]
        : [
            { opacity: 1, filter: "blur(0)" },
            { opacity: 0, filter: `blur(${blur.skeleton}px)` },
          ],
      {
        duration: isReduced ? duration.pageReduced : duration.pageSkeletonReveal,
        delay: isReduced ? 0 : duration.pageSkeletonHold,
        easing: "cubic-bezier(0.23, 1, 0.32, 1)",
        fill: "forwards",
      },
    ));

    const finished = Promise.all([...animations, ...skeletonAnimations].map(animation => animation.finished.catch(() => false)))
      .then(() => {
        animations.forEach(animation => animation.cancel());
        skeletons.forEach(element => element.remove());
        host.dataset.pageEnterState = "done";
        return true;
      });
    return Object.freeze({ animations, finished });
  }

  const pageEntrance = Object.freeze({ bind: bindPageEntrance });

  const progressiveBlurAngles = Object.freeze({ top: 0, right: 90, bottom: 180, left: 270 });
  function mountProgressiveBlur(element, options = {}) {
    if (!element) return null;
    const direction = options.direction || "bottom";
    const blurLayers = options.blurLayers ?? progressiveBlurDefaults.layers;
    const blurIntensity = options.blurIntensity ?? progressiveBlurDefaults.intensity;
    const layers = Math.max(blurLayers, 2);
    const segmentSize = 1 / (blurLayers + 1);
    const angle = progressiveBlurAngles[direction] ?? progressiveBlurAngles.bottom;
    const fragment = document.createDocumentFragment();

    Array.from({ length: layers }).forEach((_, index) => {
      const gradientStops = [
        index * segmentSize,
        (index + 1) * segmentSize,
        (index + 2) * segmentSize,
        (index + 3) * segmentSize,
      ].map((position, positionIndex) =>
        `rgba(255, 255, 255, ${positionIndex === 1 || positionIndex === 2 ? 1 : 0}) ${position * 100}%`
      );
      const gradient = `linear-gradient(${angle}deg, ${gradientStops.join(", ")})`;
      const layer = document.createElement("i");
      layer.className = "t-progressive-blur-layer";
      layer.dataset.progressiveBlurLayer = String(index);
      layer.style.position = "absolute";
      layer.style.inset = "0";
      layer.style.pointerEvents = "none";
      layer.style.borderRadius = "inherit";
      layer.style.maskImage = gradient;
      layer.style.webkitMaskImage = gradient;
      layer.style.backdropFilter = `blur(${index * blurIntensity}px)`;
      layer.style.webkitBackdropFilter = `blur(${index * blurIntensity}px)`;
      fragment.append(layer);
    });

    element.replaceChildren(fragment);
    return element;
  }

  const progressiveBlur = Object.freeze({
    angles: progressiveBlurAngles,
    mount: mountProgressiveBlur,
  });

  function createKbd(value, options = {}) {
    const element = document.createElement("kbd");
    element.className = "dp-kbd";
    element.dataset.slot = "kbd";
    element.textContent = value;
    if (options.ariaLabel) element.setAttribute("aria-label", options.ariaLabel);
    return element;
  }

  function createKbdGroup(values, options = {}) {
    const group = document.createElement("kbd");
    group.className = ["dp-kbd-group", options.className].filter(Boolean).join(" ");
    group.dataset.slot = "kbd-group";
    if (options.ariaLabel) group.setAttribute("aria-label", options.ariaLabel);
    group.append(...values.map(value => createKbd(value)));
    return group;
  }

  const keyboard = Object.freeze({ key: createKbd, group: createKbdGroup });

  const motion = Object.freeze({
    duration,
    surface,
    curve: Object.freeze({
      emphasis: Object.freeze([0.32, 0.72, 0, 1]),
      exit: Object.freeze([0.23, 1, 0.32, 1]),
      blur: Object.freeze([0.2, 0.8, 0.2, 1]),
      blurCss: "cubic-bezier(0.2, 0.8, 0.2, 1)",
      dissolveCss: "cubic-bezier(0.23, 1, 0.32, 1)",
      smoothCss: "cubic-bezier(0.22, 1, 0.36, 1)",
      accelerateCss: "cubic-bezier(0.4, 0, 1, 1)",
      drawerCss: "cubic-bezier(0.32, 0.72, 0, 1)",
      tabsLayoutCss: springEasing(spring.layout, duration.tabsLayout, 30),
      traceRowCss: springEasing(spring.traceRow, duration.traceRow, 36),
      deckEnterCss: springEasing(spring.peek, duration.deckEnter, 48),
    }),
    spring,
    tabs,
    tabPanels,
    peek,
    deck,
    iconSwap,
    iconButton,
    shellToggle,
    theme,
    popover,
    shellControls,
    sidebar,
    geometry,
    sidebarResize,
    content,
    overlay,
    layout,
    spinner,
    tooltip,
    disclosure,
    textSwap,
    spinningCounter,
    skeletonReveal,
    pageEntrance,
    progressiveBlur,
    keyboard,
    distance,
    blur,
    scale,
    limits,
    opacity: Object.freeze({
      furniture: 0.96,
    }),
  });

  const cssTokens = {
    "--smooth-out": motion.curve.smoothCss,
    "--motion-press": `${duration.press}ms`,
    "--motion-reduced": `${duration.reduced}ms`,
    "--motion-instant": `${duration.instant}ms`,
    "--motion-feedback": `${duration.feedback}ms`,
    "--motion-exit": `${duration.exit}ms`,
    "--motion-popover": `${duration.popover}ms`,
    "--motion-focus": `${duration.focus}ms`,
    "--motion-dissolve": `${duration.dissolve}ms`,
    "--motion-peek-enter": `${duration.peekEnter}ms`,
    "--motion-peek-exit": `${duration.peekExit}ms`,
    "--motion-tabs-layout": `${duration.tabsLayout}ms`,
    "--motion-tabs-layout-ease": motion.curve.tabsLayoutCss,
    "--tabs-dur": `${duration.tabsSliding}ms`,
    "--tabs-ease": motion.curve.smoothCss,
    "--motion-trace-row": `${duration.traceRow}ms`,
    "--text-swap-dur": `${duration.textSwap}ms`,
    "--text-swap-translate-y": `${distance.textSwap}px`,
    "--text-swap-blur": `${blur.swap}px`,
    "--text-swap-ease": "ease-in-out",
    "--icon-swap-dur": `${duration.iconSwap}ms`,
    "--icon-swap-blur": `${blur.iconSwap}px`,
    "--icon-swap-start-scale": String(scale.iconSwapStart),
    "--icon-swap-ease": "ease-in-out",
    "--motion-counter-spin": `${duration.counterSpin}ms`,
    "--counter-spin-blur": `${blur.counter}px`,
    "--motion-skeleton-pulse": `${duration.skeletonPulse}ms`,
    "--motion-skeleton-reveal": `${duration.skeletonReveal}ms`,
    "--skeleton-blur": `${blur.skeleton}px`,
    "--motion-dissolve-ease": motion.curve.dissolveCss,
    "--motion-accelerate-ease": motion.curve.accelerateCss,
    "--motion-tab-shift": `${duration.tabShift}ms`,
    "--motion-tab-exit": `${duration.tabExit}ms`,
    "--motion-tab-enter": `${duration.tabEnter}ms`,
    "--motion-icon": `${duration.iconMorph}ms`,
    "--motion-content": `${duration.contentBlur}ms`,
    "--motion-instrument": `${duration.instrument}ms`,
    "--motion-instrument-hide-delay": `${duration.instrumentHideDelay}ms`,
    "--motion-furniture": `${duration.furniture}ms`,
    "--motion-sidebar-show": `${duration.sidebarShow}ms`,
    "--motion-sidebar-hide": `${duration.sidebarHide}ms`,
    "--motion-distance-sheet-enter": `${distance.sheetEnter}px`,
    "--motion-refresh-morph": `${duration.refreshMorph}ms`,
    "--motion-refresh-loop": `${duration.refreshLoop}ms`,
    "--motion-refresh-done-hold": `${duration.refreshDoneHold}ms`,
    "--motion-acknowledge": `${duration.acknowledge}ms`,
    "--motion-copy-feedback": `${duration.copyFeedback}ms`,
    "--motion-shimmer": `${duration.shimmer}ms`,
    "--motion-blur-dissolve": `${blur.dissolve}px`,
    "--motion-distance-dissolve": `${distance.dissolve}px`,
    "--motion-drawer-ease": motion.curve.drawerCss,
  };

  for (const [name, value] of Object.entries(cssTokens)) {
    document.documentElement.style.setProperty(name, value);
  }

  const primitiveStyles = document.createElement("style");
  primitiveStyles.id = "board-motion-primitives";
  primitiveStyles.textContent = `
    .t-text-swap {
      display: inline-block;
      transform: translateY(0);
      filter: blur(0);
      opacity: 1;
      transition:
        transform var(--text-swap-dur) var(--text-swap-ease),
        filter    var(--text-swap-dur) var(--text-swap-ease),
        opacity   var(--text-swap-dur) var(--text-swap-ease);
      will-change: transform, filter, opacity;
    }
    .t-text-swap.is-exit {
      transform: translateY(calc(var(--text-swap-translate-y) * -1));
      filter: blur(var(--text-swap-blur));
      opacity: 0;
    }
    .t-text-swap.is-enter-start {
      transform: translateY(var(--text-swap-translate-y));
      filter: blur(var(--text-swap-blur));
      opacity: 0;
      transition: none;
    }
    .t-icon-swap { position: relative; display: inline-grid; place-items: center; line-height: 0; }
    .t-icon-swap .t-icon { grid-area: 1 / 1; transition: opacity var(--icon-swap-dur) var(--icon-swap-ease), filter var(--icon-swap-dur) var(--icon-swap-ease), transform var(--icon-swap-dur) var(--icon-swap-ease); will-change: opacity, filter, transform; }
    .t-icon-swap[data-state="a"] .t-icon[data-icon="a"], .t-icon-swap[data-state="b"] .t-icon[data-icon="b"] { opacity: 1; filter: blur(0); transform: scale(1); }
    .t-icon-swap[data-state="a"] .t-icon[data-icon="b"], .t-icon-swap[data-state="b"] .t-icon[data-icon="a"] { opacity: 0; filter: blur(var(--icon-swap-blur)); transform: scale(var(--icon-swap-start-scale)); }
    .t-icon-swap.is-instant .t-icon { transition: none !important; }
    .t-spin-counter { display: inline-flex; align-items: baseline; font-variant-numeric: tabular-nums; }
    .t-spin-cell { position: relative; display: inline-grid; min-width: .58em; height: 1em; overflow: hidden; line-height: 1; text-align: center; }
    .t-spin-old, .t-spin-new { grid-area: 1 / 1; display: block; will-change: transform, opacity, filter; }
    .t-spin-cell.is-spinning .t-spin-old { animation: t-spin-out var(--motion-counter-spin) var(--motion-tabs-layout-ease) var(--spin-delay, 0ms) both; }
    .t-spin-cell.is-spinning .t-spin-new { animation: t-spin-in var(--motion-counter-spin) var(--motion-tabs-layout-ease) var(--spin-delay, 0ms) both; }
    @keyframes t-spin-out { to { opacity: 0; filter: blur(var(--counter-spin-blur)); transform: translateY(-105%); } }
    @keyframes t-spin-in { from { opacity: 0; filter: blur(var(--counter-spin-blur)); transform: translateY(105%); } to { opacity: 1; filter: blur(0); transform: translateY(0); } }
    .t-skel { position: relative; }
    .t-skel-skeleton, .t-skel-content { position: absolute; inset: 0; }
    .t-skel-skeleton { z-index: 1; opacity: 1; filter: blur(0); transition: opacity var(--motion-skeleton-reveal) ease-in-out, filter var(--motion-skeleton-reveal) ease-in-out; }
    .t-skel-content { z-index: 2; opacity: 0; filter: blur(var(--skeleton-blur)); transition: opacity var(--motion-skeleton-reveal) ease-in-out, filter var(--motion-skeleton-reveal) ease-in-out; }
    .t-skel.is-revealed .t-skel-skeleton { opacity: 0; filter: blur(var(--skeleton-blur)); pointer-events: none; }
    .t-skel.is-revealed .t-skel-content { opacity: 1; filter: blur(0); }
    .t-skel.is-resetting .t-skel-skeleton, .t-skel.is-resetting .t-skel-content { transition: none !important; }
    .t-skel-skeleton.is-pulsing > * { animation: t-skel-pulse var(--motion-skeleton-pulse) ease-in-out 1; }
    @keyframes t-skel-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .5; } }
    @media (prefers-reduced-motion: reduce) {
      .t-text-swap { transition: none !important; }
      .t-icon-swap .t-icon { transition: none !important; }
      .t-spin-cell.is-spinning > * { animation: none !important; }
      .t-skel-skeleton, .t-skel-content { transition: none !important; }
      .t-skel-skeleton.is-pulsing > * { animation: none !important; }
    }
  `;
  document.head.append(primitiveStyles);

  function bindContextMenus(root = document) {
    if (root.documentElement?.dataset.contextMenusBound === "true") return;
    if (root.documentElement) root.documentElement.dataset.contextMenusBound = "true";

    const contextMenu = document.createElement("div");
    contextMenu.className = "dp-context-menu dp-menu dp-popover";
    contextMenu.setAttribute("role", "menu");
    contextMenu.setAttribute("aria-label", "Context actions");
    contextMenu.hidden = true;
    document.body.append(contextMenu);

    let trigger = null;
    let keyboardOpen = false;

    const contextTarget = (eventTarget) =>
      eventTarget instanceof Element ? eventTarget.closest("[data-context-actions]") : null;

    const parseActions = (target) => target.dataset.contextActions
      .split("|")
      .map((entry) => entry.trim())
      .filter(Boolean)
      .map((entry) => {
        if (entry === "-") return { separator: true };
        const [rawAction, label, shortcut] = entry.split(":");
        return {
          action: (label === undefined ? entry : rawAction).replace(/!$/, ""),
          label: label ?? entry,
          shortcut: shortcut || null,
          tone: rawAction.endsWith("!") ? "danger" : "neutral",
        };
      });

    const close = ({ restoreFocus = true } = {}) => {
      if (contextMenu.hidden) return;
      contextMenu.hidden = true;
      contextMenu.setAttribute("aria-hidden", "true");
      contextMenu.replaceChildren();
      if (restoreFocus && trigger instanceof HTMLElement) trigger.focus({ preventScroll: true });
      trigger = null;
    };

    const targetLabel = (target) =>
      target.dataset.contextLabel || target.getAttribute("aria-label") || target.textContent.trim().replace(/\s+/g, " ");

    const performDefault = (target, action, label) => {
      if (action === "open") {
        target.click();
        return;
      }
      if (action === "new-view" && target.matches("a[href]")) {
        window.open(target.href, "_blank", "noopener,noreferrer");
        return;
      }
      if (action === "close-tab") {
        target.querySelector(".tab-close")?.click();
        return;
      }
      if (action === "copy-link" || action === "copy-path") {
        const value = target.dataset.contextValue || (target.matches("a[href]") ? target.href : targetLabel(target));
        navigator.clipboard?.writeText(value);
      }
      window.BOARD_TOAST?.show({
        title: label,
        description: targetLabel(target),
        status: "Mockup",
      });
    };

    const activate = (target, item) => {
      const detail = { action: item.action, target };
      const actionEvent = new CustomEvent("dp:context-action", { bubbles: true, cancelable: true, detail });
      target.dispatchEvent(actionEvent);
      close();
      if (!actionEvent.defaultPrevented) performDefault(target, item.action, item.label);
    };

    const place = (x, y) => {
      const edge = 8;
      contextMenu.style.left = `${Math.max(edge, x)}px`;
      contextMenu.style.top = `${Math.max(edge, y)}px`;
      const rect = contextMenu.getBoundingClientRect();
      contextMenu.style.left = `${Math.max(edge, Math.min(x, window.innerWidth - rect.width - edge))}px`;
      contextMenu.style.top = `${Math.max(edge, Math.min(y, window.innerHeight - rect.height - edge))}px`;
    };

    const open = (target, x, y, fromKeyboard = false) => {
      close({ restoreFocus: false });
      trigger = target;
      keyboardOpen = fromKeyboard;
      const fragment = document.createDocumentFragment();
      parseActions(target).forEach((item) => {
        if (item.separator) {
          const separator = document.createElement("div");
          separator.className = "dp-menu-separator";
          separator.setAttribute("role", "separator");
          fragment.append(separator);
          return;
        }
        const button = document.createElement("button");
        button.type = "button";
        button.className = "dp-menu-item";
        button.setAttribute("role", "menuitem");
        button.dataset.action = item.action;
        button.dataset.tone = item.tone;
        button.textContent = item.label;
        if (item.shortcut) {
          const shortcut = document.createElement("span");
          shortcut.className = "dp-menu-shortcut";
          shortcut.setAttribute("aria-hidden", "true");
          // "⌘⇧W" → modifier chips + one key chip; space-separated forms pass through
          const keys = item.shortcut.includes(" ")
            ? item.shortcut.split(" ")
            : [...item.shortcut.match(/[⌘⇧⌥⌃]/g) ?? [], item.shortcut.replace(/[⌘⇧⌥⌃]/g, "")].filter(Boolean);
          shortcut.append(createKbdGroup(keys));
          button.append(shortcut);
        }
        button.addEventListener("click", () => activate(target, item));
        fragment.append(button);
      });
      contextMenu.replaceChildren(fragment);
      contextMenu.hidden = false;
      contextMenu.setAttribute("aria-hidden", "false");
      place(x, y);
      if (keyboardOpen) contextMenu.querySelector("[role='menuitem']")?.focus({ preventScroll: true });
    };

    root.addEventListener("contextmenu", (event) => {
      const target = contextTarget(event.target);
      if (!target) return;
      event.preventDefault();
      open(target, event.clientX, event.clientY);
    });

    root.addEventListener("keydown", (event) => {
      if (!contextMenu.hidden) {
        const items = [...contextMenu.querySelectorAll("[role='menuitem']")];
        const current = items.indexOf(document.activeElement);
        if (event.key === "Escape") {
          event.preventDefault();
          close();
        } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          const direction = event.key === "ArrowDown" ? 1 : -1;
          items[(current + direction + items.length) % items.length]?.focus();
        } else if (event.key === "Home" || event.key === "End") {
          event.preventDefault();
          items[event.key === "Home" ? 0 : items.length - 1]?.focus();
        }
        return;
      }
      if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
        const target = contextTarget(event.target);
        if (!target) return;
        event.preventDefault();
        const rect = target.getBoundingClientRect();
        open(target, rect.left + 12, rect.bottom - 4, true);
      }
    });

    root.addEventListener("pointerdown", (event) => {
      if (!contextMenu.hidden && !contextMenu.contains(event.target)) close({ restoreFocus: false });
    }, true);
    window.addEventListener?.("blur", () => close({ restoreFocus: false }));
    window.addEventListener?.("resize", () => close({ restoreFocus: false }));
  }

  const bindLayoutGuides = (root = document) => {
    /* Review tooling belongs only to standalone mockups. Production imports
       this motion engine but deliberately has no review navigation. */
    if (!root.querySelector(".dp-review-nav")) return;
    if (root.querySelector(".dp-layout-guides")) return;
    const guideQueryEnabled = /(?:^|[?&])guides=1(?:&|$)/.test(window.location?.search || "");
    const overlay = document.createElement("div");
    overlay.className = "dp-layout-guides";
    overlay.setAttribute("aria-hidden", "true");
    const guideColors = ["#168cff", "#a855f7", "#16a36a", "#f97316", "#e5484d"];
    const createSelection = (className = "") => {
      const selection = document.createElement("div");
      selection.className = `dp-layout-selection ${className}`.trim();
      const guides = ["left", "center-x", "right", "top", "center-y", "bottom"].map((edge) => {
        const guide = document.createElement("span");
        guide.className = "dp-layout-guide";
        guide.dataset.axis = edge.includes("left") || edge.includes("right") || edge.includes("-x") ? "x" : "y";
        guide.dataset.guideEdge = edge;
        return guide;
      });
      const parentBox = document.createElement("span");
      parentBox.className = "dp-layout-parent-box";
      const inspectedBox = document.createElement("span");
      inspectedBox.className = "dp-layout-inspected-box";
      selection.append(parentBox, ...guides, inspectedBox);
      return { root: selection, guides, parentBox, inspectedBox };
    };
    const hoverSelection = createSelection("dp-layout-hover-selection");
    hoverSelection.root.hidden = true;
    const measure = document.createElement("span");
    measure.className = "dp-layout-measure";
    measure.hidden = true;
    overlay.append(hoverSelection.root, measure);
    document.body.append(overlay);

    const guideToggle = document.createElement("button");
    guideToggle.type = "button";
    guideToggle.className = "dp-review-guide-toggle";
    guideToggle.textContent = "Inspect layout";
    const shortcut = keyboard.key("G");
    guideToggle.append(shortcut);
    root.querySelector(".dp-review-nav nav")?.append(guideToggle);

    const selectedTargets = new Map();
    let hoveredTarget = null;
    let pinned = false;
    const positionBox = (node, rect) => {
      node.style.left = `${rect.left}px`;
      node.style.top = `${rect.top}px`;
      node.style.width = `${rect.width}px`;
      node.style.height = `${rect.height}px`;
    };
    const clearSelections = () => {
      selectedTargets.forEach((selection) => selection.root.remove());
      selectedTargets.clear();
      document.body.classList.remove("dp-guides-pinned");
      pinned = false;
    };
    const inspect = (selection, target) => {
      if (!target?.closest || target.closest(".dp-review-nav, .dp-layout-guides")) return false;
      const rect = target.getBoundingClientRect();
      if (!rect.width || !rect.height) return false;
      const parent = target.parentElement;
      const parentRect = parent?.getBoundingClientRect?.() || rect;
      const parentStyle = parent && window.getComputedStyle(parent);
      const positions = {
        left: rect.left,
        "center-x": rect.left + rect.width / 2,
        right: rect.right,
        top: rect.top,
        "center-y": rect.top + rect.height / 2,
        bottom: rect.bottom,
      };
      selection.guides.forEach((guide) => guide.style.setProperty("--guide-position", `${positions[guide.dataset.guideEdge]}px`));
      positionBox(selection.inspectedBox, rect);
      positionBox(selection.parentBox, parentRect);
      const padding = parentStyle ? [parentStyle.paddingTop, parentStyle.paddingRight, parentStyle.paddingBottom, parentStyle.paddingLeft].map((value) => Math.round(Number.parseFloat(value) || 0)) : [0, 0, 0, 0];
      measure.textContent = `${Math.round(rect.width)} × ${Math.round(rect.height)} · parent padding ${padding.join(" / ")}${selectedTargets.size ? ` · ${selectedTargets.size} selected` : ""}`;
      measure.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 420))}px`;
      measure.style.top = `${Math.max(8, rect.top - 28)}px`;
      measure.hidden = false;
      return true;
    };
    const clearInspection = () => {
      clearSelections();
      hoverSelection.root.hidden = true;
      measure.hidden = true;
      hoveredTarget = null;
    };
    const selectTarget = (target, additive = false) => {
      if (!target?.closest || target.closest(".dp-review-nav, .dp-layout-guides")) return false;
      if (additive && selectedTargets.has(target)) {
        selectedTargets.get(target).root.remove();
        selectedTargets.delete(target);
        pinned = selectedTargets.size > 0;
        document.body.classList.toggle("dp-guides-pinned", pinned);
        if (!pinned) measure.hidden = true;
        else {
          const [lastTarget, lastSelection] = [...selectedTargets.entries()].at(-1);
          inspect(lastSelection, lastTarget);
        }
        return true;
      }
      if (!additive) clearSelections();
      if (!selectedTargets.has(target)) {
        const selection = createSelection("dp-layout-pinned-selection");
        selection.root.style.setProperty("--guide-color", guideColors[selectedTargets.size % guideColors.length]);
        overlay.insertBefore(selection.root, measure);
        selectedTargets.set(target, selection);
      }
      hoverSelection.root.hidden = true;
      inspect(selectedTargets.get(target), target);
      pinned = true;
      document.body.classList.add("dp-guides-pinned");
      measure.textContent = `${measure.textContent.replace(/ · \d+ selected$/, "")} · ${selectedTargets.size} selected`;
      return true;
    };
    const setEnabled = (enabled, updateUrl = false) => {
      document.body.classList.toggle("dp-guides-on", enabled);
      guideToggle.setAttribute("aria-pressed", String(enabled));
      if (!enabled) clearInspection();
      if (!updateUrl) return;
      const query = (window.location?.search || "").slice(1).split("&").filter(Boolean).filter((part) => !part.startsWith("guides="));
      if (enabled) query.push("guides=1");
      const next = `${window.location?.pathname || ""}${query.length ? `?${query.join("&")}` : ""}${window.location?.hash || ""}`;
      window.history?.replaceState?.({}, "", next);
    };
    const toggle = () => setEnabled(!document.body.classList.contains("dp-guides-on"), true);
    guideToggle.addEventListener("click", toggle);
    root.addEventListener("pointerover", (event) => {
      if (!document.body.classList.contains("dp-guides-on") || selectedTargets.has(event.target)) return;
      hoveredTarget = event.target;
      hoverSelection.root.hidden = false;
      hoverSelection.root.style.setProperty("--guide-color", "#ff453a");
      if (!inspect(hoverSelection, event.target)) hoverSelection.root.hidden = true;
    });
    root.addEventListener("click", (event) => {
      if (!document.body.classList.contains("dp-guides-on") || event.target.closest?.(".dp-review-nav")) return;
      if (!event.shiftKey) clearSelections();
      if (!selectTarget(event.target, event.shiftKey)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    root.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && pinned) {
        event.preventDefault();
        clearInspection();
        return;
      }
      if (["Enter", " "].includes(event.key) && document.body.classList.contains("dp-guides-on") && !event.target.closest?.(".dp-review-nav")) {
        if (!event.shiftKey) clearSelections();
        if (!selectTarget(event.target, event.shiftKey)) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      if (event.key.toLowerCase() !== "g" || event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.target.closest?.("input, textarea, select, [contenteditable='true']")) return;
      event.preventDefault();
      toggle();
    }, true);
    const refreshSelections = () => {
      selectedTargets.forEach((selection, target) => inspect(selection, target));
      if (hoveredTarget && !hoverSelection.root.hidden) inspect(hoverSelection, hoveredTarget);
    };
    window.addEventListener?.("resize", refreshSelections);
    root.addEventListener("scroll", refreshSelections, true);
    setEnabled(guideQueryEnabled);
  };

  Object.defineProperty(window, "BOARD_MOTION", {
    configurable: false,
    enumerable: true,
    writable: false,
    value: motion,
  });
  const bindShellControls = () => {
    motion.iconButton.bind(document);
    motion.shellControls.bind(document);
    motion.pageEntrance.bind(document);
    bindContextMenus(document);
    bindLayoutGuides(document);
  };
  if (document.readyState === "loading") window.addEventListener?.("DOMContentLoaded", bindShellControls, { once: true });
  else bindShellControls();
})();
