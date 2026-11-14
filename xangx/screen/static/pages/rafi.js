/* Σελίδα Ράφι: τα δεδομένα από /api/screen/shelves, ζωντανά από /api/events/stream. */
(function () {
  "use strict";
  var MAX_CANS = 12;
  var MIN_ROWS = 6;
  var showAll = false;
  var last = null;
  var paused = false;
  var timer = null;

  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function icon(name) { var i = el("i", "ph ph-" + name); i.setAttribute("aria-hidden", "true"); return i; }

  function camHref(cam) { return "/stock/camera" + (cam ? "?cam=" + encodeURIComponent(cam) : ""); }

  function cans(full, gone, small) {
    var total = Math.min(full + gone, MAX_CANS);
    var f = Math.min(full, total);
    var frag = document.createDocumentFragment();
    for (var k = 0; k < total; k++) {
      frag.appendChild(el("i", small ? (k < f ? "" : "g") : (k < f ? "can" : "can gone")));
    }
    return frag;
  }

  /* ---------- βιτρίνα ---------- */
  function renderBand(d) {
    var l = d.latest;
    $("kick").textContent = "Ράφι · " + d.total + " θέσεις" + (l ? " · τελευταία κίνηση " + l.at_full : " · καμία κίνηση σήμερα");
    var lead = $("lead");
    lead.textContent = "";
    lead.appendChild(el("b", null, d.low_count ? d.low_count + " σε χαμηλό" : "Κανένα σε χαμηλό"));
    var btn = $("cam-btn");
    btn.hidden = !d.camera_id;
    if (d.camera_id) btn.href = camHref(d.camera_id);
  }

  /* ---------- κάρτα «πριν / μετά» ---------- */
  function photoBox(url, ts, fullCans, goneCans) {
    var box = el("div", "photo");
    if (url) {
      var img = el("img");
      img.src = url;
      img.alt = "";
      img.loading = "lazy";
      box.appendChild(img);
    }
    if (ts) box.appendChild(el("span", "ts", ts));
    if (!url && fullCans != null) {
      var row = el("div", "shelfrow");
      row.appendChild(cans(fullCans, goneCans));
      box.appendChild(row);
    }
    return box;
  }

  function renderPhotos(d) {
    var body = $("photo-body");
    body.textContent = "";
    var l = d.latest;
    if (!l) {
      var note = el("div", "empty-note", "Σήμερα δεν έγινε καμία κίνηση στο ράφι. Στείλε φωτογραφία από την κάμερα και θα φανεί εδώ.");
      body.appendChild(note);
      $("photo-sub").textContent = "";
      return;
    }
    $("photo-sub").textContent = l.position_name;
    var taken = l.kind === "taken";
    var left = l.left;
    var beforeN = left == null ? null : (taken ? left + l.qty : Math.max(left - l.qty, 0));
    var afterN = left;
    var both = !!(l.before_url && l.after_url);
    var grid = el("div", "photos");

    var a = el("div");
    a.appendChild(photoBox(both ? l.before_url : null, l.before_at_full, beforeN, 0));
    a.appendChild(el("div", "cap m", "Πριν" + (beforeN != null ? " · " + beforeN + " " + l.product_name : "")));
    var b = el("div");
    b.appendChild(photoBox(l.after_url && both ? l.after_url : null, l.at_full,
      afterN == null ? null : (taken ? afterN : beforeN + l.qty), taken ? (l.qty || 0) : 0));
    var cap = el("div", "cap");
    cap.appendChild(el("b", null, "Μετά · " + (taken ? "πήραν " : "μπήκαν ") + l.qty));
    b.appendChild(cap);
    grid.appendChild(a);
    grid.appendChild(b);
    body.appendChild(grid);

    var tog = el("div", "tog");
    tog.style.background = "var(--strip)";
    var txt = el("span");
    txt.appendChild(el("b", null, "Αποθήκη: "));
    txt.appendChild(document.createTextNode(l.low ? l.product_name + " σε χαμηλό" : l.product_name + " σε επαρκές απόθεμα"));
    tog.appendChild(txt);
    var link = el("a", "btn q", l.low ? "Δες την παραγγελία" : "Άνοιξε την κάμερα");
    link.href = l.low ? "/" : camHref(l.camera_id);
    tog.appendChild(link);
    body.appendChild(tog);
  }

  /* ---------- λίστα θέσεων ---------- */
  function rowNode(p) {
    var hot = p.low;
    var row = el("div", "rw" + (hot ? " hot" : ""));
    row.appendChild(el("span", "no", String(p.slot)));
    var mid = el("span");
    mid.appendChild(el("b", null, p.product_name));
    mid.appendChild(el("span", "m", p.name));
    mid.lastChild.style.display = "block";
    var lastMove = p.last;
    if (lastMove && lastMove.kind === "taken" && p.on_hand != null) {
      var sm = el("span", "cansm");
      sm.appendChild(cans(p.on_hand, lastMove.qty, true));
      mid.appendChild(sm);
    }
    row.appendChild(mid);
    var right = el("span", "n");
    if (lastMove) {
      right.appendChild(el("b", hot ? "warn" : null,
        (lastMove.kind === "taken" ? "πήραν " : "μπήκαν ") + lastMove.qty + " στις " + lastMove.at));
    } else {
      right.appendChild(el("span", "m", "καμία κίνηση"));
    }
    var stock = el("span", "m");
    stock.style.display = "block";
    if (p.on_hand != null) {
      stock.textContent = hot && p.low_threshold != null
        ? "μένουν " + p.on_hand + ", όριο " + p.low_threshold
        : "απόθεμα " + p.on_hand;
    }
    right.appendChild(stock);
    row.appendChild(right);
    return row;
  }

  function renderPositions(d) {
    var body = $("pos-body");
    body.textContent = "";
    $("pos-title").textContent = "Οι " + d.total + " θέσεις";
    if (!d.positions.length) {
      body.appendChild(el("div", "empty-note", "Δεν υπάρχουν θέσεις ραφιού ακόμα."));
      return;
    }
    var active = d.positions.filter(function (p) { return p.low || p.last; }).length;
    var visible = showAll ? d.positions.length : Math.min(d.positions.length, Math.max(active, MIN_ROWS));
    d.positions.slice(0, visible).forEach(function (p) { body.appendChild(rowNode(p)); });
    var hidden = d.positions.length - Math.min(d.positions.length, Math.max(active, MIN_ROWS));
    if (hidden > 0) {
      var more = el("div", "more");
      var btn = el("button", "btn", showAll ? "Δες λιγότερες" : "Δες και τις άλλες " + hidden + " θέσεις");
      btn.type = "button";
      btn.setAttribute("aria-expanded", showAll ? "true" : "false");
      btn.addEventListener("click", function () { showAll = !showAll; renderPositions(last); });
      more.appendChild(btn);
      body.appendChild(more);
    }
  }

  function render(d) {
    last = d;
    renderBand(d);
    renderPhotos(d);
    renderPositions(d);
    $("photo-card").setAttribute("aria-busy", "false");
    $("pos-card").setAttribute("aria-busy", "false");
  }

  function showError() {
    var msg = "Δεν μπόρεσα να φορτώσω το ράφι. Δοκίμασε ξανά σε λίγο.";
    $("photo-body").textContent = "";
    $("photo-body").appendChild(el("div", "empty-note", msg));
    $("pos-body").textContent = "";
    $("pos-body").appendChild(el("div", "empty-note", msg));
    $("photo-card").setAttribute("aria-busy", "false");
    $("pos-card").setAttribute("aria-busy", "false");
  }

  function load() {
    var q = new URLSearchParams(location.search).get("until_id");
    var url = "/api/screen/shelves" + (q && /^\d+$/.test(q) ? "?until_id=" + q : "");
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.json();
    }).then(render).catch(showError);
  }

  function loadSoon() {
    clearTimeout(timer);
    timer = setTimeout(load, 250);
  }

  /* ---------- παύση και ζωντανή ένδειξη (όπως στην αρχική) ---------- */
  function paintPause() {
    ["stop-btn", "stop-ibtn"].forEach(function (id) {
      var b = $(id);
      var ic = b.querySelector("i");
      ic.className = "ph " + (paused ? "ph-play" : "ph-pause");
      var label = paused ? "Συνέχισε" : "Παύση όλων";
      var sp = b.querySelector("span");
      if (sp) sp.textContent = label; else b.setAttribute("aria-label", label);
    });
  }

  function loadPause() {
    fetch("/api/state").then(function (r) { return r.json(); })
      .then(function (s) { paused = !!s.paused; paintPause(); }).catch(function () {});
  }

  function togglePause() {
    fetch(paused ? "/api/resume" : "/api/pause", { method: "POST" })
      .catch(function () {}).then(loadPause);
  }
  $("stop-btn").addEventListener("click", togglePause);
  $("stop-ibtn").addEventListener("click", togglePause);

  function live(on) {
    $("live-text").textContent = on ? "Ζωντανά" : "Σύνδεση…";
    $("live-dot").style.background = on ? "#55B48A" : "#9AA6B6";
  }

  function connect() {
    var es = new EventSource("/api/events/stream");
    es.onopen = function () { live(true); loadSoon(); };
    es.onerror = function () { live(false); };
    es.onmessage = function (msg) {
      var e;
      try { e = JSON.parse(msg.data); } catch (err) { return; }
      if (/^(shelf\.|stock\.|approval\.|system\.)/.test(e.type || "")) loadSoon();
      if (/^system\./.test(e.type || "")) loadPause();
    };
  }

  load();
  loadPause();
  connect();
})();
