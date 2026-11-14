"use strict";
// Οθόνες του Xangxothaika: vanilla JS, όλο το κείμενο μπαίνει με textContent.

const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function icon(name, weight) {
  return el("i", (weight || "ph") + " ph-" + name);
}

function eur(cents) {
  const sign = cents < 0 ? "−" : "";
  const abs = Math.abs(Math.round(cents));
  const whole = Math.floor(abs / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return sign + whole + "," + String(abs % 100).padStart(2, "0") + " €";
}

function clock(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return d.toLocaleTimeString("el-GR", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "Europe/Athens" });
}

function hhmm(minute) {
  return String(Math.floor(minute / 60)).padStart(2, "0") + ":" + String(minute % 60).padStart(2, "0");
}

async function api(path, options) {
  const res = await fetch(path, options);
  let data = null;
  try { data = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) {
    const msg = data && data.detail ? data.detail : "Κάτι δεν πήγε καλά. Δοκίμασε ξανά.";
    throw new Error(typeof msg === "string" ? msg : "Κάτι δεν πήγε καλά. Δοκίμασε ξανά.");
  }
  return data;
}

const post = (path, body) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}),
});

/* ---------- ομάδες και εγκρίσεις ---------- */
const TEAM_ICON = { stock: "package", orders: "truck", invoices: "receipt", accountant: "calculator", master: "circles-three" };
const TEAM_NAME = { stock: "Αποθήκη", orders: "Παραγγελίες", invoices: "Τιμολόγια", accountant: "Λογιστής", master: "Συνεργάτης" };

function approvalInfo(action) {
  if (action === "send_order") return { team: "orders", ask: "Οι Παραγγελίες σε ρωτούν", yes: "Ναι, στείλ' τη" };
  if (action === "request_credit") return { team: "accountant", ask: "Ο Λογιστής σε ρωτά", yes: "Ναι, στείλε το αίτημα" };
  return { team: "master", ask: "Ο συνεργάτης σε ρωτά", yes: "Ναι" };
}

/* ---------- οθόνες ---------- */
let current = "arxiki";

function route() {
  current = location.hash === "#synomilia" ? "synomilia" : "arxiki";
  document.querySelectorAll(".screen").forEach((s) => s.classList.toggle("on", s.id === "s-" + current));
  document.querySelectorAll(".links a").forEach((a) => a.classList.toggle("on", a.dataset.k === current));
  document.querySelectorAll(".tabs a").forEach((a) => {
    const on = a.dataset.k === current;
    a.classList.toggle("on", on);
    a.querySelector("i").className = (on ? "ph-fill" : "ph") + " ph-" + a.dataset.i;
  });
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);

/* ---------- σκηνή της απόφασης ---------- */
let paused = false;
let lastState = null;
const signatures = {};

function stageNode(stage, a, state, opts) {
  const list = state.pending_approvals;
  const info = approvalInfo(a.action);
  stage.replaceChildren();
  stage.classList.remove("low");

  const shd = el("div", "shd");
  const lead = el("span");
  lead.append(icon(opts.chat ? "hand-pointing" : TEAM_ICON[info.team]), document.createTextNode(opts.chat ? "Θέλει εσένα" : info.ask));
  shd.append(lead, el("span", null, "1 από " + list.length));

  const body = el("div", "body");
  const left = el("div");
  const title = el("h2", null, a.summary || a.approval_id);
  title.id = opts.titleId;
  stage.setAttribute("aria-labelledby", opts.titleId);
  left.appendChild(title);
  if (a.why) left.appendChild(el("p", "why", a.why));

  const acts = el("div", "acts");
  const msg = el("span", "msg");
  const yes = el("button", "btn yes", info.yes);
  yes.prepend(icon("check", "ph-bold"));
  const no = el("button", "btn no", "Όχι");
  yes.type = no.type = "button";
  const decide = (decision) => async () => {
    yes.disabled = no.disabled = true;
    msg.textContent = "";
    try {
      const r = await post("/api/approvals/" + encodeURIComponent(a.approval_id), { decision });
      if (r && r.ok === false) {
        // Keep the card as it is so the owner can read why nothing happened.
        msg.textContent = r.message || "Δεν έγινε.";
        yes.disabled = no.disabled = false;
        return;
      }
      refreshSoon();
    } catch (e) {
      msg.textContent = e.message;
      yes.disabled = no.disabled = false;
    }
  };
  yes.addEventListener("click", decide("yes"));
  no.addEventListener("click", decide("no"));
  acts.append(yes, no, msg);
  left.appendChild(acts);

  const right = el("div");
  const focus = opts.chat ? null : state.shelf_focus;
  if (focus) {
    const well = el("div", "well");
    well.appendChild(el("span", "m", focus.position_name + " · " + focus.at));
    const row = el("div", "shelfrow");
    const total = Math.min(focus.before, 16);
    const full = Math.min(focus.left, total);
    row.setAttribute("aria-label", "Μένουν " + focus.left + " από " + focus.before);
    for (let i = 0; i < total; i++) row.appendChild(el("i", "can" + (i < full ? "" : " gone")));
    well.appendChild(row);
    right.appendChild(well);
  }
  if (a.lines && a.lines.length) {
    const qty = el("div", "qty");
    a.lines.forEach((l) => {
      const row = el("div");
      row.append(el("b", null, l.qty === null ? "" : String(l.qty).replace(".", ",")), el("span", null, l.text));
      qty.appendChild(row);
    });
    right.appendChild(qty);
  } else if (a.exact) {
    const qty = el("div", "qty");
    qty.appendChild(el("div", "txt", a.exact));
    right.appendChild(qty);
  }
  if (opts.chat) {
    // In the chat the stage is a single column: the lines go under the question.
    left.insertBefore(right, acts);
    body.classList.add("one");
    body.appendChild(left);
  } else {
    body.appendChild(left);
    if (right.children.length) body.appendChild(right);
    else body.classList.add("one");
  }
  stage.append(shd, body);

  if (list.length > 1) {
    const second = list[1];
    const sft = el("div", "sft");
    sft.appendChild(icon(TEAM_ICON[approvalInfo(second.action).team]));
    sft.appendChild(el("span", null, "Μετά: " + (second.summary || second.approval_id)));
    stage.appendChild(sft);
  }
}

