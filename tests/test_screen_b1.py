import json
import os
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xangx.accountant import core as accountant
from xangx.demo.seed import seed_all
from xangx.master import agent
from xangx.master import tools as tool_catalog
from xangx.orders import tools as order_tools
from xangx.screen import api_b1
from xangx.shared.db import connect, init_db
from xangx.shared.events import append_event, read_events
from xangx.stock import worker


def _reader(item):
    """What the invoice reader would answer for one of the demo invoices."""
    from scripts.make_demo_invoices import totals

    net, _, vat, total = totals(item["lines"])
    return {
        "supplier_afm": item["supplier"]["afm"], "series": item["series"],
        "number": item["number"], "date": item["date"],
        "lines": [{k: l[k] for k in ("name", "qty", "unit_cents", "net_cents", "vat_rate")}
                  for l in item["lines"]],
        "net_cents": net, "vat_by_rate": {str(r): c for r, c in vat.items()},
        "total_cents": total, "iban": item["iban"],
        "pages": {"seen": 1, "total": 1}, "hidden_instructions": item.get("hidden"),
    }


def build_scene(conn, data_dir):
    """Today's scene: Coca-Cola low, order sent after a yes, delivery, three invoices read,
    a credit request waiting for the owner. Returns ids worth knowing."""
    from xangx.invoices import core as invoices
    from scripts.make_demo_invoices import invoices as demo_invoices

    tool_catalog.load_role_tools()
    append_event(conn, type="shelf.items_taken", actor="camera",
                 payload={"position_id": "fridge1-r1", "product_id": "cola-330", "taken": 5,
                          "confidence": "high", "file_ref": "b.jpg", "prev_file_ref": "a.jpg"})
    worker.run_once(conn)
    order_id = order_tools.propose_order(conn, "sup-ymittou")["order_id"]
    append_event(conn, type="approval.requested", actor="master",
                 payload={"approval_id": "A-T1", "action": "send_order", "summary": "s",
                          "exact": "e", "why": "w", "ref": order_id, "tool": "send_order",
                          "args": {"order_id": order_id}})
    before_yes = conn.execute("SELECT MAX(id) AS m FROM events").fetchone()["m"]
    agent.decide_approval(conn, "A-T1", "yes")

    s = dict(actor="system")
    append_event(conn, type="delivery.started", payload={
        "delivery_id": "D-1", "order_id": "P-0318", "supplier_id": "sup-ymittou"}, **s)
    append_event(conn, type="delivery.completed", payload={
        "delivery_id": "D-1", "shortages": [], "unknown_barcodes": [],
        "lines": [{"product_id": "cola-330", "expected": 48, "scanned": 48},
                  {"product_id": "water-1500x6", "expected": 10, "scanned": 9},
                  {"product_id": "chips-salt-90", "expected": 24, "scanned": 24},
                  {"product_id": "choco-milk-100", "expected": 30, "scanned": 30},
                  {"product_id": "juice-orange-1l", "expected": 12, "scanned": 12}]}, **s)

    inbox = Path(data_dir) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    ids = {}
    for item in demo_invoices(date.today()):
        (inbox / "x.pdf").write_bytes(item["file"].encode("utf-8"))
        [iid] = invoices.scan_inbox(conn)
        invoices.process_invoice(conn, iid, reader=lambda p, item=item: _reader(item))
        ids[item["series"] + "-" + item["number"]] = iid
    accountant.set_payment_reminder(conn, ids["Β-512"])

    said, _ = append_event(conn, type="agent.said", actor="master",
                           payload={"text": "x", "channel": "web"})
    ids["credit_approval"] = f"A-{said.id}"
    agent._request_approval(conn, [], tool_catalog.TOOLS["request_credit"],
                            {"invoice_id": ids["Α-2271"]}, ids["credit_approval"], said.id)
    ids["order_id"] = order_id
    ids["before_yes"] = before_yes
    return ids


