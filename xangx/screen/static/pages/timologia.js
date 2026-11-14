/* Σελίδα Τιμολόγια. Χωρίς ?id: η λίστα της μέρας. Με ?id: ένα τιμολόγιο, και η ερώτηση για πιστωτικό.
   Δεδομένα από /api/screen/invoices, ζωντανά από /api/events/stream. Κάθε κείμενο μπαίνει με textContent. */
(function () {
  "use strict";
  var PAGE = "/static/pages/timologia.html";
  var params = new URLSearchParams(location.search);
  var ID = params.get("id");
  if (ID !== null && !/^[A-Za-z0-9-]{1,40}$/.test(ID)) ID = "";
  var paused = false, timer = null, loading = false, again = false, sending = false;
  var done = ""; // το μήνυμα μετά από «ναι» ή «όχι», να μη χαθεί με την ανανέωση

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function icon(kind, name) { return el("i", kind + " ph-" + name); }
  function show(id, on) { $(id).hidden = !on; }

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

  /* ---------- κοινά ---------- */
  function problem(text) {
    show("list", false); show("detail", false); show("problem", true);
    $("problem-text").textContent = text;
    $("title").textContent = "Τιμολόγια";
    show("sum-top", false);
    $("wrap").setAttribute("aria-busy", "false");
  }
  function lead(text) {
    $("lead").textContent = text || "";
    show("lead", !!text);
  }

  /* ---------- λίστα ---------- */
  function listRow(inv) {
    var a = el("a", "tr2");
    a.href = PAGE + "?id=" + encodeURIComponent(inv.invoice_id);
    var who = el("span", null, inv.supplier || "Δεν διαβάστηκε ακόμα");
    who.appendChild(el("span", "sub m", inv.label));
    a.appendChild(who);
    a.appendChild(el("span", "c", inv.label));
    a.appendChild(el("span", "n", inv.total_text || ""));
    a.appendChild(el("span", "st" + (inv.level === "warn" ? " warn" : " m"), inv.status));
    return a;
  }
  function renderList(data) {
    var n = data.invoices.length;
    $("kick").textContent = "Τιμολόγια · σήμερα";
    $("title").textContent = n ? (n === 1 ? "1 τιμολόγιο σήμερα" : n + " τιμολόγια σήμερα") : "Κανένα τιμολόγιο σήμερα";
    lead(n ? "" : "Όταν έρθει τιμολόγιο στο email ή ανεβεί φωτογραφία, θα εμφανιστεί εδώ.");
    show("sum-top", false); show("detail", false); show("problem", false);
    show("list", n > 0);
    var body = $("list-body");
    body.replaceChildren();
    data.invoices.forEach(function (inv) { body.appendChild(listRow(inv)); });
  }

  /* ---------- ένα τιμολόγιο ---------- */
  function td(cls, text, tag) { return el(tag || "span", cls, text); }

  function lineRow(l, withOrder) {
    var tr = el("div", "tr2" + (l.diff ? " diff" : ""));
    var name = l.diff ? el("b", null, l.name) : document.createTextNode(l.name);
    var cell = el("span");
    cell.appendChild(name);
    var sub = withOrder
      ? "παραγγείλαμε " + l.ordered + ", ήρθαν " + l.delivered + ", χρεώθηκαν " + l.charged
      : "χρεώθηκαν " + l.charged;
    cell.appendChild(el("span", "sub " + (l.diff ? "warn" : "m"), sub));
    tr.appendChild(cell);
    if (withOrder) {
      tr.appendChild(td("n c", String(l.ordered)));
      tr.appendChild(td("n c" + (l.delivered < l.charged ? " warn" : ""), String(l.delivered)));
    }
    tr.appendChild(td("n c", String(l.charged)));
    var price = el("span", "n");
    if (l.unit_text) {
      price.appendChild(l.price_pct_text || l.diff ? el("b", null, l.unit_text) : document.createTextNode(l.unit_text));
    }
    if (l.price_pct_text) {
      var down = l.price_pct < 0;
      var up = el("span", "up" + (down ? " down" : ""));
      up.appendChild(icon("ph-bold", down ? "arrow-down" : "arrow-up"));
      up.appendChild(document.createTextNode(l.price_pct_text.replace(/^[+−]/, "")));
      price.appendChild(document.createTextNode(" "));
      price.appendChild(up);
      price.appendChild(el("span", "m was", "ήταν " + l.old_unit_text));
    }
    tr.appendChild(price);
    return tr;
  }

  function headRow(cols) {
    var h = $("lines-th");
    h.replaceChildren();
    cols.forEach(function (c) { h.appendChild(el("span", c[1], c[0])); });
  }

  function checkRow(c) {
    var d = el("div", "ck" + (c.ok ? "" : " x"));
    d.appendChild(icon("ph-fill", c.ok ? "check-circle" : "warning-circle"));
    var s = el("span");
    if (c.lead) { s.appendChild(el("b", null, c.lead)); s.appendChild(document.createTextNode(" ")); }
    s.appendChild(document.createTextNode(c.text));
    d.appendChild(s);
    return d;
  }

  function renderDetail(d) {
    var title = d.label + (d.supplier ? " · " + d.supplier : "");
    $("title").textContent = title;
    document.title = d.label + " · Τιμολόγια · Xangxothaika";
    var k = $("kick");
    k.replaceChildren();
    var back = el("a", null, "Τιμολόγια");
    back.href = PAGE;
    k.appendChild(back);
    var rest = [];
    if (d.date) rest.push(d.date);
    if (d.order_id) rest.push("παραγγελία " + d.order_id);
    if (d.received_at) rest.push("ήρθε " + d.received_at);
    if (rest.length) k.appendChild(document.createTextNode(" · " + rest.join(" · ")));
    show("list", false); show("problem", false); show("detail", true);

    if (!d.read) {
      show("sum-top", false);
      lead(d.duplicate_of ? "Μοιάζει διπλό με το " + d.duplicate_of + "." : "Δεν έχει διαβαστεί ακόμα. Θα εμφανιστεί μόλις διαβαστεί.");
      show("lines-card", false); show("checks-card", false);
      $("detail").classList.add("solo");
      show("credit-col", false);
      return;
    }
    lead(done);
    show("lines-card", true); show("checks-card", true);
    $("sum-big").textContent = d.total_text;
    show("sum-top", true);

    var withOrder = d.has_order && d.has_delivery;
    var card = $("lines-card");
    card.classList.toggle("noorder", !withOrder);
    $("lines-title").textContent = withOrder ? "Παραγγείλαμε, ήρθαν, χρεώθηκαν" : "Γραμμές του τιμολογίου";
    $("lines-count").textContent = d.lines.length + (d.lines.length === 1 ? " γραμμή" : " γραμμές");
    headRow(withOrder
      ? [["Προϊόν", ""], ["Παραγγείλαμε", "n"], ["Ήρθαν", "n"], ["Χρεώθηκαν", "n"], ["Τιμή", "n"]]
      : [["Προϊόν", ""], ["Χρεώθηκαν", "n"], ["Τιμή", "n"]]);
    var body = $("lines-body");
    body.replaceChildren();
    d.lines.forEach(function (l) { body.appendChild(lineRow(l, withOrder)); });
    var sum = el("span", "n sum");
    sum.appendChild(el("b", null, "= " + d.total_text));
    $("lines-total").replaceChildren(el("span", null, "Καθαρό " + d.net_text + " + ΦΠΑ " + d.vat_text), sum);

    var checks = $("checks-body");
    checks.replaceChildren();
    d.checks.forEach(function (c) { checks.appendChild(checkRow(c)); });
    show("checks-card", d.checks.length > 0);

    var credit = d.credit;
    show("credit-col", !!credit);
    $("detail").classList.toggle("solo", !credit);
    if (credit) {
      $("credit-at").textContent = credit.at;
      $("credit-q").textContent = credit.amount_text ? "Ζητάω πιστωτικό " + credit.amount_text + ";" : (credit.summary || "Να ζητήσω πιστωτικό;");
      $("credit-why").textContent = credit.why;
      var will = $("credit-will");
      will.replaceChildren();
      if (credit.supplier) {
        var w = el("div");
        w.appendChild(icon("ph", "envelope-simple"));
        w.appendChild(el("span", null, "Στέλνω email στον " + credit.supplier + " με το αίτημα"));
        will.appendChild(w);
      }
      will.hidden = !credit.supplier;
      $("credit-yes").dataset.approval = credit.approval_id;
      $("credit-no").dataset.approval = credit.approval_id;
    }
  }

  function decide(decision) {
    if (sending) return;
    var id = $("credit-yes").dataset.approval;
    if (!id) return;
    sending = true;
    $("credit-yes").disabled = $("credit-no").disabled = true;
    $("credit-msg").textContent = decision === "yes" ? "Στέλνω το αίτημα…" : "";
    fetch("/api/approvals/" + encodeURIComponent(id), {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({decision: decision})
    }).then(function (r) {
      return r.json().then(function (j) { return {ok: r.ok, body: j}; });
    }).then(function (res) {
      var msg = res.body && (res.body.message || res.body.detail);
      if (res.ok && res.body.ok) {
        done = msg || "";
        load();
      } else {
        $("credit-msg").textContent = msg || "Δεν έγινε. Δοκίμασε ξανά.";
        $("credit-yes").disabled = $("credit-no").disabled = false;
      }
    }).catch(function () {
      $("credit-msg").textContent = "Δεν έγινε. Έλεγξε τη σύνδεση και δοκίμασε ξανά.";
      $("credit-yes").disabled = $("credit-no").disabled = false;
    }).then(function () { sending = false; });
  }

  /* ---------- φόρτωση ---------- */
  function failed() {
    setLive(false, "Χωρίς σύνδεση");
    problem("Δεν μπόρεσα να φορτώσω τα τιμολόγια. Δοκίμασε ξανά σε λίγο.");
  }

  function load() {
    if (loading) { again = true; return; }
    loading = true;
    var url = ID ? "/api/screen/invoices/" + encodeURIComponent(ID) : "/api/screen/invoices";
    fetch(url).then(function (r) {
      if (r.status === 404 && ID) { problem("Δεν βρήκα αυτό το τιμολόγιο."); return null; }
      if (!r.ok) throw new Error(r.status);
      return r.json();
    }).then(function (data) {
      if (!data) return;
      setPaused(data.paused);
      clock = data.now;
      setLive(true, "Ζωντανά");
      if (ID) renderDetail(data); else renderList(data);
      $("wrap").setAttribute("aria-busy", "false");
    }).catch(failed).then(function () {
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
  $("credit-yes").addEventListener("click", function () { decide("yes"); });
  $("credit-no").addEventListener("click", function () { decide("no"); });
  if (ID === "") problem("Δεν βρήκα αυτό το τιμολόγιο."); else load();
  listen();
})();
