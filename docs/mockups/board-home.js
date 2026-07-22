(() => {
  const motion = window.BOARD_MOTION;
  const body = document.body;
  const page = document.querySelector(".page");
  const answerCount = () => document.querySelector("[data-answer-count]");
  const answerPhrase = () => document.querySelector("[data-answer-phrase]");
  const answerLine = document.querySelector("[data-answer]");
  const answerSub = document.querySelector("[data-answer-sub]");
  const capacityEl = document.querySelector("[data-capacity]");
  const deckEl = document.querySelector("[data-deck]");
  const zeroEl = document.querySelector("[data-zero]");
  const posEl = document.querySelector("[data-deck-pos]");
  const undoEl = document.querySelector("[data-undo]");
  const notTodayEl = document.querySelector("[data-not-today]");
  const stripEls = Object.fromEntries([...document.querySelectorAll("[data-strip]")].map(node => [node.dataset.strip, node]));
  const lineEl = key => document.querySelector(`[data-line-${key}]`);
  const rowsEl = key => document.querySelector(`[data-rows-${key}]`);
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

  /* ---------- fixtures — kinds/verbs mirror the real Ask model; verbs carry their object ---------- */

  const ASKS = {
    "release-note": {
      area: "market launch", kind: "review", waitedMin: 12,
      title: "Approve the release note publish",
      reason: "The release is ready — publishing waits on its only external write. The note itself is already reviewed by the drafting agent.",
      next: { b: "The publish workflow resumes", small: "no other changes" },
      one: { label: "Approve", word: "approved", done: { area: "market launch", text: "Release note published — publish workflow resumed." } },
      two: { label: "Reject…", io: "Why not? — goes back to the agent as guidance", word: "sent back", dir: -1,
             done: { area: "market launch", text: "Release note sent back with guidance." } },
    },
    audience: {
      area: "market launch", kind: "question", waitedMin: 240,
      title: "Choose the launch brief audience",
      reason: "Both segment analyses are in and agree on reach. One line from you lets two agents continue drafting tonight.",
      next: { b: "Two agents resume drafting", small: "the brief continues without you" },
      one: { label: "Answer…", io: "Your call — one line is enough", word: "answered",
             done: { area: "market launch", text: "Audience chosen — 2 agents resumed drafting the brief." } },
      two: { label: "Open brief", route: true, word: "answered",
             done: { area: "market launch", text: "Audience settled in the session — brief resumed." } },
    },
    conflict: {
      area: "O-1A evidence", kind: "review", waitedMin: 52,
      title: "Review the award-date conflict",
      reason: "Three sources disagree about the award date. The evidence agent lined them up side by side — one look settles the map.",
      next: { b: "The evidence map settles", small: "no agents blocked on this" },
      one: { label: "Review sources", route: true, word: "settled",
             done: { area: "O-1A evidence", text: "Source conflict resolved — evidence map settled." } },
      two: { label: "Ask agent…", io: "Tell the evidence agent what to check", word: "redirected", dir: -1,
             done: { area: "O-1A evidence", text: "Guidance sent — evidence agent re-checking sources." } },
    },
    labs: {
      area: "health", kind: "question", waitedMin: 0,
      title: "Confirm the lab result import",
      reason: "The health agent found a new lab report in the inbox and wants to file it under the marathon block.",
      next: { b: "The report files itself", small: "health area updates quietly" },
      one: { label: "Confirm", word: "confirmed", done: { area: "health", text: "Lab results filed under the marathon block." } },
      two: { label: "Open report", route: true, word: "confirmed",
             done: { area: "health", text: "Lab report reviewed and filed." } },
    },
    "run-timeout": {
      area: "market launch", kind: "fyi", waitedMin: 0,
      title: "Retry the retention sweep?",
      reason: "The market analyst timed out on a slow source mid-sweep. Partial notes are saved on the session — nothing lost.",
      next: { b: "The sweep runs again", small: "picks up where it stopped" },
      one: { label: "Retry sweep", word: "retried", done: { area: "market launch", text: "Retention sweep retried after a timeout." } },
      two: { label: "Open session", route: true, word: "closed",
             done: { area: "market launch", text: "Partial sweep reviewed — closed as enough." } },
    },
    tone: {
      area: "market launch", kind: "question", waitedMin: 95,
      title: "Pick the launch brief tone",
      reason: "The drafting agent has two openings ready — measured or bold. It will carry your pick through the whole brief.",
      next: { b: "Drafting continues in your voice", small: "one draft, not two" },
      one: { label: "Answer…", io: "measured / bold — or your own words", word: "answered",
             done: { area: "market launch", text: "Brief tone picked — drafting continues." } },
      two: { label: "Open drafts", route: true, word: "answered",
             done: { area: "market launch", text: "Tone settled in the session." } },
    },
    rotation: {
      area: "product", kind: "review", waitedMin: 30,
      title: "Approve the API key rotation",
      reason: "The provider audit wants to rotate two stale keys tonight. Sessions stay up; only the keys change.",
      next: { b: "Rotation runs tonight", small: "zero downtime expected" },
      one: { label: "Approve", word: "approved", done: { area: "product", text: "API keys rotated — audit closed clean." } },
      two: { label: "Reject…", io: "Why not? — goes back to the agent as guidance", word: "sent back", dir: -1,
             done: { area: "product", text: "Key rotation declined with guidance." } },
    },
  };

  const RUNS = {
    analyst: {
      icon: "#dp-agent", title: "Market analyst", step: "comparing retention signals", detail: "7 sources checked", elapsedMin: 4,
      brought: "retention brief → Memory",
      done: { area: "market launch", text: "Market analyst back — retention brief saved to Memory." },
    },
    scout: {
      icon: "#dp-globe", title: "Opportunity scout", step: "checking judging leads", detail: "9 of 14 reviewed", elapsedMin: 11, due: "back by 22:00",
      brought: "3 judging leads short-listed",
      done: { area: "O-1A evidence", text: "Opportunity scout back — 3 judging leads short-listed." },
    },
  };

  const DONE = {
    competitor: { area: "market launch", text: "Competitor evidence review — 7 sources grouped, brief in Memory." },
    provider: { area: "product", text: "Provider migration audit — 18 models verified, no blocked sessions." },
  };

  const SCHEDULED = [{ icon: "#dp-zap", title: "Inbox brief", detail: "tomorrow · 08:30", route: "Opening — Inbox brief automation" }];

  const SNOOZES = [
    { label: "In an hour", wake: "back in an hour" },
    { label: "Tonight", wake: "back tonight" },
    { label: "Tomorrow", wake: "back tomorrow 09:00" },
  ];

  const SCENES = {
    morning: { queue: ["release-note", "audience", "conflict"], running: ["analyst", "scout"], done: ["competitor", "provider"], handled: 2 },
    heavy: { queue: ["release-note", "audience", "conflict", "tone", "rotation"], running: ["analyst", "scout"], done: ["competitor"], handled: 1 },
    clear: { queue: [], running: ["analyst", "scout"], done: ["competitor", "provider"], handled: 4 },
    quiet: { queue: [], running: [], done: [], handled: 0 },
  };
  const USUAL_DAY = 4;

  const state = {
    queue: [], running: [], done: [], aside: [],
    tally: {}, handled: 0, scene: "morning",
    io: null,           // "input" | "snooze" | null — what the head card's foot shows
    last: null,         // undo buffer, depth 1
    zeroShown: false,
  };

  const fmtWait = min => min < 1 ? "just now" : min < 60 ? `waiting ${min}m` : min < 60 * 24 ? `waiting ${Math.round(min / 60)}h` : `waiting ${Math.round(min / (60 * 24))} days`;
  const fmtAge = min => min < 1 ? "now" : min < 60 ? `${min}m` : min < 60 * 24 ? `${Math.round(min / 60)}h` : `${Math.round(min / (60 * 24))}d`;
  function el(html) {
    const template = document.createElement("template");
    template.innerHTML = html.trim();
    return template.content.firstElementChild;
  }
  const icon = href => `<svg aria-hidden="true"><use href="${href}"/></svg>`;
  const route = label => window.BOARD_TOAST.show(label);
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* BlurSwap law: old and new content overlap in the shared motion controller. */
  function crossSwap(target, html) {
    if (target.dataset.raw === html) return;
    target.dataset.raw = html;
    motion.content.overlap(target, () => { target.innerHTML = `<span>${html}</span>`; });
  }

  /* ---------- the answer — the page states its own conclusion, top-left ---------- */

  function answerParts() {
    const n = state.queue.length;
    if (n === 0 && state.running.length === 0 && state.done.length === 0) return { count: "", phrase: "That’s it for today." };
    if (n === 0) return { count: "", phrase: "All clear." };
    if (n === 1) return { count: "", phrase: "One thing needs you." };
    return { count: String(n), phrase: " things need you." };
  }

  function subParts() {
    const parts = [];
    if (state.queue.length === 0) {
      if (state.running.length) parts.push("the agents will call if anything changes");
      else if (state.handled === 0) parts.push("agents are asleep — nothing waited for you");
      if (state.handled > 0) parts.push(`<b>${state.handled} handled</b> today`);
      parts.push("next wake tomorrow 08:30");
      return parts.join(" · ");
    }
    if (state.running.length) parts.push(`<b>${plural(state.running.length, "agent")} out</b>`);
    if (state.handled > 0) parts.push(`<b>${state.handled} handled</b> today`);
    return parts.join(" · ");
  }

  function syncAnswer({ animate = true } = {}) {
    if (!answerLine.querySelector("[data-answer-count]")) {
      answerLine.replaceChildren(
        el(`<span class="t-spin-counter" data-answer-count></span>`),
        el(`<span data-answer-phrase></span>`),
      );
    }
    const next = answerParts();
    const countEl = answerCount();
    const phraseEl = answerPhrase();
    const oldPhrase = phraseEl.textContent;
    if (!animate) {
      countEl.textContent = next.count;
      countEl.dataset.value = next.count;
      phraseEl.textContent = next.phrase;
    } else if (oldPhrase === next.phrase) {
      motion.spinningCounter.set(countEl, next.count);
    } else {
      if ((countEl.dataset.value ?? countEl.textContent) !== next.count) {
        motion.spinningCounter.set(countEl, next.count || " ");
        if (!next.count) setTimeout(() => { if (!answerParts().count) countEl.textContent = ""; }, motion.duration.counterSpin + 140);
      }
      motion.textSwap.swap(phraseEl, next.phrase);
    }
    const sub = subParts();
    if (animate) crossSwap(answerSub, sub);
    else if (answerSub.dataset.raw !== sub) { answerSub.dataset.raw = sub; answerSub.innerHTML = sub; }
  }

  function syncCapacity() {
    capacityEl.hidden = state.queue.length <= USUAL_DAY;
  }

  /* ---------- the deck — one decision at a time; slivers never preview ---------- */

  function verbFor(ask, slot) {
    return slot === 1 ? ask.one : ask.two;
  }

  function cardNode(id, depth) {
    const ask = ASKS[id];
    const node = el(`
      <article class="deck-card" data-depth="${depth}" data-ask="${id}">
        <p class="card-eyebrow"><span>${ask.area} · ${ask.kind}</span><time>${fmtWait(ask.waitedMin)}</time></p>
        <h3 class="card-title">${ask.title}</h3>
        <p class="card-reason">${ask.reason}</p>
        <p class="card-then">${ask.next.b} · ${ask.next.small}</p>
        <div class="card-foot">
          <div class="card-verbs">
            <button type="button" class="verb dp-button primary" data-slot="1"><span class="verb-key">1</span>${ask.one.label}</button>
            <button type="button" class="verb dp-button quiet" data-slot="2"><span class="verb-key">2</span>${ask.two.label}</button>
            <button type="button" class="verb dp-button quiet" data-slot="3"><span class="verb-key">3</span>Later</button>
          </div>
          <form class="card-io" data-io-hidden>
            <input aria-label="Inline reply" autocomplete="off">
            <span class="io-hint">↵ send · esc back</span>
          </form>
          <div class="card-snooze" data-io-hidden>
            <span class="snooze-title">Back when?</span>
            ${SNOOZES.map((preset, index) => `<button type="button" class="verb dp-button quiet" data-snooze="${index}"><span class="verb-key">${index + 1}</span>${preset.label}</button>`).join("")}
          </div>
        </div>
      </article>`);
    node.querySelectorAll("[data-slot]").forEach(button =>
      button.addEventListener("click", () => { if (node.dataset.depth === "0") act(Number(button.dataset.slot)); }));
    node.querySelectorAll("[data-snooze]").forEach(button =>
      button.addEventListener("click", () => { if (node.dataset.depth === "0") snooze(Number(button.dataset.snooze)); }));
    const io = node.querySelector(".card-io");
    io.addEventListener("submit", event => {
      event.preventDefault();
      submitIO(io.querySelector("input").value.trim());
    });
    return node;
  }

  function buildDeck() {
    deckEl.replaceChildren(...(state.queue.length ? [cardNode(state.queue[0], 0)] : []));
    state.io = null;
  }

  // blur-bridged head swap: the leaving ask exits on the verb's direction while the next rises in place
  function swapHead(leaving, dir) {
    if (leaving) {
      leaving.dataset.depth = "gone";
      motion.deck.exit(leaving, { direction: dir });
      setTimeout(() => leaving.remove(), motion.duration.deckExit + 80);
    }
    if (!state.queue.length) return;
    const node = cardNode(state.queue[0], 0);
    deckEl.append(node);
    motion.deck.promote(node);
  }

  const headCard = () => deckEl.querySelector('[data-depth="0"]');

  /* the rest of the queue — visible rows behind the head; clicking one brings it forward */
  const restEl = document.querySelector("[data-rest]");
  const REST_CAP = 3;

  function restRow(id) {
    const ask = ASKS[id];
    const row = el(`
      <button type="button" class="rest-row" data-id="${id}">
        <b>${ask.title}</b>
        <small>${ask.kind} · ${ask.area} · ${fmtAge(ask.waitedMin)}</small>
      </button>`);
    row.addEventListener("click", () => promoteTo(id));
    return row;
  }

  function renderRest({ animate = true } = {}) {
    const ids = state.queue.slice(1, 1 + REST_CAP);
    const overflow = state.queue.length - 1 - REST_CAP;
    const more = overflow > 0 ? [el(`<p class="rest-more">+${overflow} more waiting</p>`)] : [];
    const existing = new Map([...restEl.querySelectorAll("[data-id]")].map(node => [node.dataset.id, node]));
    const next = ids.map(id => existing.get(id) || restRow(id));
    if (!animate || reduced()) { restEl.replaceChildren(...next, ...more); return; }
    const before = new Map([...restEl.children].map(node => [node, { top: node.getBoundingClientRect().top }]));
    const entering = next.filter(node => !existing.has(node.dataset.id));
    const staying = next.filter(node => existing.has(node.dataset.id));
    const leaving = [...existing.values()].filter(node => !ids.includes(node.dataset.id));
    restEl.style.position = "relative";
    leaving.forEach(node => { node.style.cssText += `position:absolute;top:${node.offsetTop}px;left:0;right:0;pointer-events:none;`; });
    restEl.replaceChildren(...leaving, ...next, ...more);
    motion.layout.animateListChange({ before, staying, entering, leaving });
    setTimeout(() => leaving.forEach(node => node.remove()), motion.duration.traceRow + 60);
  }

  function promoteTo(id) {
    if (state.io || state.queue[0] === id || !state.queue.includes(id)) return;
    const leaving = headCard();
    while (state.queue[0] !== id) state.queue.push(state.queue.shift());
    swapHead(leaving, 0);
    syncChrome();
    renderRest();
  }

  function posLabel() {
    const n = state.queue.length;
    return n === 0 ? "" : n === 1 ? "last one" : `1 of ${n}`;
  }

  function syncChrome({ animate = true } = {}) {
    const label = posLabel();
    const current = posEl.dataset.value ?? posEl.textContent.trim();
    if (!animate) { posEl.textContent = label; posEl.dataset.value = label; }
    else if (/^1 of \d+$/.test(current) && /^1 of \d+$/.test(label)) motion.spinningCounter.set(posEl, label);
    else if (current !== label) { posEl.dataset.value = label; motion.textSwap.swap(posEl, label || " "); }
    undoEl.hidden = !state.last;
    notTodayEl.hidden = state.queue.length === 0;
  }

  /* verbs ↔ inline reply ↔ snooze swap inside one fixed slot on the head card */
  function setFoot(mode) {
    const head = headCard();
    if (!head) return;
    state.io = mode;
    const verbs = head.querySelector(".card-verbs");
    const io = head.querySelector(".card-io");
    const snoozeRow = head.querySelector(".card-snooze");
    const show = mode === "input" ? io : mode === "snooze" ? snoozeRow : verbs;
    [verbs, io, snoozeRow].forEach(nodeEl => {
      if (nodeEl === show) nodeEl.removeAttribute("data-io-hidden");
      else nodeEl.setAttribute("data-io-hidden", "");
    });
    if (mode === "input") {
      const input = io.querySelector("input");
      input.value = "";
      input.placeholder = state.ioVerb?.io || "";
      setTimeout(() => input.focus(), 30);
    }
  }

  /* ---------- triage — optimistic commit, directional exit, spring promote ---------- */

  function act(slot) {
    const id = state.queue[0];
    if (!id || state.zeroShown) return;
    const ask = ASKS[id];
    if (slot === 3) { setFoot(state.io === "snooze" ? null : "snooze"); return; }
    const verb = verbFor(ask, slot);
    if (verb.io) { state.ioVerb = verb; setFoot("input"); return; }
    markCommitted(slot);
    if (verb.route) {
      route(`Opening — ${ask.title}`);
      const head = id;
      wait(1100).then(() => { if (state.queue[0] === head && !state.zeroShown) resolveHead(verb); });
      return;
    }
    resolveHead(verb);
  }

  function markCommitted(slot) {
    headCard()?.querySelector(`[data-slot="${slot}"]`)?.classList.add("committed");
  }

  function submitIO(text) {
    const verb = state.ioVerb;
    if (!verb) return;
    resolveHead(verb, text);
  }

  function snooze(index) {
    const preset = SNOOZES[index];
    if (!preset) return;
    setAside(preset.wake);
  }

  function setAside(wake) {
    const id = state.queue[0];
    if (!id) return;
    state.aside.push({ id, wake });
    state.last = { type: "aside", id, dir: 0 };
    advance({ dir: 0 });
  }

  // J — "choose another": the head drops to the back of the queue; nothing is resolved
  function skipHead() {
    if (state.queue.length < 2 || state.io) return;
    const leaving = headCard();
    state.queue.push(state.queue.shift());
    swapHead(leaving, 0);
    syncChrome();
    renderRest();
  }

  function resolveHead(verb, note) {
    const id = state.queue[0];
    const doneId = addDone(verb.done);
    state.tally[verb.word] = (state.tally[verb.word] || 0) + 1;
    state.handled += 1;
    state.done.unshift(doneId);
    state.last = { type: "resolve", id, dir: verb.dir ?? 1, doneId, word: verb.word };
    advance({ dir: verb.dir ?? 1 });
  }

  let doneSeq = 0;
  function addDone(data) {
    const id = `done-${doneSeq++}`;
    DONE[id] = data;
    return id;
  }

  function advance({ dir }) {
    const leaving = headCard();
    state.queue.shift();
    state.io = null;
    swapHead(leaving, dir);
    // the ask and its departing queue row own the first beat; the periphery follows one beat later
    syncChrome();
    renderRest();
    setTimeout(() => {
      syncAnswer();
      syncStrips();
      syncCapacity();
    }, 140);
    if (state.queue.length === 0) setTimeout(() => revealZero({ celebrate: true }), motion.duration.deckExit + 60);
  }

  function undo() {
    const last = state.last;
    if (!last) return;
    state.last = null;
    if (last.type === "resolve") {
      state.queue.unshift(last.id);
      state.done = state.done.filter(key => key !== last.doneId);
      state.tally[last.word] -= 1;
      state.handled -= 1;
    } else {
      state.queue.unshift(last.id);
      state.aside = state.aside.filter(item => item.id !== last.id);
    }
    if (state.zeroShown) hideZero();
    const displaced = headCard();
    if (displaced) {
      displaced.dataset.depth = "gone";
      motion.deck.exit(displaced, { direction: 0, duration: motion.duration.exit });
      setTimeout(() => displaced.remove(), motion.duration.exit + 60);
    }
    const card = cardNode(last.id, 0);
    deckEl.append(card);
    motion.deck.return(card, { direction: last.dir });
    syncAnswer();
    syncChrome();
    syncStrips();
    syncCapacity();
    renderRest();
  }

  /* ---------- cleared attention — compact confirmation beside undo ---------- */

  function revealZero({ celebrate = false } = {}) {
    state.zeroShown = true;
    zeroEl.hidden = false;
    deckEl.replaceChildren();
    syncChrome();
    if (!celebrate) return;
    motion.content.enter([zeroEl], { distance: motion.distance.dissolve });
  }

  function hideZero() {
    state.zeroShown = false;
    zeroEl.hidden = true;
  }

  /* ---------- ambient strips — aggregate the normal, itemize at click-depth ---------- */

  function stripLines() {
    const runsLead = state.running[0] ? RUNS[state.running[0]] : null;
    return {
      working: state.running.length
        ? `<b>${plural(state.running.length, "agent")} out</b> — ${runsLead.title.toLowerCase()}: <span class="live">${runsLead.step}</span> · ${runsLead.elapsedMin}m`
        : "",
      scheduled: SCHEDULED.length ? `<b>Asleep</b> until ${SCHEDULED[0].detail.replace(" · ", " ")} — ${SCHEDULED[0].title.toLowerCase()}` : "",
      done: state.done.length
        ? `<b>${state.done.length} landed today</b> — latest: ${DONE[state.done[0]].text.replace(/\.$/, "").toLowerCase()}`
        : "",
      aside: state.aside.length
        ? `<b>${state.aside.length} set aside</b> · first ${state.aside[0].wake} — or sooner if it moves`
        : "",
    };
  }

  function runRow(id) {
    const run = RUNS[id];
    return el(`
      <button class="frow" type="button" data-id="${id}" data-route="Opening — ${run.title}">
        <span class="frow-eyebrow"><span class="frow-glyph">${icon(run.icon)}</span><span class="frow-name">${run.title}</span><time>${run.elapsedMin}m${run.due ? ` · ${run.due}` : ""}</time></span>
        <span class="frow-line" data-run-line>${run.step} · ${run.detail}</span>
      </button>`);
  }

  function doneRow(id) {
    const item = DONE[id];
    return el(`
      <button class="frow" type="button" data-id="${id}" data-route="Opening the result">
        <span class="frow-eyebrow"><span class="frow-glyph ok">${icon("#dp-check")}</span><span>${item.area}</span></span>
        <span class="frow-line">${item.text}</span>
      </button>`);
  }

  function asideRow(item) {
    const ask = ASKS[item.id];
    return el(`
      <button class="frow" type="button" data-id="${item.id}" data-route="Opening — ${ask.title}">
        <span class="frow-eyebrow"><span class="frow-glyph">${icon("#dp-clock")}</span><span>${ask.area}</span><time>${item.wake}</time></span>
        <span class="frow-line">${ask.title}</span>
      </button>`);
  }

  function scheduledRow(item) {
    return el(`
      <button class="frow" type="button" data-route="${item.route}">
        <span class="frow-eyebrow"><span class="frow-glyph">${icon(item.icon)}</span><span class="frow-name">${item.title}</span><time>${item.detail}</time></span>
        <span class="frow-line">runs on its own — results land in “Landed today”</span>
      </button>`);
  }

  function syncStrips({ animate = true } = {}) {
    const lines = stripLines();
    for (const key of ["working", "scheduled", "done", "aside"]) {
      const target = lineEl(key);
      const empty = !lines[key];
      // empty tiers vanish — the floor shows only what exists
      stripEls[key].hidden = empty;
      if (empty) { closeStrip(stripEls[key]); target.dataset.raw = ""; continue; }
      // system-triggered: quiet overlapping crossfade in the same pixels, never a slide
      if (animate) crossSwap(target, lines[key]);
      else if (target.dataset.raw !== lines[key]) { target.dataset.raw = lines[key]; target.innerHTML = lines[key]; }
    }
    rowsEl("working").replaceChildren(...state.running.map(runRow));
    rowsEl("scheduled").replaceChildren(...SCHEDULED.map(scheduledRow));
    rowsEl("done").replaceChildren(...state.done.slice(0, 4).map(doneRow));
    rowsEl("aside").replaceChildren(...state.aside.map(asideRow));
    document.querySelector(".strips").hidden = Object.values(stripLines()).every(line => !line);
  }

  let openStrip = null;
  function closeStrip(strip) {
    if (!strip || !strip.classList.contains("open")) return;
    strip.classList.remove("open");
    strip.querySelector(".strip-head").setAttribute("aria-expanded", "false");
    if (openStrip === strip) openStrip = null;
  }
  function toggleStrip(strip) {
    const opening = !strip.classList.contains("open");
    if (openStrip && openStrip !== strip) closeStrip(openStrip);
    strip.classList.toggle("open", opening);
    strip.querySelector(".strip-head").setAttribute("aria-expanded", String(opening));
    openStrip = opening ? strip : null;
  }
  Object.values(stripEls).forEach(strip => {
    strip.querySelector(".strip-head").addEventListener("click", () => {
      if (!strip.classList.contains("still")) toggleStrip(strip);
    });
  });
  document.addEventListener("pointerdown", event => {
    if (openStrip && !openStrip.contains(event.target)) closeStrip(openStrip);
  });

  /* ---------- lifecycle: an agent lands — discovery, not interruption ---------- */

  function settleRun(id) {
    if (!state.running.includes(id)) return;
    const run = RUNS[id];
    const row = rowsEl("working").querySelector(`[data-id="${id}"]`);
    if (row) {
      row.classList.add("returned");
      const line = row.querySelector("[data-run-line]");
      if (line) motion.textSwap.swap(line, `back — brought you: ${run.brought}`);
    }
    const tease = `<b>${run.title.toLowerCase()} is back</b> — brought you: ${run.brought}`;
    crossSwap(lineEl("working"), tease);
    wait(2600).then(() => {
      if (!state.running.includes(id)) return;
      state.running = state.running.filter(key => key !== id);
      state.done = [addDone(run.done), ...state.done];
      syncAnswer();
      syncStrips();
    });
  }

  // minute-granularity ambience — nothing ticks per second
  setInterval(() => {
    Object.values(RUNS).forEach(run => { run.elapsedMin += 1; });
    Object.values(ASKS).forEach(ask => { ask.waitedMin += 1; });
    syncStrips({ animate: false });
    const head = headCard();
    if (head) {
      const time = head.querySelector(".card-eyebrow time");
      const next = fmtWait(ASKS[head.dataset.ask].waitedMin);
      if (time && time.textContent !== next) time.textContent = next;
    }
  }, 60000);

  /* ---------- scenes ---------- */

  function syncAll({ animate = true } = {}) {
    syncAnswer({ animate });
    syncChrome({ animate });
    syncStrips({ animate });
    syncCapacity();
    renderRest({ animate });
    if (state.queue.length === 0) revealZero({ celebrate: false });
  }

  function applyScene(name) {
    const scene = SCENES[name];
    state.scene = name;
    state.queue = [...scene.queue];
    state.running = [...scene.running];
    state.done = [...scene.done];
    state.aside = [];
    state.tally = name === "clear" ? { approved: 2, answered: 1, settled: 1 } : {};
    state.handled = scene.handled;
    state.last = null;
    state.io = null;
    document.querySelectorAll("[data-scene-button]").forEach(button =>
      button.classList.toggle("on", button.dataset.sceneButton === name));
    motion.content.swap(page, () => {
      hideZero();
      buildDeck();
      syncAll({ animate: false });
    });
  }

  /* ---------- demo plate ---------- */

  const plate = document.querySelector(".plate");
  body.classList.toggle("demo-mode", new URL(location.href).searchParams.has("demo"));
  const labToggle = plate.querySelector(".lab-toggle");
  labToggle.addEventListener("click", () => {
    const open = plate.classList.toggle("open");
    labToggle.setAttribute("aria-expanded", String(open));
  });
  plate.querySelectorAll("[data-scene-button]").forEach(button =>
    button.addEventListener("click", () => applyScene(button.dataset.sceneButton)));
  plate.querySelector("[data-demo='complete-run']").addEventListener("click", () => {
    state.running.length ? settleRun(state.running[0]) : route("Nothing is running");
  });
  plate.querySelector("[data-demo='new-ask']").addEventListener("click", () => {
    if (state.queue.includes("labs") || state.aside.some(item => item.id === "labs")) { route("The new ask is already here"); return; }
    // arrivals append to the tail — the head never moves, only the numbers change
    state.queue.push("labs");
    if (state.zeroShown) {
      hideZero();
      buildDeck();
      motion.deck.promote(headCard());
    }
    syncAnswer();
    syncChrome();
    syncCapacity();
    renderRest();
  });
  plate.querySelector("[data-demo='stale-run']").addEventListener("click", () => {
    if (!state.running.includes("analyst") || state.queue.includes("run-timeout")) { route("Analyst is not running"); return; }
    state.running = state.running.filter(key => key !== "analyst");
    state.queue.push("run-timeout");
    if (state.zeroShown) {
      hideZero();
      buildDeck();
      motion.deck.promote(headCard());
    }
    syncAnswer();
    syncChrome();
    syncStrips();
    syncCapacity();
    renderRest();
  });
  plate.querySelector("[data-demo='reset']").addEventListener("click", () => applyScene(state.scene));

  /* ---------- shell ---------- */

  const sidebar = document.querySelector(".rail");
  const workspace = document.querySelector(".workspace");
  const sidebarToggle = document.getElementById("sidebar-toggle");
  const compactShell = motion.geometry.maxWidthQuery("--breakpoint-compact");

  motion.theme.bindToggle(document.querySelector(".theme-toggle"), { lightIcon: "#dp-sun", darkIcon: "#dp-moon" });
  motion.sidebarResize.bind(document.querySelector(".rail-resize"), { variable: "--sidebar-width", keyboard: true });

  const toggleSidebar = (hidden = !body.classList.contains("rail-hidden"), instant = false) => {
    motion.sidebar.sync({ root: body, className: "rail-hidden", hidden, sidebar, dependents: [workspace], instant });
    motion.shellToggle.sync(sidebarToggle, {
      expanded: !hidden,
      expandedIcon: "#dp-panel-left-close",
      collapsedIcon: "#dp-panel-left-open",
      animate: !instant,
    });
  };
  sidebarToggle.addEventListener("click", () => toggleSidebar());
  const syncCompactShell = () => toggleSidebar(compactShell.matches, true);
  compactShell.addEventListener?.("change", syncCompactShell);
  syncCompactShell();

  document.addEventListener("click", event => {
    const trigger = event.target.closest("[data-route]");
    if (trigger) route(trigger.dataset.route);
  });
  undoEl.addEventListener("click", undo);
  notTodayEl.addEventListener("click", () => setAside("back tomorrow morning"));

  /* ---------- the front door ---------- */

  const captureInput = document.querySelector(".capture input");
  const captureNote = document.querySelector("[data-capture-note]");
  let noteTimer = null;
  const submitCapture = () => {
    if (!captureInput.value.trim()) return;
    captureInput.value = "";
    motion.textSwap.swap(captureNote, "saved → o-1a evidence");
    clearTimeout(noteTimer);
    noteTimer = setTimeout(() => motion.textSwap.swap(captureNote, ""), 2600);
  };
  document.querySelector(".capture").addEventListener("submit", event => {
    event.preventDefault();
    submitCapture();
  });
  captureInput.addEventListener("keydown", event => {
    if (event.key !== "Enter" || event.isComposing) return;
    event.preventDefault();
    submitCapture();
  });

  /* ---------- keyboard — verbs commit on keydown; the ask animates as aftermath ---------- */

  window.addEventListener("keydown", event => {
    const meta = event.metaKey || event.ctrlKey;
    if (meta && event.key.toLowerCase() === "k") { event.preventDefault(); captureInput.focus(); captureInput.select(); return; }
    if (meta && event.key.toLowerCase() === "b") { event.preventDefault(); toggleSidebar(); return; }
    const inField = /^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName || "");
    if (event.key === "Escape") {
      if (document.activeElement === captureInput) { captureInput.blur(); return; }
      if (state.io) { setFoot(null); state.ioVerb = null; return; }
      if (openStrip) { closeStrip(openStrip); return; }
      return;
    }
    if (inField || meta || event.altKey) return;
    const key = event.key.toLowerCase();
    if (key >= "1" && key <= "3") {
      event.preventDefault();
      if (state.io === "snooze") snooze(Number(key) - 1);
      else act(Number(key));
    } else if (key === "enter") {
      if (document.activeElement?.tagName === "BUTTON") return;
      event.preventDefault();
      act(1);
    } else if (key === "h") {
      event.preventDefault();
      if (state.queue.length) setFoot(state.io === "snooze" ? null : "snooze");
    } else if (key === "z") {
      event.preventDefault();
      undo();
    } else if (key === "j") {
      event.preventDefault();
      skipHead();
    }
  });

  applyScene("morning");
})();
