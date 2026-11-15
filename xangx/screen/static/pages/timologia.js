/* Σελίδα Τιμολόγια. Χωρίς ?id: η λίστα (αποφάσεις, πληρωμές, όλα τα τιμολόγια, τάσεις, αγορές, φάκελος του μήνα).
   Με ?id: ένα τιμολόγιο, και η ερώτηση για πιστωτικό.
   Δεδομένα: /api/screen/invoices-page, /api/screen/invoices/{id}, /accountant/trends/purchases.
   Ζωντανά από /api/events/stream. Κάθε κείμενο από τον server μπαίνει με textContent. */
(function () {
  "use strict";
  var PAGE = "/static/pages/timologia.html";
  var SVGNS = "http://www.w3.org/2000/svg";
  var params = new URLSearchParams(location.search);
  var ID = params.get("id");
  if (ID !== null && !/^[A-Za-z0-9-]{1,40}$/.test(ID)) ID = "";
  var paused = false, timer = null, loading = false, again = false, sending = false;
  var done = "";            // το μήνυμα μετά από «όχι», να μη χαθεί με την ανανέωση
  var sent = null;          // το πιστωτικό που μόλις ζητήθηκε: {amount, label, message}
  var btns = [];            // τα κουμπιά «Στείλε» και «Όχι» της σκηνής (υπολογιστής και κινητό)
  var openMonths = {};      // ποιοι παλιότεροι μήνες είναι ανοιχτοί
  var moreOpen = false;     // ο πρώτος μήνας δείχνει όλες τις γραμμές
  var lastPage = null;      // η τελευταία απάντηση της λίστας, για να ξαναζωγραφίζεται ο πίνακας
  var tabsBound = false;

  var WEEKDAYS = ["Κυριακή", "Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο"];
  var GENITIVE = ["Ιανουαρίου", "Φεβρουαρίου", "Μαρτίου", "Απριλίου", "Μαΐου", "Ιουνίου", "Ιουλίου",
    "Αυγούστου", "Σεπτεμβρίου", "Οκτωβρίου", "Νοεμβρίου", "Δεκεμβρίου"];

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function txt(s) { return document.createTextNode(s); }
  function icon(kind, name) { return el("i", kind + " ph-" + name); }
  function show(id, on) { $(id).hidden = !on; }
  function svg(tag, attrs) {
    var n = document.createElementNS(SVGNS, tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    return n;
  }
  // σβήνει ό,τι υπάρχει μετά το πρώτο στοιχείο του (την κεφαλίδα .hd, ή τη γραμμή .th του πίνακα)
  function clearAfter(first) {
    while (first.nextSibling) first.parentNode.removeChild(first.nextSibling);
  }
  function clearCard(card) { clearAfter(card.querySelector(".hd")); }

  /* ---------- μορφές ---------- */
  function eur(cents) {
    var c = Math.round(Number(cents) || 0), neg = c < 0;
    c = Math.abs(c);
    var whole = String(Math.floor(c / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return (neg ? "−" : "") + whole + "," + String(c % 100).padStart(2, "0") + " €";
  }
  function num(x) { return String(x).replace(".", ","); }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }
  function utc(iso) {
    var p = String(iso || "").slice(0, 10).split("-");
    return p.length === 3 ? Date.UTC(+p[0], +p[1] - 1, +p[2]) : NaN;
  }
  function dm(iso) { var p = String(iso || "").slice(0, 10).split("-"); return p.length === 3 ? p[2] + "/" + p[1] : String(iso || ""); }
  function dmy(iso) { var p = String(iso || "").slice(0, 10).split("-"); return p.length === 3 ? p[2] + "/" + p[1] + "/" + p[0] : String(iso || ""); }
  function localToday() {
    var d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function dayWord(iso, today) {
    var d = Math.round((utc(today) - utc(iso)) / 864e5);
    if (isNaN(d)) return dm(iso);
    if (d === 0) return "σήμερα";
    if (d === 1) return "χθες";
    if (d === 2) return "προχθές";
    if (d > 2 && d <= 6) return WEEKDAYS[new Date(utc(iso)).getUTCDay()];
    return dm(iso);
  }
  function dayName(iso) { return WEEKDAYS[new Date(utc(iso)).getUTCDay()]; }
  function genitive(key, fallback) {
    var m = /^\d{4}-(\d{2})$/.exec(key || "");
    return m && GENITIVE[+m[1] - 1] ? GENITIVE[+m[1] - 1] : fallback;
  }
  function cap(s) { s = String(s || ""); return s.charAt(0).toUpperCase() + s.slice(1); }
  function href(id) { return PAGE + "?id=" + encodeURIComponent(id); }

  /* ---------- ζωντανά και παύση ---------- */
  var clock = "";
  function setLive(on, text) {
    $("live-dot").className = on ? "on" : "";
    $("live-text").textContent = on && clock ? text + " · " + clock : text;
  }
  function setPaused(p) {
    paused = !!p;
    $("paused-banner").hidden = !paused;
    var label = paused ? "Συνέχισε" : "Παύση όλων";
    var icn = paused ? "play" : "pause";
    $("stop-btn").replaceChildren(icon("ph", icn), el("span", null, label));
    $("stop-ibtn").replaceChildren(icon("ph", icn));
    $("stop-ibtn").setAttribute("aria-label", label);
  }
  function togglePause() {
    var url = paused ? "/api/resume" : "/api/pause";
    $("stop-btn").disabled = $("stop-ibtn").disabled = true;
    fetch(url, {method: "POST"}).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.ok) setPaused(d.paused);
    }).catch(function () {}).then(function () {
      $("stop-btn").disabled = $("stop-ibtn").disabled = false;
    });
  }

  /* ---------- κοινά κομμάτια ---------- */
  function views(name) {
    ["v-wait", "v-lista", "v-tim", "v-problem"].forEach(function (v) { show(v, v === name); });
    $("s-tim").setAttribute("aria-busy", name === "v-wait" ? "true" : "false");
  }
  function problem(text) {
    views("v-problem");
    $("problem-text").textContent = text;
  }
  function hd(ic, title, right, kind) {
    var d = el("div", "hd");
    var b = el("b");
    b.append(icon(kind || "ph", ic), txt(title));
    d.append(b, el("span", "r", right || ""));
    return d;
  }
  function empty(card, text) { card.appendChild(el("p", "empty", text)); }
  function ring(size, days, label, mini) {
    var r = mini ? 12 : size / 2 - 6, c = size / 2, circ = 2 * Math.PI * r;
    var fill = Math.min(Math.max(days, 0), 30) / 30;
    var wrap = el("span", "ring" + (mini ? " tb-mini" : "") + (days <= 0 && !mini ? " tb-r0" : ""));
    if (!mini) wrap.style.width = wrap.style.height = size + "px";
    wrap.setAttribute("role", "img");
    wrap.setAttribute("aria-label", label);
    var s = svg("svg", {viewBox: "0 0 " + size + " " + size, "aria-hidden": "true"});
    var t = svg("circle", {cx: c, cy: c, r: r, "class": "rt"});
    var f = svg("circle", {cx: c, cy: c, r: r, "class": "rf", "stroke-dasharray": circ.toFixed(1),
      "stroke-dashoffset": (circ * (1 - fill)).toFixed(1), transform: "rotate(-90 " + c + " " + c + ")"});
    s.append(t, f);
    var rv = el("span", "rv");
    rv.appendChild(el("b", null, String(Math.max(days, 0))));
    wrap.append(s, rv);
    return wrap;
  }
  function dueText(days, dueOn) {
    if (days === 0) return "λήγει σήμερα";
    if (days < 0) return "έληξε πριν " + plural(-days, "μέρα", "μέρες");
    return "σε " + plural(days, "μέρα", "μέρες") + ", " + dm(dueOn);
  }

  /* ---------- λίστα: βιτρίνα ---------- */
  function leadNodes(counts) {
    var out = [];
    if (counts.all) {
      out.push(txt(counts.all + " από " + counts.suppliers + (counts.suppliers === 1 ? " προμηθευτή" : " προμηθευτές") + ". "));
    }
    if (!counts.today) { out.push(txt("Σήμερα δεν ήρθε κανένα.")); return out; }
    out.push(txt("Σήμερα ήρθαν "), el("b", null, String(counts.today)));
    if (counts.need_you) {
      out.push(txt(counts.need_you === 1 ? ", το " : ", τα "), el("b", null, String(counts.need_you)),
        txt(counts.need_you === 1 ? " θέλει εσένα." : " θέλουν εσένα."));
    } else out.push(txt("."));
    return out;
  }

  /* ---------- λίστα: θέλουν εσένα ---------- */
  function needRow(n) {
    var red = n.kind !== "pay";
    var row = el("div", "tb-nr " + (red ? "red" : "brz"));
    var ic = el("span", "tb-ic " + (red ? "red" : "m-log"));
    ic.appendChild(icon("ph-fill", n.kind === "credit" ? "warning-circle" : n.kind === "iban" ? "bank" : "wallet"));
    var t = el("div", "t");
    var title, line, label, go, link = href(n.invoice_id);
    t.appendChild(el("span", null, n.label + " · " + n.supplier));
    if (n.kind === "credit") {
      title = "Ζητάω πιστωτικό;";
      line = "Λείπει " + n.missing + " " + n.product + (n.order_id ? " από την παραλαβή " + n.order_id : "");
      label = "Άνοιξε";
    } else if (n.kind === "iban") {
      title = "Νέο IBAN …" + n.new_last4;
      line = "Ξέρουμε το …" + n.known_last4 + ". Δεν το αλλάζω χωρίς εσένα.";
      label = "Έλεγχος";
    } else {
      title = "Πλήρωσες;";
      line = (n.how ? cap(n.how) + ". " : "") + "Απαντάς στην αρχική.";
      label = "Αρχική";
      link = "/";
    }
    t.append(el("b", null, title), el("em", null, line));
    var r = el("div", "r");
    if (n.amount_cents != null) r.appendChild(el("b", null, eur(n.amount_cents)));
    var a = el("a", "btn q", label);
    a.href = link;
    a.appendChild(icon("ph", "arrow-right"));
    r.appendChild(a);
    row.append(ic, t, r);
    return row;
  }
  function okLine(page) {
    var ok = page.ok_today || [];
    if (!ok.length) return null;
    var s = el("div", "sft");
    s.appendChild(icon("ph", "check-circle"));
    s.appendChild(el("span", "tx", ok.length === 1
      ? ok[0].label + " · " + ok[0].supplier + ": εντάξει, χωρίς ερώτηση."
      : ok.length + " ακόμα εντάξει, χωρίς ερώτηση"));
    return s;
  }
  function renderStage(page) {
    var st = $("l-stage");
    st.replaceChildren();
    var none = !page.need.length;
    st.classList.toggle("low", none);
    var shd = el("div", "shd");
    var l = el("span");
    l.append(icon("ph", "hand-palm"), txt("Θέλουν εσένα"));
    shd.append(l, el("span", null, String(page.need.length)));
    st.appendChild(shd);
    if (none) {
      var body = el("div", "body one"), box = el("div");
      box.appendChild(el("h2", null, "Καμία απόφαση δεν περιμένει"));
      body.appendChild(box);
      st.appendChild(body);
    } else {
      var np = el("div", "tb-np");
      page.need.forEach(function (n) { np.appendChild(needRow(n)); });
      st.appendChild(np);
    }
    var sft = okLine(page);
    if (sft) st.appendChild(sft);
  }
  function mobNeedRow(n) {
    var red = n.kind !== "pay";
    var a = el("a", "tb-mr " + (red ? "red" : "brz"));
    a.href = n.kind === "pay" ? "/" : href(n.invoice_id);
    var ic = el("span", "tb-ic " + (red ? "red" : "m-log"));
    ic.appendChild(icon("ph-fill", n.kind === "credit" ? "warning-circle" : n.kind === "iban" ? "bank" : "wallet"));
    var t = el("span", "t");
    if (n.kind === "pay") t.append(el("b", null, n.label + " · Πλήρωσες;"), el("span", null, cap(n.how || "")));
    else t.append(el("b", null, n.label + " · " + n.supplier),
      el("span", null, n.kind === "credit" ? "Ζητάω πιστωτικό;" : "Νέο IBAN …" + n.new_last4));
    var r = el("span", "r");
    if (n.amount_cents != null) r.appendChild(el("b", null, eur(n.amount_cents)));
    r.appendChild(icon("ph", "caret-right"));
    a.append(ic, t, r);
    return a;
  }

  /* ---------- λίστα: πληρωμές των νέων ---------- */
  function folderLine(f) {
    return f.name + " ως τώρα: " + plural(f.count, "τιμολόγιο", "τιμολόγια") + " · " + eur(f.total_cents);
  }
  function renderPayments(page) {
    var items = page.payments.items || [];
    var n = items.length ? plural(items.length, "τιμολόγιο", "τιμολόγια") : "";
    $("l-pay-n").textContent = n;
    var card = $("l-pay");
    clearCard(card);
    if (!items.length) {
      empty(card, "Καμία πληρωμή μπροστά");
      if (page.month_folder) card.appendChild(el("p", "empty", folderLine(page.month_folder)));
      return;
    }
    var tot = el("div", "tb-tot");
    tot.append(el("b", null, eur(page.payments.total_cents)), el("span", null, "ως " + dmy(page.payments.until)));
    card.appendChild(tot);
    items.slice(0, 4).forEach(function (it) {
      var row = el("div", "tb-pr" + (it.days <= 0 ? " tb-due" : ""));
      var t = el("div", "t");
      t.append(el("b", null, it.label), el("span", null, dueText(it.days, it.due_on) + " · " + it.supplier));
      row.append(ring(64, it.days, it.days <= 0 ? "σήμερα" : "σε " + plural(it.days, "μέρα", "μέρες")), t,
        el("b", null, eur(it.amount_cents)));
      card.appendChild(row);
    });
    var cf = el("div", "cf");
    cf.appendChild(el("span", "tb-ink", "Τη μέρα που λήγει, σε ρωτώ «Πλήρωσες;» στην αρχική."));
    card.appendChild(cf);
  }
  function renderMobPayments(page) {
    var items = page.payments.items || [];
    var card = el("article", "card mt16");
    card.setAttribute("aria-label", "Πληρωμές των νέων");
    card.appendChild(hd("calendar-blank", "Πληρωμές των νέων", items.length ? String(items.length) : ""));
    if (!items.length) {
      empty(card, "Καμία πληρωμή μπροστά");
      if (page.month_folder) card.appendChild(el("p", "empty", folderLine(page.month_folder)));
      return card;
    }
    var tot = el("div", "tb-mtot");
    tot.append(el("b", null, eur(page.payments.total_cents)), el("span", null, "ως " + dmy(page.payments.until)));
    card.appendChild(tot);
    var r3 = el("div", "tb-r3");
    items.slice(0, 3).forEach(function (it) {
      var d = el("div");
      var when = el("span");
      when.append(txt(it.days <= 0 ? "σήμερα" : dm(it.due_on)), el("br"), txt(eur(it.amount_cents)));
      d.append(ring(56, it.days, it.days <= 0 ? "σήμερα" : "σε " + plural(it.days, "μέρα", "μέρες")),
        el("b", null, it.label), when);
      r3.appendChild(d);
    });
    card.appendChild(r3);
    return card;
  }

  /* ---------- λίστα: όλα τα τιμολόγια ---------- */
  function statusNode(r, today, mobile) {
    var s = el("span", "st2" + (mobile ? " c-st" : ""));
    var p = r.pay;
    if (r.level === "need" || r.level === "warn") {
      s.classList.add("al");
      s.append(icon("ph", /IBAN/i.test(r.status) ? "bank" : "warning"), txt(r.status));
    } else if (p && p.state === "due") {
      s.classList.add("tb-due2");
      var when = p.days <= 0 ? "σήμερα" : "σε " + plural(p.days, "μέρα", "μέρες") + ", " + dm(p.due_on);
      s.append(ring(32, p.days, when, true), txt("Πληρωμή " + (p.days <= 0 ? "σήμερα" : when)));
    } else if (p && p.state === "paid") {
      s.classList.add("tb-paid");
      s.append(icon("ph", "check"), txt("Πληρώθηκε " + dayWord(p.date, today)));
    } else {
      s.textContent = r.status || "";
    }
    return s;
  }
  function allRow(r, today, mobile, inToday) {
    var need = r.level === "need";
    var a = el("a", "tr2" + (need ? " bad hot" : ""));
    a.href = href(r.invoice_id);
    var who = r.supplier || "Δεν διαβάστηκε ακόμα";
    var num = el("span", "num");
    var came = r.arrived && r.arrived.date === today
      ? r.arrived.at + (r.arrived.via === "email" ? " · email" : r.arrived.via === "photo" ? " · φωτογραφία" : "")
      : dayWord(r.arrived && r.arrived.date, today);
    num.append(el("b", null, r.label), el("span", "m", mobile && !inToday ? who + " · " + came : who));
    var total = el("b", "n", r.total_cents ? eur(r.total_cents) : "");
    if (mobile) {
      a.append(num, total);
      if (inToday || need) a.appendChild(statusNode(r, today, true));
    } else {
      a.append(num, el("span", null, came), el("span", "n", r.lines ? String(r.lines) : ""),
        statusNode(r, today, false), total);
    }
    return a;
  }
  function sub(name, count, total) {
    var d = el("div", "tb-sub", name);
    d.appendChild(el("span", null, count + " · " + eur(total)));
    return d;
  }
  function fillTable(box, page, mobile) {
    var today = page.today, limit = mobile ? 3 : 6, firstMonth = true;
    page.months.forEach(function (g) {
      var isToday = g.key === "today";
      var rows = g.rows;
      if (isToday) {
        box.appendChild(sub(g.name, g.count, g.total_cents));
      } else if (firstMonth) {
        box.appendChild(sub(g.name, g.count, g.total_cents));
      } else {
        var open = !!openMonths[g.key];
        var head = el("div", "tb-sub tb-cl");
        var a = el("a");
        a.href = "#";
        a.setAttribute("role", "button");
        a.setAttribute("aria-expanded", String(open));
        a.append(icon("ph", open ? "caret-down" : "caret-right"), txt(g.name));
        a.addEventListener("click", function (ev) {
          ev.preventDefault();
          openMonths[g.key] = !openMonths[g.key];
          drawTables();
        });
        head.append(a, el("span", null, g.count + " · " + eur(g.total_cents)));
        box.appendChild(head);
        if (!open) return;
      }
      var cut = !isToday && firstMonth && !moreOpen && rows.length > limit;
      (cut ? rows.slice(0, limit) : rows).forEach(function (r) { box.appendChild(allRow(r, today, mobile, isToday)); });
      if (cut) {
        var more = el("a", "more2", "Δες και τα άλλα " + (rows.length - limit) +
          (mobile ? "" : " του " + genitive(g.key, g.name)) + " ");
        more.href = "#";
        more.appendChild(icon("ph", "arrow-right"));
        more.addEventListener("click", function (ev) {
          ev.preventDefault();
          moreOpen = true;
          drawTables();
        });
        box.appendChild(more);
      }
      if (!isToday) firstMonth = false;
    });
  }
  var EMPTY_ALL = "Δεν υπάρχουν ακόμα τιμολόγια. Έρχονται μόνα τους από email και myDATA, ή ανέβασε ένα.";
  function drawTables() {
    var page = lastPage;
    if (!page) return;
    var card = $("l-all"), none = !page.months.length;
    var th = card.querySelector(".th");
    th.style.display = none ? "none" : "";
    clearAfter(th);
    if (none) empty(card, EMPTY_ALL);
    else fillTable(card, page, false);
    var m = $("m-all");
    m.replaceChildren();
    var sec = el("section", "card tbl tb-ml");
    if (none) empty(sec, EMPTY_ALL);
    else fillTable(sec, page, true);
    m.appendChild(sec);
  }

  /* ---------- λίστα: τάσεις και αγορές ---------- */
  function changeNodes(c, ref) {
    var up = c.pct > 0;
    var tr = el("div", "tb-tr");
    tr.appendChild(el("b", null, c.name));
    var p = el("p");
    var pct = el("b", null, num(Math.abs(c.pct).toFixed(1)).replace(/,0$/, "") + "%");
    var arrow = icon("ph", up ? "arrow-up" : "arrow-down");
    if (!up) { pct.style.color = arrow.style.color = "var(--ok)"; }
    p.append(arrow, pct, txt(" " + (ref ? "στο " + ref.label + ": " : "") + eur(c.was_cents) + " → " + eur(c.now_cents)));
    tr.appendChild(p);
    return tr;
  }
  function chart(data, c) {
    var row = (data.prices || []).filter(function (p) { return p.product_id === c.product_id; })[0];
    if (!row || !row.points || !row.points.length) return null;
    var pts = row.points.slice().sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; });
    var cheap = row.cheapest && row.cheapest.supplier_id !== row.last_supplier_id ? row.cheapest : null;
    var vals = pts.map(function (p) { return p.unit_cents; });
    if (cheap) vals.push(cheap.unit_cents);
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var t0 = utc(pts[0].date), t1 = utc(pts[pts.length - 1].date), span = t1 - t0;
    function X(date) { return span > 0 ? 16 + (utc(date) - t0) / span * 274 : 290; }
    function Y(v) { return hi === lo ? 70 : 110 - (v - lo) / (hi - lo) * 80; }
    var last = pts[pts.length - 1], first = pts[0];
    var s = svg("svg", {viewBox: "0 0 320 150", "class": "tb-ch", role: "img",
      "aria-label": "Τιμή " + eur(first.unit_cents) + " στις " + dm(first.date) + ", " + eur(last.unit_cents) +
        " στις " + dm(last.date) + (cheap ? ". Η " + cheap.name + " " + eur(cheap.unit_cents) + "." : "")});
    [[30, "#D5DDE8"], [70, "#D5DDE8"], [110, "#AFBCCD"]].forEach(function (g) {
      s.appendChild(svg("line", {x1: 16, y1: g[0], x2: 304, y2: g[0], stroke: g[1]}));
    });
    s.appendChild(svg("polyline", {points: pts.map(function (p) { return X(p.date).toFixed(1) + "," + Y(p.unit_cents).toFixed(1); }).join(" "),
      fill: "none", stroke: "#16305A", "stroke-width": 3, "stroke-linejoin": "round"}));
    var lx = X(last.date), ly = Y(last.unit_cents);
    s.appendChild(svg("circle", {cx: lx.toFixed(1), cy: ly.toFixed(1), r: 6, fill: "#A3351B"}));
    function label(x, y, anchor, size, color, weight, text) {
      var t = svg("text", {x: x, y: y, "text-anchor": anchor, "font-size": size, fill: color});
      if (weight) t.setAttribute("font-weight", weight);
      t.textContent = text;
      s.appendChild(t);
    }
    label(Math.min(lx + 6, 304).toFixed(1), Math.max(ly - 10, 14).toFixed(1), "end", 14, "#A3351B", 700, eur(last.unit_cents));
    if (first.unit_cents !== last.unit_cents) label(16, Math.min(Y(first.unit_cents) + 18, 128).toFixed(1), "start", 13, "#4B586B", 0, eur(first.unit_cents));
    if (cheap) {
      var mine = pts.filter(function (p) { return p.supplier_id === cheap.supplier_id; });
      var cx = mine.length ? X(mine[mine.length - 1].date) : 160, cy = Y(cheap.unit_cents);
      s.appendChild(svg("circle", {cx: cx.toFixed(1), cy: cy.toFixed(1), r: 6, fill: "#fff", stroke: "#0F7180", "stroke-width": 3}));
      var left = cx > 150;
      label((left ? cx - 10 : cx + 10).toFixed(1), (cy - 10).toFixed(1), left ? "end" : "start", 13, "#0F7180", 700,
        cheap.name + " " + eur(cheap.unit_cents));
    }
    label(16, 146, "start", 12, "#4B586B", 0, dm(first.date));
    if (span > 0) label(160, 146, "middle", 12, "#4B586B", 0, dm(new Date(t0 + span / 2).toISOString()));
    label(304, 146, "end", 12, "#4B586B", 0, dm(last.date));
    var well = el("div", "lwell");
    well.appendChild(s);
    return well;
  }
  var NO_TRENDS = "Δεν υπάρχουν ακόμα τάσεις";
  function fillTrends(card, data, withChart, ref) {
    var ch = data && data.changes || [];
    var days = data && data.days || 30;
    if (!data) { empty(card, NO_TRENDS); return ""; }
    if (!ch.length) {
      empty(card, "Καμία αλλαγή τιμής τις τελευταίες " + days + " μέρες");
      var top = (data.prices || []).slice().sort(function (a, b) { return (b.qty_in_window || 0) - (a.qty_in_window || 0); })[0];
      if (top && top.qty_in_window) {
        var tr = el("div", "tb-tr");
        tr.appendChild(el("b", null, top.name));
        tr.appendChild(el("p", null, "Τελευταία τιμή " + eur(top.last_cents) + ", αγοράστηκαν " + num(top.qty_in_window) + " τον τελευταίο μήνα"));
        card.appendChild(tr);
      }
      return "";
    }
    var inv = withChart && ref && ref.invoice_id && ref.label ? ref : null;
    card.appendChild(changeNodes(ch[0], inv));
    var cheapSentence = null;
    if (withChart) {
      var w = chart(data, ch[0]);
      if (w) card.appendChild(w);
    }
    var row = (data.prices || []).filter(function (p) { return p.product_id === ch[0].product_id; })[0];
    if (row && row.cheapest && row.cheapest.supplier_id !== row.last_supplier_id) {
      cheapSentence = "Η " + row.cheapest.name + " την έχει " + eur(row.cheapest.unit_cents);
    }
    if (cheapSentence || inv) {
      var cf = el("div", "cf");
      cf.appendChild(el("span", "m", cheapSentence || ""));
      if (inv) {
        var go = el("a", "lk", inv.label + " ");
        go.href = href(inv.invoice_id);
        go.appendChild(icon("ph", "arrow-right"));
        cf.appendChild(go);
      }
      card.appendChild(cf);
    }
    return plural(ch.length, "αλλαγή", "αλλαγές") + (withChart ? " τον μήνα" : "");
  }
  function renderTrends(data, ref) {
    var card = $("l-tr");
    clearCard(card);
    $("l-tr-n").textContent = fillTrends(card, data, true, ref);
    var m = $("m-tr");
    m.replaceChildren();
    var mc = el("article", "card");
    var hdr = hd("chart-line-up", "Τάσεις τιμών", "");
    mc.appendChild(hdr);
    hdr.querySelector(".r").textContent = fillTrends(mc, data, false);
    m.appendChild(mc);
  }
  function renderSuppliers(data) {
    var card = $("l-sp");
    clearCard(card);
    var list = data && data.purchases && data.purchases.suppliers || [];
    $("l-sp-n").textContent = data ? data.days + " μέρες" : "";
    if (!data) { empty(card, NO_TRENDS); return; }
    if (!list.length) { empty(card, "Καμία αγορά τις τελευταίες " + data.days + " μέρες"); return; }
    var max = Math.max.apply(null, list.map(function (s) { return s.total_cents; })) || 1;
    list.slice().sort(function (a, b) { return b.total_cents - a.total_cents; }).forEach(function (s) {
      var row = el("div", "tb-sp");
      var name = el("span", null, s.name + " ");
      name.appendChild(el("em", null, "· " + s.invoices));
      var bar = el("span", "bar"), fill = el("i");
      fill.style.width = Math.max(2, Math.round(s.total_cents / max * 100)) + "%";
      bar.appendChild(fill);
      row.append(name, el("b", null, eur(s.total_cents)), bar);
      card.appendChild(row);
    });
  }
  function renderFolder(page) {
    var box = $("l-fold-b");
    box.replaceChildren();
    var f = page.month_folder;
    if (!f) { box.appendChild(el("p", "empty", "Δεν υπάρχουν ακόμα τιμολόγια αυτόν τον μήνα")); return; }
    var v = el("p", "v", plural(f.count, "τιμολόγιο", "τιμολόγια") + " · " + eur(f.total_cents));
    v.appendChild(el("span", null, f.name + " ως τώρα, με πληρωμές και πιστωτικά"));
    var a = el("a", "btn q");
    a.href = "/accountant/month";
    a.style.alignSelf = "flex-start";
    a.append(icon("ph", "download-simple"), txt("Κατέβασε το ZIP"));
    box.append(v, a);
  }

  /* ---------- λίστα: σύνολο ---------- */
  function bindTabs() {
    if (tabsBound) return;
    tabsBound = true;
    var tabs = document.querySelectorAll(".seg2 button");
    tabs.forEach(function (b) {
      b.addEventListener("click", function () {
        tabs.forEach(function (x) {
          x.setAttribute("aria-selected", String(x === b));
          $(x.dataset.p).hidden = x !== b;
        });
      });
    });
  }
  function renderList(page, trends) {
    lastPage = page;
    views("v-lista");
    document.title = "Τιμολόγια · Xangxothaika";
    var kick = "Χαρτιά · " + dayName(page.today) + " " + dmy(page.today);
    $("l-kick").textContent = kick;
    $("m-kick").textContent = "Χαρτιά · " + dmy(page.today);
    ["l-lead", "m-lead"].forEach(function (id) { $(id).replaceChildren.apply($(id), leadNodes(page.counts)); });
    $("l-all-n").textContent = String(page.counts.all);
    renderStage(page);
    renderPayments(page);
    renderTrends(trends, page.trend_ref);
    renderSuppliers(trends);
    renderFolder(page);
    var need = $("m-need");
    need.replaceChildren();
    if (page.need.length) {
      var c = el("article", "card");
      page.need.forEach(function (n) { c.appendChild(mobNeedRow(n)); });
      need.appendChild(c);
    } else {
      var idle = el("article", "stage mc"), mcb = el("div", "mcb");
      mcb.appendChild(el("h2", null, "Καμία απόφαση δεν περιμένει"));
      var ok = (page.ok_today || []);
      if (ok.length) mcb.appendChild(el("p", "why", ok.length === 1
        ? ok[0].label + " · " + ok[0].supplier + ": εντάξει, χωρίς ερώτηση."
        : ok.length + " ακόμα εντάξει, χωρίς ερώτηση"));
      idle.appendChild(mcb);
      need.appendChild(idle);
    }
    need.appendChild(renderMobPayments(page));
    var cn = $("m-c-need");
    cn.textContent = String(page.need.length);
    cn.style.display = page.need.length ? "" : "none";
    $("m-c-all").textContent = String(page.counts.all);
    drawTables();
    bindTabs();
  }

  /* ---------- ένα τιμολόγιο ---------- */
  function split(text) {
    var i = String(text || "").indexOf(": ");
    return i > 0 ? [text.slice(0, i), text.slice(i + 2)] : [text || "", ""];
  }
  var TEAM = {
    invoices: ["receipt", "m-tim"], stock: ["package", "m-apo"], orders: ["truck", "m-par"],
    accountant: ["calculator", "m-log"], master: ["handshake", "m-syn"], agent: ["handshake", "m-syn"],
    owner: ["hand-palm", "you"], you: ["hand-palm", "you"]
  };
  var KIND_ICON = {
    "invoice.received": "envelope-simple", "invoice.read": "scan", "invoice.matched": "barcode",
    "price.changed": "trend-up", "payment.reminder_set": "calendar-blank", you: "hand-palm"
  };
  function teamBadge(team, kind, via) {
    var t = TEAM[team] || TEAM.master;
    var c = el("span", "c " + t[1]);
    var name = KIND_ICON[kind] || t[0];
    if (kind === "invoice.received" && via === "photo") name = "camera";
    c.appendChild(icon(t[1] === "you" ? "ph-fill" : "ph", name));
    return c;
  }
  var METHOD = {cash: "μετρητά", card: "κάρτα", bank: "τράπεζα"};

  function creditNumbers(box) {
    return {charged: num(box.expected), came: num(box.actual), missing: num(box.missing)};
  }
  function creditBox(box) {
    var n = creditNumbers(box);
    var b = el("div", "hb-qb");
    b.setAttribute("aria-label", box.product + ": χρεώθηκαν " + n.charged + ", ήρθαν " + n.came + ", λείπει " + n.missing);
    var hdr = el("p", "hb-qhd");
    hdr.append(icon("ph", "package"), txt(box.product + (box.more > 0 ? " και άλλα " + box.more : "") +
      (box.order_id ? " · παραλαβή " + box.order_id : "")));
    var tiles = el("div", "hb-qtiles");
    [["tim", "receipt", n.charged, "τιμολόγιο"], ["apo", "package", n.came, "στο ράφι"],
      ["red", "warning-circle", n.missing, "λείπει"]].forEach(function (t) {
      var d = el("div", "hb-qt " + t[0]);
      d.append(icon("ph-fill", t[1]), el("b", null, t[2]), el("span", null, t[3]));
      tiles.appendChild(d);
    });
    var cr = el("div", "hb-qcr");
    var lab = el("span", null, "Πιστωτικό");
    lab.appendChild(el("small", null, box.more === 0
      ? n.missing + " × " + eur(box.unit_cents) + " + ΦΠΑ " + box.vat_rate + "%"
      : (box.more + 1) + " είδη + ΦΠΑ"));
    cr.append(lab, el("b", null, eur(box.amount_cents)));
    b.append(hdr, tiles, cr);
    return b;
  }
  function actions(approvalId, mobile) {
    var acts = el("div", mobile ? "acts hb-cra" : "acts");
    var msg = el("span", "msg");
    msg.hidden = true; // άδειο, να μη σπάει γραμμή και να μη μεγαλώνει η σκηνή
    var yes = el("button", "btn yes", "Στείλε το αίτημα");
    yes.prepend(icon("ph", "paper-plane-tilt"));
    var no = el("button", "btn no", "Όχι");
    yes.type = no.type = "button";
    yes.addEventListener("click", function () { decide("yes", approvalId); });
    no.addEventListener("click", function () { decide("no", approvalId); });
    acts.append(yes, no, msg);
    btns.push({yes: yes, no: no, msg: msg});
    return acts;
  }
  function allMsg(text) { btns.forEach(function (b) { b.msg.textContent = text; b.msg.hidden = !text; }); }
  function allBusy(on) { btns.forEach(function (b) { b.yes.disabled = b.no.disabled = on; }); }

  function stageDesk(d) {
    var st = $("d-stage");
    st.replaceChildren();
    st.className = "stage";
    var shd = el("div", "shd"), l = el("span");
    l.append(icon("ph", "calculator"), txt("Ο Λογιστής σε ρωτά"));
    shd.append(l, el("span", null, "1 από 1"));
    var box = d.credit_box, sc = d.credit || {};
    var amount = box ? box.amount_cents : sc.amount_cents;
    var supplier = box ? box.supplier : sc.supplier;
    var title = el("h2", null, sent ? "Ζητήθηκε πιστωτικό " + eur(sent.amount)
      : amount != null ? "Ζητάω πιστωτικό " + eur(amount) + ";" : sc.summary || "Να ζητήσω πιστωτικό;");
    title.id = "cr-t";
    st.setAttribute("aria-labelledby", "cr-t");
    var body, lc;
    if (sent) {
      body = el("div", "body one");
      lc = el("div");
      var big = el("div", "hb-big");
      big.append(icon("ph-fill", "check-circle"), title);
      lc.append(big, el("p", "why", sent.message || "Το αίτημα καταγράφηκε."));
      body.appendChild(lc);
      st.append(shd, body);
      return;
    }
    body = el("div", box ? "body hb-cr" : "body one");
    lc = el("div", box ? "lc" : null);
    lc.appendChild(title);
    var approvalId = (box && box.approval_id) || sc.approval_id;
    if (box) {
      var why = el("p", "why");
      why.append(txt("Χρεώθηκαν "), el("b", null, num(box.expected)), txt(", ήρθαν "), el("b", null, num(box.actual)), txt("."));
      lc.appendChild(why);
    } else if (sc.why) lc.appendChild(el("p", "why", sc.why));
    if (supplier) {
      var exact = el("p", "exact hb-lt");
      exact.append(icon("ph", "envelope-simple"), el("span", null, amount != null
        ? "Τι ακριβώς: email προς " + supplier + " με αίτημα πιστωτικού " + eur(amount) + " για το " + d.label + "."
        : "Τι ακριβώς: email προς " + supplier + " με το αίτημα."));
      lc.appendChild(exact);
    }
    lc.appendChild(actions(approvalId, false));
    body.appendChild(lc);
    if (box) body.appendChild(creditBox(box));
    st.append(shd, body);
    if (supplier) {
      var sft = el("div", "sft");
      sft.appendChild(icon("ph", "envelope-simple"));
      sft.appendChild(el("span", "tx", "Με το «Ναι» στέλνω email προς " + supplier + ". Η ίδια ερώτηση είναι και στην αρχική."));
      st.appendChild(sft);
    }
  }
  function stageMob(d) {
    var box = d.credit_box, sc = d.credit || {};
    var amount = box ? box.amount_cents : sc.amount_cents;
    var card = el("article", "stage mc");
    var mcb = el("div", "mcb");
    var h2 = el("h2", null, amount != null ? "Ζητάω πιστωτικό " + eur(amount) + ";" : sc.summary || "Να ζητήσω πιστωτικό;");
    h2.id = "cr-tm";
    card.setAttribute("aria-labelledby", "cr-tm");
    if (sent) {
      h2.textContent = "Ζητήθηκε πιστωτικό " + eur(sent.amount);
      var big = el("div", "hb-big");
      big.append(icon("ph-fill", "check-circle"), h2);
      mcb.append(big, el("p", "why", sent.message || "Το αίτημα καταγράφηκε."));
    } else {
      mcb.appendChild(h2);
      if (box) mcb.appendChild(creditBox(box));
      else if (sc.why) mcb.appendChild(el("p", "why", sc.why));
      mcb.appendChild(actions((box && box.approval_id) || sc.approval_id, true));
    }
    card.appendChild(mcb);
    return card;
  }

  function lineCols(withOrder) {
    return withOrder ? null : "minmax(0,2fr) 88px 72px 88px";
  }
  function lineValue(l) {
    return l.unit_cents != null && l.charged != null ? eur(l.charged * l.unit_cents) : "";
  }
  function chgLine(l, withLink) {
    var down = l.price_pct < 0;
    var c = el("span", "chg");
    var b = el("b");
    b.append(icon("ph", down ? "arrow-down" : "arrow-up"), txt(String(l.price_pct_text || "").replace(/^[+−-]/, "")));
    if (down) { b.style.color = "var(--ok)"; }
    var cheap = withLink && l.cheaper && l.cheaper.supplier && l.cheaper.cents != null ? l.cheaper : null;
    c.append(b, txt("ήταν " + l.old_unit_text + (withLink ? ". " : "") +
      (cheap ? "Η " + cheap.supplier + " την έχει " + eur(cheap.cents) + ". " : "")));
    if (withLink) {
      var a = el("a", "lk u", "Τάσεις τιμών");
      a.href = PAGE;
      c.appendChild(a);
    }
    return c;
  }
  function linesDesk(d, withOrder) {
    var card = $("d-lines");
    clearCard(card);
    $("d-lines-t").textContent = withOrder ? "Παραγγείλαμε, ήρθαν, χρεώθηκαν" : "Γραμμές του τιμολογίου";
    $("d-lines-n").textContent = plural(d.lines.length, "γραμμή", "γραμμές");
    var cols = lineCols(withOrder);
    var th = el("div", "tr2 th");
    (withOrder ? [["Είδος", ""], ["Παραγγείλαμε", "n"], ["Ήρθαν", "n"], ["Χρεώθηκαν", "n"], ["Τιμή", "n"], ["Αξία", "n"]]
      : [["Είδος", ""], ["Χρεώθηκαν", "n"], ["Τιμή", "n"], ["Αξία", "n"]]).forEach(function (c) { th.appendChild(el("span", c[1], c[0])); });
    if (cols) th.style.gridTemplateColumns = cols;
    card.appendChild(th);
    d.lines.forEach(function (l) {
      var cls = l.diff ? " bad hot" : l.price_pct_text ? " tb-chg" : "";
      var tr = el("div", "tr2" + cls);
      if (cols) tr.style.gridTemplateColumns = cols;
      tr.appendChild(el("b", null, l.name));
      if (withOrder) {
        tr.appendChild(el("span", "n", String(l.ordered)));
        tr.appendChild(el("span", "n" + (l.delivered < l.charged ? " x" : ""), String(l.delivered)));
      }
      tr.appendChild(el("span", "n", String(l.charged)));
      tr.appendChild(el("span", "n", l.unit_text || ""));
      tr.appendChild(el("b", "n", lineValue(l)));
      if (l.price_pct_text) tr.appendChild(chgLine(l, true));
      card.appendChild(tr);
    });
    var tot = el("div", "tot");
    var vat = d.vat || [];
    tot.append(el("span", null, "Καθαρή αξία " + d.net_text + (vat.length
      ? vat.map(function (v) { return " + ΦΠΑ " + v.rate + "% " + eur(v.cents); }).join("")
      : " + ΦΠΑ " + d.vat_text)), el("b", null, "= " + d.total_text));
    card.appendChild(tot);
  }
  function linesMob(d, withOrder) {
    var card = el("section", "card mt16");
    card.setAttribute("aria-label", "Γραμμές");
    card.appendChild(hd("list-dashes", "Γραμμές", String(d.lines.length)));
    d.lines.forEach(function (l) {
      var r = el("div", "tb-lr" + (l.diff ? " bad" : l.price_pct_text ? " chg" : ""));
      var a = el("div", "a");
      a.append(el("b", null, l.name), el("b", "v", lineValue(l)));
      r.appendChild(a);
      if (l.diff && withOrder) {
        var q = el("div", "q");
        [[l.ordered, "παραγγείλαμε", ""], [l.delivered, "ήρθαν", l.delivered < l.charged ? "x" : ""], [l.charged, "χρεώθηκαν", ""]].forEach(function (c) {
          var s = el("span", c[2]);
          s.append(el("b", null, String(c[0])), txt(c[1]));
          q.appendChild(s);
        });
        r.appendChild(q);
      } else {
        var nt = el("p", "nt");
        if (l.price_pct_text) {
          var down = l.price_pct < 0, b = el("b");
          b.append(icon("ph", down ? "arrow-down" : "arrow-up"), txt(String(l.price_pct_text).replace(/^[+−-]/, "")));
          if (down) b.style.color = "var(--ok)";
          nt.append(b, txt(l.charged + " × " + (l.unit_text || "") + ", ήταν " + l.old_unit_text));
        } else {
          nt.textContent = l.charged + " × " + (l.unit_text || "") + (withOrder && l.delivered === l.charged ? ", όλα ήρθαν" : "");
        }
        r.appendChild(nt);
      }
      card.appendChild(r);
    });
    return card;
  }

  function historyItems(d) {
    var h = d.history || [];
    return h.length > 6 ? h.slice(h.length - 6) : h;
  }
  function historyDesk(d) {
    var card = $("d-hist"), items = historyItems(d);
    card.hidden = !items.length;
    if (!items.length) return;
    clearCard(card);
    $("d-hist-n").textContent = items.some(function (e) { return /\d\/\d/.test(e.at); }) ? "" : "σήμερα";
    var grid = el("div", "tb-st");
    if (items.length <= 3) grid.style.gridTemplateColumns = "repeat(" + items.length + ",minmax(0,1fr))";
    items.forEach(function (e) {
      var p = split(e.text);
      var cell = el("div", e.team === "you" ? "you" : "");
      cell.append(teamBadge(e.team, e.kind, d.via), el("time", null, e.at), el("b", null, p[0]));
      if (p[1]) cell.appendChild(el("span", null, p[1]));
      grid.appendChild(cell);
    });
    card.appendChild(grid);
  }
  function historyMob(d) {
    var items = historyItems(d);
    if (!items.length) return null;
    var card = el("section", "card mt16");
    card.setAttribute("aria-label", "Τι έγινε με αυτό το τιμολόγιο");
    card.appendChild(hd("clock-counter-clockwise", "Τι έγινε", ""));
    items.forEach(function (e) {
      var p = split(e.text);
      var row = el("div", "tb-mst" + (e.team === "you" ? " you" : ""));
      var t = el("span");
      t.appendChild(el("b", null, p[0]));
      if (p[1]) t.appendChild(txt(", " + p[1]));
      row.append(teamBadge(e.team, e.kind, d.via), el("time", null, e.at), t);
      card.appendChild(row);
    });
    return card;
  }

  function checkRow(c, withValue) {
    var row = el("div", "tb-ck" + (c.ok ? "" : " bad"));
    row.appendChild(icon("ph-bold", c.ok ? "check" : "x"));
    var s = el("span");
    if (c.lead) { s.appendChild(el("b", null, c.lead)); s.appendChild(txt(" ")); }
    s.appendChild(txt(c.text));
    row.append(s, el("span", null, withValue && c.value ? String(c.value) : ""));
    return row;
  }
  var NOT_A_CHECK = { price: 1, reminder: 1, credit: 1 };
  function checkList(d) {
    return (d.checks || []).filter(function (c) { return !NOT_A_CHECK[c.kind]; });
  }
  function checksDesk(d) {
    var card = $("d-checks"), list = checkList(d);
    card.hidden = !list.length;
    clearCard(card);
    list.forEach(function (c) { card.appendChild(checkRow(c, true)); });
  }
  function checksMob(d) {
    var list = checkList(d);
    if (!list.length) return null;
    var bad = list.filter(function (c) { return !c.ok; }), ok = list.filter(function (c) { return c.ok; });
    var card = el("section", "card mt16");
    card.setAttribute("aria-label", "Έλεγχοι");
    card.appendChild(hd("seal-check", "Έλεγχοι", bad.length ? ok.length + " σωστοί, " + bad.length + " όχι" : "όλοι σωστοί"));
    bad.concat(ok).forEach(function (c) { card.appendChild(checkRow(c, !c.ok)); });
    return card;
  }

  function payBody(d, today, mobile) {
    var p = d.payment, out = [];
    var row = el("div", "tb-pay");
    var txtBox = el("div");
    if (p.paid) {
      var tick = el("span", "ring");
      tick.style.width = tick.style.height = "72px";
      tick.appendChild(icon("ph-fill", "check-circle"));
      tick.firstChild.style.cssText = "font-size:56px;color:var(--ok)";
      txtBox.append(el("b", null, "Πληρώθηκε " + dayWord(p.paid.date, today)),
        el("span", null, "Με " + (METHOD[p.paid.method] || "άλλον τρόπο") + (p.due_on ? ". Ήταν να λήξει " + dmy(p.due_on) + "." : ".")));
      row.append(tick, txtBox);
    } else {
      var days = p.days == null ? 0 : p.days;
      txtBox.append(el("b", null, "Λήγει " + dmy(p.due_on)),
        el("span", null, (days === 0 ? "σήμερα" : days < 0 ? "πριν " + plural(-days, "μέρα", "μέρες") : "σε " + plural(days, "μέρα", "μέρες")) +
          (mobile ? ", θα σε ρωτήσω «Πλήρωσες;»" : ". Εκείνη τη μέρα σε ρωτώ στην αρχική «Πλήρωσες;».")));
      row.append(ring(72, days, days <= 0 ? "σήμερα" : "σε " + plural(days, "μέρα", "μέρες")), txtBox);
    }
    out.push(row);
    if (p.event_id != null && !p.paid) {
      var cf = el("div", "cf");
      cf.appendChild(el("span", "m", (mobile ? "Υπενθύμιση" : "Υπενθύμιση από τον Λογιστή") +
        (p.reminder_at ? ", " + p.reminder_at : "")));
      var u = window.xangxUndoNode ? window.xangxUndoNode(p.undo, p.event_id, "υπενθύμιση πληρωμής", soon) : null;
      if (u) cf.appendChild(u);
      out.push(cf);
    }
    return out;
  }
  function payDesk(d, today) {
    var card = $("d-pay");
    card.hidden = !d.payment || (d.payment.due_on == null && !d.payment.paid);
    if (card.hidden) return;
    clearCard(card);
    var right = card.querySelector(".hd .r");
    if (!right) { right = el("span", "r"); card.querySelector(".hd").appendChild(right); }
    right.textContent = d.payment.terms_days ? "πίστωση " + d.payment.terms_days + (d.payment.terms_days === 1 ? " ημέρας" : " ημερών") : "";
    payBody(d, today, false).forEach(function (n) { card.appendChild(n); });
  }
  function payMob(d, today) {
    if (!d.payment || (d.payment.due_on == null && !d.payment.paid)) return null;
    var card = el("section", "card mt16");
    card.setAttribute("aria-label", "Πληρωμή");
    payBody(d, today, true).forEach(function (n) { card.appendChild(n); });
    return card;
  }

  function renderDetail(d, page) {
    btns = [];
    views("v-tim");
    var today = page && page.today || localToday();
    var arrived = null;
    if (page) page.months.forEach(function (g) {
      g.rows.forEach(function (r) { if (r.invoice_id === d.invoice_id) arrived = r.arrived; });
    });
    var title = d.label + (d.supplier ? " · " + d.supplier : "");
    document.title = d.label + " · Τιμολόγια · Xangxothaika";
    $("h-tim2").textContent = title;
    $("m2-title").textContent = d.label;
    var viaTxt = d.via === "email" ? " με email" : d.via === "photo" ? " με φωτογραφία" : "";
    var when = arrived ? dayWord(arrived.date, today) : "";
    var kick = "Ήρθε" + viaTxt + (when ? " " + when : "") + (d.received_at ? (when ? ", " : " ") + d.received_at : "") +
      (d.order_id ? " · παραγγελία " + d.order_id : "");
    var kickIcon = d.via === "photo" ? "camera" : d.via === "email" ? "envelope-simple" : "receipt";
    $("d-kick").textContent = kick;
    $("d-kick-i").className = "ph ph-" + kickIcon;
    $("m2-kick").textContent = (d.via === "email" ? "email" : d.via === "photo" ? "φωτογραφία" : "ήρθε") +
      (when ? " · " + when : "") + (d.received_at ? " " + d.received_at : "");
    $("m2-kick-i").className = "ph ph-" + kickIcon;
    $("m2-lead").textContent = (d.supplier || "") + (d.supplier && d.order_id ? " · " : "") + (d.order_id || "");
    var m = $("m2-body");
    m.replaceChildren();

    if (!d.read) {
      var text = d.duplicate_of ? "Μοιάζει διπλό με το " + d.duplicate_of + "." : "Δεν έχει διαβαστεί ακόμα. Θα εμφανιστεί μόλις διαβαστεί.";
      var lead = $("d-lead");
      lead.textContent = text;
      lead.hidden = false;
      $("m2-lead").textContent = text;
      ["d-sum", "d-stage", "d-g"].forEach(function (id) { show(id, false); });
      show("m2-sum", false);
      return;
    }
    var withOrder = d.has_order && d.has_delivery;
    var msg = !sent && done ? done : "";
    $("d-lead").textContent = msg;
    $("d-lead").hidden = !msg;
    show("d-sum", true);
    $("d-total").textContent = d.total_text;
    show("m2-sum", true);
    $("m2-total").textContent = d.total_text;
    show("d-g", true);

    var pending = (d.credit_box && d.credit_box.approval_id) || (d.credit && d.credit.approval_id) || null;
    if (sent && pending && pending !== sent.approval_id) sent = null; // άλλο, νέο αίτημα
    var showStage = !!pending || !!sent;
    show("d-stage", showStage);
    $("d-g").classList.toggle("mt", showStage);
    if (showStage) {
      stageDesk(d);
      m.appendChild(stageMob(d));
    }
    linesDesk(d, withOrder);
    show("d-lines", true);
    historyDesk(d);
    checksDesk(d);
    payDesk(d, today);

    m.appendChild(linesMob(d, withOrder));
    [payMob(d, today), historyMob(d), checksMob(d)].forEach(function (n) { if (n) m.appendChild(n); });
  }

  function decide(decision, approvalId) {
    if (sending || !approvalId) return;
    sending = true;
    allBusy(true);
    allMsg(decision === "yes" ? "Στέλνω το αίτημα…" : "");
    var amount = null, supplier = null;
    if (lastDetail) {
      var b = lastDetail.credit_box, c = lastDetail.credit;
      amount = b ? b.amount_cents : c ? c.amount_cents : null;
      supplier = b ? b.supplier : c ? c.supplier : null;
    }
    fetch("/api/approvals/" + encodeURIComponent(approvalId), {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({decision: decision})
    }).then(function (r) {
      return r.json().then(function (j) { return {ok: r.ok, body: j}; });
    }).then(function (res) {
      var msg = res.body && (res.body.message || res.body.detail);
      if (res.ok && res.body.ok) {
        if (decision === "yes" && amount != null) sent = {approval_id: approvalId, amount: amount, supplier: supplier, message: msg || ""};
        else done = msg || "";
        load();
      } else {
        allMsg(msg || "Δεν έγινε. Δοκίμασε ξανά.");
        allBusy(false);
      }
    }).catch(function () {
      allMsg("Δεν έγινε. Έλεγξε τη σύνδεση και δοκίμασε ξανά.");
      allBusy(false);
    }).then(function () { sending = false; });
  }

  /* ---------- φόρτωση ---------- */
  var lastDetail = null;
  function failed() {
    setLive(false, "Χωρίς σύνδεση");
    problem("Δεν μπόρεσα να φορτώσω τα τιμολόγια. Δοκίμασε ξανά σε λίγο.");
  }
  function getJson(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) { var e = new Error(String(r.status)); e.status = r.status; throw e; }
      return r.json();
    });
  }
  function load() {
    if (loading) { again = true; return; }
    loading = true;
    var jobs = ID
      ? [getJson("/api/screen/invoices/" + encodeURIComponent(ID)), getJson("/api/screen/invoices-page").catch(function () { return null; })]
      : [getJson("/api/screen/invoices-page"), getJson("/accountant/trends/purchases?days=30").catch(function () { return null; })];
    Promise.all(jobs).then(function (res) {
      var data = res[0];
      setPaused(data.paused);
      clock = data.now;
      setLive(true, "Ζωντανά");
      if (ID) { lastDetail = data; renderDetail(data, res[1]); } else renderList(data, res[1]);
    }).catch(function (e) {
      if (ID && e && e.status === 404) problem("Δεν βρήκα αυτό το τιμολόγιο.");
      else failed();
    }).then(function () {
      loading = false;
      if (again) { again = false; load(); }
    });
  }

  function soon() {
    clearTimeout(timer);
    timer = setTimeout(load, 400);
  }

  function listen() {
    if (!window.EventSource) return;
    var es = new EventSource("/api/events/stream");
    es.onopen = function () { setLive(true, "Ζωντανά"); };
    es.onmessage = function (m) {
      try {
        var ev = JSON.parse(m.data);
        if (ev.type === "system.paused") setPaused(true);
        if (ev.type === "system.resumed") setPaused(false);
      } catch (e) { /* ένα κακό μήνυμα δεν σταματά τη σελίδα */ }
      soon();
    };
    es.onerror = function () { setLive(false, "Σύνδεση…"); };
  }

  $("stop-btn").addEventListener("click", togglePause);
  $("stop-ibtn").addEventListener("click", togglePause);
  if (ID === "") problem("Δεν βρήκα αυτό το τιμολόγιο."); else load();
  listen();
})();
