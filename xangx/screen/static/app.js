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
  syncMenu();
  window.scrollTo(0, 0);
}
// The left menu and the phone tab bar are built by shell.js (deferred), so this also runs once after it.
function syncMenu() {
  document.querySelectorAll(".side a.nv").forEach((a) => {
    const on = a.getAttribute("href") === (current === "synomilia" ? "/#synomilia" : "/");
    a.classList.toggle("on", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  const title = document.querySelector(".mtop > b");
  if (title) title.textContent = current === "synomilia" ? "Συνομιλία" : "Αρχική";
  document.querySelectorAll(".tabbar a.tb").forEach((a) => {
    if (a.getAttribute("href") !== "/") return;
    const on = current === "arxiki";
    a.classList.toggle("on", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
}
window.addEventListener("hashchange", route);
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", syncMenu);

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
  syDecision(first || null);
}

let liveOn = false;

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

/* ---------- ζωντανή ροή ---------- */
function connectStream() {
  const es = new EventSource("/api/events/stream");
  es.onopen = () => {
    liveOn = true;
    refreshSoon();
  };
  es.onerror = () => {
    liveOn = false;
  };
  es.onmessage = (msg) => {
    let e;
    try { e = JSON.parse(msg.data); } catch (err) { return; }
    if (e.quiet) { refreshSoon(); return; }  // till sales: only the totals change
    if (liveSteps && (e.type === "agent.tool_ran" || e.type === "approval.requested")) {
      syLive(e);
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

/* ---- Σελίδα Συνομιλία (σχέδιο Β): όλο το κείμενο με textContent, ποτέ innerHTML με δεδομένα ---- */
const SY_TEAM = { orders: "Παραγγελίες", accountant: "Λογιστής", master: "Συνεργάτης" };
const SY_PANELS = { cur: "m-cur", noritera: "m-hist", vimata: "m-steps" };
const sy = {
  q: null, clock: "", at: 0, doneAt: 0, doneClock: "", working: false, failed: false, reply: "",
  steps: [], rounds: [], pending: [], need: null, day: null, view: "cur", sig: "",
};

function syText(id, text) { $(id).textContent = text; }

// Text where codes like P-0319 never break across two lines.
function syCodes(node, text) {
  node.replaceChildren();
  String(text).split(/([A-ZΑ-Ω]-\d+)/).forEach((part, i) => {
    if (!part) return;
    node.appendChild(i % 2 ? el("span", "sy-nw", part) : document.createTextNode(part));
  });
}

function syWants(k) { return k + (k === 1 ? " θέλει εσένα" : " θέλουν εσένα"); }

function syElapsed() {
  if (!sy.q || !sy.doneAt || sy.failed) return "—";
  return String(Math.max(1, Math.round((sy.doneAt - sy.at) / 1000)));
}

// The time tile: the number big, the unit small next to it.
function syTime(id, t) {
  const b = $(id);
  b.classList.toggle("sy-nil", t === "—");
  b.replaceChildren(document.createTextNode(t === "—" ? "κανένα σήμερα" : t));
  if (t !== "—") b.appendChild(el("small", "sy-u", " δευτ."));
}

// A count tile with nothing to show says so quietly instead of a lone dash.
function syCount(id, text) {
  const b = $(id);
  b.classList.toggle("sy-nil", text === "—");
  b.textContent = text === "—" ? "κανένα σήμερα" : text;
}

// Few steps leave the left column short next to the decision (many steps, the right one). Then the stage
// stacks: the question next to the tiles, the answer and the steps across, the decision across at the bottom.
function syFit() {
  const cb = document.querySelector("#s-synomilia .sy-cb");
  if (!cb || !cb.offsetParent) return;
  cb.classList.remove("sy-low");
  const l = cb.querySelector(".sy-cl").getBoundingClientRect().bottom;
  const r = cb.querySelector(".sy-cr").getBoundingClientRect().bottom;
  cb.classList.toggle("sy-low", Math.abs(l - r) > 56);
}

// One step of the vertical path: done (check), waiting for the owner (hand, yellow), or a warning (red).
function syStepNode(text, waiting) {
  const kind = waiting ? "w" : /Προσοχή|IBAN/.test(text) ? "al" : "";
  const li = el("li", kind || null);
  const dot = el("span", "sy-d");
  dot.appendChild(kind === "w" ? icon("hand-pointing", "ph-fill") : kind === "al" ? icon("warning", "ph-bold") : icon("check", "ph-bold"));
  const line = el("span");
  richText(line, text);
  li.append(dot, line);
  return li;
}

// Called from the live stream while an answer is being worked on.
function syLive(e) {
  if (sy.steps.some((s) => s.id === e.id)) return;
  sy.steps.push({ id: e.id, text: e.text, waiting: e.type === "approval.requested" });
  syDrawAsk();
}

// The last question: the stage on the desktop, the card and the steps screen on the phone.
function syDrawAsk() {
  const asked = sy.q !== null;
  const wait = sy.pending.length;
  const n = sy.steps.length;
  syText("sy-who", asked ? "Εσύ · " + sy.clock : "Η τελευταία σου ερώτηση");
  syText("sy-when", !asked ? "" : sy.working ? "δουλεύω…" : sy.failed ? "δεν απάντησε" : "απάντησε " + sy.doneClock);
  syText("sy-q", asked ? "«" + sy.q + "»" : "Ρώτα ή ζήτα κάτι");
  const ans = $("sy-ans");
  const mans = $("sy-mans");
  if (!asked) {
    ans.textContent = "Γράψε στο πεδίο πάνω, ή πάτα μία έτοιμη ερώτηση.";
    mans.textContent = "Γράψε στο πεδίο κάτω.";
  } else if (sy.working) {
    ans.textContent = mans.textContent = "δουλεύω…";
  } else {
    richText(ans, sy.reply);
    richText(mans, sy.reply);
  }
  $("sy-msug").hidden = asked;
  const list = [$("sy-steps"), $("sy-steps-m")];
  list.forEach((ol) => ol.replaceChildren(...sy.steps.map((s) => syStepNode(s.text, s.waiting))));
  $("sy-steps").classList.toggle("sy-one", n <= 3);

  const time = syElapsed();
  syTime("sy-t-time", time);
  syTime("sy-s-time", time);
  syCount("sy-t-steps", asked ? String(n) : "—");
  syCount("sy-s-steps", asked ? String(n) : "—");
  syText("sy-t-wait", String(wait));
  syText("sy-s-wait", String(wait));
  const tile = $("sy-t-wait").parentNode;
  tile.classList.toggle("sy-al", wait > 0);
  tile.classList.toggle("apo", wait === 0);

  syText("sy-mkk", asked ? "Εσύ · " + sy.clock + (sy.working || sy.failed ? "" : " · απάντησε " + sy.doneClock) : "Η τελευταία σου ερώτηση");
  syText("sy-mq", asked ? "«" + sy.q + "»" : "Ρώτα ή ζήτα κάτι");
  const link = $("sy-msteps");
  link.hidden = !n;
  if (n) {
    $("sy-msteps-t").replaceChildren(el("b", null, plural(n, "βήμα", "βήματα")),
      document.createTextNode((time !== "—" ? " σε " + time + " δευτ." : "") + (wait ? " · " + syWants(wait) : "")));
  }
  syFit();
}

// The decision that waits for the owner (or the calm line when nothing waits). Same call as the home page.
function syButtons(box, a, info, extra) {
  box.replaceChildren();
  const msg = el("span", "msg");
  const yes = el("button", "btn yes", info.yes);
  yes.prepend(a.action === "send_order" || a.action === "request_credit" ? icon("paper-plane-tilt") : icon("check", "ph-bold"));
  const no = el("button", "btn no", "Όχι");
  yes.type = no.type = "button";
  const decide = (decision) => async () => {
    yes.disabled = no.disabled = true;
    msg.textContent = "";
    try {
      const r = await post("/api/approvals/" + encodeURIComponent(a.approval_id), { decision });
      if (r && r.ok === false) {
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
  box.append(yes, no, msg);
}

function syDecision(a) {
  sy.need = a;
  const info = a ? approvalInfo(a.action) : null;
  $("sy-from-i").className = "ph-fill ph-" + (a ? TEAM_ICON[info.team] : "check-circle");
  syText("sy-from", a ? info.ask : "Αποφάσεις");
  syCodes($("sy-need-h"), a ? (a.summary || a.approval_id) : "Δεν περιμένει τίποτα από σένα");
  const ex = $("sy-need-ex");
  ex.textContent = a && a.exact ? a.exact : "";
  ex.hidden = !ex.textContent;
  const why = $("sy-need-why");
  why.textContent = a && a.why ? a.why : "";
  why.hidden = !why.textContent;
  const acts = $("sy-need-acts");
  acts.hidden = !a;
  if (a) syButtons(acts, a, info); else acts.replaceChildren();
  // phone: the same decision, first on the page
  if (a) {
    syText("sy-mk", "Θέλει εσένα · " + SY_TEAM[info.team]);
    syCodes($("sy-mneed-h"), a.summary || a.approval_id);
    syText("sy-mneed-ex", a.exact || "");
    syButtons($("sy-mneed-acts"), a, info);
  }
  syApplyView();
}

// Lead of the showcase and the kicker: today's rounds, steps and what waits for the owner.
function syDrawDay() {
  const n = sy.rounds.length;
  const m = sy.rounds.reduce((acc, r) => acc + r.steps.length, 0);
  const k = sy.pending.length;
  const d = sy.day;
  syText("sy-kick", "Συνεργάτης" + (d ? " · " + d.weekday + " " + d.date : ""));
  syText("sy-kick-m", "Συνεργάτης" + (d ? " · " + d.date : ""));
  const lead = $("sy-lead");
  lead.replaceChildren(document.createTextNode("Σήμερα έκανε "), el("b", null, String(n)),
    document.createTextNode(n === 1 ? " γύρο και " : " γύρους και "), el("b", null, String(m)),
    document.createTextNode(m === 1 ? " βήμα." : " βήματα."));
  const leadM = $("sy-lead-m");
  leadM.replaceChildren(el("b", null, String(n)), document.createTextNode(n === 1 ? " γύρος σήμερα." : " γύροι σήμερα."));
  if (k) {
    lead.append(document.createTextNode(" "), el("b", null, String(k)), document.createTextNode(k === 1 ? " θέλει εσένα." : " θέλουν εσένα."));
    leadM.append(document.createTextNode(" "), el("b", null, String(k)), document.createTextNode(k === 1 ? " θέλει εσένα." : " θέλουν εσένα."));
  }
  syText("sy-d-rounds", String(n));
  syText("sy-d-steps", String(m));
  syText("sy-d-wait", String(k));
}

// One cell of "Νωρίτερα σήμερα" on the desktop: who (colour), when, what, how many steps.
function roundNode(r) {
  const mcp = r.kind === "mcp";
  const cell = el("div", "sy-hc");
  const who = el("span", "sy-w " + (mcp ? "log" : "syn"));
  who.appendChild(icon(mcp ? "calculator" : "circles-three", "ph-fill"));
  const text = el("span", "sy-hx");
  if (r.text) richText(text, r.text); else text.textContent = "δουλεύω…";
  const num = el("b", "sy-n" + (mcp ? " log" : ""));
  num.append(el("b", null, String(r.steps.length)), el("small", null, r.steps.length === 1 ? "βήμα" : "βήματα"));
  const top = el("span", "sy-top");
  top.append(who, num);
  cell.append(top, el("b", "sy-t", r.hhmm), el("b", "sy-ht", r.label.replace(/^Μόνος του · /, "") || "Μόνος του"), text);
  return cell;
}

function syNowCell(a) {
  const cell = el("div", "sy-hc sy-now");
  const who = el("span", "sy-w yes");
  who.appendChild(icon("hand-pointing", "ph-fill"));
  const text = el("span", "sy-hx");
  syCodes(text, a.summary || a.approval_id);
  const top = el("span", "sy-top");
  top.append(who);
  cell.append(top, el("b", "sy-t", "τώρα"), el("b", "sy-ht", "Περιμένει εσένα"), text);
  return cell;
}

// One row of the phone's "Νωρίτερα σήμερα" screen.
function syRoundRow(r) {
  const mcp = r.kind === "mcp";
  const wait = r.steps.some((s) => s.waiting);
  const row = el("div", "sy-mr " + (wait ? "sy-wait" : mcp ? "log" : "syn"));
  const who = el("span", "sy-w " + (mcp ? "log" : "syn"));
  who.appendChild(icon(mcp ? "calculator" : "circles-three", "ph-fill"));
  const p = el("span", "p");
  const text = el("span");
  if (r.text) richText(text, r.text); else text.textContent = "δουλεύω…";
  p.append(el("b", null, r.hhmm + " · " + (r.label.replace(/^Μόνος του · /, "") || "Μόνος του")), text);
  const num = el("b", "sy-n");
  num.append(el("b", null, String(r.steps.length)), el("small", null, r.steps.length === 1 ? "βήμα" : "βήματα"));
  row.append(who, p, num);
  return row;
}

function syDrawHist() {
  const last = sy.rounds.slice(-3);
  const cells = last.map(roundNode);
  if (sy.pending.length) cells.push(syNowCell(sy.pending[0]));
  const host = $("sy-hist");
  host.style.setProperty("--sy-n", String(Math.max(cells.length, 1)));
  if (cells.length) host.replaceChildren(...cells);
  else host.replaceChildren(el("p", "sy-none", "Τίποτα ακόμα σήμερα"));
  const steps = last.reduce((acc, r) => acc + r.steps.length, 0);
  syText("sy-hist-r", last.length ? last.length + " " + (last.length === 1 ? "γύρος" : "γύροι") + " · " + plural(steps, "βήμα", "βήματα") : "");

  const mhist = $("sy-mhist");
  if (last.length) mhist.replaceChildren(...last.slice().reverse().map(syRoundRow));
  else mhist.replaceChildren(el("p", "sy-none", "Τίποτα ακόμα σήμερα"));
  syText("sy-tile-s", last.length
    ? last.length + " " + (last.length === 1 ? "γύρος" : "γύροι") + " · τελευταίος " + last[last.length - 1].hhmm
    : "Τίποτα ακόμα σήμερα");
}

function renderRounds(rounds) {
  const state = lastState;
  const pending = state && state.pending_approvals ? state.pending_approvals : [];
  const sig = JSON.stringify([rounds, pending.map((a) => a.approval_id + a.summary), state && state.day]);
  if (sig === sy.sig) return;
  sy.sig = sig;
  sy.rounds = rounds;
  sy.pending = pending;
  sy.day = state ? state.day : null;
  syDrawDay();
  syDrawHist();
  syDrawAsk();
}

// The phone has three screens inside the page: the card, the steps, the earlier rounds. The hash is not used
// (route() only knows #synomilia), so the state is a class on the page plus the history entry.
function syApplyView() {
  const page = $("s-synomilia");
  Object.keys(SY_PANELS).forEach((k) => {
    $(SY_PANELS[k]).hidden = k !== sy.view;
    page.classList.toggle("sy-v-" + k, k === sy.view);
  });
  $("sy-mneed").hidden = sy.view !== "cur" || !sy.need;
  $("ask-chat-m").hidden = sy.view !== "cur";
  setTimeout(syFit, 0);
}

function syView(view) {
  sy.view = SY_PANELS[view] ? view : "cur";
  syApplyView();
}

function syGo(view) {
  if (view === "back") {
    if (history.state && history.state.sy) history.back(); else syView("cur");
    return;
  }
  history.pushState({ sy: view }, "", location.href);
  syView(view);
  window.scrollTo(0, 0);
}

function syInit() {
  document.querySelectorAll("[data-sy-go]").forEach((a) => {
    a.addEventListener("click", (ev) => { ev.preventDefault(); syGo(a.dataset.syGo); });
  });
  document.querySelectorAll("#sy-sug .sy-q").forEach((b) => {
    b.addEventListener("click", () => { if (!sy.working) sendChat(b.textContent); });
  });
  document.querySelectorAll("#sy-msug .sy-mrow").forEach((b) => {
    b.addEventListener("click", () => { if (!sy.working) sendChat(b.querySelector("span").textContent); });
  });
  document.querySelectorAll('a[data-k="synomilia"]').forEach((a) => {
    a.addEventListener("click", () => syView("cur"));
  });
  window.addEventListener("popstate", (ev) => syView(ev.state && ev.state.sy));
  window.addEventListener("hashchange", () => syView("cur"));
  window.addEventListener("resize", syFit);
  if (document.fonts) document.fonts.ready.then(syFit);
  bindAsk("ask-chat-m", "ask-chat-input-m", false);
  syDecision(null);
  syDrawDay();
  syDrawHist();
  syDrawAsk();
}

function nowClock() {
  return clock(new Date().toISOString());
}

// The steps of an answer that came without live steps: read them back from the events of this answer.
async function showSteps(eventIds) {
  if (!eventIds || !eventIds.length) return;
  try {
    const state = await api("/api/state");
    const ids = new Set(eventIds);
    const found = (state.recent || [])
      .filter((e) => ids.has(e.id) && (e.type === "agent.tool_ran" || e.type === "approval.requested")).reverse();
    // a step may also have come in live while the state was being fetched
    found.filter((e) => !sy.steps.some((s) => s.id === e.id))
      .forEach((e) => sy.steps.push({ id: e.id, text: e.text, waiting: e.type === "approval.requested" }));
    syDrawAsk();
  } catch (e) { /* the answer is already shown */ }
}

async function sendChat(text) {
  const sends = document.querySelectorAll(".send");
  sends.forEach((b) => { b.disabled = true; });
  Object.assign(sy, { q: text, clock: nowClock(), at: Date.now(), doneAt: 0, doneClock: "", working: true, failed: false, reply: "", steps: [] });
  syView("cur");
  syDrawAsk();
  liveSteps = $("sy-steps");
  try {
    const r = await post("/api/chat", { text });
    Object.assign(sy, { working: false, doneAt: Date.now(), doneClock: nowClock(), reply: r.reply || "Δεν έχω κάτι να πω." });
    syDrawAsk();
    if (!sy.steps.length) await showSteps(r.event_ids);
  } catch (e) {
    Object.assign(sy, { working: false, failed: true, doneAt: Date.now(), doneClock: nowClock(), reply: e.message });
    syDrawAsk();
  }
  liveSteps = null;
  sends.forEach((b) => { b.disabled = false; });
  refreshSoon();
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
syInit();

route();
refresh();
connectStream();
