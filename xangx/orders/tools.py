"""Order tools for the partner (master): propose an order, send it after the owner's yes."""

import math
import re
from datetime import datetime, timezone

from xangx.master.tools import greek_day_start_utc, tool
from xangx.orders import mail
from xangx.shared import views
from xangx.shared.db import connect, init_db
from xangx.shared.events import append_event, read_events
from xangx.shared.money import format_eur
from xangx.stock.worker import sales_per_day

ACTOR = "orders"
COVER_DAYS = 3
LEAD_DAYS = 1


def _qty(n):
    return int(n) if float(n) == int(n) else round(n, 1)


def _supplier(conn, supplier_id):
    row = conn.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
    return dict(row) if row else None


def _low_today(conn):
    """{product_id: on_hand} from today's stock.low_detected (e.g. the shelf camera)."""
    since = greek_day_start_utc()
    out = {}
    for e in read_events(conn, types=["stock.low_detected"]):
        if e.at >= since:
            out[e.payload["product_id"]] = e.payload["on_hand"]
    return out


def _next_order_id(conn):
    top = 0
    for e in read_events(conn):
        if e.type.startswith("order."):
            m = re.fullmatch(r"P-(\d+)", str(e.payload.get("order_id", "")))
            if m:
                top = max(top, int(m.group(1)))
    return f"P-{top + 1:04d}"


def _order_events(conn, order_id):
    return [e for e in read_events(conn) if e.type.startswith("order.")
            and e.payload.get("order_id") == order_id]


def _on_order(conn):
    """{product_id: qty} still coming: orders sent, not delivered (delivery.completed) or cancelled."""
    done = {e.payload.get("delivery_id") for e in read_events(conn, types=["delivery.completed"])}
    cancelled = {e.payload.get("order_id") for e in read_events(conn, types=["order.cancelled"])}
    sent = {e.payload.get("order_id") for e in read_events(conn, types=["order.sent"])}
    out = {}
    for e in read_events(conn, types=["order.proposed"]):
        oid = e.payload.get("order_id")
        if oid in sent and oid not in cancelled and f"D-{oid}" not in done:
            for l in e.payload.get("lines", []):
                out[l["product_id"]] = out.get(l["product_id"], 0) + l["qty"]
    return out


