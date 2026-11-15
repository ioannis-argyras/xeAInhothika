"""Background work for invoices: every 5 s, take files from data/inbox/ and process them.
Every 30 s, also invoices attached to supplier emails (only with IMAP_HOST in .env).

Without internet nothing is lost: a read that fails for the network (or a busy database) is
tried again after 5 s, 30 s, 2 min and then every 5 min, and a myDATA check that could not
reach myDATA is asked again every 5 min. A file that Claude cannot use is tried 3 times only.
"""

import sqlite3
import threading
import time
import traceback

from xangx.invoices import core, email_in
from xangx.shared import connect, init_db
from xangx.shared import views

INTERVAL_S = 5
EMAIL_INTERVAL_S = 30
MAX_TRIES = 3  # a file Claude cannot use: stop, so we do not pay for it again and again
RETRY_S = (5, 30, 120, 300)  # network down: then every 5 minutes, without end
FLAG_AFTER = 3  # tries, before the owner is told
MYDATA_RETRY_S = 300


def temporary(exc):
    """True for a failure that goes away by itself: no internet, Claude busy, database busy."""
    if isinstance(exc, sqlite3.OperationalError):
        return True
    try:
        import anthropic
    except ImportError:
        return False
    return isinstance(exc, (anthropic.APIConnectionError, anthropic.RateLimitError,
                            anthropic.InternalServerError))


def run_once(conn, failures, now=None):
    """One pass: ingest the inbox, then process what is open. Returns processed ids.

    `failures` keeps, per invoice, the tries so far and when to try again."""
    if views.is_paused(conn):
        return []
    now = time.monotonic() if now is None else now
    core.scan_inbox(conn)
    done = []
    for item in core.open_invoices(conn):
        iid = item["invoice_id"]
        if item["state"] != "not_processed":
            continue
        state = failures.get(iid)
        if state and (state["gave_up"] or now < state["next"]):
            continue
        try:
            core.process_invoice(conn, iid)
            done.append(iid)
            failures.pop(iid, None)
        except Exception as exc:  # keep the loop alive; try again later
            state = failures.setdefault(iid, {"tries": 0, "next": 0.0, "gave_up": False})
            state["tries"] += 1
            offline = temporary(exc)
            if offline:
                state["next"] = now + RETRY_S[min(state["tries"], len(RETRY_S)) - 1]
            else:
                state["next"] = now + INTERVAL_S
                state["gave_up"] = state["tries"] >= MAX_TRIES
            print(f"[invoices] {iid} failed (try {state['tries']}, "
                  f"{'temporary' if offline else 'file'}): {type(exc).__name__}", flush=True)
            if not offline:
                traceback.print_exc()
            if state["tries"] == FLAG_AFTER:
                try:
                    core.flag_unread(conn, iid, offline)
                except Exception:
                    traceback.print_exc()
    return done


def retry_mydata(conn, schedule, now=None):
    """Every 5 minutes: ask myDATA again for invoices it did not answer. Returns new event ids."""
    now = time.monotonic() if now is None else now
    if views.is_paused(conn) or now < schedule.get("next", 0.0):
        return []
    schedule["next"] = now + MYDATA_RETRY_S
    out = []
    for iid in core.mydata_unanswered(conn):
        event = core.mydata_retry(conn, iid)
        if event is not None:
            out.append(event.id)
    return out


def poll_email(conn):
    """Invoices attached to supplier emails (only with IMAP_HOST in .env)."""
    if views.is_paused(conn) or email_in.replies.imap_settings() is None:
        return []
    return email_in.poll(conn)


def _loop(db_path):
    conn = connect(db_path)
    init_db(conn)
    failures, mydata_schedule = {}, {}
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
        try:
            retry_mydata(conn, mydata_schedule)
        except Exception:
            traceback.print_exc()
        time.sleep(INTERVAL_S)


def start(db_path=None):
    threading.Thread(target=_loop, args=(db_path,), name="invoices-worker", daemon=True).start()
