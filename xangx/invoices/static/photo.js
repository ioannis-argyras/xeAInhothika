/* Φωτογραφία τιμολογίου: διάλεξε αρχείο, στείλε το, και δες το αποτέλεσμα. Κάθε κείμενο μπαίνει με textContent. */
(function () {
  "use strict";
  var MAX = 10 * 1024 * 1024, POLL_MS = 2000, GIVE_UP_MS = 90000;
  var file = null, previewUrl = null, sending = false, timer = null;
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function err(msg) {
    ["pc-err", "m-err"].forEach(function (id) { $(id).textContent = msg || ""; $(id).hidden = !msg; });
  }

  function setFile(f) {
    err("");
    if (!f) return;
    var okType = /^image\//.test(f.type) || f.type === "application/pdf";
    if (!okType) { err("Διάλεξε φωτογραφία ή PDF."); return; }
    if (f.size > MAX) { err("Το αρχείο πρέπει να είναι έως 10 MB."); return; }
    file = f;
    if (previewUrl) { URL.revokeObjectURL(previewUrl); previewUrl = null; }
    var isPdf = f.type === "application/pdf";
    $("shot").hidden = false;
    $("pv").hidden = isPdf;
    $("pdf").hidden = !isPdf;
    if (isPdf) { $("pdf-name").textContent = f.name; }
    else { previewUrl = URL.createObjectURL(f); $("pv").src = previewUrl; }
    $("cam").hidden = true; $("gal").hidden = true;
    $("send").hidden = false; $("pair").hidden = false;
    $("fname").textContent = f.name; $("fname").hidden = false;
    $("drop-hint").hidden = true; $("pc-pick").hidden = true; $("pc-send").hidden = false;
  }

  function pick(input) { input.value = ""; input.click(); }
  $("cam").addEventListener("click", function () { pick($("in-cam")); });
  $("again").addEventListener("click", function () { pick($("in-cam")); });
  $("gal").addEventListener("click", function () { pick($("in-gal")); });
  $("gal2").addEventListener("click", function () { pick($("in-gal")); });
  $("pc-pick").addEventListener("click", function () { pick($("in-gal")); });
  $("in-cam").addEventListener("change", function () { setFile(this.files[0]); });
  $("in-gal").addEventListener("change", function () { setFile(this.files[0]); });

  var drop = $("drop");
  ["dragenter", "dragover"].forEach(function (t) {
    drop.addEventListener(t, function (e) { e.preventDefault(); });
  });
  drop.addEventListener("drop", function (e) {
    e.preventDefault();
    if (e.dataTransfer && e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
  });

  function lock(on) {
    sending = on;
    ["send", "pc-send", "again", "gal2", "pc-pick"].forEach(function (id) { $(id).disabled = on; });
  }

  function send() {
    if (!file || sending) return;
    lock(true); err("");
    fetch("/invoices/photo", { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "Δεν στάλθηκε. Δοκίμασε ξανά.");
          return j;
        });
      })
      .then(function (j) { startResult(j.invoice_id); })
      .catch(function (e) { err(e.message || "Δεν στάλθηκε. Δοκίμασε ξανά."); lock(false); });
  }
  $("send").addEventListener("click", send);
  $("pc-send").addEventListener("click", send);

  function hhmm() {
    var d = new Date();
    return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }
  function check(ok, text, boldLead) {
    var row = el("div", "ck" + (ok ? "" : " x"));
    row.appendChild(el("i", ok ? "ph-fill ph-check-circle" : "ph-fill ph-warning-circle"));
    var s = el("span");
    if (boldLead) { s.appendChild(el("b", null, boldLead)); s.appendChild(document.createTextNode(" ")); }
    s.appendChild(document.createTextNode(text));
    row.appendChild(s);
    return row;
  }
  function setTitle(icon, color, text) {
    var b = $("res-title");
    b.textContent = "";
    var i = el("i", icon);
    if (color) i.style.color = color;
    b.appendChild(i);
    b.appendChild(document.createTextNode(text));
  }

  function startResult(id) {
    $("m-col").hidden = true;
    $("res").hidden = false; $("res").setAttribute("aria-busy", "true");
    $("pc-send").hidden = true; $("pc-pick").hidden = false;
    $("fname").hidden = true; $("drop-hint").hidden = false;
    file = null; lock(false);
    $("res-at").textContent = "Στάλθηκε " + hhmm();
    setTitle("ph ph-hourglass", null, "Διαβάζεται…");
    $("res-link").href = "/static/pages/timologia.html?id=" + encodeURIComponent(id);
    var started = Date.now();
    if (timer) clearInterval(timer);
    function tick() {
      fetch("/invoices/photo/status?invoice_id=" + encodeURIComponent(id))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (s) {
          if (s) render(s);
          if (s && s.done) { clearInterval(timer); $("res").setAttribute("aria-busy", "false"); return; }
          if (Date.now() - started > GIVE_UP_MS) {
            clearInterval(timer);
            $("res").setAttribute("aria-busy", "false");
            $("res-late").hidden = false;
          }
        })
        .catch(function () {});
    }
    timer = setInterval(tick, POLL_MS);
    tick();
  }

  function renderItems(box, items) {
    var sec = $("res-items");
    if (!sec) {
      sec = el("div", "items"); sec.id = "res-items";
      box.parentNode.insertBefore(sec, box);
    }
    sec.textContent = "";
    sec.hidden = !items || !items.length;
    if (sec.hidden) return;
    sec.appendChild(el("div", "items-hd", "Τι γράφει το τιμολόγιο"));
    items.slice(0, 12).forEach(function (it) {
      var row = el("div", "it");
      var name = el("span", "nm", it.name);
      name.title = it.name;
      row.appendChild(name);
      row.appendChild(el("span", "q", "× " + it.qty));
      row.appendChild(el("span", "v", it.net));
      sec.appendChild(row);
    });
    if (items.length > 12) {
      var more = el("div", "it more");
      more.appendChild(document.createTextNode("και άλλες " + (items.length - 12) + " · "));
      var a = el("a", null, "Δες το τιμολόγιο");
      a.href = $("res-link").href;
      more.appendChild(a);
      sec.appendChild(more);
    }
    sec.appendChild(el("p", "items-note", "Αυτά μπαίνουν στην αποθήκη μόλις ελεγχθεί η παραλαβή."));
  }

  function render(s) {
    var box = $("res-checks");
    box.textContent = "";
    if (s.read) {
      renderItems(box, s.read.items);
      $("res-head").hidden = false;
      $("res-who").textContent = s.read.number + (s.read.supplier ? " · " + s.read.supplier : "");
      $("res-big").textContent = s.read.total;
      var lines = s.read.lines;
      box.appendChild(check(!!s.read.checks_ok,
        s.read.checks_ok ? "Διάβασε " + lines + " γραμμές, ποσά και ΦΠΑ σωστά"
                         : "Διάβασε " + lines + " γραμμές, αλλά τα ποσά θέλουν έλεγχο"));
    }
    if (s.mydata) box.appendChild(check(/βρέθηκε το τιμολόγιο/.test(s.mydata) && !/ΔΙΑΦΟΡΑ/.test(s.mydata), s.mydata));
    if (s.done) {
      var n = (s.issues || []).length;
      (s.issues || []).forEach(function (t, k) { box.appendChild(check(false, t, k === 0 ? (n === 1 ? "1 θέμα:" : n + " θέματα:") : "")); });
      setTitle("ph-fill ph-check-circle", "var(--ok)", "Το διάβασε ο Agent Τιμολογίων");
      $("res-at").textContent = hhmm();
      $("res-link").hidden = false;
    }
  }
})();
