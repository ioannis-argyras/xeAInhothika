/* Αναίρεση: το κουμπί δίπλα σε ό,τι γυρίζει πίσω, και το «Είσαι σίγουρος;» πριν γίνει.
   Κάθε κείμενο μπαίνει με textContent, ποτέ ως HTML. */
(function () {
  "use strict";
  var dlg = null, parts = null, current = null;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function icon(name) { return el("i", "ph ph-" + name); }

  function build() {
    dlg = el("dialog", "ud-dlg");
    dlg.setAttribute("aria-labelledby", "ud-title");
    var box = el("div", "ud-box");
    var title = el("h2", null, "Είσαι σίγουρος;");
    title.id = "ud-title";
    title.tabIndex = -1;
    var text = el("p", "ud-text");
    var msg = el("p", "ud-msg");
    msg.setAttribute("role", "alert");
    var acts = el("div", "ud-acts");
    var no = el("button", "btn ud-no", "Όχι");
    var yes = el("button", "btn ud-yes", "Ναι, αναίρεσέ το");
    no.type = yes.type = "button";
    acts.append(no, yes);
    box.append(title, text, msg, acts);
    dlg.appendChild(box);
    document.body.appendChild(dlg);
    parts = { title: title, text: text, msg: msg, no: no, yes: yes };
    no.addEventListener("click", function () { dlg.close(); });
    dlg.addEventListener("click", function (e) { if (e.target === dlg) dlg.close(); });
    yes.addEventListener("click", send);
  }

  function send() {
    if (!current) return;
    var c = current;
    parts.yes.disabled = parts.no.disabled = true;
    parts.msg.textContent = "";
    fetch("/api/undo/" + encodeURIComponent(c.id), { method: "POST" }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d }; });
    }).then(function (res) {
      if (res.ok && res.d && res.d.ok) {
        dlg.close();
        c.done();
        return;
      }
      parts.msg.textContent = (res.d && typeof res.d.detail === "string")
        ? res.d.detail : "Δεν έγινε. Δοκίμασε ξανά.";
      parts.no.disabled = false;
      parts.no.textContent = "Κλείσε";
    }).catch(function () {
      parts.msg.textContent = "Δεν έγινε. Δοκίμασε ξανά.";
      parts.yes.disabled = parts.no.disabled = false;
    });
  }

  function ask(id, what, done) {
    if (!dlg) build();
    current = { id: id, done: done };
    parts.text.textContent = what ? "Θα αναιρεθεί: " + what : "";
    parts.msg.textContent = "";
    parts.yes.disabled = parts.no.disabled = false;
    parts.no.textContent = "Όχι";
    dlg.showModal();
    parts.title.focus();
  }

  function note(text, name) {
    var n = el("span", "ud-note m");
    if (name) n.appendChild(icon(name));
    n.appendChild(document.createTextNode(text));
    return n;
  }

  // state: "can" | "done" | "sent" | null. Returns the node to put in the row, or null.
  window.xangxUndoNode = function (state, id, what, onDone) {
    if (state === "done") return note("Αναιρέθηκε", "check");
    if (state === "sent") return note("Έφυγε ήδη, δεν αναιρείται.", null);
    if (state !== "can") return null;
    var holder = el("span", "ud");
    var btn = el("button", "btn undo");
    btn.type = "button";
    btn.append(icon("arrow-counter-clockwise"), document.createTextNode("Αναίρεση"));
    btn.setAttribute("aria-label", "Αναίρεση: " + what);
    btn.addEventListener("click", function () {
      ask(id, what, function () {
        holder.replaceChildren(note("Αναιρέθηκε", "check"));
        if (onDone) onDone();
      });
    });
    holder.appendChild(btn);
    return holder;
  };
})();
