"""Background work for invoices: every 5 s, take files from data/inbox/ and process them."""

import threading
import time
import traceback

from xangx.invoices import core
from xangx.shared import connect, init_db
from xangx.shared import views

INTERVAL_S = 5
MAX_TRIES = 3


def run_once(conn, failures):
    """One pass: ingest the inbox, then process what is open. Returns processed ids."""
    if views.is_paused(conn):
        return []
    core.scan_inbox(conn)
    done = []
    for item in core.open_invoices(conn):
        iid = item["invoice_id"]
        if item["state"] != "not_processed" or failures.get(iid, 0) >= MAX_TRIES:
            continue
        try:
            core.process_invoice(conn, iid)
            done.append(iid)
        except Exception:  # keep the loop alive; retry later, a few times
            failures[iid] = failures.get(iid, 0) + 1
            print(f"[invoices] {iid} failed (try {failures[iid]}):")
            traceback.print_exc()
    return done


def _loop(db_path):
    conn = connect(db_path)
    init_db(conn)
    failures = {}
    while True:
        try:
            run_once(conn, failures)
        except Exception:
            traceback.print_exc()
        time.sleep(INTERVAL_S)


def start(db_path=None):
    threading.Thread(target=_loop, args=(db_path,), name="invoices-worker", daemon=True).start()
