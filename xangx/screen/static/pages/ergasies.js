/* Σελίδα Εργασίες: τα δεδομένα από το /api/screen/tasks, ζωντανά από το /api/events/stream.
   Η Παύση και το «Ζωντανά» είναι του shell.js. Κάθε κείμενο μπαίνει με textContent, ποτέ ως HTML. */
(function () {
  "use strict";
  /* μέλος (key του server) → χρώμα (eg-k-…) και εικονίδιο */
  var KIND = {
    invoices: {k: "tim", icon: "receipt"},
    accountant: {k: "log", icon: "calculator"},
    orders: {k: "par", icon: "truck"},
    master: {k: "syn", icon: "circles-three"},
    stock: {k: "apo", icon: "package"},
    agent: {k: "syn", icon: "plugs-connected"}
  };
  var FALLBACK = {k: "syn", icon: "circles-three"};
  var PER_MEMBER = 3, PER_SIDE = 5;
  var timer = null, loading = false, again = false, last = null, opened = {}, shownKey = null;

  /* ασφαλές για στοιχείο που λείπει: επιστρέφει ένα αποκομμένο div */
  function $(id) { return document.getElementById(id) || document.createElement("div"); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function icon(kind, name) { return el("i", kind + " ph-" + name); }
  function kindOf(key) { return KIND[key] || FALLBACK; }
  function plural(n, one, many) { return n === 1 ? one : many; }

  /* ---------- κοινά κομμάτια ---------- */
  function mi(key) {
    var k = kindOf(key);
    var s = el("span", "eg-mi eg-k-" + k.k);
    s.appendChild(icon("ph-fill", k.icon));
    return s;
  }
  function howMark(yes) {
    var s = el("span", "eg-hw" + (yes ? " eg-y" : ""));
    var label = yes ? "με το «ναι» σου" : "μόνο του";
    s.title = label;
    s.setAttribute("role", "img");
    s.setAttribute("aria-label", label);
    s.appendChild(yes ? icon("ph-fill", "hand-pointing") : icon("ph-bold", "check"));
    return s;
  }
  function invoiceLink(task) {
    var id = task.links && task.links.invoice_id;
    if (!id) return null;
    var a = el("a", null, "τιμολόγιο ");
    a.href = "/static/pages/timologia.html?id=" + encodeURIComponent(id);
    a.appendChild(icon("ph", "arrow-right"));
    return a;
  }
  function gone(text, name) {
    var s = el("span", "eg-gone");
    s.appendChild(icon("ph", name));
    s.appendChild(document.createTextNode(text));
    return s;
  }
  /* η ενέργεια δεξιά: αναίρεση, «έφυγε», «αναιρέθηκε» ή ο σύνδεσμος του τιμολογίου */
  function action(task) {
    if (task.undo === "can" && window.xangxUndoNode) return window.xangxUndoNode("can", task.id, task.text, soon);
    if (task.undo === "sent") return gone("έφυγε", "lock-simple");
    if (task.undo === "done") return gone("αναιρέθηκε", "check");
    return invoiceLink(task);
  }
  function quiet(text) {
    var p = el("p", "eg-quiet");
    p.appendChild(icon("ph-fill", "check-circle"));
    p.appendChild(document.createTextNode(text));
    return p;
  }
  /* έως `limit` κόμβοι· οι υπόλοιποι κρύβονται πίσω από «Δες και τις άλλες N», που τους ανοίγει (και το θυμάται στις ανανεώσεις) */
  function limited(parent, nodes, limit, key) {
    var extra = opened[key] ? [] : nodes.slice(limit);
    nodes.forEach(function (n) {
      if (extra.indexOf(n) >= 0) n.hidden = true;
      parent.appendChild(n);
    });
    if (!extra.length) return;
    var wrap = el("div", "eg-more");
    var b = el("button", "eg-morebtn", "Δες και τις άλλες " + extra.length + " ");
    b.type = "button";
    b.appendChild(icon("ph", "arrow-right"));
    b.addEventListener("click", function () {
      opened[key] = true;
      extra.forEach(function (n) { n.hidden = false; });
      wrap.remove();
    });
    wrap.appendChild(b);
    parent.appendChild(wrap);
  }
  function failBox() {
    var box = el("div", "eg-fail");
    box.appendChild(el("p", null, "Δεν μπόρεσα να φορτώσω τις εργασίες."));
    var retry = el("button", "btn q", "Ξαναδοκίμασε");
    retry.type = "button";
    retry.addEventListener("click", load);
    box.appendChild(retry);
    return box;
  }

  /* ---------- σκηνή: «auto από done», και δίπλα τα «ναι» και τα λάθη ---------- */
  function stage(p, nums, short) {
    var n = $(p + "-n");
    n.className = "eg-n" + (nums.done ? "" : " eg-n0");
    if (nums.done) {
      n.replaceChildren(el("b", null, String(nums.auto)), el("span", null, "από " + nums.done));
    } else {
      n.replaceChildren(el("b", null, "Τίποτα ακόμα σήμερα"));
    }
    $(p + "-nl").textContent = nums.done
      ? (short ? "έγιναν μόνες τους" : "έγιναν μόνες τους, χωρίς να σε ρωτήσουν")
      : "Ό,τι κάνει ο συνεργάτης θα φανεί εδώ.";
    var bar = $(p + "-bar");
    bar.replaceChildren();
    bar.hidden = !nums.done;
    if (nums.done) {
      bar.setAttribute("aria-label", nums.auto + " μόνες τους, " + nums.yes + " με το «ναι» σου");
      if (nums.auto) {
        var a = el("i", null, short ? "μόνες τους" : nums.auto + " μόνες τους");
        a.style.flex = String(nums.auto);
        bar.appendChild(a);
      }
      if (nums.yes) {
        var y = el("u", null, String(nums.yes));
        y.style.flex = String(nums.yes);
        bar.appendChild(y);
      }
    }
  }

  /* ---------- δεξιά στήλη και κινητό: μέλη, αναιρούνται, προσοχή ---------- */
  function memberBars(groups) {
    var host = $("eg-members");
    host.replaceChildren();
    $("eg-members-n").textContent = String(groups.reduce(function (s, g) { return s + g.count; }, 0));
    var max = groups.reduce(function (m, g) { return Math.max(m, g.count); }, 1);
    if (!groups.length) {
      var none = quiet("Κανένα μέλος ακόμα");
      none.className += " eg-qd";
      host.appendChild(none);
    }
    groups.forEach(function (g) {
      var row = el("div", "eg-m eg-k-" + kindOf(g.key).k);
      var bar = el("span", "eg-mbar");
      var fill = el("i");
      fill.style.width = Math.round(g.count / max * 100) + "%";
      bar.appendChild(fill);
      row.append(el("span", null, g.name), bar, el("b", null, String(g.count)));
      host.appendChild(row);
    });
  }
  function undoRow(item, withIcon) {
    var t = item.task;
    var row = el("div", "eg-un eg-k-" + kindOf(item.group.key).k);
    if (withIcon) row.appendChild(mi(item.group.key));
    var ux = el("span", "eg-ux");
    ux.appendChild(el("b", null, t.text));
    ux.appendChild(el("span", null, item.group.name + " · " + t.at));
    row.appendChild(ux);
    row.appendChild(window.xangxUndoNode("can", t.id, t.text, soon));
    return row;
  }
  function undoList(body, count, items, withIcon, key) {
    body.replaceChildren();
    count.textContent = String(items.length);
    if (!items.length) { body.appendChild(quiet("Τίποτα για αναίρεση")); return; }
    limited(body, items.map(function (it) { return undoRow(it, withIcon); }), PER_SIDE, key);
  }
  function warnRow(item, mobile) {
    var t = item.task;
    var row = el("div", "eg-wr" + (mobile ? " eg-mw" : ""));
    var wi = el("span", "eg-wi");
    wi.appendChild(icon("ph-fill", "warning"));
    var ux = el("span", "eg-ux");
    ux.appendChild(el("b", null, t.text));
    ux.appendChild(el("span", null, item.group.name + " · " + t.at));
    row.append(wi, ux);
    var link = invoiceLink(t);
    if (link) row.appendChild(link);
    return row;
  }

  /* ---------- «Τι έγινε»: μια λωρίδα ανά μέλος, έως 3 γραμμές ---------- */
  function deskRow(t) {
    var yes = t.how === "yes";
    var tr = el("div", "eg-tr" + (yes ? " eg-yes" : "") + (t.warn ? " eg-bad" : ""));
    tr.appendChild(el("b", "eg-t", t.at));
    tr.appendChild(howMark(yes));
    var x = el("span", "eg-x", t.text);
    if (yes) x.appendChild(el("b", "eg-yl", "με το «ναι» σου"));
    tr.appendChild(x);
    var a = el("span", "eg-a");
    var act = action(t);
    if (act) a.appendChild(act);
    tr.appendChild(a);
    return tr;
  }
  function subBar(g) {
    var sub = el("div", "eg-sub");
    var left = el("span");
    left.appendChild(mi(g.key));
    left.appendChild(document.createTextNode(g.name));
    sub.append(left, el("b", null, String(g.count)));
    return sub;
  }
  function groupList(groups) {
    var host = $("eg-groups");
    host.replaceChildren();
    if (!groups.length) {
      host.appendChild(el("p", "eg-empty", "Δεν έχει γίνει καμία εργασία σήμερα. Ό,τι κάνει ο συνεργάτης θα εμφανιστεί εδώ."));
      return;
    }
    groups.forEach(function (g) {
      var box = el("div", "eg-grp eg-k-" + kindOf(g.key).k);
      box.dataset.key = g.key;
      box.appendChild(subBar(g));
      g.tasks.map(deskRow).forEach(function (r, i) {
        r.hidden = !opened[g.key] && i >= PER_MEMBER;
        box.appendChild(r);
      });
      balanceBox(box);
      host.appendChild(box);
    });
  }

  /* Ισορροπία των δύο στηλών (μόνο στη σελίδα με δύο στήλες): όσο η λίστα «Τι έγινε» είναι ψηλότερη
     από τη δεξιά στήλη κατά ≥ TOL px, κρύβεται η τελευταία ορατή γραμμή του μέλους με τις περισσότερες,
     και το «Δες και τις άλλες N» του μέλους τις ανοίγει. Μέλος που το άνοιξε ο χρήστης δεν κρύβεται ξανά. */
  var TOL = 56;
  function sideBySide() {
    var l = $("eg-list"), s = document.querySelector(".eg-g .stack");
    return !!(s && l.offsetHeight && s.offsetHeight && Math.abs(l.getBoundingClientRect().top - s.getBoundingClientRect().top) < 8);
  }
  function gap() {
    return $("eg-list").offsetHeight - document.querySelector(".eg-g .stack").offsetHeight;
  }
  function rowsOf(box) {
    return [].slice.call(box.children).filter(function (c) { return c.classList.contains("eg-tr"); });
  }
  function balanceBox(box) {
    var hid = rowsOf(box).filter(function (r) { return r.hidden; });
    var more = box.querySelector(".eg-more");
    if (!hid.length) { if (more) more.remove(); return; }
    if (!more) {
      more = el("div", "eg-more");
      var b = el("button", "eg-morebtn");
      b.type = "button";
      b.addEventListener("click", function () {
        opened[box.dataset.key] = true;
        rowsOf(box).forEach(function (r) { r.hidden = false; });
        more.remove();
      });
      more.appendChild(b);
      box.appendChild(more);
    }
    more.firstChild.replaceChildren(document.createTextNode("Δες και τις άλλες " + hid.length + " "), icon("ph", "arrow-right"));
  }
  function balance() {
    var boxes = [].slice.call(document.querySelectorAll("#eg-groups .eg-grp"));
    boxes.forEach(function (box) {
      [].slice.call(box.querySelectorAll(".eg-tr[data-bal]")).forEach(function (r) { r.hidden = false; delete r.dataset.bal; });
      balanceBox(box);
    });
    if (!sideBySide()) return;
    var guard = 200;
    while (gap() >= TOL && guard-- > 0) {
      /* πρώτα μένει τουλάχιστον μία γραμμή ανά μέλος· μόνο αν δεν φτάνει, κρύβονται και οι τελευταίες */
      var best = null, bestN = 1;
      for (var floor = 1; floor >= 0 && !best; floor--) {
        bestN = floor;
        boxes.forEach(function (box) {
          if (opened[box.dataset.key]) return;
          var vis = rowsOf(box).filter(function (r) { return !r.hidden; });
          if (vis.length > bestN) { best = {box: box, last: vis[vis.length - 1]}; bestN = vis.length; }
        });
      }
      if (!best) break;
      best.last.hidden = true;
      best.last.dataset.bal = "1";
      balanceBox(best.box);
      if (gap() < -TOL) {
        best.last.hidden = false;
        delete best.last.dataset.bal;
        balanceBox(best.box);
        break;
      }
    }
  }

  /* ---------- κινητό: πλακίδια μελών και οθόνη μέλους ---------- */
  function tiles(groups) {
    var host = $("egm-tiles");
    host.replaceChildren();
    groups.forEach(function (g) {
      var a = el("a", "eg-tile eg-k-" + kindOf(g.key).k);
      a.href = "#" + encodeURIComponent(g.key);
      a.append(mi(g.key), el("b", null, String(g.count)), el("span", null, g.name));
      host.appendChild(a);
    });
  }
  function memberRow(t) {
    var yes = t.how === "yes";
    var row = el("div", "eg-mr" + (yes ? " eg-yes" : "") + (t.warn ? " eg-bad" : ""));
    row.appendChild(el("b", "eg-t", t.at));
    var x = el("span", "eg-x", t.text);
    var em = el("em", yes ? "" : "eg-a2");
    em.appendChild(yes ? icon("ph-fill", "hand-pointing") : icon("ph", "check"));
    em.appendChild(document.createTextNode(yes ? "με το «ναι» σου" : "μόνο του"));
    var foot = el("div", "eg-mf");
    foot.appendChild(em);
    var act = action(t);
    if (act) foot.appendChild(act);
    x.appendChild(foot);
    row.appendChild(x);
    return row;
  }
  function memberScreen(g) {
    var card = $("egm-mem");
    card.className = "card eg-k-" + kindOf(g.key).k;
    card.replaceChildren(subBar(g));
    g.tasks.forEach(function (t) { card.appendChild(memberRow(t)); });
  }
  function memberKey() {
    try { return decodeURIComponent(location.hash.slice(1)); } catch (e) { return ""; }
  }
  /* #<μέλος> ανοίγει την οθόνη του· χωρίς hash, ή με άγνωστο, φαίνεται η κύρια */
  function show() {
    var key = memberKey(), g = null;
    if (last && key) {
      last.groups.forEach(function (x) { if (x.key === key) g = x; });
    }
    var now = g ? g.key : null;
    if (g) memberScreen(g);
    $("eg-pmain").hidden = !!g;
    $("eg-pmem").hidden = !g;
    if (now !== shownKey) { shownKey = now; window.scrollTo(0, 0); }
  }

  /* ---------- σχεδίαση ---------- */
  function render(data) {
    last = data;
    var groups = data.groups || [], tot = data.totals || {}, day = data.day || {};
    var done = tot.done || 0, yes = tot.with_yes || 0, errs = tot.errors || 0;
    var nums = {done: done, yes: yes, auto: Math.max(0, done - yes)};
    var stamp = [day.weekday, day.date].filter(Boolean).join(" ");
    $("eg-kick").textContent = stamp + (day.now ? " · " + day.now : "") || "Εργασίες";
    $("egm-kick").textContent = (day.date || "") + (day.now ? " · " + day.now : "") || "Εργασίες";
    $("eg-shd-t").textContent = "Σήμερα" + (day.now ? " ως τις " + day.now : "");
    $("eg-shd-c").textContent = done ? done + " " + plural(done, "εργασία", "εργασίες") : "Καμία εργασία";

    stage("eg", nums, false);
    stage("egm", nums, true);
    $("eg-yes").querySelector("b").textContent = String(yes);
    var err = $("eg-err");
    err.className = "eg-big " + (errs ? "eg-bad" : "eg-ok");
    err.replaceChildren(icon("ph-fill", errs ? "warning-circle" : "check-circle"), el("b", null, String(errs)), el("span", null, "λάθη"));
    $("egm-yes").querySelector("b").textContent = String(yes);
    var merr = $("egm-err");
    merr.className = errs ? "eg-bad" : "";
    merr.replaceChildren(icon("ph-fill", errs ? "warning-circle" : "check-circle"), el("b", null, String(errs)), document.createTextNode(" λάθη"));

    var all = [];
    groups.forEach(function (g) { g.tasks.forEach(function (t) { all.push({task: t, group: g}); }); });
    all.sort(function (a, b) { return b.task.id - a.task.id; });
    var undos = all.filter(function (x) { return x.task.undo === "can" && window.xangxUndoNode; });
    var warns = all.filter(function (x) { return x.task.warn; });

    groupList(groups);
    memberBars(groups);
    undoList($("eg-undo-body"), $("eg-undo-n"), undos, true, "_undo");
    var wbody = $("eg-warn-body");
    wbody.replaceChildren();
    $("eg-warn-n").textContent = warns.length ? warns.length + " · " + errs + " " + plural(errs, "λάθος", "λάθη") : "0 · " + errs + " λάθη";
    if (!warns.length) wbody.appendChild(quiet("Καμία σήμερα"));
    else limited(wbody, warns.map(function (x) { return warnRow(x, false); }), PER_SIDE, "_warn");

    $("egm-fail").replaceChildren();
    var mw = $("egm-warn");
    mw.replaceChildren();
    limited(mw, warns.map(function (x) { return warnRow(x, true); }), PER_SIDE, "_mwarn");
    tiles(groups);
    undoList($("egm-undo-body"), $("egm-undo-n"), undos, false, "_mundo");
    show();
    balance();
    $("eg-stage").setAttribute("aria-busy", "false");
    $("eg-list").setAttribute("aria-busy", "false");
  }

  function failed() {
    $("eg-groups").replaceChildren(failBox());
    $("egm-fail").replaceChildren(failBox());
    $("eg-stage").setAttribute("aria-busy", "false");
    $("eg-list").setAttribute("aria-busy", "false");
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
    es.onmessage = soon;
  }

  function init() {
    window.addEventListener("hashchange", show);
    var rt = null;
    window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(balance, 120); });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(balance);
    load();
    listen();
  }
  init();
})();
