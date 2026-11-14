"""Database connection and schema."""

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = "data/xangx.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    store_id  TEXT NOT NULL,
    actor     TEXT NOT NULL,
    type      TEXT NOT NULL,
    subject   TEXT,
    caused_by INTEGER REFERENCES events(id),
    idem_key  TEXT UNIQUE,
    payload   TEXT NOT NULL,
    v         INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_subject ON events(subject);
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at);

CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TABLE IF NOT EXISTS cursors (
    actor   TEXT PRIMARY KEY,
    last_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    barcode          TEXT,
    category         TEXT,
    vat_rate         INTEGER NOT NULL,
    sell_price_cents INTEGER NOT NULL,
    low_threshold    REAL
);

CREATE TABLE IF NOT EXISTS suppliers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    afm         TEXT,
    email       TEXT,
    phone       TEXT,
    known_ibans TEXT NOT NULL DEFAULT '[]',
    credit_days INTEGER
);

CREATE TABLE IF NOT EXISTS supplier_products (
    supplier_id   TEXT NOT NULL REFERENCES suppliers(id),
    product_id    TEXT NOT NULL REFERENCES products(id),
    supplier_code TEXT,
    pack_size     INTEGER,
    case_barcode  TEXT,
    PRIMARY KEY (supplier_id, product_id)
);

CREATE TABLE IF NOT EXISTS positions (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    product_id TEXT REFERENCES products(id),
    camera_id  TEXT
);
"""


def connect(path=None):
    """Open the database.

    The path is `path`, else the XANGX_DB environment variable, else
    data/xangx.sqlite. The parent folder is created if missing.
    ":memory:" is accepted (used by the tests).
    """
    target = path or os.environ.get("XANGX_DB") or DEFAULT_DB_PATH
    target = str(target)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn):
    """Create all tables, indexes and triggers. Safe to run again."""
    conn.executescript(SCHEMA)
    columns = [r[1] for r in conn.execute("PRAGMA table_info(supplier_products)")]
    if "case_barcode" not in columns:
        conn.execute("ALTER TABLE supplier_products ADD COLUMN case_barcode TEXT")
    conn.commit()
