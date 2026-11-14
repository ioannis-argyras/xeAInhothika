/* Μηχανή του χρόνου: ξαναπαίζει τη μέρα από τα γεγονότα.
   Δεδομένα: /api/screen/replay (γεγονότα της μέρας) και /api/state?until_id= (η αρχική όπως ήταν). */
(function () {
  "use strict";

  var PLAY_MS = 5000;
  var FEED_MAX = 40;
  var CLUSTER_PX = 34;   // markers of different teams closer than this many pixels share one marker
  var GROUP_MIN = 20;    // events of one team closer than this make one marker
  var TEAM_ICON = {stock: "package", orders: "truck", invoices: "receipt",
                   accountant: "calculator", master: "circles-three", owner: "user", agent: "plugs-connected"};
  var TEAM_NAME = {stock: "Αποθήκη", orders: "Παραγγελίες", invoices: "Τιμολόγια",
                   accountant: "Λογιστής", master: "Συνεργάτης", owner: "Ιδιοκτήτης", agent: "Άλλος agent"};
  var TEAM_ORDER = ["stock", "orders", "invoices", "accountant", "master", "agent", "owner"];

  function $(id) { return document.getElementById(id); }
  var ruler = $("ruler"), knob = $("knob");

  var events = [];       // today's replay events, oldest first
  var clusters = [];     // [{start, left, el, groups}]
  var minute = 0;        // knob position, minutes from midnight
  var atEnd = true;      // knob follows the newest event
  var playing = false;
  var stateSeq = 0;
  var stateTimer = null;
  var paused = false;
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function hhmm(m) {
    m = Math.max(0, Math.min(1439, Math.round(m)));
    return ("0" + Math.floor(m / 60)).slice(-2) + ":" + ("0" + (m % 60)).slice(-2);
  }
  function eur(cents) {
    return (cents / 100).toLocaleString("el-GR", {style: "currency", currency: "EUR"});
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function getJSON(url) {
    return fetch(url, {cache: "no-store"}).then(function (r) {
      if (!r.ok) throw new Error(url + " " + r.status);
      return r.json();
    });
  }
  function post(url) { return fetch(url, {method: "POST"}).then(function (r) { return r.json(); }); }

  /* ---------- markers on the ruler ---------- */
  function buildMarkers() {
    var holder = $("marks");
    holder.textContent = "";
    clusters = [];
    var open = {}, groups = [];
    events.forEach(function (e) {
      var g = open[e.team];
      if (!g || e.minute - g.start >= GROUP_MIN) {
        g = {team: e.team, start: e.minute};
        open[e.team] = g;
        groups.push(g);
      }
    });
    groups.sort(function (a, b) { return a.start - b.start; });
    var c = null;
    var clusterMin = Math.max(25, CLUSTER_PX / ruler.getBoundingClientRect().width * 1440);
    groups.forEach(function (g) {
      if (!c || g.start - c.start >= clusterMin) {
        c = {start: g.start, groups: []};
        clusters.push(c);
      }
      if (!c.groups.some(function (x) { return x.team === g.team; })) c.groups.push(g);
    });
    clusters.forEach(function (c) {
      c.groups.sort(function (a, b) { return TEAM_ORDER.indexOf(a.team) - TEAM_ORDER.indexOf(b.team); });
      var left = c.start / 1440 * 100;
      var mk = el("span", "mk" + (left > 92 ? " end" : ""));
      mk.style.left = left + "%";
      mk.title = hhmm(c.start) + " · " + c.groups.map(function (g) { return TEAM_NAME[g.team]; }).join(", ");
      c.groups.forEach(function (g) {
        var i = el("i", "ph ph-" + (TEAM_ICON[g.team] || "circles-three"));
        mk.appendChild(i);
      });
      c.el = mk;
      holder.appendChild(mk);
    });
  }

  /* ---------- knob, list ---------- */
  function setMinute(m) {
    minute = Math.max(0, Math.min(1439, m));
    var pct = minute / 1440 * 100;
    knob.style.left = pct + "%";
    $("ps").style.width = pct + "%";
    $("knob-time").textContent = hhmm(minute);
    knob.setAttribute("aria-valuenow", String(Math.round(minute)));
    knob.setAttribute("aria-valuetext", hhmm(minute));
    clusters.forEach(function (c) { c.el.classList.toggle("fut", c.start > minute); });
    renderFeed();
  }

  function upTo(m) {
    return events.filter(function (e) { return e.minute <= m; });
  }

  function renderFeed() {
    var feed = $("feed");
    feed.textContent = "";
    if (!events.length) {
      feed.appendChild(el("p", "empty", "Δεν έχει γίνει ακόμα τίποτα σήμερα."));
      return;
    }
    var done = upTo(minute);
    if (!done.length) {
      feed.appendChild(el("p", "empty", "Ως αυτή την ώρα δεν είχε γίνει ακόμα τίποτα."));
      return;
    }
    var shown = done.slice(-FEED_MAX).reverse();
    shown.forEach(function (e) {
      var row = el("div", "ev");
      row.appendChild(el("b", null, e.hhmm));
      row.appendChild(el("span", null, e.text));
      feed.appendChild(row);
    });
    if (done.length > shown.length) {
      var more = el("div", "ev more");
      more.appendChild(el("b", null, "…"));
      more.appendChild(el("span", null, "και " + (done.length - shown.length) + " παλαιότερα"));
      feed.appendChild(more);
    }
  }

  /* ---------- "the home screen as it was" ---------- */
  function untilIdFor(m) {
    var done = upTo(m);
    if (done.length) return done[done.length - 1].id;
    return events.length ? events[0].id - 1 : null;
  }

  function loadState(m) {
    var until = untilIdFor(m);
    var seq = ++stateSeq;
    $("thumb-title").textContent = "Η αρχική στις " + hhmm(m);
    return getJSON("/api/state" + (until == null ? "" : "?until_id=" + until)).then(function (s) {
      if (seq !== stateSeq) return;
      var n = s.today.sales_count;
      $("th-sales").textContent = n
        ? "Ως τότε " + eur(s.today.sales_cents) + " από " + n + (n === 1 ? " απόδειξη" : " αποδείξεις")
        : "Ως τότε καμία πώληση";
      var a = s.pending_approvals[0];
      $("th-approval").textContent = a ? a.summary : "Καμία απόφαση δεν περιμένει";
      var low = s.stock.map(function (x) { return x.name; });
      $("th-low").textContent = low.length
        ? "Χαμηλό: " + low.slice(0, 3).join(", ") + (low.length > 3 ? " και άλλα " + (low.length - 3) : "")
        : "Τίποτα δεν είναι σε χαμηλό";
      setPaused(!!s.paused);
    }).catch(function () {
      if (seq === stateSeq) $("th-sales").textContent = "Δεν φορτώθηκε η αρχική. Δοκίμασε ξανά σε λίγο.";
    });
  }

  function loadStateSoon(m) {
    clearTimeout(stateTimer);
    stateTimer = setTimeout(function () { loadState(m); }, 150);
  }

  /* ---------- play ---------- */
  function play() {
    if (playing || !events.length) return;
    var from = events[0].minute, to = events[events.length - 1].minute;
    var btn = $("play");
    if (reduced) { atEnd = true; setMinute(to); loadState(to); return; }
    playing = true;
    atEnd = false;
    btn.disabled = true;
    var t0 = performance.now();
    var step = 0;
    function frame(now) {
      var p = Math.min(1, (now - t0) / PLAY_MS);
      var m = from + (to - from) * p;
      setMinute(m);
      var due = Math.min(5, Math.floor(p * 5));
      if (due > step) {
        step = due;
        loadState(due === 5 ? to : m);
      }
      if (p < 1) { requestAnimationFrame(frame); return; }
      playing = false;
      atEnd = true;
      btn.disabled = false;
    }
    setMinute(from);
    requestAnimationFrame(frame);
  }

  /* ---------- dragging on the ruler ---------- */
  var dragging = false;
  function minuteAt(clientX) {
    var r = ruler.getBoundingClientRect();
    return Math.max(0, Math.min(1439, (clientX - r.left) / r.width * 1440));
  }
  function moveTo(m) {
    atEnd = false;
    setMinute(m);
    loadStateSoon(m);
  }
  ruler.addEventListener("pointerdown", function (ev) {
    if (playing) return;
    dragging = true;
    try { ruler.setPointerCapture(ev.pointerId); } catch (e) { /* not essential */ }
    moveTo(minuteAt(ev.clientX));
  });
  ruler.addEventListener("pointermove", function (ev) {
    if (dragging) moveTo(minuteAt(ev.clientX));
  });
  function endDrag() { dragging = false; }
  ruler.addEventListener("pointerup", endDrag);
  ruler.addEventListener("pointercancel", endDrag);
  knob.addEventListener("keydown", function (ev) {
    var d = {ArrowLeft: -10, ArrowDown: -10, ArrowRight: 10, ArrowUp: 10, PageDown: -60, PageUp: 60}[ev.key];
    if (d == null || playing) return;
    ev.preventDefault();
    moveTo(minute + d);
  });
  $("play").addEventListener("click", play);

  /* ---------- load ---------- */
  function loadReplay(keepPosition) {
    return getJSON("/api/screen/replay").then(function (data) {
      var follow = atEnd;
      events = data.events;
      $("day-title").textContent = data.weekday + " " + data.date;
      $("play").disabled = !events.length;
      buildMarkers();
      if (!keepPosition || follow || !events.length) {
        atEnd = true;
        var end = events.length ? events[events.length - 1].minute : 0;
        setMinute(end);
        return loadState(end);
      }
      setMinute(minute);
      return loadState(minute);
    });
  }

  /* ---------- pause button and live dot (same as the home screen) ---------- */
  function setPaused(p) {
    paused = p;
    var label = p ? "Συνέχισε" : "Παύση όλων";
    var icon = "ph ph-" + (p ? "play" : "pause");
    $("stop-btn").querySelector("span").textContent = label;
    $("stop-btn").querySelector("i").className = icon;
    $("stop-ibtn").setAttribute("aria-label", label);
    $("stop-ibtn").querySelector("i").className = icon;
    $("paused-banner").hidden = !p;
  }
  function togglePause() {
    post(paused ? "/api/resume" : "/api/pause").then(function (r) { setPaused(!!r.paused); });
  }
  $("stop-btn").addEventListener("click", togglePause);
  $("stop-ibtn").addEventListener("click", togglePause);

  var liveOn = false;
  function renderLive() {
    $("live-dot").className = liveOn ? "on" : "";
    var now = new Date().toLocaleTimeString("el-GR", {hour: "2-digit", minute: "2-digit", hour12: false,
                                                       timeZone: "Europe/Athens"});
    $("live-text").textContent = liveOn ? "Ζωντανά · " + now : "Χωρίς σύνδεση";
  }
  var reloadTimer = null;
  function connectStream() {
    var es = new EventSource("/api/events/stream");
    es.onopen = function () { liveOn = true; renderLive(); };
    es.onerror = function () { liveOn = false; renderLive(); };
    es.onmessage = function (msg) {
      var e;
      try { e = JSON.parse(msg.data); } catch (err) { return; }
      if (e.quiet || playing || dragging) return;
      clearTimeout(reloadTimer);
      reloadTimer = setTimeout(function () { loadReplay(true); }, 400);
    };
  }
  setInterval(renderLive, 30000);
  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { buildMarkers(); setMinute(minute); }, 150);
  });

  loadReplay(false).catch(function () {
    $("th-sales").textContent = "Δεν φορτώθηκε η μέρα. Δοκίμασε ξανά σε λίγο.";
  });
  connectStream();
})();
