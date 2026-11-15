/* Shelf page: data from /api/screen/shelves, live from /api/events/stream.
   Every piece of server text goes in with textContent; photo URLs only with img.src. */
(function () {
  "use strict";
  var COLLAPSE_OVER = 4;   // a camera with more rows than this shows only the first rows
  var SHOWN_ROWS = 3;
  var last = null;
  var paused = false;
  var timer = null;
  var opened = {};         // cameras without a low position that the user opened
  var showAll = {};        // cameras whose full row list is open
  var DAYS = ["Κυριακή", "Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο"];

  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function add(parent) {
    for (var k = 1; k < arguments.length; k++) if (arguments[k]) parent.appendChild(arguments[k]);
    return parent;
  }

  /* kind is "ph", "ph-bold" or "ph-fill" (the three Phosphor sets the page loads) */
  function icon(kind, name) {
    var i = el("i", kind + " ph-" + name);
    i.setAttribute("aria-hidden", "true");
    return i;
  }

  function pl(n, one, many) { return n === 1 ? one : many; }

  function camHref(cam) { return "/stock/camera" + (cam ? "?cam=" + encodeURIComponent(cam) : ""); }

  function clear(node) { node.textContent = ""; return node; }

  function note(text, dark) { return el("div", "rf-note" + (dark ? " rf-dark" : ""), text); }

  /* ---------- dates ---------- */
  function two(n) { return (n < 10 ? "0" : "") + n; }

  function dm(iso) {
    var p = String(iso).split("-");
    return p.length === 3 ? p[2] + "/" + p[1] : String(iso);
  }

  function dayOf(iso) { return DAYS[new Date(iso + "T12:00:00").getDay()]; }

  function whenWord(days, iso) {
    if (days < 0) return "έχει λήξει";
    if (days === 0) return "σήμερα";
    if (days === 1) return "αύριο";
    return days <= 6 ? dayOf(iso) : dm(iso);
  }

  function inThreeDays() {
    var d = new Date();
    d.setDate(d.getDate() + 3);
    var w = d.getDay();
    return (w === 6 ? "το " : "την ") + DAYS[w];
  }

  /* ---------- shared pieces ---------- */
  function cameraLabels(d) {
    var seen = {};
    d.positions.forEach(function (p) {
      var key = p.camera_id || "";
      var head = String(p.name).split(" · ")[0];
      if (!(key in seen)) seen[key] = head;
      else if (seen[key] !== head) seen[key] = null;
    });
    var out = {};
    Object.keys(seen).forEach(function (k) { out[k] = seen[k] || k || "Χωρίς κάμερα"; });
    return out;
  }

  function slotName(p) {
    var s = String(p.name);
    var i = s.indexOf(" · ");
    return i < 0 ? s : s.slice(i + 3);
  }

  function threshold(p) { return p.low_threshold != null && p.low_threshold > 0 ? p.low_threshold : null; }

  /* level bar: full at 4 times the limit, the limit mark stays at 25% */
  function level(p) {
    var thr = threshold(p);
    if (thr == null || p.on_hand == null) return null;
    var bar = el("span", "rf-lv" + (p.low ? " lo" : ""));
    bar.setAttribute("role", "img");
    bar.setAttribute("aria-label", p.on_hand + " " + pl(p.on_hand, "κομμάτι", "κομμάτια") + ", όριο " + thr);
    var fill = el("i");
    fill.style.width = Math.round(Math.min(p.on_hand / (4 * thr), 1) * 100) + "%";
    add(bar, fill, el("u"));
    return bar;
  }

  function moveText(m) { return (m.kind === "taken" ? "πήραν " : "πρόσθεσαν ") + m.qty; }

  function gap(p) {
    var thr = threshold(p);
    if (thr == null || p.on_hand == null) return "";
    var diff = p.on_hand - thr;
    if (p.low) return diff < 0 ? -diff + " κάτω από το όριο" : "στο όριο";
    return "+" + diff + " πάνω από το όριο";
  }

  function cansRow(before, left) {
    if (before == null || left == null || before < 1 || before > 3) return null;
    var row = el("div", "rf-row");
    for (var k = 0; k < before; k++) row.appendChild(el("i", k < left ? "" : "g"));
    return row;
  }

  /* ---------- hero (desktop and mobile) ---------- */
  function renderBand(d) {
    var now = new Date();
    var date = two(now.getDate()) + "/" + two(now.getMonth() + 1) + "/" + now.getFullYear();
    $("rf-kick").textContent = "Αποθήκη · " + DAYS[now.getDay()] + " " + date;
    $("rf-mkick").textContent = "Αποθήκη · " + date;

    var cams = Object.keys(cameraLabels(d)).length;
    var soon = (d.expiring || []).filter(function (x) { return x.days_left <= 1; });
    var tomorrow = soon.every(function (x) { return x.days_left === 1; }) ? "αύριο"
      : soon.every(function (x) { return x.days_left === 0; }) ? "σήμερα" : "ως αύριο";

    function tail(node) {
      if (d.low_count) add(node, el("b", null, String(d.low_count)), document.createTextNode(" σε χαμηλό"));
      else node.appendChild(document.createTextNode("Κανένα σε χαμηλό"));
      node.appendChild(document.createTextNode(", "));
      if (soon.length) add(node, el("b", null, String(soon.length)), document.createTextNode(" " + pl(soon.length, "λήγει ", "λήγουν ") + tomorrow));
      else node.appendChild(document.createTextNode("τίποτα δεν λήγει αύριο"));
      node.appendChild(document.createTextNode("."));
    }

    var lead = clear($("rf-lead"));
    var mlead = clear($("rf-mlead"));
    if (!d.total) {
      lead.textContent = "Δεν υπάρχουν θέσεις ραφιού ακόμα.";
      mlead.textContent = "Δεν υπάρχουν θέσεις ραφιού ακόμα.";
    } else {
      lead.appendChild(document.createTextNode(d.total + " " + pl(d.total, "θέση", "θέσεις") + " σε " + cams + " " + pl(cams, "κάμερα", "κάμερες") + ". "));
      tail(lead);
      if (d.latest) lead.appendChild(document.createTextNode(" Τελευταία κίνηση " + d.latest.at + "."));
      tail(mlead);
    }

    [["rf-cam-btn"], ["rf-mcam-btn"]].forEach(function (ids) {
      var btn = $(ids[0]);
      btn.hidden = !d.camera_id;
      if (d.camera_id) btn.href = camHref(d.camera_id);
    });
  }

  /* ---------- scene: what the camera saw ---------- */
  function photoBox(url, ts, cans) {
    var box = el("div", "rf-img");
    if (url) {
      var img = el("img");
      img.src = url;
      img.alt = "";
      img.loading = "lazy";
      box.appendChild(img);
    }
    if (ts) box.appendChild(el("span", "ts", ts));
    if (!url && cans) box.appendChild(cans);
    return box;
  }

  function shot(box, caption) {
    var f = el("figure", "rf-shot");
    add(f, box, caption);
    return f;
  }

  function renderScene(d, labels) {
    var l = d.latest;
    var body = clear($("rf-scene-body"));
    var posOf = null;
    if (l) d.positions.forEach(function (p) { if (p.position_id === l.position_id) posOf = p; });
    $("rf-scene-t").textContent = "Τι είδε η κάμερα" + (l && labels[l.camera_id || ""] ? " · " + labels[l.camera_id || ""] : "");
    $("rf-scene-at").textContent = l ? l.at : "";

    if (!l) {
      var solo = el("div", "rf-cb rf-solo");
      var left0 = el("div", "rf-cl");
      add(left0, el("h2", null, "Καμία κίνηση στα ράφια σήμερα"), el("p", "from", "Η κάμερα θα το δείξει εδώ μόλις δει κίνηση."));
      add(body, add(solo, left0));
      return;
    }

    var taken = l.kind === "taken";
    var thr = posOf ? threshold(posOf) : null;
    var cb = el("div", "rf-cb");
    var cl = el("div", "rf-cl");
    cl.appendChild(el("p", "from", l.position_name));
    cl.appendChild(el("h2", null, (taken ? "Πήραν " : "Πρόσθεσαν ") + l.qty + " " + l.product_name));
    var ph = el("div", "rf-ph");
    var both = !!(l.before_url && l.after_url);
    var capB = el("figcaption", null, "Πριν" + (l.before != null ? " · " + l.before + " τεμ." : ""));
    var capA = el("figcaption");
    capA.appendChild(el("b", null, "Μετά · " + moveText(l)));
    var cans = taken ? cansRow(l.before, l.left) : null;
    var cansBefore = cans ? cansRow(l.before, l.before) : null;
    add(ph,
      shot(photoBox(both ? l.before_url : null, l.before_at_full, cansBefore), capB),
      shot(photoBox(both ? l.after_url : null, l.at_full || l.at, cans), capA));
    cl.appendChild(ph);

    var np = el("div", "rf-np" + (l.low ? "" : " rf-ok"));
    np.appendChild(el("p", "rf-nl0", l.product_name));
    var nn = el("p", "rf-nn");
    if (l.before != null) add(nn, el("b", null, String(l.before)), icon("ph-bold", "arrow-right"));
    nn.appendChild(el("b", l.low ? "x" : null, l.left == null ? "—" : String(l.left)));
    np.appendChild(nn);
    np.appendChild(el("p", "rf-nl", (taken ? "μένει " : "τώρα ") + (l.left == null ? "—" : l.left) + (thr != null ? " · όριο " + thr : "")));
    if (l.low) {
      var strip = el("div", "rf-low rf-base");
      add(strip, icon("ph-fill", "warning-circle"), el("span", null, "Σε χαμηλό" + (thr != null ? " · όριο " + thr : "")));
      np.appendChild(strip);
    }
    add(body, add(cb, cl, np));

    var ft = el("div", "sft");
    ft.appendChild(icon("ph", "check-circle"));
    ft.appendChild(el("span", "tx", l.low
      ? l.product_name + " σε χαμηλό. Ο συνεργάτης βλέπει αν θέλει παραγγελία."
      : l.product_name + " σε επαρκές απόθεμα."));
    if (l.low) {
      var a = el("a", null, "Παραγγελίες ");
      a.href = "/";
      a.appendChild(icon("ph", "arrow-right"));
      ft.appendChild(a);
    }
    body.appendChild(ft);
  }

  function renderMobileScene(d, labels) {
    var l = d.latest;
    var a = clear($("rf-mcam"));
    a.href = camHref(l ? l.camera_id : d.camera_id);
    var th = el("span", "rf-mth");
    var t = el("span", "t");
    if (l) {
      if (l.after_url) {
        var img = el("img");
        img.src = l.after_url;
        img.alt = "";
        th.appendChild(img);
      } else if (l.kind === "taken") {
        var row = cansRow(l.before, l.left);
        if (row) th.appendChild(row);
      }
      var mx = el("span", "rf-mx");
      if (l.before != null) add(mx, el("b", null, String(l.before)), icon("ph-bold", "arrow-right"));
      mx.appendChild(el("b", l.low ? "x" : null, l.left == null ? "—" : String(l.left)));
      if (l.low) mx.appendChild(el("em", null, "σε χαμηλό"));
      add(t, el("span", null, (labels[l.camera_id || ""] || "Ράφι") + " · " + l.at),
        el("b", null, (l.kind === "taken" ? "Πήραν " : "Πρόσθεσαν ") + l.qty + " " + l.product_name), mx);
    } else {
      add(t, el("span", null, "Ράφι"), el("b", null, "Καμία κίνηση στα ράφια σήμερα"));
    }
    var go = el("span", "go");
    go.setAttribute("aria-label", "Φωτογραφίες");
    go.appendChild(icon("ph", "caret-right"));
    add(a, th, t, go);
  }

  /* ---------- low list ---------- */
  function renderLow(d) {
    var low = d.positions.filter(function (p) { return p.low; });
    var txt = low.length + " από " + d.total;
    $("rf-low-n").textContent = txt;
    $("rf-mlow-n").textContent = txt;
    var body = clear($("rf-low-body"));
    var mbody = clear($("rf-mlow-body"));
    if (!low.length) {
      body.appendChild(note(d.total ? "Κανένα σε χαμηλό. Όλες οι θέσεις είναι πάνω από το όριο." : "Δεν υπάρχουν θέσεις ραφιού ακόμα."));
      mbody.appendChild(note(d.total ? "Κανένα σε χαμηλό. Όλες οι θέσεις είναι πάνω από το όριο." : "Δεν υπάρχουν θέσεις ραφιού ακόμα."));
      return;
    }
    low.forEach(function (p) {
      var thr = threshold(p);
      var mv = p.last ? "πήραν " + p.last.qty + " στις " + p.last.at : (gap(p) || "καμία κίνηση σήμερα");
      if (p.last && p.last.kind === "added") mv = "πρόσθεσαν " + p.last.qty + " στις " + p.last.at;

      var row = el("div", "rf-lr");
      var t = el("div", "t");
      var sub = el("span", null, p.name + " · ");
      sub.appendChild(el("em", null, mv));
      add(t, el("b", null, p.product_name), sub);
      var v = el("p", "v");
      v.appendChild(el("b", null, p.on_hand == null ? "—" : String(p.on_hand)));
      if (thr != null) v.appendChild(el("small", null, "όριο " + thr));
      add(row, t, v, level(p));
      body.appendChild(row);

      var m = el("div", "rf-mr");
      var a = el("div", "a");
      add(a, el("b", null, p.product_name), el("b", "v x", p.on_hand == null ? "—" : String(p.on_hand)));
      var s = el("span", "s", p.name);
      if (p.last) { s.appendChild(document.createTextNode(" · ")); s.appendChild(el("em", null, mv)); }
      if (thr != null) s.appendChild(document.createTextNode(" · όριο " + thr));
      add(m, a, level(p), s);
      mbody.appendChild(m);
    });
  }

  /* ---------- all positions, grouped by camera ---------- */
  function groupsOf(d, labels) {
    var order = [];
    var by = {};
    d.positions.forEach(function (p) {
      var id = p.camera_id || "";
      if (!by[id]) {
        by[id] = { id: id, label: labels[id], rows: [], lowN: 0, lastAt: null, lastId: -1 };
        order.push(by[id]);
      }
      var g = by[id];
      g.rows.push(p);
      if (p.low) g.lowN++;
      if (p.last && p.last.id > g.lastId) { g.lastId = p.last.id; g.lastAt = p.last.at; }
    });
    return order;
  }

  function toggleOpen(id) {
    opened[id] = !opened[id];
    renderPositions(last);
  }

  function opener(g, text) {
    var a = el("a", null, text);
    a.href = "#";
    a.setAttribute("role", "button");
    a.setAttribute("aria-expanded", "false");
    a.prepend(icon("ph", "caret-right"));
    a.addEventListener("click", function (e) { e.preventDefault(); toggleOpen(g.id); });
    return a;
  }

  function rowDesk(p) {
    var tr = el("div", "rf-tr" + (p.low ? " bad" : ""));
    var pc = el("span", "p");
    add(pc, el("b", null, p.product_name), el("span", null, slotName(p)));
    var bar = level(p) || el("span");
    var n = el("b", "n" + (p.low ? " x" : ""), p.on_hand == null ? "—" : String(p.on_hand));
    var mv = el("span", "mv" + (p.low ? " out" : (p.last ? "" : " ok")));
    if (p.last) {
      mv.appendChild(icon("ph-bold", p.last.kind === "taken" ? "arrow-down" : "arrow-up"));
      mv.appendChild(document.createTextNode(moveText(p.last) + " · " + p.last.at));
    } else {
      mv.textContent = gap(p);
    }
    return add(tr, pc, bar, n, mv);
  }

  function rowMob(p) {
    var m = el("div", "rf-mr" + (p.low ? " bad" : ""));
    var a = el("div", "a");
    add(a, el("b", null, p.product_name), el("b", "v" + (p.low ? " x" : ""), p.on_hand == null ? "—" : String(p.on_hand)));
    return add(m, a, level(p));
  }

  function renderPositions(d) {
    var labels = cameraLabels(d);
    var groups = groupsOf(d, labels);
    var body = clear($("rf-all-body"));
    var mob = clear($("rf-mall-body"));
    $("rf-all-n").textContent = "η κάθετη γραμμή είναι το όριο · " + d.total + " " + pl(d.total, "θέση", "θέσεις");
    if (!groups.length) {
      body.appendChild(note("Δεν υπάρχουν θέσεις ραφιού ακόμα."));
      mob.appendChild(note("Δεν υπάρχουν θέσεις ραφιού ακόμα."));
      return;
    }
    groups.forEach(function (g) {
      var n = g.rows.length;
      var count = n + " " + pl(n, "θέση", "θέσεις");
      var open = g.lowN > 0 || !!opened[g.id];

      var sub = el("div", "rf-sub");
      var subM = el("div", "rf-sub");
      if (!open) {
        sub.className = "rf-sub rf-cl";
        subM.className = "rf-sub rf-cl";
        add(sub, opener(g, g.label), el("span", null, count + " · όλες πάνω από το όριο"));
        add(subM, opener(g, g.label), el("span", null, n + " · εντάξει"));
        body.appendChild(sub);
        mob.appendChild(subM);
        return;
      }
      var head = el("span");
      add(head, icon("ph", "camera"), document.createTextNode(g.label));
      var info = count + (g.lowN ? " · " + g.lowN + " σε χαμηλό" : " · όλες πάνω από το όριο") +
        (g.lastAt ? " · κίνηση " + g.lastAt : " · καμία κίνηση σήμερα");
      add(sub, head, el("span", null, info));
      body.appendChild(sub);

      var cut = n > COLLAPSE_OVER && !showAll[g.id];
      var shown = cut ? Math.max(SHOWN_ROWS, g.lowN) : n;
      g.rows.slice(0, shown).forEach(function (p) { body.appendChild(rowDesk(p)); });
      if (n > COLLAPSE_OVER) {
        var more = el("a", "more2", cut ? "Δες και τις άλλες " + (n - shown) + " θέσεις · " + g.label + " " : "Δες λιγότερες ");
        more.href = "#";
        more.setAttribute("role", "button");
        more.setAttribute("aria-expanded", cut ? "false" : "true");
        more.appendChild(icon("ph", cut ? "arrow-right" : "arrow-up"));
        more.addEventListener("click", function (e) {
          e.preventDefault();
          showAll[g.id] = cut;
          renderPositions(last);
        });
        body.appendChild(more);
      }

      var head2 = el("span");
      add(head2, icon("ph", "camera"), document.createTextNode(g.label));
      add(subM, head2, el("span", null, n + (g.lowN ? " · " + g.lowN + " χαμηλό" : "")));
      mob.appendChild(subM);
      g.rows.forEach(function (p) { mob.appendChild(rowMob(p)); });
    });
  }

  /* ---------- expiring soon ---------- */
  function ring(days, size, r) {
    var NS = "http://www.w3.org/2000/svg";
    var c = 2 * Math.PI * r;
    var half = size / 2;
    var wrap = el("span", "ring" + (days <= 1 ? " rf-r1" : ""));
    wrap.style.width = size + "px";
    wrap.style.height = size + "px";
    wrap.setAttribute("role", "img");
    wrap.setAttribute("aria-label", days < 0 ? "έχει λήξει" : days === 0 ? "λήγει σήμερα" : "λήγει σε " + days + " " + pl(days, "μέρα", "μέρες"));
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + size + " " + size);
    svg.setAttribute("aria-hidden", "true");
    var track = document.createElementNS(NS, "circle");
    ["cx", "cy"].forEach(function (k) { track.setAttribute(k, String(half)); });
    track.setAttribute("r", String(r));
    track.setAttribute("class", "rt");
    var arc = document.createElementNS(NS, "circle");
    ["cx", "cy"].forEach(function (k) { arc.setAttribute(k, String(half)); });
    arc.setAttribute("r", String(r));
    arc.setAttribute("class", "rf");
    arc.setAttribute("stroke-dasharray", c.toFixed(1));
    arc.setAttribute("stroke-dashoffset", (c * (1 - Math.max(0.06, Math.min(days, 5) / 5))).toFixed(1));
    arc.setAttribute("transform", "rotate(-90 " + half + " " + half + ")");
    add(svg, track, arc);
    var rv = el("span", "rv");
    rv.appendChild(el("b", null, String(Math.max(days, 0))));
    add(wrap, svg, rv);
    return wrap;
  }

  function expRow(x, size, r, mobile) {
    var hot = x.days_left <= 1;
    var row = el("div", "rf-ex" + (hot ? " rf-ex1" : ""));
    var t = el("div", "t");
    var when = x.days_left < 0 ? "έχει λήξει" : (mobile ? "" : "λήγει ") + whenWord(x.days_left, x.expires_on);
    add(t, el("b", null, x.name), el("span", null, when + ", " + dm(x.expires_on)));
    var v = el("p", "v");
    add(v, el("b", null, String(x.qty)), el("small", null, "τεμ."));
    return add(row, ring(x.days_left, size, r), t, v);
  }

  function renderExpiring(d) {
    var list = d.expiring || [];
    $("rf-exp-n").textContent = list.length ? list.length + " " + pl(list.length, "παρτίδα", "παρτίδες") : "";
    var body = clear($("rf-exp-body"));
    var mob = clear($("rf-mexp-body"));
    if (!list.length) {
      body.appendChild(note("Τίποτα ως " + inThreeDays()));
      mob.appendChild(note("Τίποτα ως " + inThreeDays()));
      return;
    }
    list.forEach(function (x) {
      body.appendChild(expRow(x, 64, 26, false));
      mob.appendChild(expRow(x, 56, 22, true));
    });
  }

  /* ---------- mobile tiles ---------- */
  function renderTiles(d) {
    var cams = Object.keys(cameraLabels(d)).length;
    $("rf-t-all").textContent = String(d.total);
    $("rf-t-all-s").textContent = cams + " " + pl(cams, "κάμερα", "κάμερες");
    var list = d.expiring || [];
    $("rf-t-exp").textContent = String(list.length);
    var s = "καμία παρτίδα";
    if (list.length) {
      var w = String(list[0].name).split(" ")[0].toLowerCase();
      s = w + " " + whenWord(list[0].days_left, list[0].expires_on);
    }
    $("rf-t-exp-s").textContent = s;
  }

  /* ---------- today from the camera ---------- */
  function renderDay(d) {
    var taken = 0, added = 0, moves = 0;
    d.positions.forEach(function (p) {
      taken += p.taken_today || 0;
      added += p.added_today || 0;
      moves += (p.moves || []).length;
    });
    var body = clear($("rf-day-body"));
    [[taken, "πήραν"], [added, "πρόσθεσαν"], [moves, pl(moves, "κίνηση", "κινήσεις")]].forEach(function (c) {
      var cell = el("div");
      add(cell, el("b", null, String(c[0])), el("span", null, c[1]));
      body.appendChild(cell);
    });
    $("rf-day-last").textContent = d.latest ? "Τελευταία κίνηση " + d.latest.at : "Καμία κίνηση σήμερα";
  }

  var BUSY = ["rf-scene", "rf-low-card", "rf-all-card", "rf-exp-card", "rf-day-card"];

  function render(d) {
    last = d;
    var labels = cameraLabels(d);
    renderBand(d);
    renderScene(d, labels);
    renderMobileScene(d, labels);
    renderLow(d);
    renderPositions(d);
    renderExpiring(d);
    renderTiles(d);
    renderDay(d);
    BUSY.forEach(function (id) { $(id).setAttribute("aria-busy", "false"); });
  }

  function showError() {
    var msg = "Δεν μπόρεσα να φορτώσω το ράφι. Δοκίμασε ξανά σε λίγο.";
    ["rf-scene-body", "rf-low-body", "rf-all-body", "rf-exp-body", "rf-mlow-body", "rf-mall-body", "rf-mexp-body"].forEach(function (id) {
      clear($(id)).appendChild(note(msg, id === "rf-scene-body"));
    });
    BUSY.forEach(function (id) { $(id).setAttribute("aria-busy", "false"); });
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

  /* ---------- mobile: the tiles open their own screen (#ola, #lixeis) ---------- */
  function showScreen() {
    var map = { "#ola": "m-all", "#lixeis": "m-exp" };
    var on = map[location.hash];
    ["m-low", "m-all", "m-exp"].forEach(function (id) { $(id).hidden = on ? id !== on : id !== "m-low"; });
    $("rf-tiles").hidden = !!on;
    $("rf-mcam").hidden = !!on;
    window.scrollTo(0, 0);
  }

  /* ---------- pause and the live dot ---------- */
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

  function init() {
    $("stop-btn").addEventListener("click", togglePause);
    $("stop-ibtn").addEventListener("click", togglePause);
    window.addEventListener("hashchange", showScreen);
    showScreen();
    load();
    loadPause();
    connect();
  }

  init();
})();
