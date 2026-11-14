/* Σελίδα Εργασίες: τα δεδομένα από το /api/screen/tasks, ζωντανά από το /api/events/stream.
   Κάθε κείμενο μπαίνει με textContent, ποτέ ως HTML. */
(function () {
  "use strict";
  var TEAM_ICON = {orders: "truck", stock: "package", invoices: "receipt",
                   accountant: "calculator", master: "circles-three"};
  var paused = false, timer = null, loading = false, again = false;

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function icon(kind, name) { return el("i", kind + " ph-" + name); }

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

  /* ---------- σχεδίαση ---------- */
  function title(t) {
    if (!t.done && !t.with_yes && !t.errors) return "Ακόμα καμία εργασία σήμερα.";
    var alone = Math.max(0, t.done - t.with_yes);
    return alone + " έγιναν μόνες τους, " + t.with_yes + " με το «ναι» σου, " + t.errors + " λάθη.";
  }

  function link(task, cls) {
    var id = task.links && task.links.invoice_id;
    if (!id) return null;
    var a = el("a", cls, "τιμολόγιο");
    a.href = "/static/pages/timologia.html?id=" + encodeURIComponent(id);
    return a;
  }

  function row(task) {
    var yes = task.how === "yes";
    var how = el("span", "how c" + (yes ? " yes" : ""));
    how.appendChild(yes ? icon("ph-fill", "hand-pointing") : icon("ph", "check"));
    how.appendChild(yes ? el("b", null, "Με το ναι σου") : document.createTextNode("Μόνο του"));
    var textCell = el("span", null, task.text);
    var sub = el("span", "sub m", yes ? "Με το ναι σου" : "Μόνο του");
    var mobileLink = link(task, "");
    if (mobileLink) { sub.appendChild(document.createTextNode(" · ")); sub.appendChild(mobileLink); }
    textCell.appendChild(sub);
    var tr = el("div", "tr2" + (task.warn ? " diff" : ""));
    tr.appendChild(el("b", null, task.at));
    tr.appendChild(textCell);
    tr.appendChild(how);
    tr.appendChild(link(task, "n c") || el("span", "c"));
    return tr;
  }

  function render(data) {
    setPaused(data.paused);
    $("kick").textContent = "Εργασίες · " + data.day.weekday + " " + data.day.date + " · " + data.day.now;
    $("title").textContent = title(data.totals);
    clock = data.day.now;
    setLive(true, "Ζωντανά");
    var body = $("tasks-body");
    body.replaceChildren();
    if (!data.groups.length) {
      body.appendChild(el("p", "empty", "Δεν έχει γίνει καμία εργασία σήμερα. Ό,τι κάνει ο συνεργάτης θα εμφανιστεί εδώ."));
    }
    data.groups.forEach(function (g) {
      var head = el("div", "grp");
      head.appendChild(icon("ph", TEAM_ICON[g.key] || "circles-three"));
      head.appendChild(document.createTextNode(g.name));
      head.appendChild(el("span", null, String(g.count)));
      body.appendChild(head);
      g.tasks.forEach(function (t) { body.appendChild(row(t)); });
    });
    $("tasks").setAttribute("aria-busy", "false");
  }

  function failed() {
    setLive(false, "Χωρίς σύνδεση");
    var body = $("tasks-body");
    body.replaceChildren(el("p", "empty", "Δεν μπόρεσα να φορτώσω τις εργασίες."));
    var retry = el("button", "btn qd retry", "Ξαναδοκίμασε");
    retry.type = "button";
    retry.addEventListener("click", load);
    body.appendChild(retry);
    $("tasks").setAttribute("aria-busy", "false");
  }

  function load() {
    if (loading) { again = true; return; }
    loading = true;
    fetch("/api/screen/tasks").then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    }).then(render).catch(failed).then(function () {
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
  load();
  listen();
})();
