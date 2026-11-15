"use strict";
/* Η αρχική Β: ο χάρακας της μέρας, «Πλήρωσες;», η ομάδα, οι πωλήσεις, η αναίρεση και το καρουζέλ του κινητού
   (σχέδιο: design/stadio1-v2/B-arxiki/, εγκρίθηκε 15/11/2026). Τα δεδομένα έρχονται από το /api/screen/home και από το /api/state.
   Όλο το κείμενο μπαίνει με textContent, τίποτα από τον server δεν γίνεται HTML.
   Φορτώνεται πριν από το app.js, που καλεί τη window.xangxHome(state) σε κάθε render και χρησιμοποιεί τα post() και
   refreshSoon() του (global, διαβάζονται όταν τρέχει η συνάρτηση, όχι όταν φορτώνει το αρχείο). */
(function () {
  const MEMBERS = {
    master: { name: "Συνεργάτης", icon: "handshake", cls: "m-syn" },
    stock: { name: "Αποθήκη", icon: "package", cls: "m-apo" },
    orders: { name: "Παραγγελίες", icon: "truck", cls: "m-par" },
    invoices: { name: "Τιμολόγια", icon: "receipt", cls: "m-tim" },
    accountant: { name: "Λογιστής", icon: "calculator", cls: "m-log" }
  };
  const ORDER = ["master", "stock", "orders", "invoices", "accountant"];
  const DAY_JS = ["Κυριακή", "Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο"];
  const DAY_PY = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"];
  const TIMOLOGIA = "/static/pages/timologia.html";
  const RAFI = "/static/pages/rafi.html";
  const ERGASIES = "/static/pages/ergasies.html";
  const PAY_METHODS = [["cash", "Μετρητά", "money"], ["card", "Κάρτα", "credit-card"],
    ["bank", "Τράπεζα", "bank"], ["not_yet", "Όχι ακόμα", "clock"]];
  const SVG = "http://www.w3.org/2000/svg";

  let data = null;
  let lastSig = null;
  let sent = null;       // the credit request the owner just sent: {approval_id, credit, message, at, ...}
  let carHadSent = false;

  /* ---------- μικροεργαλεία ---------- */
  function byId(id) { return document.getElementById(id); }
  function h(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function ic(name, weight) {
    const i = h("i", (weight || "ph") + " ph-" + name);
    i.setAttribute("aria-hidden", "true");
    return i;
  }
  function money(cents) {
    const abs = Math.abs(Math.round(cents));
    const whole = String(Math.floor(abs / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return (cents < 0 ? "−" : "") + whole + "," + String(abs % 100).padStart(2, "0") + " €";
  }
  function count(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, "."); }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }
  function ddmm(iso) { const p = String(iso).split("-"); return p[2] + "/" + p[1]; }
  function dayName(iso) { return DAY_JS[new Date(iso + "T12:00:00").getDay()]; }
  function due(days) {
    if (days > 0) return "σε " + plural(days, "μέρα", "μέρες");
    if (days === 0) return "σήμερα";
    return "έληξε πριν από " + plural(-days, "μέρα", "μέρες");
  }
  function qty(n) { return String(Math.round(n * 10) / 10).replace(".", ","); }
  /* η μέρα ως λέξη: σήμερα, χθες, προχθές, όνομα μέρας (ως 6 μέρες πριν), αλλιώς ΗΗ/ΜΜ */
  function ago(iso, todayIso) {
    const n = Math.round((new Date(todayIso + "T12:00:00") - new Date(iso + "T12:00:00")) / 86400000);
    if (n <= 0) return "σήμερα";
    if (n === 1) return "χθες";
    if (n === 2) return "προχθές";
    return n <= 6 ? dayName(iso) : ddmm(iso);
  }
  function pctText(node, pct) {
    if (pct > 0) node.appendChild(ic("arrow-up"));
    if (pct < 0) node.appendChild(ic("arrow-down"));
    node.appendChild(document.createTextNode(Math.abs(pct) + "%"));
    return node;
  }
  function pctClass(pct) { return pct > 0 ? "okt" : pct < 0 ? "al" : ""; }
  function lowerFirst(text) { return String(text || "").charAt(0).toLowerCase() + String(text || "").slice(1); }

  /* ---------- οι προσθήκες για τις στιγμές #2, #5, #6, #9 (σχέδιο 15/11/2026, arxiki-95.css) ---------- */
  // ο κύκλος του μέλους με το εικονίδιο του γεγονότος
  function a95Ic(team, icon) {
    const c = h("span", "a95-ic " + (MEMBERS[team] ? MEMBERS[team].cls : "m-syn"));
    c.appendChild(ic(icon));
    return c;
  }
  // η φράση σε κομμάτια από τον server: το em είναι «bad» (κόκκινο), «good» (πράσινο) ή «b» (έντονο). Μόνο κείμενο, ποτέ HTML.
  function a95Fill(node, parts) {
    const tags = { bad: "em", good: "strong", b: "b" };
    (parts || []).forEach((p) => {
      if (p.em && tags[p.em]) node.appendChild(h(tags[p.em], null, p.t));
      else node.appendChild(document.createTextNode(p.t));
    });
    return node;
  }
  // «Μόλις τώρα»: έως 4 γραμμές, το νεότερο πάνω
  function a95Just(d) {
    const items = (d.just_now || []).slice(0, 4);
    if (!items.length) return null;
    const box = h("div", "a95-just");
    box.appendChild(h("p", "a95-jl", "Μόλις τώρα"));
    items.forEach((it) => {
      const row = h("div", "a95-jr" + (it.kind === "price.changed" ? " a95-pr" : ""));
      const text = h("div");
      text.append(h("small", null, it.who), a95Fill(h("b"), it.parts));
      row.append(h("time", null, it.hhmm), a95Ic(it.team, it.icon), text);
      box.appendChild(row);
    });
    return box;
  }
  // η αιτία της παραγγελίας, κάτω στη σκηνή
  function a95Cause(cause) {
    const row = h("div", "sft a95-cause");
    row.appendChild(a95Ic("stock", "camera"));
    row.appendChild(a95Fill(h("span"), cause.parts));
    return row;
  }
  // το ποσό του τίτλου μένει σε μία γραμμή (χωρίς αλλαγή στο app.js: ξαναφτιάχνουμε τον τίτλο εδώ)
  function a95Money(title) {
    if (!title || title.querySelector(".a95-nw") || title.children.length) return;
    const m = /\d[\d.]*,\d\d €/.exec(title.textContent);
    if (!m) return;
    const text = title.textContent;
    title.replaceChildren(document.createTextNode(text.slice(0, m.index)), h("span", "a95-nw", m[0]),
      document.createTextNode(text.slice(m.index + m[0].length)));
  }
  // η σκηνή της αρχικής στον υπολογιστή τη ζωγραφίζει το app.js· εδώ μπαίνουν από πάνω τα δικά μας κομμάτια
  function a95Stage(state, d) {
    const stage = byId("home-stage");
    if (!stage) return;
    const first = (state.pending_approvals || [])[0];
    const sigOf = (x) => JSON.stringify(x || null);
    if (first) {
      stage.classList.remove("a95-calm");
      const order = first.action === "send_order";
      if (order) a95Money(stage.querySelector("h2"));
      const cause = order && d.order && d.order.cause ? d.order.cause : null;
      const have = stage.querySelector(".a95-cause");
      const other = stage.querySelector(".sft:not(.a95-cause)");  // η δεύτερη απόφαση «Μετά: …» έχει ήδη τη θέση
      if (have && (!cause || other || have.dataset.sig !== sigOf(cause))) have.remove();
      if (cause && !other && !stage.querySelector(".a95-cause")) {
        const node = a95Cause(cause);
        node.dataset.sig = sigOf(cause);
        stage.appendChild(node);
      }
      return;
    }
    if (!stage.classList.contains("low")) { stage.classList.remove("a95-calm"); return; }
    stage.classList.add("a95-calm");
    const box = stage.querySelector(".body > div");
    if (!box) return;
    const sig = sigOf(d.just_now);
    const have = box.querySelector(".a95-just");
    if (have && have.dataset.sig === sig) return;
    if (have) have.remove();
    const node = a95Just(d);
    if (node) { node.dataset.sig = sig; box.appendChild(node); }
  }
  // κινητό: μία γραμμή κάτω από την κύρια κάρτα. Στη σκηνή της παραγγελίας η αιτία, σε άλλη απόφαση η πρώτη γραμμή του «Μόλις τώρα»
  function a95Mline(d, kind) {
    let line = document.getElementById("hb-mline");
    let content = null;
    if (kind === "order" && d.order && d.order.cause) {
      const text = d.order.cause.parts.slice(1).map((x) => x.t).join("");
      content = { team: "stock", icon: "camera", hhmm: d.order.cause.hhmm,
        parts: [{ t: text.charAt(0).toUpperCase() + text.slice(1) }] };
    } else if (kind === "other" && d.just_now && d.just_now.length) {
      const first = d.just_now[0];
      content = { team: first.team, icon: first.icon, hhmm: first.hhmm, parts: first.parts };
    }
    if (!content) { if (line) line.remove(); return; }
    if (!line) {
      line = h("p", "a95-mline");
      line.id = "hb-mline";
      byId("hb-car").insertAdjacentElement("afterend", line);
    }
    const text = h("span");
    text.append(h("b", null, content.hhmm), document.createTextNode(" "));
    a95Fill(text, content.parts);
    line.replaceChildren(a95Ic(content.team, content.icon), text);
  }

  /* δαχτυλίδι αντίστροφης μέτρησης: γεμίζει κατά min(μέρες, 30) / 30 */
  function ring(size, days, label) {
    const r = size === 56 ? 22 : 26;
    const c = size / 2;
    const circ = 2 * Math.PI * r;
    const fill = Math.min(Math.max(days, 0), 30) / 30;
    const wrap = h("span", "ring");
    wrap.style.width = wrap.style.height = size + "px";
    wrap.setAttribute("role", "img");
    wrap.setAttribute("aria-label", label);
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 " + size + " " + size);
    svg.setAttribute("aria-hidden", "true");
    const circle = (cls) => {
      const e = document.createElementNS(SVG, "circle");
      e.setAttribute("cx", c);
      e.setAttribute("cy", c);
      e.setAttribute("r", r);
      e.setAttribute("class", cls);
      return e;
    };
    const rf = circle("rf");
    rf.setAttribute("stroke-dasharray", circ.toFixed(1));
    rf.setAttribute("stroke-dashoffset", (circ * (1 - fill)).toFixed(1));
    rf.setAttribute("transform", "rotate(-90 " + c + " " + c + ")");
    svg.append(circle("rt"), rf);
    const rv = h("span", "rv");
    rv.appendChild(h("b", null, String(Math.max(days, 0))));
    wrap.append(svg, rv);
    return wrap;
  }

  /* ---------- ο χάρακας της μέρας ---------- */
  function groups(timeline) {
    // Η ίδια ομάδα μέσα σε 20 λεπτά: ένα εικονίδιο (με πλήθος). Εικονίδια πιο κοντά από 45 λεπτά κάθονται μαζί.
    const lastOf = {};
    const items = [];
    (timeline || []).forEach((e) => {
      const team = MEMBERS[e.team] ? e.team : "master";
      const prev = lastOf[team];
      if (prev && e.minute - prev.last < 20) { prev.last = e.minute; prev.n += 1; return; }
      const item = { team: team, minute: e.minute, last: e.minute, n: 1 };
      lastOf[team] = item;
      items.push(item);
    });
    const out = [];
    items.sort((a, b) => a.minute - b.minute).forEach((it) => {
      const g = out[out.length - 1];
      if (g && it.minute - g.minute < 45) {
        const same = g.teams.find((t) => t.team === it.team);
        if (same) same.n += it.n; else g.teams.push({ team: it.team, n: it.n });
      } else out.push({ minute: it.minute, teams: [{ team: it.team, n: it.n }] });
    });
    return out;
  }

  function renderRuler(box, state, needInvoices, mobile) {
    box.replaceChildren();
    const now = String(state.day.now).split(":").map(Number);
    const nowPct = (now[0] * 60 + now[1]) / 1440 * 100;
    const pc = (v) => v.toFixed(2) + "%";
    const add = (cls, css, text) => {
      const n = h("div", cls, text);
      if (css) n.style.cssText = css;
      box.appendChild(n);
      return n;
    };
    add("tr");
    add("fut", "left:" + pc(nowPct));
    add("ps", "width:" + pc(nowPct));
    [25, 50, 75].forEach((p) => add("tk", "left:" + p + "%"));
    const labels = mobile
      ? [[0, "00:00", "s"], [50, "12:00", ""], [100, "24:00", "e"]]
      : [[0, "00:00", "s"], [25, "06:00", ""], [50, "12:00", ""], [75, "18:00", ""], [100, "24:00", "e"]];
    labels.forEach((l) => add(("h " + l[2]).trim(), "left:" + l[0] + "%", l[1]));
    add("dnow" + (nowPct > 88 ? " rev" : ""), "left:" + pc(nowPct)).appendChild(h("span", null, "τώρα"));
    const gs = groups(state.timeline);
    let invGroup = -1;
    gs.forEach((g, gi) => { if (g.teams.some((t) => t.team === "invoices")) invGroup = gi; });
    const told = [];
    gs.forEach((g, gi) => {
      const p = g.minute / 1440 * 100;
      const shift = p < 2 ? "translateX(-4px)" : p > 92 ? "translateX(calc(-100% + 16px))" : "translateX(-16px)";
      const mk = add("mk3", "left:" + pc(p) + ";transform:" + shift);
      g.teams.forEach((t) => {
        const m = MEMBERS[t.team];
        const i = ic(m.icon);
        i.classList.add(m.cls);
        if (t.team === "invoices" && gi === invGroup && needInvoices) i.classList.add("x");
        if (t.n > 1) i.setAttribute("data-n", String(t.n));
        mk.appendChild(i);
        told.push(String(Math.floor(g.minute / 60)).padStart(2, "0") + ":" + String(g.minute % 60).padStart(2, "0") + " " + m.name);
      });
    });
    box.setAttribute("aria-label", "Η μέρα: " + (told.length ? told.join(", ") + ". " : "") + "Τώρα " + state.day.now);
  }

  /* ---------- «Πλήρωσες; Πώς;» ---------- */
  function parsePay(p) {
    // Τα κείμενα βγαίνουν από το payments.card_texts: «Πλήρωσες το τιμολόγιο Β-512;» και «Προμηθευτής, ποσό, πώς».
    const m = /τιμολόγιο (.+);$/.exec(p.title || "");
    const parts = String(p.text || "").split(", ");
    return { label: m ? m[1] : p.invoice_id, supplier: parts[0] || "", amount: parts[1] || "",
      how: parts.slice(2).join(", "), title: p.title || "" };
  }

  function payButtons(p, cls, withIcons) {
    const msg = h("p", "hb-msg");
    msg.setAttribute("role", "alert");
    const buttons = PAY_METHODS.map((m) => {
      const b = h("button", "btn " + cls);
      b.type = "button";
      b.append(ic(m[2]), document.createTextNode(m[1]));
      return b;
    });
    buttons.forEach((b, i) => {
      b.addEventListener("click", async () => {
        buttons.forEach((x) => { x.disabled = true; });
        msg.textContent = "";
        try {
          await post("/api/payments/" + encodeURIComponent(p.invoice_id), { method: PAY_METHODS[i][0] });
          refreshSoon();
        } catch (e) {
          msg.textContent = e.message;
          buttons.forEach((x) => { x.disabled = false; });
        }
      });
    });
    return { buttons: buttons, msg: msg };
  }

  function ibanFor(d, label) {
    return (d.iban || []).find((r) => r.invoice_label === label) || null;
  }

  /* η συμπαγής λωρίδα του νέου IBAN (σχέδιο Β): «same» όταν είναι στο ίδιο τιμολόγιο με το «Πλήρωσες;» από πάνω */
  function ibanStrip(iban, label, same) {
    const strip = h("div", "ibanB");
    const ibt = h("p", "ibt");
    ibt.append(ic("warning"), document.createTextNode(same ? "Νέο IBAN στο ίδιο τιμολόγιο" : "Νέο IBAN στο " + label));
    strip.appendChild(ibt);
    const ibx = h("div", "ibx");
    if (iban.known_last4) {
      const a = h("div");
      a.append(h("span", null, "γνωστό"), h("b", null, "…" + iban.known_last4));
      ibx.append(a, ic("arrow-right"));
    }
    const c = h("div");
    c.append(h("span", null, "στο " + label), h("b", null, "…" + iban.new_last4));
    ibx.appendChild(c);
    const lk = h("a", "lk");
    lk.href = TIMOLOGIA;
    lk.append(document.createTextNode("Έλεγχος στο " + label + " "), ic("arrow-right"));
    strip.append(ibx, h("p", null, "Δεν το αλλάζω χωρίς εσένα."), lk);
    return strip;
  }

  function renderPay(state, d) {
    const box = byId("hb-pay");
    const list = state.pending_payments || [];
    const ibans = d.iban || [];
    // Το νέο IBAN φαίνεται και όταν δεν περιμένει «Πλήρωσες;» (στιγμή 1:40 του demo, εύρημα του Μανώλη 15/11).
    const show = list.length > 0 || ibans.length > 0;
    byId("hb-top").classList.toggle("hb-solo", !show);
    box.hidden = !show;
    box.replaceChildren();
    if (!show) return;
    box.setAttribute("aria-label", list.length ? "Πλήρωσες;" : "Νέο IBAN");
    let shown = null;
    if (list.length) {
      const p = parsePay(list[0]);
      shown = p.label;
      const hd = h("div", "hd");
      const b = h("b");
      b.append(ic("wallet"), document.createTextNode("Πλήρωσες; Πώς;"));
      hd.append(b, h("span", "r", plural(list.length, "τιμολόγιο", "τιμολόγια")));
      const row = h("div", "askrow");
      const top = h("div", "hb-askt");
      top.append(h("p", "m", p.label + " · " + p.supplier),
        h("p", "hb-amt", (p.amount + " " + p.how).trim()));
      const pay = payButtons(list[0], "pm", true);
      const grid = h("div", "hb-pms");
      grid.append.apply(grid, pay.buttons);
      row.append(top, grid, pay.msg);
      box.append(hd, row);
      const iban = ibanFor(d, p.label);
      if (iban) box.appendChild(ibanStrip(iban, p.label, true));
    } else {
      const hd = h("div", "hd");
      const b = h("b");
      b.append(ic("bank"), document.createTextNode("Νέο IBAN"));
      hd.append(b, h("span", "r", plural(ibans.length, "τιμολόγιο", "τιμολόγια")));
      box.appendChild(hd);
    }
    ibans.filter((r) => r.invoice_label !== shown).forEach((r) => box.appendChild(ibanStrip(r, r.invoice_label, false)));
  }

  /* ---------- η ομάδα ---------- */
  function lastFooter(last, href, arrow, before) {
    const f = h(href ? "a" : "div", "mft");
    if (href) f.href = href;
    if (last) f.append(h("b", null, last.at), h("span", null, last.text));
    else if (before) f.append(h("b", null, before.date), h("span", null, before.text));
    else f.appendChild(h("span", null, "Τίποτα ακόμα σήμερα"));
    if (arrow) f.appendChild(ic("arrow-right"));
    return f;
  }

  function when(e) {
    if (e.days <= 0) return "σήμερα";
    if (e.days === 1) return "αύριο";
    return e.days <= 6 ? dayName(e.expires_on) : ddmm(e.expires_on);
  }

  function memberBody(key, d) {
    const body = h("div", "mbd");
    if (key === "master") {
      const s = d.sales.same_day;
      if (s.pct === null) { body.appendChild(h("p", "mv none", "Δεν υπάρχει ακόμα σύγκριση")); return body; }
      body.append(pctText(h("p", "mv " + pctClass(s.pct)), s.pct), h("p", null, "χθες, από τα " + s.plural));
      const vals = s.prev_cents.concat([d.sales.yesterday.total_cents]);
      const max = Math.max.apply(null, vals) || 1;
      const sats = h("div", "sats");
      sats.setAttribute("aria-label", "Τα " + s.prev_cents.length + " προηγούμενα " + s.plural + " και χθες");
      vals.forEach((v, i) => {
        const bar = h("i", i === vals.length - 1 ? "y" : "");
        bar.style.height = (v / max * 100).toFixed(1) + "%";
        sats.appendChild(bar);
      });
      const mean = s.prev_cents.reduce((a, b) => a + b, 0) / s.prev_cents.length;
      const avg = h("span", "avg");
      avg.style.bottom = (mean / max * 100).toFixed(1) + "%";
      sats.appendChild(avg);
      body.appendChild(sats);
    } else if (key === "stock") {
      const low = d.stock.low;
      if (!low.length) body.appendChild(h("p", "mv", "Όλα πάνω από το όριο"));
      else body.appendChild(h("p", "mv al", plural(low.length, "χαμηλό", "χαμηλά")));
      low.slice(0, 3).forEach((s) => {
        const row = h("div", "stk" + (s.camera ? " a95-new" : ""));
        const seg = h("span", "seg");
        const total = Math.min(Math.max(Math.round(s.low), 1), 10);
        const filled = Math.min(total, Math.round(s.on_hand / s.low * total));
        for (let i = 0; i < total; i++) seg.appendChild(h("i", i < filled ? "lo" : ""));
        row.append(h("span", null, s.short), seg, h("b", null, qty(s.on_hand) + "/" + qty(s.low)));
        body.appendChild(row);
        if (s.camera) {
          const took = h("p", "a95-took");
          took.append(ic("camera"), document.createTextNode("πήραν " + qty(s.camera.taken) + " στις " + s.camera.hhmm));
          body.appendChild(took);
        }
      });
      if (d.stock.expiring.length) {
        body.appendChild(h("p", null, "Λήγουν: " + d.stock.expiring.map((e) => e.short + " " + when(e)).join(", ")));
      }
    } else if (key === "orders") {
      const o = d.order;
      if (!o) { body.appendChild(h("p", "mv none", "Καμία παραγγελία σήμερα")); return body; }
      body.append(h("p", "mv", money(o.net_cents)), h("p", null, o.order_id + ", " + o.supplier));
      const reached = { proposed: 1, sent: 2, received: 3 }[o.step] || 1;
      const stp = h("ol", "stp");
      ["Πρόταση", "Στάλθηκε", "Παραλαβή"].forEach((name, i) => {
        const li = h("li", i < reached ? "on" : "");
        li.append(h("i"), document.createTextNode(name));
        stp.appendChild(li);
      });
      body.appendChild(stp);
      if (o.reply) {
        const reply = h("div", "a95-reply");
        const text = h("div");
        text.append(h("b", null, o.reply.summary), h("small", null, "απάντηση του προμηθευτή, " + o.reply.hhmm));
        reply.append(a95Ic("orders", "envelope-open"), text);
        body.appendChild(reply);
      }
    } else if (key === "invoices") {
      const inv = d.invoices;
      if (!inv.today && inv.recent && inv.recent.length) {
        const n = inv.recent.length;
        body.append(h("p", "mv hb-quiet", "Κανένα σήμερα"),
          h("p", null, n === 1 ? "Το τελευταίο που ήρθε" : "Τα " + n + " τελευταία που ήρθαν"));
        const docs = h("div", "docs3");
        inv.recent.forEach((r) => {
          const s = h("span");
          s.append(ic("file-text", "ph-fill"), document.createTextNode(r.label), h("em", null, ago(r.date, d.today)));
          docs.appendChild(s);
        });
        body.appendChild(docs);
        return body;
      }
      if (!inv.today) { body.appendChild(h("p", "mv none", "Κανένα τιμολόγιο σήμερα")); return body; }
      body.append(
        h("p", "mv" + (inv.need_you ? " al" : ""),
          inv.need_you ? (inv.need_you === 1 ? "1 θέλει εσένα" : inv.need_you + " θέλουν εσένα") : "Όλα σωστά"),
        h("p", null, "από " + inv.today + " που ήρθαν με email"));
      const docs = h("div", "docs3");
      inv.docs.forEach((doc) => {
        const s = h("span", doc.need ? "x" : "");
        s.append(ic("file-text", "ph-fill"), document.createTextNode(doc.label));
        docs.appendChild(s);
      });
      body.appendChild(docs);
    } else {
      const nx = d.payments.next;
      if (!nx) {
        body.appendChild(h("p", "mv hb-quiet", "Καμία πληρωμή μπροστά"));
        const month = d.invoices.month;
        if (month && month.count) {
          const row = h("div", "hb-lst");
          const text = h("span", null, month.name + " ως τώρα");
          const b = h("b", null, plural(month.count, "τιμολόγιο", "τιμολόγια") + " · ");
          b.appendChild(h("span", "nw", money(month.total_cents)));
          text.appendChild(b);
          row.append(ic("folder-simple"), text);
          body.appendChild(row);
        }
        const paid = d.payments.last_paid;
        if (paid) {
          const row = h("div", "hb-lst");
          const text = h("span", null, "Πληρώθηκε τελευταία");
          const b = h("b", null, paid.label + " · ");
          b.append(h("span", "nw", money(paid.amount_cents)), document.createTextNode(" · " + ago(paid.date, d.today)));
          text.appendChild(b);
          row.append(ic("check-circle"), text);
          body.appendChild(row);
        }
        return body;
      }
      body.classList.add("mrow2");
      const t = h("div");
      t.append(h("p", "mv", money(nx.amount_cents)), h("p", null, due(nx.days) + ", πληρωμή " + nx.label));
      body.append(ring(56, nx.days, due(nx.days)), t);
    }
    return body;
  }

  function renderTeam(d) {
    const box = byId("hb-team");
    box.replaceChildren();
    ORDER.forEach((key) => {
      const m = MEMBERS[key];
      const hot = key === "invoices" && d.invoices.need_you > 0;
      const card = h(key === "invoices" ? "a" : "div", "mem" + (hot ? " hot" : ""));
      if (key === "invoices") card.href = TIMOLOGIA;
      const head = h("div", "mh " + m.cls);
      const mic = h("span", "mic" + (hot ? " x" : ""));
      mic.appendChild(ic(m.icon));
      head.append(mic, h("b", null, m.name));
      const href = key === "stock" ? RAFI : null;
      card.append(head, memberBody(key, d), lastFooter(d.last[key], href, key === "stock" || key === "invoices",
        d.last_before && d.last_before[key]));
      if (key === "stock") {
        const f = card.lastChild;
        f.href = RAFI;
      }
      box.appendChild(card);
    });
  }

  function renderTeamMobile(d) {
    const box = byId("hb-team-m");
    box.replaceChildren();
    ORDER.forEach((key) => {
      const m = MEMBERS[key];
      const href = key === "master" ? "#synomilia" : key === "stock" ? RAFI : key === "invoices" ? TIMOLOGIA : null;
      const tile = h(href ? "a" : "div", "mt5 " + m.cls + (key === "accountant" ? " wide" : ""));
      if (href) tile.href = href;
      const hot = key === "invoices" && d.invoices.need_you > 0;
      const mic = h("span", "mic" + (hot ? " x" : ""));
      mic.appendChild(ic(m.icon));
      tile.append(mic, h("b", null, m.name));
      let value, sub = null, none = false, quiet = false;
      if (key === "master") {
        const s = d.sales.same_day;
        if (s.pct === null) { value = "Δεν υπάρχει ακόμα σύγκριση"; none = true; }
        else { value = pctText(h("span", "mtv"), s.pct); sub = "χθες, από τα " + s.plural; }
      } else if (key === "stock") {
        const low = d.stock.low;
        if (!low.length) value = "Όλα πάνω από το όριο";
        else { value = plural(low.length, "χαμηλό", "χαμηλά"); sub = low.slice(0, 3).map((s) => s.short.toLowerCase() + " " + qty(s.on_hand) +
          (s.camera ? " (πήραν " + qty(s.camera.taken) + ")" : "")).join(", "); }
      } else if (key === "orders") {
        const o = d.order;
        if (!o) { value = "Καμία παραγγελία σήμερα"; none = true; }
        else {
          value = money(o.net_cents);
          const verb = { proposed: "πρόταση", sent: "στάλθηκε η", received: "παραλήφθηκε η" }[o.step];
          sub = o.at + " " + verb + " ";
          if (o.reply) sub = o.reply.hhmm + " ο προμηθευτής " + lowerFirst(o.reply.summary);
        }
      } else if (key === "invoices") {
        const inv = d.invoices;
        if (!inv.today && inv.recent && inv.recent.length) {
          value = "Κανένα σήμερα";
          quiet = true;
          sub = "τελευταίο: " + inv.recent[0].label + ", " + ago(inv.recent[0].date, d.today);
        } else if (!inv.today) { value = "Κανένα τιμολόγιο σήμερα"; none = true; }
        else { value = inv.need_you ? inv.need_you + " για σένα" : "Όλα σωστά"; sub = "από " + inv.today + " με email"; }
      } else {
        const nx = d.payments.next;
        if (!nx) {
          value = "Καμία πληρωμή μπροστά";
          quiet = true;
          const parts = [];
          const month = d.invoices.month;
          if (month && month.count) {
            parts.push(month.name + ": " + plural(month.count, "τιμολόγιο", "τιμολόγια") + ", " + money(month.total_cents) + ".");
          }
          const paid = d.payments.last_paid;
          if (paid) parts.push("Τελευταία πληρωμή: " + paid.label + ", " + ago(paid.date, d.today) + ".");
          sub = parts.length ? parts.join(" ") : null;
        } else { value = due(nx.days); sub = "πληρωμή " + nx.label + ", " + money(nx.amount_cents); }
      }
      const mtv = typeof value === "string" ? h("span", "mtv" + (none ? " none" : "") + (quiet ? " hb-quiet" : ""), value) : value;
      tile.appendChild(mtv);
      const mts = h("span", "mts");
      if (sub) mts.appendChild(document.createTextNode(sub));
      if (key === "orders" && d.order && !d.order.reply) mts.appendChild(h("span", "nw", d.order.order_id));
      tile.appendChild(mts);
      box.appendChild(tile);
    });
  }

  /* ---------- πωλήσεις ---------- */
  function salesHead(range) {
    const hd = h("div", "hd");
    const b = h("b");
    b.append(ic("chart-bar"), document.createTextNode("Πωλήσεις 30 ημερών"));
    hd.append(b, h("span", "r", range));
    return hd;
  }

  function renderSales(d) {
    const box = byId("hb-sales");
    box.replaceChildren();
    const s = d.sales;
    box.appendChild(salesHead(s.from + " έως " + s.to));
    if (!s.total_cents) { box.appendChild(h("p", "empty", "Δεν υπάρχουν ακόμα πωλήσεις")); return; }
    const top = h("div", "sB");
    const left = h("div");
    left.append(h("b", "xl", money(s.total_cents)),
      h("span", "m", "σε 30 μέρες, από " + count(s.receipts) + " αποδείξεις"));
    const line = h("div", "sline");
    const a = h("span");
    a.append(h("i", "kk avg"), h("b", null, money(s.avg_cents)), document.createTextNode(" μέσος όρος τη μέρα"));
    line.appendChild(a);
    const sd = s.same_day;
    const c = h("span");
    if (sd.pct === null) c.appendChild(document.createTextNode("Δεν υπάρχει ακόμα σύγκριση με τα " + sd.plural));
    else {
      c.append(h("i", "kk sat"), pctText(h("b", "up " + pctClass(sd.pct)), sd.pct),
        document.createTextNode(" χθες, από τα " + sd.plural));
    }
    line.appendChild(c);
    top.append(left, line);

    const max = Math.max.apply(null, s.per_day.map((x) => x.total_cents)) || 1;
    const chart = h("div", "chartB");
    chart.setAttribute("role", "img");
    chart.setAttribute("aria-label", "Πωλήσεις ανά μέρα, τα " + sd.plural + " σε σκούρα ζώνη, σήμερα χωρίς πωλήσεις ακόμα");
    const cols = h("div", "cols");
    s.per_day.forEach((day, i) => {
      const last = i === s.per_day.length - 1;
      const col = h("span", "d" + (day.weekday === s.yesterday.weekday ? " s" : "") + (last ? " y" : ""));
      col.title = day.date + ": " + money(day.total_cents);
      const bar = h("i");
      const hgt = day.total_cents / max * 100;
      bar.style.height = hgt.toFixed(1) + "%";
      col.appendChild(bar);
      if (last) {
        const em = h("em", null, "χθες " + money(day.total_cents));
        em.style.bottom = "calc(" + hgt.toFixed(1) + "% + 8px)";
        col.appendChild(em);
      }
      cols.appendChild(col);
    });
    const today = h("span", "d t");
    today.title = "Σήμερα: δεν υπάρχουν ακόμα πωλήσεις";
    today.appendChild(h("i"));
    cols.appendChild(today);
    const avg = h("div", "avg");
    avg.style.bottom = (s.avg_cents / max * 100).toFixed(1) + "%";
    chart.append(cols, avg);
    const axis = h("div", "axis");
    axis.append(h("span", null, s.per_day[0].date), h("span", null, s.per_day[Math.min(16, s.per_day.length - 1)].date),
      h("b", null, "Σήμερα"));
    box.append(top, chart, axis);
  }

  function renderSalesMobile(d) {
    const box = byId("hb-sales-m");
    box.replaceChildren();
    const s = d.sales;
    const tl = h("span", "tl");
    const lead = h("span");
    lead.append(ic("chart-bar"), document.createTextNode("Πωλήσεις"));
    tl.appendChild(lead);
    box.appendChild(tl);
    const body = h("span", "tb2");
    box.appendChild(body);
    if (!s.total_cents) { body.appendChild(h("span", "m", "Δεν υπάρχουν ακόμα πωλήσεις")); return; }
    const y = s.yesterday;
    const first = h("span");
    first.append(h("span", "v", money(y.total_cents)), document.createElement("br"),
      h("span", "m", "χθες, " + DAY_PY[y.weekday]));
    const max = Math.max.apply(null, s.per_day.map((x) => x.total_cents)) || 1;
    const bars = h("span", "mbars s2");
    bars.setAttribute("aria-hidden", "true");
    s.per_day.slice(-14).forEach((day, i, all) => {
      const bar = h("i", i === all.length - 1 ? "y" : day.weekday === y.weekday ? "s" : "");
      bar.style.height = (day.total_cents / max * 100).toFixed(0) + "%";
      bars.appendChild(bar);
    });
    body.append(first, bars);
    if (s.same_day.pct !== null) {
      const cmp = pctText(h("span", "up " + pctClass(s.same_day.pct)), s.same_day.pct);
      cmp.appendChild(document.createTextNode(" από τα " + s.same_day.plural));
      body.appendChild(cmp);
    }
  }

  /* ---------- μπορείς να τα αναιρέσεις ---------- */
  function renderUndo(box, d, mobile) {
    box.replaceChildren();
    const u = d.undo;
    const hd = h("div", "hd hd-need");
    const b = h("b");
    b.append(ic("arrow-counter-clockwise"), document.createTextNode("Μπορείς να τα αναιρέσεις"));
    hd.appendChild(b);
    if (u.items.length) hd.appendChild(h("span", "r", u.items.length + " από " + u.can_total));
    box.appendChild(hd);
    if (!u.items.length) {
      box.appendChild(h("p", "empty", "Τίποτα για αναίρεση τώρα"));
      if (u.kinds && u.kinds.length) {
        const what = h("div", "a95-undo");
        what.appendChild(h("p", "a95-ul", "Αναιρείς μέσα σε " + plural(u.hours, "ώρα", "ώρες")));
        u.kinds.forEach((k) => {
          const row = h("p", "a95-ur");
          row.append(a95Ic(k.team, k.icon), h("span", null, k.label));
          what.appendChild(row);
        });
        box.appendChild(what);
      }
    }
    u.items.forEach((it, i) => {
      const row = h("div", "rem3" + (i === 0 ? " first" : ""));
      const t = h("div", "t");
      t.append(h("b", null, "Πληρωμή " + it.label), h("span", null, it.supplier || ""));
      const rb = h("div", "rb");
      const when_ = h("span");
      when_.append(h("b", null, due(it.days)), document.createTextNode(", " + ddmm(it.due_on)));
      rb.appendChild(when_);
      const node = window.xangxUndoNode("can", it.event_id, "Πληρωμή " + it.label + ", " + money(it.amount_cents), refreshSoon);
      if (node) rb.appendChild(node);
      row.append(ring(64, it.days, due(it.days)), t, h("b", "v", money(it.amount_cents)), rb);
      box.appendChild(row);
    });
    const cf = h("a", "cf");
    cf.href = ERGASIES;
    const lk = h("span", "lk");
    lk.append(document.createTextNode(mobile ? "Όλες οι εργασίες " : "Όλες οι εργασίες της μέρας "), ic("arrow-right"));
    cf.appendChild(lk);
    box.appendChild(cf);
  }

  /* ---------- το καρουζέλ του κινητού: «Θέλουν εσένα» ---------- */
  function payCard(item, d) {
    const p = parsePay(item);
    const card = h("article", "stage mc");
    card.setAttribute("aria-label", p.title);
    const mcb = h("div", "mcb");
    mcb.append(h("p", "from", p.label + " · " + p.supplier), h("h2", null, p.title));
    const why = h("p", "why");
    why.append(h("b", null, p.amount), document.createTextNode(" " + p.how + "."));
    mcb.appendChild(why);
    const iban = ibanFor(d, p.label);
    if (iban) {
      const well = h("div", "well hb-ibw-w");
      well.appendChild(h("span", "m", "Στο ίδιο τιμολόγιο"));
      const warn = h("p");
      warn.append(ic("warning"), document.createTextNode("Νέο IBAN …" + iban.new_last4 + ", δες την επόμενη κάρτα"));
      well.appendChild(warn);
      mcb.appendChild(well);
    }
    const pay = payButtons(item, "no", true);
    const grid = h("div", "grid2");
    grid.append.apply(grid, pay.buttons);
    mcb.append(grid, pay.msg);
    card.appendChild(mcb);
    return card;
  }

  function ibanCard(row) {
    const card = h("article", "stage mc");
    card.setAttribute("aria-label", "Νέο IBAN " + row.invoice_label);
    const mcb = h("div", "mcb");
    mcb.append(h("p", "from", row.invoice_label + " · " + (row.supplier || "")), h("h2", null, "Νέο IBAN …" + row.new_last4));
    const why = h("p", "why");
    why.append(document.createTextNode(row.known_last4 ? "Άλλος λογαριασμός από αυτόν που ξέρουμε. " : "Λογαριασμός που δεν ξέρουμε. "),
      h("b", null, "Δεν το αλλάζω χωρίς εσένα."));
    mcb.appendChild(why);
    const well = h("div", "well hb-ibw");
    const q = h("div", "hb-qty");
    if (row.known_last4) {
      const a = h("div");
      a.append(h("span", null, "γνωστό"), h("b", null, "…" + row.known_last4));
      q.appendChild(a);
    }
    const c = h("div", "x");
    c.append(h("span", null, "στο " + row.invoice_label), h("b", null, "…" + row.new_last4));
    q.appendChild(c);
    well.appendChild(q);
    mcb.appendChild(well);
    const acts = h("div", "acts");
    const lk = h("a", "btn no");
    lk.href = TIMOLOGIA;
    lk.style.flex = "1";
    lk.append(ic("bank"), document.createTextNode("Έλεγχος στο " + row.invoice_label));
    acts.appendChild(lk);
    mcb.appendChild(acts);
    card.appendChild(mcb);
    return card;
  }

  function idleCard(state, d) {
    const card = h("article", "stage mc a95-calm");
    const mcb = h("div", "mcb");
    const t = state.tasks || { done: 0, with_yes: 0, errors: 0 };
    mcb.append(h("h2", null, "Καμία απόφαση δεν περιμένει"),
      h("p", "why", t.done + " εργασίες σήμερα, " + t.with_yes + " με το «ναι» σου, " + t.errors + " λάθη."));
    const just = a95Just(d);
    if (just) mcb.appendChild(just);
    card.appendChild(mcb);
    return card;
  }

  /* η κάρτα «Ζητήθηκε» του κινητού, στη θέση του πιστωτικού που μόλις στάλθηκε */
  function sentCard(s, nextTitle, go) {
    const c = s.credit;
    const card = h("article", "stage mc");
    card.setAttribute("aria-label", "Ζητήθηκε πιστωτικό " + c.label);
    const mcb = h("div", "mcb");
    mcb.appendChild(h("p", "from", c.label + " · " + c.supplier + " · " + s.at));
    const big = h("div", "hb-big");
    big.append(ic("check-circle", "ph-fill"), h("h2", null, "Ζητήθηκε πιστωτικό " + money(c.amount_cents)));
    mcb.appendChild(big);
    const well = h("div", "well");
    const box = h("div", "hb-sent");
    [["Προς", c.supplier], ["Θέμα", c.subject], ["Ποσό", money(c.amount_cents)]].forEach((r) => {
      const row = h("div", "hb-sr");
      row.append(h("span", null, r[0]), h("b", null, r[1]));
      box.appendChild(row);
    });
    well.appendChild(box);
    mcb.appendChild(well);
    const acts = h("div", "acts hb-col");
    if (nextTitle) {
      const next = h("button", "btn yes", "Επόμενο: " + nextTitle + " ");
      next.type = "button";
      next.appendChild(ic("arrow-right"));
      next.addEventListener("click", () => go(1));
      acts.appendChild(next);
    }
    const open = h("a", "btn no", "Άνοιξε το " + c.label);
    open.href = TIMOLOGIA;
    acts.appendChild(open);
    mcb.appendChild(acts);
    card.appendChild(mcb);
    return card;
  }

  function renderCarousel(state, d) {
    const box = byId("hb-car");
    const oldCar = box.querySelector(".car");
    let keep = oldCar && oldCar.firstElementChild ? Math.round(oldCar.scrollLeft / (oldCar.firstElementChild.offsetWidth + 16)) : 0;
    box.replaceChildren();
    const cards = [];
    (state.pending_approvals || []).forEach((a, i) => {
      const st = h("article", "stage mc");
      const own = Object.assign({}, state, { pending_approvals: [a], shelf_focus: i === 0 ? state.shelf_focus : null });
      window.xangxStageNode(st, a, own, { chat: false, titleId: "hb-car-t" + cards.length,
        credit: (d.credit || {})[a.approval_id] || null, mobile: true });
      if (a.action === "send_order") {
        a95Money(st.querySelector("h2"));
        if (d.order && d.order.cause) st.classList.add("a95-order");
      }
      cards.push(st);
    });
    (state.pending_payments || []).forEach((p) => cards.push(payCard(p, d)));
    (d.iban || []).forEach((r) => cards.push(ibanCard(r)));
    // The owner already looked at another card: the "Requested" card goes away, and the owner stays where they are.
    const justSent = home.sentNow(state);
    if (justSent && justSent.seenOther) justSent.carGone = true;
    const showSent = !!justSent && !justSent.carGone;
    if (showSent) {
      const nextTitle = cards.length ? (cards[0].querySelector("h2") || {}).textContent : null;
      cards.unshift(sentCard(justSent, nextTitle, (to) => go(to)));
    }
    if (showSent && !carHadSent) keep = 0;
    else if (!showSent && carHadSent) keep = Math.max(0, keep - 1);
    carHadSent = showSent;
    const real = cards.length;
    if (!real) cards.push(idleCard(state, d));
    const first = (state.pending_approvals || [])[0];
    a95Mline(d, !real ? "idle" : !showSent && first && first.action === "send_order" ? "order" : "other");

    const chd = h("div", "chd");
    const title = h("b");
    title.append(ic("hand-palm"), document.createTextNode("Θέλουν εσένα"));
    const nav = h("span", "nav");
    const label = h("span", null, real ? "1 από " + real : "");
    const prev = h("button");
    const next = h("button");
    prev.type = next.type = "button";
    prev.setAttribute("aria-label", "Προηγούμενο");
    next.setAttribute("aria-label", "Επόμενο");
    prev.appendChild(ic("caret-left"));
    next.appendChild(ic("caret-right"));
    if (real > 1) nav.append(prev, label, next); else nav.appendChild(label);
    chd.append(title, nav);

    const car = h("div", "car");
    cards.forEach((c) => car.appendChild(c));
    const dots = h("div", "dots");
    dots.setAttribute("aria-hidden", "true");
    if (real > 1) cards.forEach((_, i) => dots.appendChild(h("i", i === 0 ? "on" : "")));
    box.append(chd, car, dots);

    const step = () => car.firstElementChild.offsetWidth + 16;
    const index = () => Math.max(0, Math.min(cards.length - 1, Math.round(car.scrollLeft / step())));
    const show = () => {
      const i = index();
      if (showSent && i !== 0) justSent.seenOther = true;
      if (real) label.textContent = (i + 1) + " από " + real;
      Array.prototype.forEach.call(dots.children, (dot, k) => dot.classList.toggle("on", k === i));
    };
    const go = (to) => {
      const i = Math.max(0, Math.min(cards.length - 1, to));
      const calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      car.scrollTo({ left: i * step(), behavior: calm ? "auto" : "smooth" });
    };
    prev.addEventListener("click", () => go(index() - 1));
    next.addEventListener("click", () => go(index() + 1));
    car.addEventListener("scroll", show, { passive: true });
    if (keep > 0 && keep < cards.length) {
      requestAnimationFrame(() => { car.scrollLeft = keep * step(); show(); });
    }
  }

  /* ---------- σύνδεση με το app.js ---------- */
  function untilQuery() {
    const id = new URLSearchParams(location.search).get("until_id");
    return id && /^\d+$/.test(id) ? "?until_id=" + id : "";
  }

  function draw(state) {
    const d = data;
    if (!d) return;
    a95Stage(state, d);
    const sig = JSON.stringify([d, state.timeline, state.day, state.pending_payments,
      state.pending_approvals, state.shelf_focus, state.tasks, sent]);
    if (sig === lastSig) return;
    lastSig = sig;
    const need = d.invoices.need_you > 0;
    renderRuler(byId("hb-ruler"), state, need, false);
    renderRuler(byId("hb-ruler-m"), state, need, true);
    renderPay(state, d);
    renderTeam(d);
    renderTeamMobile(d);
    renderSales(d);
    renderSalesMobile(d);
    renderUndo(byId("hb-undo"), d, false);
    renderUndo(byId("hb-undo-m"), d, true);
    renderCarousel(state, d);
  }

  const home = function (state) { draw(state); };
  // Φέρνει το /api/screen/home σε κάθε refresh. Αν αποτύχει, μένει η προηγούμενη απάντηση.
  home.load = async function () {
    try { data = await api("/api/screen/home" + untilQuery()); } catch (e) { /* the last answer stays */ }
  };
  Object.defineProperty(home, "data", { get: function () { return data; } });

  /* ---------- το πιστωτικό που μόλις στάλθηκε ---------- */
  home.sent = function (approvalId, credit, message) {
    sent = { approval_id: approvalId, credit: credit, message: message || "",
      at: clock(new Date().toISOString()), carGone: false, seenOther: false, newSeen: false };
  };
  // The request, once it is no longer waiting for an answer. `state` is passed in because the stage is drawn before home(state).
  home.sentNow = function (state) {
    if (!sent) return null;
    const pending = (state && state.pending_approvals) || [];
    if (pending.some((a) => a.approval_id === sent.approval_id)) return null;
    if (pending.length) sent.newSeen = true;
    return sent;
  };
  // The desktop stage after "Στείλε": the server's own message, and a way to the invoice.
  home.doneStage = function (stage, s) {
    const c = s.credit;
    stage.replaceChildren();
    stage.classList.remove("low");
    const shd = h("div", "shd");
    const lead = h("span");
    lead.append(ic("calculator"), document.createTextNode("Ο Λογιστής σε ρωτά"));
    shd.append(lead, h("span", null, "1 από 1"));
    const body = h("div", "body one");
    const box = h("div");
    box.appendChild(h("p", "from", c.label + " · " + c.supplier));
    const big = h("div", "hb-big");
    const title = h("h2", null, "Ζητήθηκε πιστωτικό " + money(c.amount_cents));
    title.id = "stage-title";
    big.append(ic("check-circle", "ph-fill"), title);
    box.append(big, h("p", "why", s.message || "Το αίτημα καταγράφηκε."));
    const acts = h("div", "acts");
    const open = h("a", "btn no", "Άνοιξε το " + c.label);
    open.href = TIMOLOGIA;
    acts.appendChild(open);
    box.appendChild(acts);
    body.appendChild(box);
    stage.setAttribute("aria-labelledby", "stage-title");
    stage.append(shd, body);
  };
  window.xangxHome = home;
})();