function idleStage(stage, state) {
  stage.replaceChildren();
  stage.classList.add("low");
  const t = state.tasks;
  const body = el("div", "body one");
  const box = el("div");
  const title = el("h2", null, "Καμία απόφαση δεν περιμένει");
  title.id = "stage-title";
  stage.setAttribute("aria-labelledby", "stage-title");
  box.append(title, el("p", "why",
    t.done + " εργασίες σήμερα, " + t.with_yes + " με το «ναι» σου, " + t.errors + " λάθη."));
  body.appendChild(box);
  stage.appendChild(body);
}

function renderStages(state) {
  const sig = JSON.stringify([state.pending_approvals, state.shelf_focus,
    state.pending_approvals.length ? null : state.tasks]);
  if (signatures.stages === sig) return;
  signatures.stages = sig;
  const first = state.pending_approvals[0];
  const home = $("home-stage");
  if (first) stageNode(home, first, state, { chat: false, titleId: "stage-title" });
  else idleStage(home, state);
  const chat = $("chat-stage");
  chat.hidden = !first;
  $("chat-grid").classList.toggle("solo", !first);
  if (first) stageNode(chat, first, state, { chat: true, titleId: "chat-stage-title" });
}

/* ---------- η ομάδα, τώρα ---------- */
function renderTeam(state) {
  const box = $("home-team");
  box.replaceChildren();
  const active = state.team.filter((t) => t.active).length;
  const hd = el("div", "hd");
  const b = el("b");
  b.append(icon("users-three"), document.createTextNode("Η ομάδα, τώρα"));
  hd.append(b, el("span", "m", active + " δουλεύουν"));
  box.appendChild(hd);
  const first = state.pending_approvals[0];
  const asking = first ? approvalInfo(first.action).team : null;
  state.team.forEach((t) => {
    const row = el("div", "who" + (t.key === asking ? " now-ask" : ""));
    const av = el("span", "av");
    av.append(icon(TEAM_ICON[t.key]), el("span", "st" + (t.active ? " go" : "")));
    const text = el("span", "t");
    text.append(el("b", null, t.name), el("span", "m", t.last ? t.last.text : "Τίποτα ακόμα σήμερα"));
    const d = el("span", "d");
    const focus = state.shelf_focus;
    if (t.key === "stock" && focus) {
      const mini = el("span", "mini");
      mini.setAttribute("aria-label", "Μένουν " + focus.left + " από " + focus.before);
      const total = Math.min(focus.before, 10);
      for (let i = 0; i < total; i++) mini.appendChild(el("i", i < Math.min(focus.left, total) ? "" : "g"));
      d.appendChild(mini);
    }
    if (t.last) d.appendChild(el("b", null, clock(t.last.at)));
    row.append(av, text, d);
    box.appendChild(row);
  });
}

/* ---------- η μέρα του μαγαζιού ---------- */
function markerGroups(timeline) {
  // Same team within 20 minutes: one icon. Icons closer than ~45 minutes sit together.
  const lastOf = {};
  const items = [];
  timeline.forEach((e) => {
    const prev = lastOf[e.team];
    if (prev && e.minute - prev.last < 20) { prev.last = e.minute; return; }
    const item = { team: e.team, minute: e.minute, last: e.minute };
    lastOf[e.team] = item;
    items.push(item);
  });
  const groups = [];
  items.sort((a, b) => a.minute - b.minute).forEach((it) => {
    const g = groups[groups.length - 1];
    if (g && it.minute - g.minute < 45) {
      if (!g.teams.includes(it.team)) g.teams.push(it.team);
    } else groups.push({ minute: it.minute, teams: [it.team] });
  });
  return groups;
}