class ScreenB1Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.env = mock.patch.dict(os.environ, {
            "XANGX_DATA_DIR": cls.dir, "XANGX_FAKE_LLM": "1",
            "XANGX_FILES": os.path.join(cls.dir, "files"), "MYDATA_USER_ID": "",
            "MYDATA_SUBSCRIPTION_KEY": "", "MYDATA_SUPPLIER_USER_ID": "",
            "MYDATA_SUPPLIER_SUBSCRIPTION_KEY": "", "XANGX_DEMO_SUPPLIER_EMAIL": "",
            "SMTP_HOST": ""})
        cls.env.start()
        # One database with the seed and the whole scene, copied for every test.
        cls.scene_db = os.path.join(cls.dir, "scene.sqlite")
        os.environ["XANGX_DB"] = cls.scene_db
        conn = connect(cls.scene_db)
        init_db(conn)
        seed_all(conn)
        cls.ids = build_scene(conn, cls.dir)
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        self.db = os.path.join(self.dir, "t.sqlite")
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.db + suffix)
            except FileNotFoundError:
                pass
        src, dst = connect(self.scene_db), connect(self.db)
        src.backup(dst)
        src.close()
        dst.close()
        os.environ["XANGX_DB"] = self.db
        self.conn = connect(self.db)
        app = FastAPI()
        app.include_router(api_b1.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.conn.close()

    def tasks(self, **params):
        r = self.client.get("/api/screen/tasks", params=params)
        self.assertEqual(r.status_code, 200)
        return r.json()

    def all_tasks(self, data):
        return [t for g in data["groups"] for t in g["tasks"]]

    def test_order_sent_after_yes_is_with_yes_and_the_proposal_is_auto(self):
        data = self.tasks()
        by_text = {t["text"]: t for t in self.all_tasks(data)}
        order = self.ids["order_id"]
        sent = next(t for t in by_text.values() if t["text"] == f"Στάλθηκε η παραγγελία {order}")
        proposed = next(t for t in by_text.values() if t["text"].startswith(f"Πρόταση παραγγελίας {order}"))
        self.assertEqual(sent["how"], "yes")
        self.assertEqual(proposed["how"], "auto")
        self.assertEqual(sent["team"], "orders")
        self.assertEqual(data["totals"]["with_yes"], 1)
        self.assertEqual(data["totals"]["errors"], 0)
        self.assertEqual(data["totals"]["done"], len(self.all_tasks(data)))
        self.assertRegex(sent["at"], r"^\d\d:\d\d$")

    def test_groups_and_invoice_links(self):
        data = self.tasks()
        self.assertEqual({g["key"] for g in data["groups"]} - {"master"},
                         {"orders", "stock", "invoices", "accountant"})
        for g in data["groups"]:
            self.assertEqual(g["count"], len(g["tasks"]))
            ids = [t["id"] for t in g["tasks"]]
            self.assertEqual(ids, sorted(ids, reverse=True))
        read = next(t for t in self.all_tasks(data) if "Α-2271" in t["text"])
        self.assertEqual(read["links"]["invoice_id"], self.ids["Α-2271"])
        self.assertFalse([t for t in self.all_tasks(data) if "INV-" in t["text"]])
        # jobs written by the seed (actor "system") still land in the right team
        self.assertEqual(next(t for t in self.all_tasks(data) if t["text"] == "Στάλθηκε η παραγγελία P-0318")["team"], "orders")
        self.assertEqual(next(t for t in self.all_tasks(data) if t["text"].startswith("Τελείωσε η παραλαβή"))["team"], "stock")
        price = next(t for t in self.all_tasks(data) if t["text"].startswith("Άλλαξε η τιμή"))
        self.assertEqual(price["links"]["invoice_id"], self.ids["Α-2271"])
        low = next(t for t in self.all_tasks(data) if "σε χαμηλό" in t["text"])
        self.assertEqual(low["links"], {})

    def test_until_id_is_respected(self):
        before = self.tasks(until_id=self.ids["before_yes"])
        texts = [t["text"] for t in self.all_tasks(before)]
        self.assertNotIn("Στάλθηκε η παραγγελία " + self.ids["order_id"], texts)
        self.assertIn("Στάλθηκε η παραγγελία P-0318", texts)  # the seed's own order, sent earlier
        self.assertEqual(before["totals"]["with_yes"], 0)
        self.assertLessEqual(before["last_id"], self.ids["before_yes"])
        self.assertLess(before["totals"]["done"], self.tasks()["totals"]["done"])

    def test_invoice_list_today(self):
        items = self.client.get("/api/screen/invoices").json()["invoices"]
        self.assertEqual({i["label"] for i in items}, {"Α-2271", "Β-512", "Γ-318", "Γ-399"})
        main = next(i for i in items if i["label"] == "Α-2271")
        self.assertTrue(main["supplier"].startswith("Διανομές Υμηττού"))
        self.assertTrue(main["total_text"].endswith("€"))
        self.assertTrue(main["pending_credit"])
        self.assertEqual(main["level"], "warn")
        bakery = next(i for i in items if i["label"] == "Β-512")
        self.assertIn("IBAN", bakery["status"])

    def test_invoice_detail_with_the_missing_item(self):
        d = self.client.get("/api/screen/invoices/" + self.ids["Α-2271"]).json()
        self.assertEqual(d["label"], "Α-2271")
        self.assertEqual(d["order_id"], "P-0318")
        self.assertEqual(len(d["lines"]), 5)
        water = next(l for l in d["lines"] if l["name"].startswith("Νερό"))
        self.assertEqual((water["ordered"], water["delivered"], water["charged"]), (10, 9, 10))
        self.assertTrue(water["diff"])
        cola = next(l for l in d["lines"] if l["name"].startswith("Coca-Cola"))
        self.assertEqual(cola["price_pct_text"], "+8,8%")
        self.assertEqual(cola["old_unit_text"], "0,57 €")
        self.assertFalse(cola["diff"])
        self.assertEqual(d["credit"]["amount_text"], "2,43 €")
        self.assertTrue(d["credit"]["supplier"].startswith("Διανομές Υμηττού"))
        self.assertEqual(d["credit"]["approval_id"], self.ids["credit_approval"])
        self.assertTrue(any(c["ok"] is False and "Coca-Cola" in c.get("lead", "") for c in d["checks"]))

    def test_iban_is_only_the_last_four(self):
        from xangx.demo.catalog_data import SUPPLIER_BY_ID

        full = {s["iban"] for s in SUPPLIER_BY_ID.values() if s.get("iban")}
        bakery_iban = next(e.payload["iban"] for e in read_events(self.conn, types=["invoice.read"])
                           if e.payload.get("number") == "512")
        full.add(bakery_iban)
        d = self.client.get("/api/screen/invoices/" + self.ids["Β-512"]).json()
        self.assertIn(bakery_iban[-4:], json.dumps(d, ensure_ascii=False))
        self.assertTrue(any("Νέο IBAN" in c.get("lead", "") for c in d["checks"]))
        blob = json.dumps([d, self.tasks(), self.client.get("/api/screen/invoices").json()],
                          ensure_ascii=False)
        for iban in full:
            self.assertNotIn(iban, blob)
            self.assertNotIn(" ".join(iban[i:i + 4] for i in range(0, len(iban), 4)), blob)

    def test_hidden_instruction_is_a_flag_not_a_command(self):
        d = self.client.get("/api/screen/invoices/" + self.ids["Γ-399"]).json()
        self.assertTrue(any("κρυμμένες οδηγίες" in c["text"] for c in d["checks"] if not c["ok"]))
        self.assertNotIn("GR00", json.dumps(d, ensure_ascii=False))

    def test_yes_to_the_credit_shows_in_tasks_and_clears_the_scene(self):
        r = agent.decide_approval(self.conn, self.ids["credit_approval"], "yes")
        self.assertTrue(r["ok"], r)
        task = next(t for t in self.all_tasks(self.tasks()) if t["text"].startswith("Ζητήθηκε πιστωτικό"))
        self.assertEqual(task["how"], "yes")
        self.assertEqual(task["links"]["invoice_id"], self.ids["Α-2271"])
        d = self.client.get("/api/screen/invoices/" + self.ids["Α-2271"]).json()
        self.assertIsNone(d["credit"])
        self.assertTrue(any("Ζητήθηκε πιστωτικό" in c["text"] for c in d["checks"]))
        self.assertEqual(self.tasks()["totals"]["with_yes"], 2)

    def test_a_no_to_the_credit_clears_the_scene_without_a_request(self):
        agent.decide_approval(self.conn, self.ids["credit_approval"], "no")
        d = self.client.get("/api/screen/invoices/" + self.ids["Α-2271"]).json()
        self.assertIsNone(d["credit"])
        self.assertFalse([t for t in self.all_tasks(self.tasks())
                          if t["text"].startswith("Ζητήθηκε πιστωτικό")])

    def test_unknown_or_odd_ids_give_404(self):
        self.assertEqual(self.client.get("/api/screen/invoices/INV-00000000").status_code, 404)
        self.assertEqual(self.client.get("/api/screen/invoices/..%2Fx").status_code, 404)
        self.assertEqual(self.client.get("/api/screen/invoices/a%20b").status_code, 404)


class EmptyDatabaseTest(unittest.TestCase):
    def test_empty_database_gives_empty_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XANGX_DB": os.path.join(tmp, "e.sqlite")}):
                app = FastAPI()
                app.include_router(api_b1.router)
                client = TestClient(app)
                data = client.get("/api/screen/tasks").json()
                self.assertEqual(data["groups"], [])
                self.assertEqual(data["totals"], {"done": 0, "with_yes": 0, "errors": 0})
                self.assertEqual(client.get("/api/screen/invoices").json()["invoices"], [])


if __name__ == "__main__":
    unittest.main()
