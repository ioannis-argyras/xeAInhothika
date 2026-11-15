/* Ιστορικό της μέρας: ξαναπαίζει τη μέρα από τα γεγονότα.
   Δεδομένα: /api/screen/replay (γεγονότα της μέρας) και /api/state?until_id= (η αρχική όπως ήταν).
   Ό,τι γράφεται στη σελίδα μπαίνει με textContent, ποτέ με innerHTML (και καθόλου SVG: οι λωρίδες είναι στοιχεία HTML). */
(function () {
  "use strict";

  var PLAY_MS = 5000;
  var FEED_DESK = 6;     // rows of "Μέχρι τις…" on desktop
  var FEED_MOB = 5;      // and on mobile
  var FEED_ALL = 40;     // rows after "Όλη η μέρα"
  var LOW_MAX = 3;       // low-stock rows in "Η αρχική"
  var BAR_MAX = 12;      // segments of a low-stock bar

  /* Σειρά, εικονίδια και χρώματα των λωρίδων: όπως στο σχέδιο. */
  var TEAMS = [
    ["master", "Συνεργάτης", "m-syn", "handshake", "var(--m-syn)", "#E4ECF8"],
    ["stock", "Αποθήκη", "m-apo", "package", "var(--m-apo)", "#E0EFF0"],
    ["orders", "Παραγγελίες", "m-par", "truck", "var(--m-par)", "#ECE9F7"],
    ["invoices", "Τιμολόγια", "m-tim", "receipt", "var(--m-tim)", "#F4E7F2"],
    ["accountant", "Λογιστής", "m-log", "calculator", "var(--m-log)", "#F5EEDE"],
    ["owner", "Εσύ", "you", "hand-palm", "#E0A800", "#FFF4D6"]
  ];
  var BY = {};
  TEAMS.forEach(function (t) { BY[t[0]] = t; });
  BY.agent = BY.master;   // another agent's MCP call has no lane of its own: it shows with the partner

  function $(id) { return document.getElementById(id); }

  var events = [];       // today's replay events, oldest first
  var dayInfo = {date: "", weekday: ""};
  var nowMin = 0;        // "now" of the Greek clock, never before the newest event
  var endMin = 1440;     // end of the axis
  var minute = 0;        // position of the replay, minutes from midnight
  var atEnd = true;      // the position follows "now"
  var playing = false;
  var dragging = false;
  var expanded = false;  // "Όλη η μέρα"
  var stateSeq = 0;
  var stateTimer = null;
  var stateCache = {};
  var dayState = null;   // the state of the whole day, for the numbers of the lead
  var paused = false;
  var views = [];
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function hhmm(m) {
    m = Math.max(0, Math.min(1439, Math.floor(m)));
    return pad(Math.floor(m / 60)) + ":" + pad(m % 60);
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
  function clear(node) { node.textContent = ""; }
  function pct(m) { return (m / endMin * 100) + "%"; }

  function athensNow() {
    var parts = new Intl.DateTimeFormat("el-GR", {hour: "2-digit", minute: "2-digit", hour12: false,
                                                  timeZone: "Europe/Athens"}).formatToParts(new Date());
    var h = 0, m = 0;
    parts.forEach(function (p) {
      if (p.type === "hour") h = parseInt(p.value, 10) % 24;
      if (p.type === "minute") m = parseInt(p.value, 10);
    });
    return h * 60 + m;
  }
  function computeNow() {
    var last = events.length ? events[events.length - 1].minute : 0;
    nowMin = Math.max(athensNow(), last);
    /* ο άξονας τελειώνει στις επόμενες ολόκληρες 3 ώρες μετά το «τώρα», όχι στις 24:00 */
    endMin = Math.min(1440, Math.ceil((nowMin + 1) / 180) * 180);
  }

  /* ---------- ποια γεγονότα φαίνονται ως τη θέση ---------- */
  function countUpTo(m) {
    var n = 0;
    while (n < events.length && events[n].minute <= m) n++;
    return n;
  }
  function decidedIds(n) {
    var ids = {};
    for (var i = 0; i < n; i++) {
      if (events[i].type === "approval.decided" && events[i].approval_id != null) ids[events[i].approval_id] = true;
    }
    return ids;
  }
  function isWaiting(e, decided) {
    return e.type === "approval.requested" && !(e.approval_id != null && decided[e.approval_id]);
  }
  function lowerFirst(t) {
    var first = t.split(" ")[0];
    /* όνομα με ψηφίο ή με δεύτερο κεφαλαίο (Α-2271, ΑΦΜ) μένει όπως είναι */
    if (/\d/.test(first) || /[A-ZΑ-Ω].*[A-ZΑ-Ω]/.test(first)) return t;
    return t.charAt(0).toLowerCase() + t.slice(1);
  }
  /* Η φράση της έγκρισης: «Περιμένει το «ναι» σου: …» ή «Ζήτησε το «ναι» σου: …». Ποτέ «Ρωτώ:», ποτέ ερωτηματικό. */
  function say(e, decided) {
    if (e.type !== "approval.requested") return e.text;
    var body = String(e.text || "").replace(/^\s*Ρωτώ:\s*/, "").replace(/[;?\s]+$/, "");
    body = body ? lowerFirst(body) : "";
    return (isWaiting(e, decided) ? "Περιμένει το «ναι» σου: " : "Ζήτησε το «ναι» σου: ") + body;
  }

  /* ---------- μικρά κομμάτια ---------- */
  function icon(team) {
    var t = BY[team] || BY.master, s = el("span", "mx-ic " + t[2]);
    s.appendChild(el("i", "ph-fill ph-" + t[3]));
    return s;
  }
  function nameOf(team) { return (BY[team] || BY.master)[1]; }

  /* ---------- λωρίδες: ο σκελετός χτίζεται μία φορά ανά διάταξη, οι κουκκίδες σε κάθε αλλαγή θέσης ---------- */
  function buildLanes(v) {
    var holder = v.lanes;
    clear(holder);
    var old = holder.parentNode.querySelector(".mx-ax");
    if (old) old.parentNode.removeChild(old);
    v.on = !!holder.offsetParent;
    v.sig = null;
    if (!v.on) return;
    var mobile = v.k === "m";
    v.rows = [];
    TEAMS.forEach(function (t) {
      var ln = el("div", "mx-ln");
      var lb = el("div", "mx-lb");
      lb.appendChild(icon(t[0]));
      if (!mobile) lb.appendChild(el("span", null, t[1]));
      var tk = el("div", "mx-tk");
      ln.appendChild(lb); ln.appendChild(tk);
      holder.appendChild(ln);
      v.rows.push([t, tk]);
    });
    var ov = el("div", "mx-ov");
    v.pa = el("div", "mx-pa"); ov.appendChild(v.pa);
    var fu = el("div", "mx-fu"); fu.style.left = pct(nowMin); ov.appendChild(fu);
    for (var gm = 0; gm <= endMin; gm += (mobile ? 180 : 60)) {
      var g = el("div", "mx-gl" + (gm % 180 ? " h" : "")); g.style.left = pct(gm); ov.appendChild(g);
    }
    var nw = el("div", "mx-nw"); nw.style.left = pct(nowMin); ov.appendChild(nw);
    v.ph = el("div", "mx-ph"); ov.appendChild(v.ph);
    holder.appendChild(ov);
    v.ov = ov;
    var ax = el("div", "mx-ax");
    v.ax = ax;
    v.labels = [];
    for (var am = 0; am <= endMin; am += (mobile ? 360 : 180)) {
      var s = el("span", am === 0 ? "s" : (am === endMin ? "e" : null), pad(am / 60) + ":00");
      s.style.left = pct(am);
      ax.appendChild(s);
      v.labels.push(s);
    }
    var kn = el("span", "mx-kn");
    kn.setAttribute("role", "slider");
    kn.setAttribute("tabindex", "0");
    kn.setAttribute("aria-label", "Ώρα της επανάληψης");
    kn.setAttribute("aria-valuemin", "0");
    kn.setAttribute("aria-valuemax", String(Math.round(nowMin)));
    kn.appendChild(el("i", "ph ph-arrows-horizontal"));
    v.knText = document.createTextNode("");
    kn.appendChild(v.knText);
    ax.appendChild(kn);
    v.kn = kn;
    holder.parentNode.appendChild(ax);
    holder.style.touchAction = "pan-y";
    ax.style.touchAction = "pan-y";
    kn.style.touchAction = "none";
  }

  /* Κουκκίδες 32px (30 στο κινητό): ό,τι πέφτει πιο κοντά από μία κουκκίδα ενώνεται, με τον αριθμό μέσα.
     Η ομάδα με το γεγονός που περιμένει εσένα γίνεται κόκκινη, μεγαλύτερη, με φωτοστέφανο. */
  function paintDots(v, n, decided) {
    var mobile = v.k === "m";
    v.rows.forEach(function (r) {
      clear(r[1]);
      var w = r[1].getBoundingClientRect().width || 1;
      var size = mobile ? 30 : 32, gap = (size + 2) / w * endMin;
      var cl = [];
      events.forEach(function (e, i) {
        var team = (BY[e.team] || BY.master)[0];
        if (team !== r[0][0]) return;
        var fut = i >= n, wt = !fut && isWaiting(e, decided), last = cl[cl.length - 1];
        if (last && last.fut === fut && e.minute - last.m < gap) { last.n++; last.wt = last.wt || wt; return; }
        cl.push({m: e.minute, n: 1, fut: fut, wt: wt});
      });
      var prevPx = -99;
      cl.forEach(function (c) {
        var px = Math.min(w - size / 2, Math.max(size / 2 + 2, c.m / endMin * w, prevPx + size + 4));
        prevPx = px;
        var d = el("span", "mx-d" + (c.wt ? " wt" : "") + (c.fut ? " fut" : "") + (r[0][0] === "owner" ? " you" : ""));
        if (c.wt && !mobile) {
          var wl = el("span", "mx-wl", "περιμένει εσένα");
          wl.style.left = (px - 20) + "px";
          r[1].appendChild(wl);
        }
        d.style.setProperty("--c", r[0][4]);
        d.style.left = px + "px";
        if (c.n > 1) d.appendChild(el("b", null, String(c.n)));
        r[1].appendChild(d);
      });
    });
  }

  function paintLanes(v, n, decided) {
    if (!v.on) return;
    v.pa.style.width = pct(minute);
    v.ph.style.left = pct(minute);
    v.knText.nodeValue = hhmm(minute);
    /* η κίτρινη ώρα μένει μέσα στο πάνελ και όταν η θέση είναι στην άκρη του άξονα */
    var aw = v.ax.getBoundingClientRect().width, kw = v.kn.getBoundingClientRect().width;
    v.kn.style.left = Math.max(kw / 2, Math.min(aw - kw / 2, minute / endMin * aw)) + "px";
    v.kn.setAttribute("aria-valuenow", String(Math.round(minute)));
    v.kn.setAttribute("aria-valuetext", hhmm(minute));
    var sig = n + ":" + Object.keys(decided).length;
    if (sig !== v.sig) { v.sig = sig; paintDots(v, n, decided); }
    /* ώρες του άξονα που πέφτουν κάτω από την κίτρινη ώρα κρύβονται */
    var kr = v.kn.getBoundingClientRect();
    v.labels.forEach(function (s) {
      s.classList.remove("off");
      var r = s.getBoundingClientRect();
      if (r.right > kr.left - 8 && r.left < kr.right + 8) s.classList.add("off");
    });
  }

  /* ---------- η σκηνή, η ροή ---------- */
  function paintCur(v, n, decided) {
    clear(v.cur);
    if (!events.length) {
      v.cur.appendChild(el("div", "t")).appendChild(el("b", null, "Σήμερα δεν έχει γίνει τίποτα ακόμα"));
      v.cur.style.gridTemplateColumns = "minmax(0,1fr)";
      return;
    }
    v.cur.style.gridTemplateColumns = "";
    if (!n) {
      var t0 = el("div", "t");
      t0.appendChild(el("b", null, "Ως αυτή την ώρα δεν είχε γίνει ακόμα τίποτα"));
      v.cur.style.gridTemplateColumns = "minmax(0,1fr)";
      v.cur.appendChild(t0);
      return;
    }
    var e = events[n - 1];
    v.cur.appendChild(icon(e.team));
    var t = el("div", "t");
    t.appendChild(el("span", null, nameOf(e.team) + " · " + e.hhmm));
    t.appendChild(el("b", null, say(e, decided)));
    v.cur.appendChild(t);
  }

  function paintFeed(v, n, decided) {
    clear(v.feed);
    var max = expanded ? FEED_ALL : (v.k === "m" ? FEED_MOB : FEED_DESK);
    var done = events.slice(0, n).reverse();
    var shown = done.slice(0, max);
    shown.forEach(function (e, i) {
      var t = BY[e.team] || BY.master, wt = isWaiting(e, decided);
      var row = el("div", "mx-fr" + (i === 0 ? " on" : "") + (wt ? " wt" : ""));
      row.style.setProperty("--c", t[4]);
      row.style.setProperty("--cb", t[5]);
      row.appendChild(el("time", null, e.hhmm));
      row.appendChild(icon(e.team));
      var tx = el("div", "t");
      tx.appendChild(el("b", null, say(e, decided)));
      tx.appendChild(el("span", null, wt ? t[1] + " · περιμένει εσένα" : t[1]));
      row.appendChild(tx);
      v.feed.appendChild(row);
    });
    clear(v.mo);
    var rest = done.length - shown.length;
    if (!events.length || !n) {
      v.mo.style.display = "";
      v.mo.appendChild(el("span", null, !events.length ? "Δεν έχει γίνει ακόμα τίποτα σήμερα." :
                                                          "Ως αυτή την ώρα δεν είχε γίνει ακόμα τίποτα."));
      return;
    }
    var canMore = !expanded && done.length > max;
    if (rest <= 0 && !canMore) { v.mo.style.display = "none"; return; }
    v.mo.style.display = "";
    v.mo.appendChild(el("span", null, rest > 0 ? "και " + rest + " νωρίτερα" : ""));
    if (canMore) {
      var a = el("a", null, v.k === "m" ? "Όλα" : "Όλη η μέρα");
      a.href = "#";
      a.appendChild(el("i", "ph ph-arrow-right"));
      a.addEventListener("click", function (ev) { ev.preventDefault(); expanded = true; paint(); });
      v.mo.appendChild(a);
    }
  }

  /* ---------- όλα μαζί, σε κάθε θέση ---------- */
  function paint() {
    var n = countUpTo(minute);
    var decided = decidedIds(n);
    var total = events.length;
    views.forEach(function (v) {
      v.clk.textContent = hhmm(minute);
      v.day.textContent = dayInfo.weekday ? dayInfo.weekday + " " + dayInfo.date.slice(0, 5) : "";
      v.count.textContent = total ? n + " από " + total + (v.k === "d" ? " γεγονότα" : "") : "";
      v.now.textContent = "τώρα " + hhmm(nowMin) + (v.k === "d" ? " · σύρε την κίτρινη ώρα" : "");
      paintCur(v, n, decided);
      paintLanes(v, n, decided);
      paintFeed(v, n, decided);
    });
    $("d-ev-t").textContent = "Μέχρι τις " + hhmm(minute);
    $("d-home-t").textContent = "Η αρχική στις " + hhmm(minute);
    $("m-tabc").textContent = String(n);
  }

  function setMinute(m) {
    minute = Math.max(0, Math.min(nowMin, m));
    paint();
  }

  function paintLead() {
    var total = events.length;
    ["d", "m"].forEach(function (k) {
      var lead = $(k + "-lead");
      clear(lead);
      if (k === "d") lead.appendChild(document.createTextNode("Ό,τι έγινε σήμερα γράφεται και δεν σβήνεται. "));
      if (!total) {
        lead.appendChild(document.createTextNode("Σήμερα δεν έχει γίνει τίποτα ακόμα."));
        return;
      }
      var t = dayState && dayState.tasks ? dayState.tasks : null;
      lead.appendChild(el("b", null, String(total)));
      lead.appendChild(document.createTextNode((total === 1 ? " γεγονός" : " γεγονότα") + (k === "m" ? " σήμερα" : "")));
      if (t) {
        lead.appendChild(document.createTextNode(": "));
        lead.appendChild(el("b", null, String(Math.max(0, t.done - t.with_yes))));
        lead.appendChild(document.createTextNode(" μόνα τους, "));
        lead.appendChild(el("b", null, String(t.with_yes)));
        lead.appendChild(document.createTextNode(" με το «ναι» σου."));
      } else {
        lead.appendChild(document.createTextNode("."));
      }
    });
    $("d-kick").textContent = "Η μέρα · " + dayInfo.weekday + " " + dayInfo.date;
    $("m-kick").textContent = "Η μέρα · " + dayInfo.date;
  }

  /* ---------- «Περιμένει εσένα», ήσυχη γραμμή, «η αρχική στις…» (από το /api/state?until_id=) ---------- */
  function untilIdFor(m) {
    var n = countUpTo(m);
    if (n) return events[n - 1].id;
    return events.length ? events[0].id - 1 : null;
  }

  function waitBox(a) {
    var box = el("div", a ? "mx-wt" : "mx-wt low");
    var h = el("div", "h");
    h.appendChild(el("i", "ph-fill " + (a ? "ph-warning-circle" : "ph-check-circle")));
    h.appendChild(document.createTextNode(a ? "Περιμένει εσένα" : "Καμία απόφαση δεν περίμενε εσένα"));
    box.appendChild(h);
    if (a) {
      box.appendChild(el("b", null, a.summary || "Μια απόφαση"));
      var why = a.why;
      if (Array.isArray(why)) why = why.join(", ");
      if (why) box.appendChild(el("span", null, String(why)));
    }
    return box;
  }

  function quietLine(p, s, short) {
    clear(p);
    var t = s.tasks || {done: 0, with_yes: 0};
    var a = el("span");
    a.appendChild(icon("master"));
    a.appendChild(el("b", null, String(Math.max(0, t.done - t.with_yes))));
    a.appendChild(document.createTextNode(short ? " μόνα τους" : " έγιναν μόνα τους"));
    var b = el("span");
    b.appendChild(icon("owner"));
    b.appendChild(el("b", null, String(t.with_yes)));
    b.appendChild(document.createTextNode(" με το «ναι» σου"));
    p.appendChild(a); p.appendChild(b);
  }

  function lowRow(item) {
    var bad = item.on_hand <= 1;
    var row = el("div", "mx-lo" + (bad ? " bad" : ""));
    row.appendChild(el("span", null, item.name));
    var br = el("span", "br");
    var cap = Math.max(1, item.low), seg = Math.min(cap, BAR_MAX);
    var full = cap > BAR_MAX ? Math.round(item.on_hand / cap * BAR_MAX) : Math.min(item.on_hand, cap);
    for (var i = 0; i < seg; i++) br.appendChild(el("i", i < full ? null : "g"));
    row.appendChild(br);
    row.appendChild(el("em", null, item.on_hand + "/" + item.low));
    return row;
  }

  function dzBox(a) {
    var dz = el("div", "mx-dz" + (a ? "" : " low"));
    var ic = el("span", "mx-ic");
    ic.appendChild(el("i", "ph-fill " + (a ? "ph-warning-circle" : "ph-check-circle")));
    dz.appendChild(ic);
    var t = el("div");
    t.appendChild(el("span", null, a ? "Περίμενε εσένα" : "Καμία απόφαση δεν περίμενε εσένα"));
    if (a) t.appendChild(el("b", null, a.summary || "Μια απόφαση"));
    dz.appendChild(t);
    return dz;
  }

  function renderState(s) {
    var a = (s.pending_approvals || [])[0] || null;
    var n = s.today ? s.today.sales_count : 0;
    ["d", "m"].forEach(function (k) {
      var w = $(k + "-wt"); clear(w); w.appendChild(waitBox(a));
      quietLine($(k + "-qt"), s, k === "m");
      var dz = $(k + "-dz"); clear(dz); dz.appendChild(dzBox(a));
      var sl = $(k + "-sl"); clear(sl);
      var h3 = el("h3"); h3.appendChild(el("i", "ph ph-cash-register")); h3.appendChild(document.createTextNode("Πωλήσεις ως τότε"));
      sl.appendChild(h3);
      if (n) {
        sl.appendChild(el("b", null, eur(s.today.sales_cents)));
        sl.appendChild(el("span", null, "από " + n + (n === 1 ? " απόδειξη" : " αποδείξεις") + (k === "d" ? " · ταμείο σε προσομοίωση" : "")));
      } else {
        sl.appendChild(el("b", null, "Δεν υπάρχει ακόμα"));
        sl.appendChild(el("span", null, "καμία πώληση ως τότε"));
      }
      var lst = $(k + "-lst"); clear(lst);
      var low = (s.stock || []).slice(0, LOW_MAX);
      if (!low.length) {
        var none = el("div", "mx-lo");
        none.style.gridTemplateColumns = "minmax(0,1fr)";
        none.appendChild(el("span", null, "Τίποτα δεν ήταν σε χαμηλό"));
        lst.appendChild(none);
      }
      low.forEach(function (item) { lst.appendChild(lowRow(item)); });
    });
    $("s-mix").setAttribute("aria-busy", "false");
  }

  function loadState(m) {
    var until = untilIdFor(m);
    var key = String(until);
    var seq = ++stateSeq;
    var done = function (s) {
      if (seq !== stateSeq) return;
      renderState(s);
    };
    if (stateCache[key]) { done(stateCache[key]); return Promise.resolve(); }
    return getJSON("/api/state" + (until == null ? "" : "?until_id=" + until)).then(function (s) {
      stateCache[key] = s;
      done(s);
    }).catch(function () {
      if (seq === stateSeq) {
        ["d", "m"].forEach(function (k) {
          var w = $(k + "-wt"); clear(w);
          w.appendChild(el("p", "mx-qt", "Δεν φορτώθηκε η αρχική. Δοκίμασε ξανά σε λίγο."));
        });
      }
    });
  }
  function loadStateSoon(m) {
    clearTimeout(stateTimer);
    stateTimer = setTimeout(function () { loadState(m); }, 150);
  }

  /* ---------- Παίξε ---------- */
  function play() {
    if (playing || !events.length) return;
    var from = events[0].minute, to = nowMin;
    if (reduced) { atEnd = true; setMinute(to); loadState(to); return; }
    playing = true;
    atEnd = false;
    var t0 = performance.now();
    var lastFetch = 0;
    function frame(now) {
      var p = Math.min(1, (now - t0) / PLAY_MS);
      setMinute(from + (to - from) * p);
      if (p >= 1) {
        playing = false;
        atEnd = true;
        loadState(to);
        return;
      }
      if (now - lastFetch > 200) { lastFetch = now; loadState(minute); }
      requestAnimationFrame(frame);
    }
    setMinute(from);
    loadState(from);
    requestAnimationFrame(frame);
  }

  /* ---------- σύρσιμο της κίτρινης ώρας, και πλήκτρα ---------- */
  function minuteAt(v, clientX) {
    var r = v.ov.getBoundingClientRect();
    return Math.max(0, Math.min(nowMin, (clientX - r.left) / r.width * endMin));
  }
  function moveTo(m) {
    atEnd = false;
    setMinute(m);
    loadStateSoon(minute);
  }
  function wireDrag(v) {
    var box = v.lanes.parentNode;
    box.addEventListener("pointerdown", function (ev) {
      if (playing || !v.on || !v.ov) return;
      if (ev.clientX < v.ov.getBoundingClientRect().left) return;
      if (ev.target.closest && ev.target.closest(".mx-lh")) return;
      dragging = true;
      try { box.setPointerCapture(ev.pointerId); } catch (e) { /* not essential */ }
      moveTo(minuteAt(v, ev.clientX));
    });
    box.addEventListener("pointermove", function (ev) {
      if (dragging && v.on) moveTo(minuteAt(v, ev.clientX));
    });
    function endDrag() { dragging = false; }
    box.addEventListener("pointerup", endDrag);
    box.addEventListener("pointercancel", endDrag);
    box.addEventListener("keydown", function (ev) {
      if (!ev.target.classList || !ev.target.classList.contains("mx-kn")) return;
      var d = {ArrowLeft: -10, ArrowDown: -10, ArrowRight: 10, ArrowUp: 10, PageDown: -60, PageUp: 60}[ev.key];
      if (d == null || playing) return;
      ev.preventDefault();
      moveTo(minute + d);
      var kn = v.kn;
      if (kn) kn.focus();
    });
  }

  /* ---------- φόρτωση ---------- */
  function relayout() {
    views.forEach(buildLanes);
    paint();
  }

  function loadReplay(keepPosition) {
    return getJSON("/api/screen/replay").then(function (data) {
      var follow = atEnd;
      events = data.events;
      dayInfo = {date: data.date, weekday: data.weekday};
      stateCache = {};
      computeNow();
      $("play").disabled = !events.length;
      $("play-m").disabled = !events.length;
      views.forEach(buildLanes);
      paintLead();
      if (!keepPosition || follow || !events.length) {
        atEnd = true;
        minute = nowMin;
      }
      setMinute(minute);
      /* οι αριθμοί της πρότασης και η παύση: η ζωντανή κατάσταση της μέρας, όχι η παλιά της επανάληψης */
      getJSON("/api/state").then(function (s) { dayState = s; paintLead(); setPaused(!!s.paused); }).catch(function () { /* το κείμενο μένει χωρίς αριθμούς */ });
      return loadState(minute);
    });
  }

  /* ---------- πλήκτρο παύσης και ζωντανή ένδειξη (όπως στην αρχική) ---------- */
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
  /* το «τώρα» προχωρά και όταν δεν έρχεται γεγονός */
  setInterval(function () {
    if (playing || dragging) return;
    var before = nowMin;
    computeNow();
    if (nowMin !== before) { views.forEach(buildLanes); if (atEnd) minute = nowMin; paint(); }
  }, 30000);

  /* ---------- σύνδεση με το HTML ---------- */
  ["d", "m"].forEach(function (k) {
    views.push({k: k, lanes: $(k + "-lanes"), cur: $(k + "-cur"), clk: $(k + "-clk"), day: $(k + "-day"),
                count: $(k + "-count"), now: $(k + "-now"), feed: $(k === "d" ? "d-feed" : "m-feedl"), mo: $(k + "-mo")});
  });
  views.forEach(wireDrag);
  $("play").addEventListener("click", play);
  $("play-m").addEventListener("click", play);
  document.querySelectorAll(".seg2 button").forEach(function (b) {
    b.addEventListener("click", function () {
      document.querySelectorAll(".seg2 button").forEach(function (x) {
        x.setAttribute("aria-selected", String(x === b));
        $(x.dataset.p).hidden = x !== b;
      });
    });
  });
  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(relayout, 150);
  });

  loadReplay(false).catch(function () {
    ["d", "m"].forEach(function (k) {
      var w = $(k + "-wt"); clear(w);
      w.appendChild(el("p", "mx-qt", "Δεν φορτώθηκε η μέρα. Δοκίμασε ξανά σε λίγο."));
    });
  });
  connectStream();
})();
