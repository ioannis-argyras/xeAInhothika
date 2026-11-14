"""Background work for the accountant: turn invoice problems into exception.flagged."""

import threading
import time
import traceback

from xangx.accountant import core
from xangx.shared import connect, init_db
from xangx.shared.cursors import consume

INTERVAL_S = 5


def _handle(conn, event):
    core.flag_for(conn, event)
    core.payment_for(conn, event)


def run_once(conn):
    return consume(conn, "accountant", core.FLAG_TYPES, lambda e: _handle(conn, e))


def _loop(db_path):
    conn = connect(db_path)
    init_db(conn)
    while True:
        try:
            run_once(conn)
        except Exception:
            traceback.print_exc()
        time.sleep(INTERVAL_S)


def start(db_path=None):
    threading.Thread(target=_loop, args=(db_path,), name="accountant-worker", daemon=True).start()
