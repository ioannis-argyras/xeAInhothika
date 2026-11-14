"""The clock: the shop starts its day and the partner wakes up by itself.

    start(db_path=None) -> None    daemon thread, returns at once; XANGX_AUTORUN=0 turns it off

Two jobs, both in one thread with its own connection:
- the morning check at 07:00 Greek time (and once at start-up if today's has not been done);
- an automatic round of the partner after low stock, an invoice with issues, a new IBAN or a
  price change. Events that arrive together are gathered into ONE round.
Nothing here sends anything: tools that need approval still end as a card for the owner.
"""

import os
import threading
import time
from datetime import datetime, timezone

from xangx.master import agent
from xangx.master import tools as tool_catalog
from xangx.screen.describe import describe, load_names
from xangx.shared.cursors import consume, set_cursor
from xangx.shared.db import connect, init_db

ACTOR = "autorun"
TRIGGER_TYPES = ["stock.low_detected", "invoice.matched", "supplier.new_iban_seen", "price.changed",
                 "supplier.cheaper_found"]
MORNING_HOUR = 7          # Greek time
QUIET_SECONDS = 45        # no new trigger for this long, then the round starts
MIN_GAP_SECONDS = 180     # at least this long between the end of one round and the start of the next
MORNING_POLL_SECONDS = 30
TICK_SECONDS = 5
MAX_REASONS = 3


def _is_trigger(event):
    if event.type == "invoice.matched":
        return bool(event.payload.get("issues"))
    return True


class Clock:
    """The state of the clock. `tick` is called every few seconds; time is passed in, so tests need no waiting."""

    def __init__(self, quiet=QUIET_SECONDS, gap=MIN_GAP_SECONDS, morning_hour=MORNING_HOUR):
        self.quiet = quiet
        self.gap = gap
        self.morning_hour = morning_hour
        self.pending = []
        self.last_trigger = None
        self.last_round_end = None
        self.last_morning_poll = None
        self._round = threading.Lock()

    # -- morning check
    def morning(self, conn, now_utc=None, now=None):
        """Run the morning check if it is past 07:00 in Greece and today's has not been done."""
        if now is not None and self.last_morning_poll is not None \
                and now - self.last_morning_poll < MORNING_POLL_SECONDS:
            return None
        self.last_morning_poll = now
        now_utc = now_utc or datetime.now(timezone.utc)
        local = now_utc + tool_catalog.greek_offset(now_utc)
        if local.hour < self.morning_hour:
            return None
        day_start = tool_catalog.greek_day_start_utc(now_utc)
        done = conn.execute(
            "SELECT 1 FROM events WHERE type = 'check.morning_done' AND at >= ? LIMIT 1", (day_start,)
        ).fetchone()
        if done:
            return None
        return agent.morning_check(conn)

    # -- wake-up
    def collect(self, conn, now, now_utc=None):
        """Gather new trigger events (only today's). Returns how many were gathered."""
        day_start = tool_catalog.greek_day_start_utc(now_utc)

        def handler(event):
            if event.at >= day_start and _is_trigger(event):
                self.pending.append(event)
                self.last_trigger = now

        return consume(conn, ACTOR, TRIGGER_TYPES, handler)

    def round_due(self, now):
        if not self.pending or self.last_trigger is None:
            return False
        if now - self.last_trigger < self.quiet:
            return False
        if self.last_round_end is not None and now - self.last_round_end < self.gap:
            return False
        return True

    def run_round(self, conn, now):
        """One round of the partner for everything gathered. Returns the result, or None if nothing ran."""
        if not self.round_due(now) or not self._round.acquire(blocking=False):
            return None
        try:
            started = time.monotonic()
            events, self.pending = self.pending, []
            reason = build_reason(conn, events)
            try:
                return agent.handle_trigger(conn, reason, [e.id for e in events])
            except Exception as exc:  # a failed round must not stop the clock
                print(f"autorun: round failed: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
                return None
            finally:
                self.last_round_end = now + (time.monotonic() - started)
        finally:
            self._round.release()

    def tick(self, conn, now, now_utc=None):
        """One look at everything. Never raises."""
        try:
            self.morning(conn, now_utc, now)
        except Exception as exc:
            print(f"autorun: morning check failed: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        try:
            self.collect(conn, now, now_utc)
            return self.run_round(conn, now)
        except Exception as exc:
            print(f"autorun: tick failed: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
            return None


def build_reason(conn, events):
    """One short Greek sentence for the gathered events, e.g. "Coca-Cola σε χαμηλό: 6 τεμάχια · …"."""
    names = load_names(conn)
    texts = []
    for e in events:
        text = describe(e, names)
        if text not in texts:
            texts.append(text)
    shown = " · ".join(texts[:MAX_REASONS])
    if len(texts) > MAX_REASONS:
        shown += f" · και {len(texts) - MAX_REASONS} ακόμα"
    return shown


def _init_cursor(conn):
    """First start: begin at the last event, so the history of the seed does not wake the partner."""
    if conn.execute("SELECT 1 FROM cursors WHERE actor = ?", (ACTOR,)).fetchone() is None:
        last = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        set_cursor(conn, ACTOR, last)


def enabled():
    return os.environ.get("XANGX_AUTORUN") != "0"


def _loop(db_path, clock):
    conn = connect(db_path)
    init_db(conn)
    _init_cursor(conn)
    while True:
        clock.tick(conn, time.monotonic())
        time.sleep(TICK_SECONDS)


def start(db_path=None):
    """Start the clock thread and return at once. Does nothing when XANGX_AUTORUN=0."""
    if not enabled():
        return None
    thread = threading.Thread(target=_loop, args=(db_path, Clock()), name="autorun", daemon=True)
    thread.start()
    return thread
