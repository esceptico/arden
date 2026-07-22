(() => {
  const body = document.body;
  const motion = window.BOARD_MOTION;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const sleep = delay => new Promise(resolve => setTimeout(resolve, delay));
  const timing = (duration = motion.duration.dissolve) => reduced.matches ? 1 : duration;

  const titles = {
    settled: 'Launch research brief',
    live: 'Competitor dataset',
    approval: 'Publish release notes',
    child: 'Countercase reviewer',
    edges: 'Launch research brief',
  };
  const sessionForScene = { settled: 'main', live: 'workflow', approval: 'approval', child: 'main', edges: 'main' };
  const sceneForSession = { main: 'settled', workflow: 'live', approval: 'approval' };
  const state = {
    scene: body.dataset.scene || 'settled',
    previousScene: 'settled',
    sidebarOpen: !body.classList.contains('compact'),
    inspectorOpen: body.classList.contains('peek-open'),
    inspectorDocked: body.classList.contains('peek-docked'),
    inspectorTab: $('.peek')?.dataset.tab || 'activity',
    model: 'claude-opus-4.6',
    effort: 'Off',
    modelEfforts: {
      'claude-opus-4.6': 'Off',
      'claude-sonnet-4.5': 'High',
      'gpt-5.4': 'Medium',
      'gemini-3.1-pro': 'High',
    },
    queue: [],
  };

  const chatTitle = $('#chat-title');
  const composerInput = $('#composer-input');
  const peek = $('.peek');
  const inspectorToggle = $('#inspector-toggle');
  const inspectorDock = $('#inspector-dock');
  const inspectorClose = $('#inspector-close');
  const queueRegion = $('#message-queue');
  const sendButton = $('.send-btn');
  const bottomStack = $('.bottom-stack');
  const budgetWrap = $('.budget-wrap');
  const budgetTrigger = $('.budget-trigger');
  const budgetPopover = $('.budget-popover');
  const sidebar = $('.rail');
  const workspace = $('.workspace');
  const narrowShell = motion.geometry.maxWidthQuery('--breakpoint-narrow');
  const singleSidebarShell = motion.geometry.maxWidthQuery('--breakpoint-single-sidebar');
  let sceneIntent = state.scene;
  let inspectorDockIntent = state.inspectorDocked;
  let restoreSidebarAfterInspector = false;
  let peekController = null;
  let inspectorTabsController = null;
  let liveToolTickerToken = 0;

  const TRACE_TAIL_MAX = motion.limits.traceTail;
  const LIVE_TOOL_CALLS = Object.freeze([
    { icon: 'files', verb: 'Read retention cohorts', detail: 'workspace evidence from expansion accounts', elapsed: '3s' },
    { icon: 'search', verb: 'Compared buyer authority', detail: 'enthusiasm separated from purchase control', elapsed: '4s' },
    { icon: 'globe', verb: 'Checked expansion evidence', detail: 'durable adoption across adjacent teams', elapsed: '3s' },
    { icon: 'file', verb: 'Saved counter-evidence notes', detail: 'strongest objections grouped for the brief', elapsed: '2s' },
    { icon: 'activity', verb: 'Drafting sourced position', detail: 'final recommendation with evidence links', elapsed: '5s' },
  ]);

  function createTraceGlyph(icon) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', `#dp-${icon}`);
    svg.append(use);
    return svg;
  }

  function createLiveToolStep(item) {
    const step = document.createElement('button');
    step.type = 'button';
    step.className = 'step live last';
    step.dataset.finishedSuffix = item.elapsed;
    step.setAttribute('aria-label', `${item.verb}: ${item.detail}`);

    const gutter = document.createElement('span');
    gutter.className = 'step-gutter';
    gutter.append(createTraceGlyph(item.icon));
    const thread = document.createElement('i');
    thread.className = 'thread';
    gutter.append(thread);

    const body = document.createElement('span');
    body.className = 'step-body';
    const verb = document.createElement('span');
    verb.className = 'step-verb';
    const label = document.createElement('span');
    label.className = 'shimmer';
    label.textContent = item.verb;
    const suffix = document.createElement('span');
    suffix.className = 'step-suffix t-text-swap';
    suffix.textContent = 'now';
    verb.append(label, suffix);
    const detail = document.createElement('span');
    detail.className = 'step-detail progress';
    detail.textContent = item.detail;
    body.append(verb, detail);
    step.append(gutter, body);
    step.addEventListener('click', () => setInspectorTab('activity'));
    return step;
  }

  function settleLiveToolStep(steps) {
    const active = $('.step.live', steps);
    if (!active) return;
    active.classList.remove('live');
    $('.shimmer', active)?.classList.remove('shimmer');
    const suffix = $('.step-suffix', active);
    if (suffix) motion.textSwap.swap(suffix, active.dataset.finishedSuffix || suffix.textContent);
  }

  function appendLiveToolCall(item) {
    const steps = $('[data-live-tool-tail]');
    if (!steps || state.scene !== 'live') return;
    const currentRows = [...steps.children].filter(row => row.classList.contains('step'));
    const before = new Map(currentRows.map(row => [row, row.getBoundingClientRect()]));
    settleLiveToolStep(steps);

    const incoming = createLiveToolStep(item);
    steps.append(incoming);
    const allRows = [...steps.children].filter(row => row.classList.contains('step'));
    const leavingRows = allRows.slice(0, Math.max(0, allRows.length - TRACE_TAIL_MAX));
    const keptRows = allRows.slice(-TRACE_TAIL_MAX);
    const containerRect = steps.getBoundingClientRect();

    leavingRows.forEach(row => {
      const rect = row.getBoundingClientRect();
      row.classList.add('trace-row-leaving');
      row.setAttribute('aria-hidden', 'true');
      row.inert = true;
      row.style.top = `${rect.top - containerRect.top}px`;
      row.style.left = `${rect.left - containerRect.left}px`;
      row.style.width = `${rect.width}px`;
      row.style.height = `${rect.height}px`;
    });
    keptRows.forEach((row, index) => row.classList.toggle('last', index === keptRows.length - 1));

    const transition = motion.layout.animateListChange({
      before,
      staying: keptRows.filter(row => row !== incoming),
      entering: incoming,
      leaving: leavingRows,
    });

    const count = $('[data-live-call-count]');
    if (count) {
      const total = Number(count.dataset.liveCallCount || 0) + 1;
      count.dataset.liveCallCount = String(total);
      if (motion.spinningCounter) {
        motion.spinningCounter.set(count, total, { label: value => `${value} tool calls` });
      } else {
        count.textContent = String(total);
      }
    }
    syncConversationRail();
    transition.then(() => {
      leavingRows.forEach(row => row.remove());
      syncConversationRail();
    });
  }

  function startLiveToolTicker() {
    const token = ++liveToolTickerToken;
    if (state.scene !== 'live') return;
    const status = $('.run-status', $('[data-scene="live"]'));
    if (status) {
      motion.textSwap.swap(status, 'Working', { animate: false });
      status.classList.add('shimmer');
    }
    void (async () => {
      for (const item of LIVE_TOOL_CALLS) {
        await sleep(motion.duration.toolCallCadence);
        if (token !== liveToolTickerToken || state.scene !== 'live') return;
        appendLiveToolCall(item);
      }
      if (token !== liveToolTickerToken || state.scene !== 'live') return;
      completeLiveRunStatus();
    })();
  }

  function completeLiveRunStatus() {
    settleLiveToolStep($('[data-live-tool-tail]'));
    const status = $('.run-status', $('[data-scene="live"]'));
    if (!status) return;
    status.classList.remove('shimmer');
    motion.textSwap.swap(status, 'Worked');
  }

  function transitionShell(update, { animate = true } = {}) {
    return motion.layout.transitionCentered(workspace, update, {
      animate,
      anchor: () => $('.chat-lane'),
    });
  }

  function syncSceneChrome() {
    body.dataset.scene = state.scene;
    chatTitle.textContent = titles[state.scene];
    composerInput.placeholder = state.scene === 'edges' ? 'Answer the agent…' : 'Ask or steer the agent…';
    $$('[data-scene-button]').forEach(button => button.classList.toggle('on', button.dataset.sceneButton === state.scene));
    $$('.session[data-session]').forEach(button => button.classList.toggle('on', button.dataset.session === sessionForScene[state.scene]));
    syncInspectorContext();
    syncConversationRail();
    syncComposerMode();
    startLiveToolTicker();
  }

  function syncComposerMode() {
    const streaming = state.scene === 'live';
    const hasDraft = Boolean(composerInput.value.trim());
    const mode = streaming ? (hasDraft ? 'queue' : 'stop') : 'send';
    body.dataset.composerMode = mode;
    motion.iconSwap.swap(sendButton, mode === 'stop' ? '#dp-stop' : '#dp-arrow-up');
    sendButton.setAttribute('aria-label', mode === 'stop' ? 'Stop response' : mode === 'queue' ? 'Queue message' : 'Send');
    sendButton.title = mode === 'stop' ? 'Stop response' : mode === 'queue' ? 'Queue message' : 'Send';
  }

  function renderQueue() {
    const activeIds = new Set(state.queue.map(item => item.id));
    $$('.queue-card', queueRegion).forEach(card => {
      if (activeIds.has(card.dataset.queueId) || card.classList.contains('exiting')) return;
      card.classList.add('exiting');
      setTimeout(() => card.remove(), timing(motion.duration.exit));
    });
    state.queue.forEach((item, index) => {
      let card = $(`.queue-card[data-queue-id="${item.id}"]`, queueRegion);
      if (!card) {
        card = document.createElement('div');
        card.className = 'queue-card entering';
        card.dataset.queueId = item.id;
        card.setAttribute('role', 'listitem');
        card.tabIndex = 0;
        card.innerHTML = '<span class="queue-position"></span><span class="queue-label"></span><span class="queue-actions"><button type="button" class="queue-edit dp-icon-button" aria-label="Edit queued message" title="Edit"><svg><use href="#dp-edit"/></svg></button><button type="button" class="queue-cancel dp-icon-button" aria-label="Cancel queued message" title="Cancel"><svg><use href="#dp-close"/></svg></button></span>';
        $$('.queue-actions .dp-icon-button', card).forEach(button => motion.iconButton.enhance(button));
        card.addEventListener('dblclick', () => editQueuedMessage(item.id));
        card.addEventListener('keydown', event => {
          if (event.key === 'Enter' || event.key === 'F2') { event.preventDefault(); editQueuedMessage(item.id); }
          else if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); removeQueuedMessage(item.id); }
          else if (event.altKey && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) { event.preventDefault(); moveQueuedMessage(item.id, event.key === 'ArrowUp' ? -1 : 1); }
        });
        $('.queue-edit', card).addEventListener('click', event => { event.stopPropagation(); editQueuedMessage(item.id); });
        $('.queue-cancel', card).addEventListener('click', event => { event.stopPropagation(); removeQueuedMessage(item.id); });
        queueRegion.append(card);
        requestAnimationFrame(() => card.classList.remove('entering'));
      }
      $('.queue-position', card).textContent = index === 0 ? 'Next' : `#${index + 1}`;
      $('.queue-label', card).textContent = item.text;
      $('.queue-edit', card).setAttribute('aria-label', `Edit queued message ${index + 1}`);
      $('.queue-cancel', card).setAttribute('aria-label', `Cancel queued message ${index + 1}`);
      card.setAttribute('aria-label', `Queued message ${index + 1} of ${state.queue.length}: ${item.text}`);
      card.style.setProperty('--queue-peek', `${-Math.min(index, 2) * motion.geometry.read('--queue-card-offset')}px`);
      card.style.setProperty('--queue-open', `${-index * 42}px`);
      card.style.setProperty('--queue-scale', String(1 - Math.min(index, 2) * motion.geometry.number('--queue-card-scale-step')));
      card.style.setProperty('--queue-opacity', index <= 2 ? '1' : '0');
      card.style.zIndex = String(100 - index);
      queueRegion.append(card);
    });
  }

  function enqueueMessage(text) {
    state.queue.push({ id: `queue-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`, text });
    renderQueue();
  }

  function editQueuedMessage(id) {
    const index = state.queue.findIndex(item => item.id === id);
    if (index < 0) return;
    composerInput.value = state.queue[index].text;
    state.queue.splice(index, 1);
    renderQueue();
    autoSizeComposer();
    syncComposerMode();
    composerInput.focus();
    composerInput.setSelectionRange(composerInput.value.length, composerInput.value.length);
  }

  function removeQueuedMessage(id) {
    state.queue = state.queue.filter(item => item.id !== id);
    renderQueue();
  }

  function moveQueuedMessage(id, direction) {
    const from = state.queue.findIndex(item => item.id === id);
    const to = from + direction;
    if (from < 0 || to < 0 || to >= state.queue.length) return;
    [state.queue[from], state.queue[to]] = [state.queue[to], state.queue[from]];
    renderQueue();
    $(`.queue-card[data-queue-id="${id}"]`, queueRegion)?.focus();
  }

  function dispatchQueueHead() {
    if (!state.queue.length) return false;
    const [next] = state.queue.splice(0, 1);
    renderQueue();
    composerInput.placeholder = `Running queued message: ${next.text}`;
    return true;
  }

  function handleComposerAction() {
    const text = composerInput.value.trim();
    if (state.scene !== 'live') return;
    if (text) {
      enqueueMessage(text);
      composerInput.value = '';
      autoSizeComposer();
      syncComposerMode();
      composerInput.focus();
    } else if (!dispatchQueueHead()) {
      setScene('settled');
    }
  }

  async function resolveApproval(button) {
    const controls=$$('[data-approval-action]');
    if(controls.some(control=>control.disabled))return;
    const allow=button.dataset.approvalAction==='allow';
    const label=$('.approval-action-label',button);
    const original=label.textContent;
    const pending=allow?'Approving':'Denying';
    const result=allow?'Approved':'Denied';
    controls.forEach(control=>{control.disabled=true});
    button.setAttribute('aria-busy','true');
    motion.textSwap.swap(label,pending);
    await sleep(timing(motion.duration.textSwap * 2));
    motion.textSwap.swap(label,result);
    await sleep(timing(motion.duration.textSwap * 2 + motion.duration.acknowledge));
    button.removeAttribute('aria-busy');
    await setScene(state.previousScene);
    motion.textSwap.swap(label,original,{animate:false});
    controls.forEach(control=>{control.disabled=false});
  }

  async function setScene(scene, { animate = true } = {}) {
    if (!titles[scene] || scene === sceneIntent) return;
    sceneIntent = scene;
    if (scene === 'approval') state.previousScene = state.scene;
    const lane = $('.chat-lane');
    const swap = () => {
      state.scene = scene;
      $$('.scene').forEach(candidate => candidate.classList.toggle('on', candidate.dataset.scene === scene));
      syncSceneChrome();
      $('.chat-scroll')?.scrollTo({ top: 0 });
      const summary = $('[data-run-summary-reveal]');
      if (scene === 'live') motion.skeletonReveal.replay(summary, { delay: motion.duration.skeletonSummaryDelay });
      else motion.skeletonReveal.reveal(summary);
    };
    if (animate) await motion.content.swap([lane, chatTitle], swap);
    else swap();
    syncConversationRail();
  }

  function syncInspectorContext() {
    const context = state.scene === 'child' ? 'child' : 'main';
    $$('.inspector-document').forEach(document => { document.hidden = document.dataset.context !== context; });
  }

  function syncInspectorControls() {
    body.classList.toggle('peek-docked', state.inspectorDocked);
    inspectorToggle?.setAttribute('aria-pressed', String(state.inspectorOpen));
    inspectorToggle?.setAttribute('aria-label', 'Show details');
    inspectorToggle?.setAttribute('aria-hidden', String(state.inspectorOpen));
    if (inspectorToggle) {
      inspectorToggle.title = 'Show details';
      inspectorToggle.inert = state.inspectorOpen;
    }
    motion.iconSwap.swap(inspectorToggle, '#dp-panel-left-close', { animate: false });
    inspectorDock?.setAttribute('aria-pressed', String(state.inspectorDocked));
    inspectorDock?.setAttribute('aria-label', state.inspectorDocked ? 'Float details panel' : 'Dock details as sidebar');
    inspectorDock && (inspectorDock.title = state.inspectorDocked ? 'Float panel' : 'Dock as sidebar');
    motion.iconSwap.swap(inspectorDock, state.inspectorDocked ? '#dp-arrows-in' : '#dp-arrows-out');
    $('#peek-toggle')?.classList.toggle('on', state.inspectorOpen);
  }

  function updateInspectorShell() {
    const open = state.inspectorOpen;
    if (open && state.inspectorDocked && singleSidebarShell.matches && state.sidebarOpen) {
      restoreSidebarAfterInspector = true;
      state.sidebarOpen = false;
    } else if (!open && restoreSidebarAfterInspector) {
      restoreSidebarAfterInspector = false;
      state.sidebarOpen = true;
    }
    syncSidebarControls();
    body.classList.toggle('peek-open', open);
  }

  function setInspectorOpen(open, { animate = true } = {}) {
    if (state.inspectorOpen === open) return;
    state.inspectorOpen = open;
    syncInspectorControls();
    return peekController?.setOpen(open, { animate, focus: false, restoreFocus: false });
  }

  async function setInspectorDocked(docked) {
    if (inspectorDockIntent === docked) return;
    inspectorDockIntent = docked;
    const apply = () => {
      state.inspectorDocked = docked;
      state.inspectorOpen = true;
      if (docked && singleSidebarShell.matches && state.sidebarOpen) {
        restoreSidebarAfterInspector = true;
        state.sidebarOpen = false;
      } else if (!docked && restoreSidebarAfterInspector) {
        restoreSidebarAfterInspector = false;
        state.sidebarOpen = true;
      }
      syncSidebarControls();
      syncInspectorControls();
    };
    transitionShell(apply);
  }

  function setInspectorTab(tab) {
    if (!['activity', 'sources'].includes(tab)) return;
    const wasOpen = state.inspectorOpen;
    inspectorTabsController?.select(tab);
    if (!wasOpen) setInspectorOpen(true);
  }

  function syncSidebarControls() {
    motion.sidebar.sync({ className: 'compact', hidden: !state.sidebarOpen, sidebar });
    sidebar?.setAttribute('aria-hidden', String(!state.sidebarOpen));
    if (sidebar) sidebar.inert = !state.sidebarOpen;
    motion.shellToggle.sync($('#sidebar-toggle'), { expanded: state.sidebarOpen, expandedIcon: '#dp-panel-left-close', collapsedIcon: '#dp-panel-right' });
    $('#compact-toggle')?.classList.toggle('wide', state.sidebarOpen);
  }

  async function setSidebarOpen(open, { animate = true } = {}) {
    if (open && singleSidebarShell.matches && state.inspectorOpen && state.inspectorDocked) {
      await setInspectorOpen(false, { animate });
    }
    if (state.sidebarOpen === open) return;
    state.sidebarOpen = open;
    if (!animate) body.classList.add('no-motion');
    transitionShell(syncSidebarControls, { animate });
    if (!animate) requestAnimationFrame(() => body.classList.remove('no-motion'));
  }

  function toggleSidebar(options) {
    setSidebarOpen(!state.sidebarOpen, options);
  }

  async function setDisclosure(container, open) {
    return motion.disclosure.set(container, open, {
      triggerSelector: '.trace-head, .workflow-toggle',
      panelSelector: '.steps, .workflow-progress, .workflow-detail',
      onLayout: syncConversationRail,
    });
  }

  function autoSizeComposer() {
    composerInput.style.height = 'auto';
    composerInput.style.height = `${Math.min(motion.geometry.read('--composer-max-input-height'), composerInput.scrollHeight)}px`;
  }

  function syncChatBottomHeight() {
    if (!bottomStack) return;
    document.documentElement.style.setProperty('--chat-bottom-h', `${bottomStack.getBoundingClientRect().height}px`);
  }

  let budgetPinned = false;
  let budgetCloseTimer = 0;
  function setBudgetOpen(open, { pinned = budgetPinned } = {}) {
    budgetPinned = open && pinned;
    motion.popover.sync(budgetWrap, budgetTrigger, budgetPopover, open);
  }
  function scheduleBudgetClose() {
    clearTimeout(budgetCloseTimer);
    budgetCloseTimer = setTimeout(() => {
      if (!budgetPinned && !budgetWrap?.matches(':focus-within')) setBudgetOpen(false, { pinned: false });
    }, timing(motion.duration.feedback));
  }

  const config = $('.model-config-wrap');
  const configTrigger = $('.model-config-trigger');
  const configMenu = $('.model-config-menu');
  const effortMenu = $('.model-effort-menu');
  let activeEffortModel = null;
  function setConfigOpen(open) {
    if (!open) setEffortOpen(false);
    motion.popover.sync(config, configTrigger, configMenu, open);
  }
  function effortTriggerFor(model) {
    return $$('[data-model-effort]', config).find(trigger => trigger.dataset.modelEffort === model) || $('.model-effort-trigger', config);
  }
  function setEffortOpen(open, model = activeEffortModel, { animateTrigger = false } = {}) {
    const trigger = effortTriggerFor(model);
    if (!trigger || !effortMenu) return;
    if (open) {
      activeEffortModel = model;
      state.model = model;
      state.effort = state.modelEfforts[model];
    }
    motion.popover.sync(config, trigger, effortMenu, open, { className: 'effort-open' });
    if (!open) activeEffortModel = null;
    syncConfig({ animateTrigger });
  }
  function syncConfig({ animateTrigger = false } = {}) {
    state.effort = state.modelEfforts[state.model];
    if (animateTrigger) {
      motion.textSwap.swap($('.model-current'), state.model);
      motion.textSwap.swap($('.effort-current'), state.effort);
    } else {
      $('.model-current').textContent = state.model;
      $('.effort-current').textContent = state.effort;
    }
    $$('[data-model]').forEach(option => option.setAttribute('aria-checked', String(option.dataset.model === state.model)));
    $$('[data-model-row]').forEach(row => row.classList.toggle('effort-active', config.classList.contains('effort-open') && row.dataset.modelRow === activeEffortModel));
    $$('[data-model-effort]').forEach(trigger => {
      $('span', trigger).textContent = state.modelEfforts[trigger.dataset.modelEffort];
      trigger.setAttribute('aria-expanded', String(config.classList.contains('effort-open') && trigger.dataset.modelEffort === activeEffortModel));
    });
    const effort = activeEffortModel ? state.modelEfforts[activeEffortModel] : state.effort;
    $$('[data-effort]', effortMenu).forEach(option => option.setAttribute('aria-checked', String(option.dataset.effort === effort)));
  }

  const RAIL_BASE_W = motion.geometry.read('--conversation-rail-base-width');
  const RAIL_ACTIVE_W = motion.geometry.read('--conversation-rail-active-width');
  const RAIL_HOVER_W = motion.geometry.read('--conversation-rail-hover-width');
  const RAIL_SIGMA_Y = motion.geometry.read('--conversation-rail-sigma-y');
  const RAIL_FULL_X = motion.geometry.read('--conversation-rail-full-x');
  const RAIL_SIGMA_X = motion.geometry.read('--conversation-rail-sigma-x');
  const RAIL_MAX_DX = motion.geometry.read('--conversation-rail-max-dx');
  const RAIL_MAX_DY = motion.geometry.read('--conversation-rail-max-dy');
  const RAIL_FADE_X = motion.geometry.read('--conversation-rail-fade-x');
  const RAIL_LABEL_ON = motion.geometry.number('--conversation-rail-label-on');
  const RAIL_LABEL_OFF = motion.geometry.number('--conversation-rail-label-off');
  const RAIL_READ_LINE = motion.geometry.read('--conversation-rail-read-line');
  const RAIL_BOTTOM_THRESHOLD = motion.geometry.read('--conversation-rail-bottom-threshold');
  const RAIL_POINTER_LEAD = motion.geometry.read('--conversation-rail-pointer-lead');
  const RAIL_LABEL_BLUR = motion.geometry.read('--conversation-rail-label-blur');
  let railAnchors = [];
  let railActiveIndex = 0;
  let railPointerRaf = 0;
  let railEngaged = false;
  let railLabelOn = false;

  function conversationAnchors() {
    const scene = $('.scene.on');
    if (!scene) return [];
    return $$(':scope .user-row, :scope .trace-head, :scope .step, :scope .assistant > h2, :scope .assistant > p, :scope .artifact, :scope .answer-footer, :scope .inline-approval, :scope .edge-card', scene)
      .filter(anchor => anchor.getClientRects().length > 0 && !anchor.classList.contains('trace-row-leaving'));
  }
  function syncConversationRail() {
    const rail = $('.conversation-rail');
    const band = $('.conversation-rail-band', rail);
    if (!rail || !band) return;
    const label = $('.conversation-rail-label', band);
    const anchors = conversationAnchors();
    const scroll = $('.chat-scroll');
    rail.hidden = anchors.length < 2 || !scroll;
    const changed = anchors.length !== railAnchors.length || anchors.some((anchor, index) => anchor !== railAnchors[index]);
    railAnchors = anchors;
    if (!changed) { updateConversationRail(); return; }
    band.replaceChildren(...anchors.map((anchor, index) => {
      const button = document.createElement('button');
      button.className = 'conversation-rail-tick';
      const title = anchor.textContent.trim().replace(/\s+/g, ' ');
      button.setAttribute('aria-label', title || `Conversation position ${index + 1} of ${anchors.length}`);
      const mark = document.createElement('span');
      mark.className = 'conversation-rail-mark';
      button.append(mark);
      button.addEventListener('click', () => {
        anchor.scrollIntoView({ block: 'start', behavior: reduced.matches ? 'auto' : 'smooth' });
      });
      return button;
    }), ...(label ? [label] : []));
    updateConversationRail();
  }
  function updateConversationRail() {
    const scroll = $('.chat-scroll');
    const ticks = $$('.conversation-rail-tick');
    const anchors = railAnchors;
    if (!scroll || !ticks.length || ticks.length !== anchors.length) return;
    const scrollRect = scroll.getBoundingClientRect();
    const readLine = scrollRect.top + RAIL_READ_LINE;
    let active = 0;
    anchors.forEach((anchor, index) => {
      const rect = anchor.getBoundingClientRect();
      if (rect.top <= readLine) active = index;
      ticks[index].classList.toggle('visible', rect.bottom > scrollRect.top && rect.top < scrollRect.bottom);
    });
    if (scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop < RAIL_BOTTOM_THRESHOLD) active = anchors.length - 1;
    const activeChanged = active !== railActiveIndex;
    railActiveIndex = active;
    ticks.forEach((tick, index) => {
      tick.classList.toggle('on', index === active);
      if (!railEngaged) $('.conversation-rail-mark', tick).style.width = `${index === active ? RAIL_ACTIVE_W : RAIL_BASE_W}px`;
    });
    if (activeChanged) {
      const follow = () => ticks[active]?.scrollIntoView({ block: 'nearest' });
      follow();
      setTimeout(follow, motion.duration.tabsSliding);
    }
  }

  function restConversationRail() {
    const label = $('.conversation-rail-label');
    railEngaged = false;
    railLabelOn = false;
    $$('.conversation-rail-mark').forEach((mark, index) => {
      mark.style.transition = 'width var(--motion-furniture) var(--ease), background-color var(--motion-furniture) var(--ease)';
      mark.style.width = `${index === railActiveIndex ? RAIL_ACTIVE_W : RAIL_BASE_W}px`;
    });
    if (label) {
      label.style.transition = 'opacity var(--motion-feedback) var(--ease), filter var(--motion-feedback) var(--ease)';
      label.style.opacity = '0';
      label.style.filter = `blur(${RAIL_LABEL_BLUR}px)`;
    }
  }

  function updateConversationRailField(cursorX, cursorY) {
    const band = $('.conversation-rail-band');
    const label = $('.conversation-rail-label');
    const marks = $$('.conversation-rail-mark');
    const scene = $('.scene.on');
    if (!band || !label || !scene || !marks.length) return;
    const bandRect = band.getBoundingClientRect();
    const contentLeft = scene.getBoundingClientRect().left;
    const limit = Math.min(bandRect.left + RAIL_MAX_DX, contentLeft);
    const xStart = bandRect.left - RAIL_POINTER_LEAD;
    const yStart = bandRect.top - RAIL_MAX_DY;
    const yEnd = bandRect.bottom + RAIL_MAX_DY;
    if (cursorX < xStart || cursorX > limit || cursorY < yStart || cursorY > yEnd) { restConversationRail(); return; }
    const envX = cursorX > limit - RAIL_FADE_X ? motion.geometry.smoothstep((limit - cursorX) / RAIL_FADE_X) : 1;
    const envY = cursorY < bandRect.top ? motion.geometry.smoothstep((cursorY - yStart) / RAIL_MAX_DY) : cursorY > bandRect.bottom ? motion.geometry.smoothstep((yEnd - cursorY) / RAIL_MAX_DY) : 1;
    const env = envX * envY;
    let nearest = 0;
    let nearestDistance = Infinity;
    let strength = 0;
    marks.forEach((mark, index) => {
      const rect = mark.getBoundingClientRect();
      const dy = cursorY - (rect.top + rect.height / 2);
      const dx = Math.max(0, cursorX - (rect.left + RAIL_FULL_X));
      const field = motion.geometry.gaussianField({ dx, dy, sigmaX: RAIL_SIGMA_X, sigmaY: RAIL_SIGMA_Y, envelope: env });
      const base = index === railActiveIndex ? RAIL_ACTIVE_W : RAIL_BASE_W;
      mark.style.transition = 'none';
      mark.style.width = `${base + (RAIL_HOVER_W - base) * field}px`;
      if (Math.abs(dy) < nearestDistance) { nearestDistance = Math.abs(dy); nearest = index; strength = field; }
    });
    railEngaged = true;
    const show = railLabelOn ? strength > RAIL_LABEL_OFF : strength > RAIL_LABEL_ON;
    railLabelOn = show;
    if (!show) {
      label.style.opacity = '0';
      label.style.filter = `blur(${RAIL_LABEL_BLUR}px)`;
      return;
    }
    const tick = marks[nearest].parentElement;
    const tickRect = tick.getBoundingClientRect();
    label.textContent = tick.getAttribute('aria-label') || 'Message';
    label.style.top = `${tickRect.top + tickRect.height / 2 - bandRect.top}px`;
    label.style.transition = label.style.opacity === '1' ? 'top var(--motion-feedback) var(--ease), opacity var(--motion-feedback) var(--ease), filter var(--motion-feedback) var(--ease)' : 'opacity var(--motion-feedback) var(--ease), filter var(--motion-feedback) var(--ease)';
    label.style.opacity = '1';
    label.style.filter = 'blur(0)';
  }

  document.addEventListener('pointermove', event => {
    const x = event.clientX, y = event.clientY;
    if (railPointerRaf) cancelAnimationFrame(railPointerRaf);
    railPointerRaf = requestAnimationFrame(() => { railPointerRaf = 0; updateConversationRailField(x, y); });
  }, { passive: true });
  function updateScrollEdges() {
    const scroll = $('.chat-scroll');
    const topEdge = $('.edge-blur.top');
    if (!scroll || !topEdge) return;
    topEdge.dataset.scrolled = String(scroll.scrollTop > 0);
  }

  $$('[data-scene-button]').forEach(button => button.addEventListener('click', () => { setScene(button.dataset.sceneButton); $('.plate')?.classList.remove('open'); }));
  $$('.session[data-session]').forEach(button => button.addEventListener('click', event => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    setScene(sceneForSession[button.dataset.session]);
  }));
  $$('.trace-head').forEach(button => button.addEventListener('click', () => setDisclosure(button.closest('.trace'), button.getAttribute('aria-expanded') !== 'true')));
  $$('[data-peek]').forEach(button => button.addEventListener('click', () => {
    const tab = button.dataset.peek;
    if (state.inspectorOpen && state.inspectorTab === tab) setInspectorOpen(false);
    else setInspectorTab(tab);
  }));
  $$('[data-focus-composer]').forEach(button => button.addEventListener('click', () => composerInput.focus()));
  $$('[data-approval-action]').forEach(button => button.addEventListener('click', () => { void resolveApproval(button); }));
  $('#sidebar-toggle')?.addEventListener('click', () => toggleSidebar());
  $('#compact-toggle')?.addEventListener('click', () => toggleSidebar());
  inspectorToggle?.addEventListener('click', () => setInspectorOpen(!state.inspectorOpen));
  inspectorClose?.addEventListener('click', () => setInspectorOpen(false));
  inspectorDock?.addEventListener('click', () => setInspectorDocked(!state.inspectorDocked));
  $('#peek-toggle')?.addEventListener('click', () => setInspectorOpen(!state.inspectorOpen));
  composerInput.addEventListener('input', () => { autoSizeComposer(); syncComposerMode(); });
  composerInput.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      handleComposerAction();
    }
  });
  $('.composer')?.addEventListener('click', event => { if (!event.target.closest('button, textarea, .model-config-menu')) composerInput.focus(); });
  $('.composer')?.addEventListener('submit', event => { event.preventDefault(); handleComposerAction(); });
  $('[data-queue-demo]')?.addEventListener('click', async () => {
    await setScene('live');
    if (!state.queue.length) {
      enqueueMessage('Compare the retention evidence with the launch brief.');
      enqueueMessage('Summarize the remaining disagreement in three bullets.');
    }
    $('.plate')?.classList.remove('open');
  });
  configTrigger?.addEventListener('click', () => setConfigOpen(!config.classList.contains('open')));
  budgetTrigger?.addEventListener('click', () => setBudgetOpen(!budgetPinned, { pinned: !budgetPinned }));
  budgetWrap?.addEventListener('pointerenter', () => {
    clearTimeout(budgetCloseTimer);
    if (!budgetPinned) setBudgetOpen(true, { pinned: false });
  });
  budgetWrap?.addEventListener('pointerleave', scheduleBudgetClose);
  budgetWrap?.addEventListener('focusin', () => setBudgetOpen(true, { pinned: budgetPinned }));
  budgetWrap?.addEventListener('focusout', scheduleBudgetClose);
  $$('[data-model]').forEach(button => button.addEventListener('click', () => {
    state.model = button.dataset.model;
    state.effort = state.modelEfforts[state.model];
    syncConfig({ animateTrigger: true });
  }));
  $$('[data-model-effort]').forEach(button => button.addEventListener('click', () => {
    const sameOpenModel = config.classList.contains('effort-open') && activeEffortModel === button.dataset.modelEffort;
    const animateTrigger = !sameOpenModel && state.model !== button.dataset.modelEffort;
    setEffortOpen(!sameOpenModel, button.dataset.modelEffort, { animateTrigger });
  }));
  $$('[data-effort]', effortMenu).forEach(button => button.addEventListener('click', () => {
    if (!activeEffortModel) return;
    state.modelEfforts[activeEffortModel] = button.dataset.effort;
    state.model = activeEffortModel;
    state.effort = button.dataset.effort;
    setEffortOpen(false, activeEffortModel, { animateTrigger: true });
  }));
  $$('.user-meta button[aria-label^="Copy"], [data-response-action="copy"]').forEach(button => {
    button.addEventListener('click', async () => {
      if (!motion.iconSwap.swap(button, '#dp-check')) return;
      await sleep(motion.duration.copyFeedback);
      motion.iconSwap.swap(button, '#dp-copy');
    });
  });
  $('.lab-toggle')?.addEventListener('click', event => {
    const plate = event.currentTarget.closest('.plate');
    const open = plate.classList.toggle('open');
    event.currentTarget.setAttribute('aria-expanded', String(open));
  });
  $('.chat-scroll')?.addEventListener('scroll', () => {
    updateConversationRail();
    updateScrollEdges();
  }, { passive: true });
  updateScrollEdges();

  const themeToggle = $('.theme-toggle');
  motion.theme.bindToggle(themeToggle, { lightIcon: '#dp-sun', darkIcon: '#dp-moon' });

  const railResize = $('.rail-resize');
  motion.sidebarResize.bind(railResize, { variable: '--sidebar-width', bodyClass: 'resizing-rail', keyboard: true });

  document.addEventListener('click', event => {
    if (config.classList.contains('open') && !event.target.closest('.model-config-wrap')) setConfigOpen(false);
    if (budgetPinned && !event.target.closest('.budget-wrap')) setBudgetOpen(false, { pinned: false });

    const inspectorTrigger = event.target.closest('#inspector-toggle, #peek-toggle, [data-peek]');
    if (state.inspectorOpen && !state.inspectorDocked && !peek.contains(event.target) && !inspectorTrigger) {
      setInspectorOpen(false);
    }

    const sidebarTrigger = event.target.closest('#sidebar-toggle, #compact-toggle');
    if (narrowShell.matches && state.sidebarOpen && !sidebar.contains(event.target) && !sidebarTrigger) {
      setSidebarOpen(false);
    }
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      if (config.classList.contains('effort-open')) setEffortOpen(false);
      else if (config.classList.contains('open')) setConfigOpen(false);
      else if (budgetWrap?.classList.contains('open')) setBudgetOpen(false, { pinned: false });
      else if (state.inspectorOpen && !state.inspectorDocked) setInspectorOpen(false);
      else if (state.scene === 'approval') setScene(state.previousScene, { animate: false });
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'b') {
      event.preventDefault();
      toggleSidebar({ animate: false });
    }
  });
  window.addEventListener('resize', () => {
    syncConversationRail();
  });
  narrowShell.addEventListener('change', event => {
    if (event.matches) setSidebarOpen(false);
    else if (!state.inspectorOpen) setSidebarOpen(true);
  });
  singleSidebarShell.addEventListener('change', event => {
    if (!state.inspectorOpen || !state.inspectorDocked) return;
    transitionShell(() => {
      if (event.matches && state.sidebarOpen) {
        restoreSidebarAfterInspector = true;
        state.sidebarOpen = false;
      } else if (!event.matches && restoreSidebarAfterInspector) {
        restoreSidebarAfterInspector = false;
        state.sidebarOpen = true;
      }
      syncSidebarControls();
    });
  });

  const preview = new URLSearchParams(location.search);
  if (preview.get('scene') && titles[preview.get('scene')]) state.scene = preview.get('scene');
  if (preview.get('peek') === '0') state.inspectorOpen = false;
  if (preview.get('sidebar') === '0') state.sidebarOpen = false;
  if (narrowShell.matches) state.sidebarOpen = false;
  if (state.inspectorOpen && state.inspectorDocked && singleSidebarShell.matches && state.sidebarOpen) {
    restoreSidebarAfterInspector = true;
    state.sidebarOpen = false;
  }
  sceneIntent = state.scene;
  inspectorDockIntent = state.inspectorDocked;
  syncSidebarControls();
  $$('.scene').forEach(scene => scene.classList.toggle('on', scene.dataset.scene === state.scene));
  $$('.trace.collapsed .steps').forEach(steps => { steps.hidden = true; });
  syncSceneChrome();
  syncInspectorControls();
  peekController = motion.peek.bind(peek, {
    closeButtons: [],
    escape: false,
    beforeOpen: change => transitionShell(updateInspectorShell, { animate: change.animate }),
    beforeClose: change => transitionShell(updateInspectorShell, { animate: change.animate }),
  });
  void peekController.setOpen(state.inspectorOpen, { force: true, animate: false, focus: false, restoreFocus: false });
  inspectorTabsController = motion.tabPanels.bind($('.dp-peek-tabs'), {
    body: () => $('.inspector-document:not([hidden])'),
    variant: 'line',
    tabSelector: '.dp-peek-tab',
    indicatorSelector: '.dp-peek-tab-indicator',
    indicatorClass: 'dp-peek-tab-indicator',
    activeClass: 'on',
    render: value => {
      state.inspectorTab = value;
      peek.dataset.tab = value;
      $$('.inspector-document').forEach(document => document.setAttribute('aria-labelledby', `inspector-tab-${value}`));
    },
  });
  inspectorTabsController?.sync({ animate: false });
  syncConfig();
  motion.progressiveBlur.mount($('.edge-blur.top'), { direction: 'top' });
  autoSizeComposer();
  syncChatBottomHeight();
  renderQueue();
  syncComposerMode();
  syncConversationRail();

  const chatResizeObserver = new ResizeObserver(() => {
    syncChatBottomHeight();
    syncConversationRail();
  });
  const chatScroll = $('.chat-scroll');
  if (chatScroll) chatResizeObserver.observe(chatScroll);
  if (bottomStack) chatResizeObserver.observe(bottomStack);
})();
