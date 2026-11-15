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
  function pctText(node, pct) {
    if (pct > 0) node.appendChild(ic("arrow-up"));
    if (pct < 0) node.appendChild(ic("arrow-down"));
    node.appendChild(document.createTextNode(Math.abs(pct) + "%"));
    return node;
  }
  function pctClass(pct) { return pct > 0 ? "okt" : pct < 0 ? "al" : ""; }

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

  function renderPay(state, d) {
    const box = byId("hb-pay");
    const list = state.pending_payments || [];
    byId("hb-top").classList.toggle("hb-solo", !list.length);
    box.hidden = !list.length;
    box.replaceChildren();
    if (!list.length) return;
    const p = parsePay(list[0]);
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
    if (iban) {
      const strip = h("div", "ibanB");
      const ibt = h("p", "ibt");
      ibt.append(ic("warning"), document.createTextNode("Νέο IBAN στο ίδιο τιμολόγιο"));
      strip.appendChild(ibt);
      const ibx = h("div", "ibx");
      if (iban.known_last4) {
        const a = h("div");
        a.append(h("span", null, "γνωστό"), h("b", null, "…" + iban.known_last4));
        ibx.append(a, ic("arrow-right"));
      }
      const c = h("div");
      c.append(h("span", null, "στο " + p.label), h("b", null, "…" + iban.new_last4));
      ibx.appendChild(c);
      const lk = h("a", "lk");
      lk.href = TIMOLOGIA;
      lk.append(document.createTextNode("Έλεγχος στο " + p.label + " "), ic("arrow-right"));
      strip.append(ibx, h("p", null, "Δεν το αλλάζω χωρίς εσένα."), lk);
      box.appendChild(strip);
    }
  }

  /* ---------- η ομάδα ---------- */
  function lastFooter(last, href, arrow) {
    const f = h(href ? "a" : "div", "mft");
    if (href) f.href = href;
    if (last) f.append(h("b", null, last.at), h("span", null, last.text));
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
        const row = h("div", "stk");
        const seg = h("span", "seg");
        const total = Math.min(Math.max(Math.round(s.low), 1), 10);
        const filled = Math.min(total, Math.round(s.on_hand / s.low * total));
        for (let i = 0; i < total; i++) seg.appendChild(h("i", i < filled ? "lo" : ""));
        row.append(h("span", null, s.short), seg, h("b", null, qty(s.on_hand) + "/" + qty(s.low)));
        body.appendChild(row);
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
    } else if (key === "invoices") {
      const inv = d.invoices;
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
      if (!nx) { body.appendChild(h("p", "mv none", "Καμία πληρωμή μπροστά")); return body; }
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
      card.append(head, memberBody(key, d), lastFooter(d.last[key], href, key === "stock" || key === "invoices"));
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
      let value, sub = null, none = false;
      if (key === "master") {
        const s = d.sales.same_day;
        if (s.pct === null) { value = "Δεν υπάρχει ακόμα σύγκριση"; none = true; }
        else { value = pctText(h("span", "mtv"), s.pct); sub = "χθες, από τα " + s.plural; }
      } else if (key === "stock") {
        const low = d.stock.low;
        if (!low.length) value = "Όλα πάνω από το όριο";
        else { value = plural(low.length, "χαμηλό", "χαμηλά"); sub = low.slice(0, 3).map((s) => s.short.toLowerCase() + " " + qty(s.on_hand)).join(", "); }
      } else if (key === "orders") {
        const o = d.order;
        if (!o) { value = "Καμία παραγγελία σήμερα"; none = true; }
        else {
          value = money(o.net_cents);
          const verb = { proposed: "πρόταση", sent: "στάλθηκε η", received: "παραλήφθηκε η" }[o.step];
          sub = o.at + " " + verb + " ";
        }
      } else if (key === "invoices") {
        const inv = d.invoices;
        if (!inv.today) { value = "Κανένα τιμολόγιο σήμερα"; none = true; }
        else { value = inv.need_you ? inv.need_you + " για σένα" : "Όλα σωστά"; sub = "από " + inv.today + " με email"; }
      } else {
        const nx = d.payments.next;
        if (!nx) { value = "Καμία πληρωμή μπροστά"; none = true; }
        else { value = due(nx.days); sub = "πληρωμή " + nx.label + ", " + money(nx.amount_cents); }
      }
      const mtv = typeof value === "string" ? h("span", "mtv" + (none ? " none" : ""), value) : value;
      tile.appendChild(mtv);
      const mts = h("span", "mts");
      if (sub) mts.appendChild(document.createTextNode(sub));
      if (key === "orders" && d.order) mts.appendChild(h("span", "nw", d.order.order_id));
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
    if (!u.items.length) box.appendChild(h("p", "empty", "Τίποτα για αναίρεση τώρα"));
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

  function idleCard(state) {
    const card = h("article", "stage mc");
    const mcb = h("div", "mcb");
    const t = state.tasks || { done: 0, with_yes: 0, errors: 0 };
    mcb.append(h("h2", null, "Καμία απόφαση δεν περιμένει"),
      h("p", "why", t.done + " εργασίες σήμερα, " + t.with_yes + " με το «ναι» σου, " + t.errors + " λάθη."));
    card.appendChild(mcb);
    return card;
  }

  function renderCarousel(state, d) {
    const box = byId("hb-car");
    const oldCar = box.querySelector(".car");
    const keep = oldCar && oldCar.firstElementChild ? Math.round(oldCar.scrollLeft / (oldCar.firstElementChild.offsetWidth + 16)) : 0;
    box.replaceChildren();
    const cards = [];
    (state.pending_approvals || []).forEach((a, i) => {
      const st = h("article", "stage mc");
      const own = Object.assign({}, state, { pending_approvals: [a], shelf_focus: i === 0 ? state.shelf_focus : null });
      window.xangxStageNode(st, a, own, { chat: false, titleId: "hb-car-t" + cards.length });
      cards.push(st);
    });
    (state.pending_payments || []).forEach((p) => cards.push(payCard(p, d)));
    (d.iban || []).forEach((r) => cards.push(ibanCard(r)));
    const real = cards.length;
    if (!real) cards.push(idleCard(state));

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
    const sig = JSON.stringify([d, state.timeline, state.day, state.pending_payments,
      state.pending_approvals, state.shelf_focus, state.tasks]);
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
  window.xangxHome = home;
})();