function renderRuler(state) {
  const ruler = $("ruler");
  ruler.replaceChildren();
  const [nh, nm] = state.day.now.split(":").map(Number);
  const nowPct = (nh * 60 + nm) / 1440 * 100;
  const div = (cls, style) => { const d = el("div", cls); if (style) d.style.cssText = style; return d; };
  ruler.append(div("tr"), div("ps", "width:" + nowPct.toFixed(2) + "%"));
  [["00:00", "left:0;transform:none"], ["06:00", "left:25%"], ["12:00", "left:50%"], ["24:00", "left:100%;transform:translateX(-100%)"]]
    .forEach(([label, style]) => {
      const h = el("span", "h", label);
      h.style.cssText = style;
      ruler.appendChild(h);
    });
  markerGroups(state.timeline).forEach((g) => {
    const pct = g.minute / 1440 * 100;
    const mk = el("span", "mk" + (pct >= nowPct - 1.5 && pct <= nowPct ? " end" : ""));
    mk.style.left = pct.toFixed(2) + "%";
    g.teams.forEach((t) => mk.appendChild(icon(TEAM_ICON[t])));
    ruler.appendChild(mk);
  });
  const now = div("now" + (nowPct > 88 ? " rev" : ""), "left:" + nowPct.toFixed(2) + "%");
  now.appendChild(el("span", null, "τώρα " + state.day.now));
  ruler.appendChild(now);
}

function renderStory(state) {
  const box = $("story");
  box.replaceChildren();
  const last = state.timeline.slice(-5);
  if (!last.length) {
    box.appendChild(el("p", "empty", "Δεν έγινε ακόμα τίποτα σήμερα."));
    return;
  }
  last.forEach((e) => {
    const cell = el("div");
    const tm = el("div", "tm");
    tm.append(el("b", null, hhmm(e.minute)), el("span", "m", TEAM_NAME[e.team] || "Συνεργάτης"));
    const text = el("span");
    richText(text, e.text);
    cell.append(tm, text);
    box.appendChild(cell);
  });
}

/* ---------- κεφαλίδα ---------- */
function renderPaused() {
  const label = paused ? "Συνέχισε" : "Παύση όλων";
  const name = paused ? "play" : "pause";
  const btn = $("stop-btn");
  btn.querySelector("span").textContent = label;
  btn.querySelector("i").className = "ph ph-" + name;
  $("stop-ibtn").setAttribute("aria-label", label);
  $("stop-ibtn").querySelector("i").className = "ph ph-" + name;
  $("paused-banner").hidden = !paused;
}

let liveOn = false;
function renderLive() {
  $("live-dot").className = liveOn ? "on" : "";
  $("live-text").textContent = liveOn
    ? "Ζωντανά" + (lastState ? " · " + lastState.day.now : "") : "Ξανασυνδέομαι…";
}

function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

function renderHome(state) {
  const d = state.day;
  $("home-kick").textContent = d.weekday + " " + d.date + " · " + d.now;
  const lead = $("home-lead");
  lead.replaceChildren();
  const done = state.tasks.done;
  let first;
  if (!done) first = "Σήμερα δεν έχει γίνει ακόμα καμία εργασία μόνη της.";
  else if (d.morning_done_at) {
    first = done === 1 ? "Από τις " + d.morning_done_at + " έγινε μόνη της 1 εργασία."
      : "Από τις " + d.morning_done_at + " έγιναν μόνες τους " + done + " εργασίες.";
  } else {
    first = done === 1 ? "Σήμερα έγινε μόνη της 1 εργασία." : "Σήμερα έγιναν μόνες τους " + done + " εργασίες.";
  }
  lead.appendChild(document.createTextNode(first + " "));
  if (state.today.sales_count) {
    lead.append(document.createTextNode("Ως τώρα "), el("b", null, eur(state.today.sales_cents)),
      document.createTextNode(" από " + plural(state.today.sales_count, "απόδειξη", "αποδείξεις") + "."));
  } else {
    lead.appendChild(document.createTextNode("Δεν υπάρχουν ακόμα πωλήσεις σήμερα."));
  }
}

function render(state) {
  lastState = state;
  paused = !!state.paused;
  renderHome(state);
  renderStages(state);
  renderTeam(state);
  renderRuler(state);
  renderStory(state);
  renderPaused();
  renderLive();
}

