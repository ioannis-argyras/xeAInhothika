"use strict";
/* Το ίδιο πλαίσιο σε κάθε σελίδα (κοινό στυλ Α v2): σκούρο μενού αριστερά στον υπολογιστή, πάνω μπάρα και
   κάτω μπάρα 5 καρτελών με «Περισσότερα» στο κινητό, και το μπάνερ παύσης.
   Η σελίδα γράφει <body data-page="<κλειδί>"> και βάζει το περιεχόμενό της μέσα σε <main class="main">.
   Όλο το κείμενο εδώ είναι σταθερό και μπαίνει με textContent· τίποτα από τον server δεν γίνεται HTML. */
(function () {
  /* ---------- ο πίνακας των συνδέσμων: εδώ προστίθεται μια νέα σελίδα στο μενού ----------
     key: το data-page της σελίδας · name: ελληνικό όνομα · url · icon: όνομα Phosphor (χωρίς το «ph-») · group: ομάδα του μενού.
     Σελίδα που δεν υπάρχει ακόμα δεν μπαίνει. */
  var NAV = [
    { key: "arxiki", name: "Αρχική", url: "/", icon: "house", group: "Σήμερα" },
    { key: "synomilia", name: "Συνομιλία", url: "/#synomilia", icon: "chat-circle-text", group: "Σήμερα" },
    { key: "ergasies", name: "Εργασίες", url: "/static/pages/ergasies.html", icon: "list-checks", group: "Σήμερα" },
    { key: "rafi", name: "Ράφι", url: "/static/pages/rafi.html", icon: "storefront", group: "Μαγαζί" },
    { key: "paralavi", name: "Παραλαβή", url: "/stock/scan", icon: "barcode", group: "Μαγαζί" },
    { key: "kamera", name: "Κάμερα ραφιού", url: "/stock/camera", icon: "camera", group: "Μαγαζί" },
    { key: "timologia", name: "Τιμολόγια", url: "/static/pages/timologia.html", icon: "receipt", group: "Χαρτιά" },
    { key: "mixani", name: "Ιστορικό", url: "/static/pages/mixani.html", icon: "clock-counter-clockwise", group: "Η μέρα" }
  ];
  /* Οι 5 καρτέλες του κινητού: τρεις από τον πίνακα, η στρογγυλή Φωτογραφία στη μέση, και το «Περισσότερα». */
  var TAB_LEFT = [
    { key: "arxiki", label: "Σήμερα", url: "/", icon: "house" },
    { key: "timologia", label: "Τιμολόγια", url: "/static/pages/timologia.html", icon: "receipt" }
  ];
  var TAB_CAMERA = { label: "Φωτογραφία", aria: "Ανέβασε τιμολόγιο", url: "/invoices/photo", icon: "camera" };
  var TAB_RIGHT = { key: "rafi", label: "Ράφι", url: "/static/pages/rafi.html", icon: "storefront" };
  var BRAND = { name: "xeAInhothika", sub: "Mini Market 24/7, Παγκράτι" };

  var paused = false;
  var lastFocus = null;
  var more = null;

  function h(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }
  function icon(name, extra) {
    var i = h("i", "ph ph-" + name + (extra ? " " + extra : ""));
    i.setAttribute("aria-hidden", "true");
    return i;
  }
  function link(cls, url) {
    var a = h("a", cls);
    a.setAttribute("href", url);
    return a;
  }
  function span(cls, text) { return h("span", cls, text); }

  /* Το κουμπί Παύσης: δύο εικονίδια και δύο κείμενα, που δείχνει το body.paused. */
  function pauseContent(btn, withText) {
    btn.appendChild(icon("pause", "if-run"));
    btn.appendChild(icon("play", "if-paused"));
    if (withText) {
      btn.appendChild(span("if-run", "Παύση όλων"));
      btn.appendChild(span("if-paused", "Συνέχεια"));
    }
  }
  function pauseButton(cls, withText) {
    var b = h("button", cls);
    b.setAttribute("type", "button");
    b.setAttribute("data-pause", "");
    pauseContent(b, withText);
    return b;
  }
  function liveLine() {
    var live = h("span", "livel");
    live.appendChild(span("dot if-run"));
    live.appendChild(span("if-run js-live-text", "Ζωντανά"));
    live.appendChild(span("if-paused", "Σε παύση"));
    return live;
  }

  function pageTitle(key) {
    var attr = document.body.getAttribute("data-title");
    if (attr) return attr;
    for (var i = 0; i < NAV.length; i++) if (NAV[i].key === key) return NAV[i].name;
    return document.title || BRAND.name;
  }

  /* ---------- υπολογιστής: σκούρο μενού ---------- */
  function buildSide(key) {
    var side = h("aside", "side");
    side.setAttribute("aria-label", "Μενού");
    var inner = h("div", "in");
    var brand = h("div", "brand");
    var wm = h("b", "wm");
    wm.appendChild(document.createTextNode("xe"));
    var em = h("em");
    em.appendChild(document.createTextNode("A"));
    em.appendChild(h("span", "ii", "I"));
    wm.appendChild(em);
    wm.appendChild(document.createTextNode("nhothika"));
    brand.appendChild(wm);
    brand.appendChild(span(null, BRAND.sub));
    inner.appendChild(brand);
    var nav = h("nav");
    var groups = [];
    NAV.forEach(function (item) {
      var grp = null;
      for (var g = 0; g < groups.length; g++) if (groups[g].name === item.group) grp = groups[g];
      if (!grp) {
        var box = h("div", "grp");
        box.appendChild(span(null, item.group));
        nav.appendChild(box);
        grp = { name: item.group, box: box };
        groups.push(grp);
      }
      grp.box.appendChild(navLink(item, key));
    });
    inner.appendChild(nav);
    var foot = h("div", "foot");
    foot.appendChild(liveLine());
    foot.appendChild(pauseButton("pbtn", true));
    inner.appendChild(foot);
    side.appendChild(inner);
    return side;
  }
  function navLink(item, key) {
    var a = link("nv" + (item.key === key ? " on" : ""), item.url);
    if (item.key === key) a.setAttribute("aria-current", "page");
    a.appendChild(icon(item.icon));
    a.appendChild(document.createTextNode(item.name));
    return a;
  }

  /* ---------- κινητό: πάνω μπάρα, κάτω μπάρα, «Περισσότερα» ---------- */
  function buildTop(key) {
    var top = h("header", "mtop");
    top.appendChild(h("b", null, pageTitle(key)));
    top.appendChild(liveLine());
    var btn = pauseButton("ibtn", false);
    btn.setAttribute("aria-label", "Παύση όλων ή συνέχεια");
    top.appendChild(btn);
    return top;
  }
  function pauseBar() {
    var bar = h("div", "pausebar");
    bar.setAttribute("role", "status");
    bar.appendChild(icon("pause-circle"));
    var text = h("span");
    text.appendChild(h("b", null, "Όλοι σε παύση."));
    text.appendChild(document.createTextNode(" Τίποτα δεν γίνεται μόνο του μέχρι να πατήσεις «Συνέχεια»."));
    bar.appendChild(text);
    var btn = h("button", "btn");
    btn.setAttribute("type", "button");
    btn.setAttribute("data-pause", "");
    btn.appendChild(icon("play"));
    btn.appendChild(document.createTextNode("Συνέχεια"));
    bar.appendChild(btn);
    return bar;
  }
  function tabLink(tab, key, extra) {
    var a = link("tb" + (extra ? " " + extra : "") + (tab.key && tab.key === key ? " on" : ""), tab.url);
    if (tab.key && tab.key === key) a.setAttribute("aria-current", "page");
    if (tab.aria) a.setAttribute("aria-label", tab.aria);
    return a;
  }
  function buildTabbar(key) {
    var nav = h("nav", "tabbar");
    nav.setAttribute("aria-label", "Καρτέλες");
    TAB_LEFT.forEach(function (tab) {
      var a = tabLink(tab, key);
      a.appendChild(icon(tab.icon));
      a.appendChild(document.createTextNode(tab.label));
      nav.appendChild(a);
    });
    var cam = tabLink(TAB_CAMERA, key, "cam");
    var circle = h("span", "c");
    circle.appendChild(icon(TAB_CAMERA.icon));
    cam.appendChild(circle);
    cam.appendChild(document.createTextNode(TAB_CAMERA.label));
    nav.appendChild(cam);
    var right = tabLink(TAB_RIGHT, key);
    right.appendChild(icon(TAB_RIGHT.icon));
    right.appendChild(document.createTextNode(TAB_RIGHT.label));
    nav.appendChild(right);
    var btn = h("button", "tb");
    btn.setAttribute("type", "button");
    btn.setAttribute("data-more", "");
    btn.setAttribute("aria-haspopup", "dialog");
    btn.appendChild(icon("dots-three-outline"));
    btn.appendChild(document.createTextNode("Περισσότερα"));
    nav.appendChild(btn);
    return nav;
  }
  /* Στο «Περισσότερα» μπαίνουν οι σελίδες που δεν είναι στις καρτέλες. */
  function buildMore(key) {
    var inTabs = {};
    TAB_LEFT.concat([TAB_RIGHT]).forEach(function (t) { inTabs[t.key] = true; });
    var box = h("div", "more");
    box.setAttribute("id", "more");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-labelledby", "more-h");
    var bd = h("div", "bd");
    bd.setAttribute("data-close", "");
    box.appendChild(bd);
    var sh = h("div", "sh");
    var shh = h("div", "shh");
    var title = h("b", null, "Περισσότερα");
    title.setAttribute("id", "more-h");
    shh.appendChild(title);
    var close = h("button", "ibtn");
    close.setAttribute("type", "button");
    close.setAttribute("data-close", "");
    close.setAttribute("aria-label", "Κλείσε");
    close.appendChild(icon("x"));
    shh.appendChild(close);
    sh.appendChild(shh);
    NAV.forEach(function (item) {
      if (!inTabs[item.key]) sh.appendChild(navLink(item, key));
    });
    sh.appendChild(pauseButton("pbtn", true));
    box.appendChild(sh);
    return box;
  }
  function openMore() {
    lastFocus = document.activeElement;
    more.classList.add("open");
    var close = more.querySelector("button[data-close]");
    if (close) close.focus();
  }
  function closeMore() {
    if (!more || !more.classList.contains("open")) return;
    more.classList.remove("open");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  /* ---------- παύση: τα ίδια /api/pause και /api/resume με το app.js ---------- */
  function applyState(state) {
    paused = !!(state && state.paused);
    document.body.classList.toggle("paused", paused);
    var texts = document.querySelectorAll(".js-live-text");
    var now = state && state.day && state.day.now;
    for (var i = 0; i < texts.length; i++) texts[i].textContent = "Ζωντανά" + (now ? " · " + now : "");
  }
  function offline() {
    var texts = document.querySelectorAll(".js-live-text");
    for (var i = 0; i < texts.length; i++) texts[i].textContent = "Ξανασυνδέομαι…";
  }
  function refresh() {
    return fetch("/api/state", { cache: "no-store" })
      .then(function (res) { return res.json(); })
      .then(applyState)
      .catch(offline);
  }
  function togglePause() {
    return fetch(paused ? "/api/resume" : "/api/pause", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    }).catch(function () { /* η ανανέωση από κάτω δείχνει την πραγματική κατάσταση */ }).then(refresh);
  }

  function init() {
    var body = document.body;
    var key = body.getAttribute("data-page");
    if (!key) return;
    var main = document.querySelector(".main");
    if (!main) {
      main = h("main", "main");
      Array.prototype.slice.call(body.childNodes).forEach(function (node) {
        if (!(node.nodeName === "SCRIPT")) main.appendChild(node);
      });
      body.insertBefore(main, body.firstChild);
    }
    body.insertBefore(buildSide(key), main);
    main.insertBefore(pauseBar(), main.firstChild);
    main.insertBefore(buildTop(key), main.firstChild);
    body.appendChild(buildTabbar(key));
    more = buildMore(key);
    body.appendChild(more);

    document.addEventListener("click", function (e) {
      var t = e.target.closest("[data-pause],[data-more],[data-close]");
      if (!t) return;
      if (t.hasAttribute("data-pause")) togglePause();
      else if (t.hasAttribute("data-more")) openMore();
      else closeMore();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeMore();
    });
    refresh();
    setInterval(function () { if (!document.hidden) refresh(); }, 5000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
