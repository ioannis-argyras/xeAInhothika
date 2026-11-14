"""Background work for invoices: every 5 s, take files from data/inbox/ and process them.
Every 30 s, also invoices attached to supplier emails (only with IMAP_HOST in .env)."""

import threading
import time
import traceback

from xangx.invoices import core, email_in
from xangx.shared import connect, init_db
from xangx.shared import views

INTERVAL_S = 5
EMAIL_INTERVAL_S = 30
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


def poll_email(conn):
    """Invoices attached to supplier emails (only with IMAP_HOST in .env)."""
    if views.is_paused(conn) or email_in.replies.imap_settings() is None:
        return []
    return email_in.poll(conn)


def _loop(db_path):
    conn = connect(db_path)
    init_db(conn)
    failures = {}
    last_email = 0.0
    while True:
        if time.monotonic() - last_email >= EMAIL_INTERVAL_S:
            last_email = time.monotonic()
            try:
                poll_email(conn)
            except Exception:  # mailbox down or slow: try again next time
                traceback.print_exc()
        try:
            run_once(conn, failures)
        except Exception:
            traceback.print_exc()
        time.sleep(INTERVAL_S)


def start(db_path=None):
    threading.Thread(target=_loop, args=(db_path,), name="invoices-worker", daemon=True).start()