async function refresh() {
  try {
    render(await api("/api/state"));
  } catch (e) {
    if (!lastState) $("home-lead").textContent = "Δεν μπόρεσα να φορτώσω. Ξαναδοκιμάζω σε λίγο.";
  }
}

let timer = null;
function refreshSoon() {
  clearTimeout(timer);
  timer = setTimeout(refresh, 500);
}

async function togglePause() {
  try {
    await post(paused ? "/api/resume" : "/api/pause");
  } catch (e) { /* the refresh below shows the real state */ }
  refresh();
}
$("stop-btn").addEventListener("click", togglePause);
$("stop-ibtn").addEventListener("click", togglePause);

/* ---------- ζωντανή ροή ---------- */
function connectStream() {
  const es = new EventSource("/api/events/stream");
  es.onopen = () => {
    liveOn = true;
    renderLive();
    refreshSoon();
  };
  es.onerror = () => {
    liveOn = false;
    renderLive();
  };
  es.onmessage = (msg) => {
    let e;
    try { e = JSON.parse(msg.data); } catch (err) { return; }
    if (e.quiet) { refreshSoon(); return; }  // till sales: only the totals change
    if (liveSteps && (e.type === "agent.tool_ran" || e.type === "approval.requested")) {
      liveSteps.hidden = false;
      liveSteps.appendChild(stepNode(e.text, e.type === "approval.requested"));
    }
    refreshSoon();
  };
}

/* ---------- συνομιλία ---------- */
let liveSteps = null;  // the steps box of the answer being worked on, filled from the live stream

// Plain text with **bold** parts, built from text nodes (never innerHTML).
function richText(node, text) {
  node.replaceChildren();
  String(text).split("**").forEach((part, i) => {
    if (!part) return;
    node.appendChild(i % 2 ? el("b", null, part) : document.createTextNode(part));
  });
}

function stepNode(text, waiting) {
  const step = el("div", "step" + (waiting ? " w" : ""));
  const ic = el("span", "ic" + (waiting ? " w" : ""));
  ic.appendChild(icon(TEAM_ICON.master));
  const body = el("span");
  const label = el("span", "m", "Συνεργάτης");
  const line = el("span");
  richText(line, text);
  body.append(label, document.createElement("br"), line);
  step.append(ic, body);
  if (waiting) {
    const r = el("span", "r");
    r.appendChild(el("b", null, "σε σένα"));
    step.appendChild(r);
  }
  return step;
}

function addMsg(node) {
  const empty = $("conv-empty");
  if (empty) empty.remove();
  $("conv").appendChild(node);
  node.scrollIntoView({ block: "end" });
}

function nowClock() {
  return clock(new Date().toISOString());
}

async function showSteps(wrap, eventIds) {
  if (!eventIds || !eventIds.length) return;
  try {
    const state = await api("/api/state");
    const ids = new Set(eventIds);
    const steps = (state.recent || []).filter((e) => ids.has(e.id) && e.type === "agent.tool_ran").reverse();
    if (!steps.length) return;
    const box = el("div", "steps");
    steps.forEach((s) => box.appendChild(stepNode(s.text, false)));
    wrap.appendChild(box);
  } catch (e) { /* the answer is already shown */ }
}

async function sendChat(text) {
  const sends = document.querySelectorAll(".send");
  sends.forEach((b) => { b.disabled = true; });
  $("chat-kick").textContent = "Εσύ · " + nowClock();
  $("chat-title").textContent = "«" + text + "»";
  const me = el("div", "cm me");
  me.append(el("span", "m who2", "Εσύ · " + nowClock()), el("p", null, text));
  addMsg(me);
  const ai = el("div", "cm ai");
  ai.appendChild(el("span", "m who2", "Συνεργάτης"));
  const body = el("p", "working", "δουλεύω…");
  ai.appendChild(body);
  const steps = el("div", "steps");
  steps.hidden = true;
  ai.appendChild(steps);
  liveSteps = steps;
  addMsg(ai);
  try {
    const r = await post("/api/chat", { text });
    body.className = "";
    richText(body, r.reply || "Δεν έχω κάτι να πω.");
    if (!steps.children.length) {
      steps.remove();
      await showSteps(ai, r.event_ids);
    }
  } catch (e) {
    ai.classList.add("err");
    body.className = "";
    body.textContent = e.message;
  }
  liveSteps = null;
  sends.forEach((b) => { b.disabled = false; });
  refreshSoon();
  ai.scrollIntoView({ block: "end" });
}

function bindAsk(formId, inputId, goToChat) {
  $(formId).addEventListener("submit", (ev) => {
    ev.preventDefault();
    const input = $(inputId);
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    if (goToChat) {
      location.hash = "#synomilia";
      route();
    }
    sendChat(text);
  });
}
bindAsk("ask-home", "ask-home-input", true);
bindAsk("ask-chat", "ask-chat-input", false);

route();
refresh();
connectStream();
