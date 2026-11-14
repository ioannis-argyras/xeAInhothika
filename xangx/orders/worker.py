"""Orders worker: every 30 s, read supplier replies from the mailbox (IMAP_* in .env).

Without IMAP_HOST it does not start: rehearsals run without a mailbox.
"""

import threading
import time

from xangx.orders import replies
from xangx.shared.db import connect, init_db

INTERVAL = 30.0


def _loop(db_path, settings, interval):
    conn = connect(db_path)
    init_db(conn)
    while True:
        try:
            for event in replies.poll(conn, settings):
                print(f"orders worker: reply to {event.payload['order_id']}", flush=True)
        except Exception as exc:  # keep polling; never print settings or email text
            print(f"orders worker: {type(exc).__name__}", flush=True)
        time.sleep(interval)


def start(db_path=None, interval=INTERVAL):
    settings = replies.imap_settings()
    if settings is None:
        return None
    t = threading.Thread(target=_loop, args=(db_path, settings, interval),
                         name="orders-worker", daemon=True)
    t.start()
    return t