def _names(conn):
    return {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM products")}


def supplier_prices(conn, product_id):
    """[(supplier_id, supplier name, unit_cents)] for every supplier of the product that has a
    known price (the last invoice line of that supplier), cheapest first."""
    out = []
    rows = conn.execute(
        "SELECT s.id, s.name, s.afm FROM supplier_products sp JOIN suppliers s ON s.id = sp.supplier_id "
        "WHERE sp.product_id = ? ORDER BY s.id", (product_id,)).fetchall()
    for r in rows:
        unit = views.last_unit_price_cents(conn, product_id, supplier_afm=r["afm"])
        if unit:
            out.append((r["id"], r["name"], unit))
    return sorted(out, key=lambda x: (x[2], x[0]))


@tool(
    name="compare_suppliers",
    description=(
        "Compare the price of one product across the suppliers that carry it (last invoice price "
        "of each). Read only: it changes nothing and needs no approval."
    ),
    input_schema={
        "type": "object",
        "properties": {"product_id": {"type": "string", "description": "e.g. cola-330"}},
        "required": ["product_id"],
    },
)
def compare_suppliers(conn, product_id):
    name = _names(conn).get(product_id)
    if name is None:
        return {"summary": "Δεν ξέρω αυτό το προϊόν.", "event_ids": []}
    prices = supplier_prices(conn, product_id)
    if not prices:
        return {"summary": f"{name}: δεν έχω τιμές από προμηθευτές.", "event_ids": [], "suppliers": []}
    by_name = sorted(prices, key=lambda x: x[0])
    text = ", ".join(f"{n} {format_eur(c)}" for _, n, c in by_name)
    return {"summary": f"{name}: {text}", "event_ids": [],
            "cheapest_supplier_id": prices[0][0],
            "suppliers": [{"supplier_id": i, "name": n, "unit_cents": c} for i, n, c in prices]}


def _needs(conn, supplier_id, now):
    """Lines to order from this supplier: products in low today, with quantity and price."""
    sup = _supplier(conn, supplier_id)
    levels = views.stock_levels(conn)
    camera_low = _low_today(conn)
    coming = _on_order(conn)
    rows = conn.execute(
        "SELECT sp.product_id, sp.pack_size, p.low_threshold FROM supplier_products sp "
        "JOIN products p ON p.id = sp.product_id WHERE sp.supplier_id = ? ORDER BY sp.product_id",
        (supplier_id,),
    ).fetchall()
    lines = []
    for r in rows:
        pid, threshold = r["product_id"], r["low_threshold"]
        if threshold is None:
            continue
        on_hand = levels.get(pid, 0) or 0
        if pid in camera_low:
            on_hand = min(on_hand, camera_low[pid])
        elif on_hand > threshold:
            continue
        per_day = round(sales_per_day(conn, pid, now), 1)
        on_order = coming.get(pid, 0)
        need = per_day * COVER_DAYS + threshold - on_hand - on_order
        if need <= 0:
            continue
        pack = r["pack_size"] or 1
        qty = math.ceil(need / pack) * pack
        unit = views.last_unit_price_cents(conn, pid, supplier_afm=sup["afm"])
        if unit is None:
            unit = views.last_unit_price_cents(conn, pid)
        if unit is None:
            continue  # no known price: nothing to propose for this product
        lines.append({"product_id": pid, "qty": qty, "unit_cents": unit, "last_unit_cents": unit,
                      "_per_day": per_day, "_on_hand": on_hand, "_on_order": on_order})
    return lines


def _suppliers_with_low(conn):
    now = datetime.now(timezone.utc)
    out = []
    for s in conn.execute("SELECT id, name FROM suppliers ORDER BY id"):
        lines = _needs(conn, s["id"], now)
        if lines:
            out.append({"supplier_id": s["id"], "name": s["name"],
                        "products": [l["product_id"] for l in lines]})
    return out


@tool(
    name="propose_order",
    description=(
        "Prepare (do not send) an order to one supplier for its products that are low today "
        "(on-hand at or below the low threshold, or flagged low by the shelf camera). Quantity "
        "covers 3 days of sales plus the threshold, minus what is already on order (sent, not "
        "delivered yet), rounded up to the pack size. If the "
        "supplier_id is unknown, the result lists the suppliers that have low products."
    ),
    input_schema={
        "type": "object",
        "properties": {"supplier_id": {"type": "string", "description": "e.g. sup-ymittou"}},
        "required": ["supplier_id"],
    },
)
def propose_order(conn, supplier_id):
    sup = _supplier(conn, supplier_id)
    if sup is None:
        return {"summary": "Δεν ξέρω αυτόν τον προμηθευτή.", "event_ids": [],
                "suppliers_with_low": _suppliers_with_low(conn)}
    now = datetime.now(timezone.utc)
    lines = _needs(conn, supplier_id, now)
    if not lines:
        return {"summary": f"Τίποτα σε χαμηλό από {sup['name']}.", "event_ids": []}
    clean = [{k: v for k, v in l.items() if not k.startswith("_")} for l in lines]

    # The same open proposal again (nothing sent or cancelled since): return it, do not duplicate.
    mine = [e for e in read_events(conn, types=["order.proposed"])
            if e.payload["supplier_id"] == supplier_id]
    if mine and mine[-1].payload["lines"] == clean:
        e = mine[-1]
        if _order_events(conn, e.payload["order_id"])[-1].type == "order.proposed":
            return {"summary": f"Η παραγγελία {e.payload['order_id']} είναι ήδη έτοιμη: "
                               f"{format_eur(e.payload['net_cents'])} στον {sup['name']}.",
                    "event_ids": [e.id], "order_id": e.payload["order_id"]}

    first = min(lines, key=lambda l: l["_on_hand"] / l["_per_day"] if l["_per_day"] else math.inf)
    order_id = _next_order_id(conn)
    net = sum(l["qty"] * l["unit_cents"] for l in clean)
    event, _ = append_event(
        conn, type="order.proposed", actor=ACTOR, subject=f"order:{order_id}",
        payload={"order_id": order_id, "supplier_id": supplier_id, "lines": clean, "net_cents": net,
                 "why": {"per_day": first["_per_day"], "on_hand": _qty(first["_on_hand"]),
                         "lead_days": LEAD_DAYS}},
    )
    names = _names(conn)
    return {"summary": f"Ετοίμασα παραγγελία {order_id}: {format_eur(net)} στον {sup['name']}",
            "event_ids": [event.id], "order_id": order_id, "net": format_eur(net),
            "lines": [{"product": names.get(l["product_id"], l["product_id"]), "qty": l["qty"],
                       "unit_price": format_eur(l["unit_cents"]), "on_hand": _qty(l["_on_hand"]),
                       "already_on_order": l["_on_order"], "sold_per_day": l["_per_day"]} for l in lines],
            "next": ("Call send_order with this order_id now. It does not send anything by itself: "
                     "it shows the owner an approval card, and the email leaves only after the "
                     "owner's yes there. Do not ask for a yes in the chat first.")}


def _proposal(conn, order_id):
    return next((e for e in read_events(conn, types=["order.proposed"])
                 if e.payload.get("order_id") == order_id), None)


def describe_send(args):
    """Greek summary / exact / why for the approval card (opens its own connection)."""
    conn = connect()
    try:
        init_db(conn)
        order_id = args.get("order_id")
        prop = _proposal(conn, order_id)
        if prop is None:
            return {"summary": f"Αποστολή παραγγελίας {order_id}", "ref": order_id}
        p = prop.payload
        sup = _supplier(conn, p["supplier_id"]) or {"name": p["supplier_id"]}
        names = _names(conn)
        exact = "\n".join(f"{l['qty']} {names.get(l['product_id'], l['product_id'])} × "
                          f"{format_eur(l['unit_cents'])}" for l in p["lines"])
        why = p.get("why") or {}
        per_day = str(why.get("per_day", "")).replace(".", ",")
        return {"summary": f"Παραγγελία {format_eur(p['net_cents'])} στον {sup['name']}",
                "exact": exact,
                "why": f"Πουλάς {per_day} τη μέρα και έχεις {why.get('on_hand')}.",
                "ref": order_id}
    finally:
        conn.close()


def _approval_yes(conn, order_id):
    """The approval.decided (yes) event for sending this order, or None."""
    requested = {e.payload["approval_id"] for e in read_events(conn, types=["approval.requested"])
                 if e.payload.get("tool") == "send_order"
                 and (e.payload.get("args") or {}).get("order_id") == order_id}
    for e in read_events(conn, types=["approval.decided"]):
        if e.payload.get("approval_id") in requested and e.payload.get("decision") == "yes":
            return e
    return None


@tool(
    name="send_order",
    description=(
        "Email a proposed order to its supplier. Call it right after propose_order, without "
        "asking the owner in the chat first: the system itself shows the owner an approval card "
        "(that card is the question), and the email leaves only after the owner's yes there. "
        "Safe to call twice: an order is sent only once."
    ),
    input_schema={
        "type": "object",
        "properties": {"order_id": {"type": "string", "description": "e.g. P-0319"}},
        "required": ["order_id"],
    },
    needs_approval=True,
    approval_action="send_order",
    describe=describe_send,
)
def send_order(conn, order_id):
    sent = [e for e in read_events(conn, types=["order.sent"]) if e.payload.get("order_id") == order_id]
    if sent:
        return {"summary": f"Η παραγγελία {order_id} έχει ήδη σταλεί.", "event_ids": [sent[0].id]}
    prop = _proposal(conn, order_id)
    if prop is None:
        return {"summary": f"Δεν βρήκα την παραγγελία {order_id}.", "event_ids": []}
    if _order_events(conn, order_id)[-1].type == "order.cancelled":
        return {"summary": f"Η παραγγελία {order_id} έχει ακυρωθεί.", "event_ids": []}
    yes = _approval_yes(conn, order_id)
    if yes is None:
        return {"summary": f"Η παραγγελία {order_id} θέλει πρώτα «ναι» από τον ιδιοκτήτη.", "event_ids": []}

    p = prop.payload
    sup = _supplier(conn, p["supplier_id"])
    to = mail.recipient(sup["email"])
    codes = {r["product_id"]: r["supplier_code"] for r in conn.execute(
        "SELECT product_id, supplier_code FROM supplier_products WHERE supplier_id = ?", (sup["id"],))}
    names = _names(conn)
    rows = "\n".join(
        f"- {l['qty']} × {names.get(l['product_id'], l['product_id'])}"
        f"{' (κωδ. ' + codes[l['product_id']] + ')' if codes.get(l['product_id']) else ''}"
        f", {format_eur(l['unit_cents'])} το τεμάχιο"
        for l in p["lines"])
    body = (f"Καλησπέρα σας,\n\nΠαρακαλούμε για την παρακάτω παραγγελία ({order_id}):\n\n{rows}\n\n"
            f"Σύνολο χωρίς ΦΠΑ: {format_eur(p['net_cents'])}\n\n"
            f"Παρακαλούμε επιβεβαιώστε την ημέρα παράδοσης απαντώντας σε αυτό το email.\n\n"
            f"Ευχαριστούμε,\n{mail.STORE_NAME}\n")
    msg = mail.build(order_id, to, f"Παραγγελία {order_id} · {mail.STORE_NAME}", body)
    message_id, how = mail.send(order_id, msg)
    event, _ = append_event(
        conn, type="order.sent", actor=ACTOR, subject=f"order:{order_id}", caused_by=yes.id,
        idem_key=f"order.sent:{order_id}",
        payload={"order_id": order_id, "to": to, "message_id": message_id},
    )
    where = "" if how == "smtp" else " (δοκιμή: το email γράφτηκε σε αρχείο)"
    text = f"Στάλθηκε η παραγγελία {order_id} στον {sup['name']}{where}"
    return {"summary": text if text.endswith(".") else text + ".", "event_ids": [event.id]}
