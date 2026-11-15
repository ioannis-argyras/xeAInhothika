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
const TEAM_ICON = { stock: "package", orders: "truck", invoices: "receipt", accountant: "calculator", master: "circles-three", agent: "plugs-connected" };

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
    if (!a.dataset.k) return;
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

  // opts.credit: the details of a credit request (from /api/screen/home), shown with the approved box.
  const credit = opts.credit || null;
  const body = el("div", credit ? "body hb-cr" : "body");
  const left = el("div", credit ? "lc" : null);
  if (credit) {
    const from = el("p", "from", credit.label + " · " + credit.supplier);
    if (!opts.mobile) {
      const see = el("a", "lk u", "Δες το τιμολόγιο");
      see.href = "/static/pages/timologia.html";
      from.append(document.createTextNode(" · "), see);
    }
    left.appendChild(from);
  }
  const title = el("h2", null, credit ? "Ζητάω πιστωτικό " + eur(credit.amount_cents) + ";" : (a.summary || a.approval_id));
  title.id = opts.titleId;
  stage.setAttribute("aria-labelledby", opts.titleId);
  left.appendChild(title);
  if (credit && !opts.mobile) {
    const num = (x) => String(x).replace(".", ",");
    const why = el("p", "why");
    why.append(document.createTextNode("Χρεώθηκαν "), el("b", null, num(credit.expected)),
      document.createTextNode(", ήρθαν "), el("b", null, num(credit.actual)), document.createTextNode("."));
    const exact = el("p", "exact hb-lt");
    exact.append(icon("envelope-simple"), el("span", null, "Τι ακριβώς: email προς " + credit.supplier +
      " με αίτημα πιστωτικού " + eur(credit.amount_cents) + " για το " + credit.label + "."));
    left.append(why, exact);
  } else if (!credit && a.why) {
    left.appendChild(el("p", "why", a.why));
  }

  const acts = el("div", credit && opts.mobile ? "acts hb-cra" : "acts");
  const msg = el("span", "msg");
  const yes = el("button", "btn yes", credit ? "Στείλε το αίτημα" : info.yes);
  yes.prepend(credit ? icon("paper-plane-tilt") : icon("check", "ph-bold"));
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
      if (credit && decision === "yes" && window.xangxHome && window.xangxHome.sent) {
        window.xangxHome.sent(a.approval_id, credit, r && r.message);
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
  const focus = opts.chat || credit ? null : state.shelf_focus;
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
  if (credit) {
    // the approved box: what was charged, what arrived, what is missing, and the credit
    const num = (x) => String(x).replace(".", ",");
    const box = el("div", "hb-qb");
    box.setAttribute("aria-label", credit.product + ": χρεώθηκαν " + num(credit.expected) + ", ήρθαν " +
      num(credit.actual) + ", λείπει " + num(credit.missing));
    const hd = el("p", "hb-qhd");
    hd.append(icon("package"), document.createTextNode(credit.product + (credit.more > 0 ? " και άλλα " + credit.more : "") +
      (credit.order_id ? " · παραλαβή " + credit.order_id : "")));
    const tiles = el("div", "hb-qtiles");
    [["tim", "receipt", credit.expected, "τιμολόγιο"], ["apo", "package", credit.actual, "στο ράφι"],
      ["red", "warning-circle", credit.missing, "λείπει"]].forEach(([cls, ic, n, text]) => {
      const t = el("div", "hb-qt " + cls);
      t.append(icon(ic, "ph-fill"), el("b", null, num(n)), el("span", null, text));
      tiles.appendChild(t);
    });
    const cr = el("div", "hb-qcr");
    const label = el("span", null, "Πιστωτικό");
    label.appendChild(el("small", null, credit.more === 0
      ? num(credit.missing) + " × " + eur(credit.unit_cents) + " + ΦΠΑ " + credit.vat_rate + "%"
      : (credit.more + 1) + " είδη + ΦΠΑ"));
    cr.append(label, el("b", null, eur(credit.amount_cents)));
    box.append(hd, tiles, cr);
    right.appendChild(box);
  } else if (a.lines && a.lines.length) {
    const qty = el("div", "qty");
    a.lines.forEach((l) => {
      const row = el("div");
      row.append(el("b", null, l.qty === null ? "" : String(l.qty).replace(".", ",")), el("span", null, l.text));
      qty.appendChild(row);
    });
    right.appendChild(qty);
  } else if (a.exact && !credit) {
    const qty = el("div", "qty");
    qty.appendChild(el("div", "txt", a.exact));
    right.appendChild(qty);
  }
  if (credit && opts.mobile) {
    // On the phone: from, title, box, buttons, in one column.
    left.insertBefore(right, acts);
    body.appendChild(left);
  } else if (opts.chat) {
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

window.xangxStageNode = stageNode;

function renderStages(state) {
  const first = state.pending_approvals[0];
  const hb = window.xangxHome;
  const credit = first && hb.data && hb.data.credit ? hb.data.credit[first.approval_id] || null : null;
  const sent = first ? null : hb.sentNow(state);
  const sig = JSON.stringify([state.pending_approvals, state.shelf_focus,
    state.pending_approvals.length ? null : state.tasks, credit, sent]);
  if (signatures.stages === sig) return;
  signatures.stages = sig;
  const home = $("home-stage");
  if (first) stageNode(home, first, state, { chat: false, titleId: "stage-title", credit });
  else if (sent && !sent.newSeen) hb.doneStage(home, sent);
  else idleStage(home, state);
  const chat = $("chat-stage");
  chat.hidden = !first;
  $("chat-grid").classList.toggle("solo", !first);
  if (first) stageNode(chat, first, state, { chat: true, titleId: "chat-stage-title" });
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
  const kickText = d.weekday + " " + d.date;
  $("home-kick").textContent = kickText;
  $("home-kick-m").textContent = kickText;
  const yesterday = window.xangxHome.data && window.xangxHome.data.sales.yesterday;
  [["home-lead", true], ["home-lead-m", false]].forEach(([id, withCount]) => {
    const lead = $(id);
    lead.replaceChildren();
    if (state.today.sales_count) {
      lead.append(document.createTextNode("Ως τώρα "), el("b", null, eur(state.today.sales_cents)),
        document.createTextNode(" από " + plural(state.today.sales_count, "απόδειξη", "αποδείξεις") + "."),
        el("span", "sim", "Ταμείο: προσομοίωση"));
      return;
    }
    lead.appendChild(document.createTextNode("Δεν υπάρχουν ακόμα πωλήσεις σήμερα."));
    if (yesterday && yesterday.total_cents) {
      lead.append(document.createTextNode(" Χθες "), el("b", null, eur(yesterday.total_cents)),
        document.createTextNode(withCount ? " από " + plural(yesterday.receipts, "απόδειξη", "αποδείξεις") + "." : "."));
    }
  });
}

function render(state) {
  lastState = state;
  paused = !!state.paused;
  renderHome(state);
  renderStages(state);
  window.xangxHome(state);
  renderRounds(state.rounds || []);
  renderPaused();
  renderLive();
}

async function refresh() {
  try {
    const [state] = await Promise.all([api("/api/state"), window.xangxHome.load()]);
    render(state);
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
  node.dataset.at = String(Date.now());
  $("conv").appendChild(node);
  node.scrollIntoView({ block: "end" });
}

// Rounds the partner did not start for the owner: the clock's and another agent's MCP calls.
// They sit in the conversation in order of time, with a small label above.
const roundNodes = new Map();  // round id -> { node, sig }

function roundNode(r) {
  const node = el("div", "cm ai auto");
  node.dataset.at = String(Date.parse(r.at) || Date.now());
  node.appendChild(el("span", "m who2", r.label + " · " + r.hhmm));
  const body = el("p");
  if (r.text) richText(body, r.text);
  else { body.className = "working"; body.textContent = "δουλεύω…"; }
  node.appendChild(body);
  if (r.steps.length) {
    const box = el("div", "steps");
    r.steps.forEach((s) => box.appendChild(stepNode(s.text, s.waiting)));
    node.appendChild(box);
  }
  return node;
}

function renderRounds(rounds) {
  const conv = $("conv");
  rounds.forEach((r) => {
    const sig = JSON.stringify([r.text, r.steps]);
    const known = roundNodes.get(r.id);
    if (known && known.sig === sig) return;
    const node = roundNode(r);
    if (known) known.node.replaceWith(node);
    else {
      const empty = $("conv-empty");
      if (empty) empty.remove();
      const at = Number(node.dataset.at);
      const after = Array.from(conv.children).find((c) => Number(c.dataset.at) > at);
      conv.insertBefore(node, after || null);
    }
    roundNodes.set(r.id, { node, sig });
  });
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
bindAsk("ask-home-m", "ask-home-input-m", true);
bindAsk("ask-chat", "ask-chat-input", false);

route();
refresh();
connectStream();
