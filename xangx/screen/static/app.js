"use strict";
// Οθόνες του Xangxothaika: vanilla JS, όλο το κείμενο μπαίνει με textContent.

const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
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

/* ---------- οθόνες ---------- */
function route() {
  const key = location.hash === "#synomilia" ? "synomilia" : "arxiki";
  document.querySelectorAll(".screen").forEach((s) => s.classList.toggle("on", s.id === "s-" + key));
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("cur", a.dataset.k === key));
}
window.addEventListener("hashchange", route);

/* ---------- αρχική ---------- */
let paused = false;
let lastState = null;

function renderNeeds(state) {
  const box = $("needs");
  box.replaceChildren();
  const list = state.pending_approvals || [];
  $("need-count").textContent = list.length ? list.length + (list.length === 1 ? " εκκρεμότητα" : " εκκρεμότητες") : "";
  $("nav-cnt").textContent = list.length ? String(list.length) : "";
  if (!list.length) {
    box.appendChild(el("p", "empty", "Δεν υπάρχει ακόμα τίποτα που να θέλει εσένα. Όταν χρειαστεί έγκριση, θα φανεί εδώ."));
    return;
  }
  list.forEach((a) => {
    const row = el("div", "need");
    const text = el("div");
    text.appendChild(el("b", null, a.summary || a.approval_id));
    if (a.exact) text.appendChild(el("p", null, a.exact));
    if (a.why) text.appendChild(el("p", null, "Γιατί: " + a.why));
    const act = el("div", "act");
    const msg = el("span", "msg");
    const no = el("button", "btn sec", "Όχι");
    const yes = el("button", "btn pri", "Ναι");
    no.type = yes.type = "button";
    const decide = (decision) => async () => {
      yes.disabled = no.disabled = true;
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
    act.append(no, yes);
    row.append(text, act, msg);
    box.appendChild(row);
  });
}

function renderShelves(state) {
  const box = $("shelves");
  box.replaceChildren();
  const list = state.shelves || [];
  if (!list.length) {
    box.appendChild(el("p", "empty", "Δεν υπάρχει ακόμα καμία κίνηση στα ράφια. Θα φανούν όταν η κάμερα δει αλλαγή."));
    return;
  }
  const low = {};
  (state.stock || []).forEach((s) => { low[s.product_id] = s; });
  list.forEach((s) => {
    const row = el("div", "shelf");
    const left = el("div");
    left.appendChild(el("b", null, s.product_name));
    const parts = [s.position_name];
    if (s.last_taken) parts.push("πήραν " + s.last_taken.qty + " στις " + clock(s.last_taken.at));
    if (s.last_added) parts.push("γέμισαν " + s.last_added.qty + " στις " + clock(s.last_added.at));
    left.appendChild(el("small", null, parts.join(" · ")));
    row.appendChild(left);
    const stock = low[s.product_id];
    row.appendChild(el("span", "st" + (stock ? " low" : ""), stock ? "χαμηλό: " + stock.on_hand : ""));
    box.appendChild(row);
  });
}

function feedItem(e, isNew) {
  const it = el("div", "it" + (isNew ? " new" : ""));
  it.dataset.id = e.id;
  it.appendChild(el("time", null, clock(e.at)));
  const p = el("p");
  richText(p, e.text);
  it.appendChild(p);
  return it;
}

function renderFeed(state) {
  const box = $("feed");
  box.replaceChildren();
  const list = state.recent || [];
  if (!list.length) {
    box.appendChild(el("p", "empty", "Δεν υπάρχει ακόμα τίποτα. Φόρτωσε demo δεδομένα ή γράψε κάτι στη συνομιλία."));
    return;
  }
  list.forEach((e) => box.appendChild(feedItem(e, false)));
}

function renderPaused() {
  const btn = $("stop-btn");
  btn.textContent = paused ? "Συνέχισε τις ενέργειες" : "Σταμάτα όλες τις ενέργειες";
  btn.classList.toggle("paused", paused);
  $("paused-banner").hidden = !paused;
}

function render(state) {
  lastState = state;
  paused = !!state.paused;
  $("today-title").textContent = new Date().toLocaleDateString("el-GR", {
    weekday: "long", day: "2-digit", month: "2-digit", year: "numeric", timeZone: "Europe/Athens",
  });
  $("today-sub").textContent = "Μίνι μάρκετ Παγκρατίου";
  $("k-sales").textContent = eur(state.today.sales_cents);
  $("k-count").textContent = state.today.sales_count
    ? state.today.sales_count + " αποδείξεις" : "Δεν υπάρχουν ακόμα πωλήσεις σήμερα";
  $("k-low").textContent = String((state.stock || []).length);
  renderNeeds(state);
  renderShelves(state);
  renderFeed(state);
  renderPaused();
}

async function refresh() {
  try {
    render(await api("/api/state"));
  } catch (e) {
    $("today-sub").textContent = "Δεν μπόρεσα να φορτώσω. Ξαναδοκιμάζω σε λίγο.";
  }
}

let timer = null;
function refreshSoon() {
  clearTimeout(timer);
  timer = setTimeout(refresh, 500);
}

$("stop-btn").addEventListener("click", async () => {
  try {
    await post(paused ? "/api/resume" : "/api/pause");
  } catch (e) { /* the refresh below shows the real state */ }
  refresh();
});

/* ---------- ζωντανή ροή ---------- */
function connectStream() {
  const es = new EventSource("/api/events/stream");
  es.onopen = () => {
    $("live-dot").classList.add("live");
    $("live-text").textContent = "Ζωντανά";
  };
  es.onerror = () => {
    $("live-dot").classList.remove("live");
    $("live-text").textContent = "Ξανασυνδέομαι…";
  };
  es.onmessage = (msg) => {
    let e;
    try { e = JSON.parse(msg.data); } catch (err) { return; }
    if (e.quiet) { refreshSoon(); return; }  // till sales: only the totals change
    if (liveSteps && (e.type === "agent.tool_ran" || e.type === "approval.requested")) {
      liveSteps.hidden = false;
      liveSteps.appendChild(el("div", "step", e.text));
    }
    const box = $("feed");
    const empty = box.querySelector(".empty");
    if (empty) empty.remove();
    box.prepend(feedItem(e, true));
    while (box.children.length > 30) box.lastChild.remove();
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

function addMsg(node) {
  const empty = $("conv-empty");
  if (empty) empty.remove();
  $("conv").appendChild(node);
  node.scrollIntoView({ block: "end" });
}

async function showSteps(wrap, eventIds) {
  if (!eventIds || !eventIds.length) return;
  try {
    const state = await api("/api/state");
    const ids = new Set(eventIds);
    const steps = (state.recent || []).filter((e) => ids.has(e.id) && e.type === "agent.tool_ran").reverse();
    if (!steps.length) return;
    const box = el("div", "steps");
    steps.forEach((s) => box.appendChild(el("div", "step", s.text)));
    wrap.appendChild(box);
  } catch (e) { /* the answer is already shown */ }
}

$("ask").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const input = $("ask-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  const send = document.querySelector(".send");
  send.disabled = true;
  addMsg(el("div", "msg me", text));
  const ai = el("div", "msg ai");
  ai.appendChild(el("div", "who", "Xangxothaika"));
  const body = el("div", "ai-text working", "δουλεύω…");
  ai.appendChild(body);
  const steps = el("div", "steps");
  steps.hidden = true;
  ai.appendChild(steps);
  liveSteps = steps;
  addMsg(ai);
  try {
    const r = await post("/api/chat", { text });
    body.className = "ai-text";
    richText(body, r.reply || "Δεν έχω κάτι να πω.");
    if (!steps.children.length) {
      steps.remove();
      await showSteps(ai, r.event_ids);
    }
  } catch (e) {
    ai.classList.add("err");
    body.className = "ai-text";
    body.textContent = e.message;
  }
  liveSteps = null;
  send.disabled = false;
  refreshSoon();
  ai.scrollIntoView({ block: "end" });
});

route();
refresh();
connectStream();
