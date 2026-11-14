"""Invoices that arrive by email (spec part 1, step 2).

The mailbox is the same one the orders role reads (IMAP_* in .env), and its reader is
reused as is (xangx.orders.replies: settings, read-only fetch, parsing). From each email we
take only the attached invoice files (PDF, JPG, PNG) and only when the sender is a known
supplier address, or XANGX_DEMO_SUPPLIER_EMAIL in the demo. The attachment then follows the
same path as a file dropped in data/inbox/ (invoice.received, source "email", from).
The email text is never read or executed; the attachment is read later as data.
"""

import hashlib
import os
from pathlib import Path

from xangx.invoices import core
from xangx.orders import replies

MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
CONTENT_EXT = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}


def allowed_senders(conn):
    """Supplier email addresses, plus the demo stand-in, all lower case."""
    rows = conn.execute("SELECT email FROM suppliers WHERE email IS NOT NULL AND email != ''")
    out = {r["email"].strip().lower() for r in rows}
    demo = (os.environ.get("XANGX_DEMO_SUPPLIER_EMAIL") or "").strip().lower()
    if demo:
        out.add(demo)
    return out


def invoice_attachments(msg):
    """[(ext, bytes)] for the attached PDF/JPG/PNG files, at most MAX_ATTACHMENTS."""
    out = []
    for part in msg.iter_attachments():
        ext = Path(part.get_filename() or "").suffix.lower()
        if ext not in core.INBOX_EXT:
            ext = CONTENT_EXT.get(part.get_content_type(), "")
        if ext not in core.INBOX_EXT:
            continue
        data = part.get_payload(decode=True) or b""
        if 0 < len(data) <= MAX_ATTACHMENT_BYTES:
            out.append((ext, data))
        if len(out) >= MAX_ATTACHMENTS:
            break
    return out


def handle_email(conn, raw):
    """invoice.received for each invoice attached to an email from a supplier. Returns new ids."""
    if not raw or len(raw) > replies.MAX_EMAIL_BYTES:
        return []
    msg = replies.parse(raw)
    senders = replies._addresses(msg.get("From")) & allowed_senders(conn)
    if not senders:
        return []
    sender = sorted(senders)[0]
    landing = core.data_dir() / "email_in"
    landing.mkdir(parents=True, exist_ok=True)
    new = []
    for ext, data in invoice_attachments(msg):
        sha = hashlib.sha256(data).hexdigest()
        if conn.execute("SELECT 1 FROM events WHERE idem_key = ?", (f"file:{sha}",)).fetchone():
            continue  # seen on an earlier pass (the mailbox is read again every time)
        path = landing / f"{sha}{ext}"
        path.write_bytes(data)
        event, created = core.ingest_file(conn, path, source="email", sender=sender)
        if created:
            new.append(event.payload["invoice_id"])
    return new


def poll(conn, settings=None):
    """One read-only pass over the mailbox. Returns the new invoice ids."""
    settings = settings or replies.imap_settings()
    if settings is None:
        return []
    new = []
    for raw in replies.fetch_recent(settings):
        new.extend(handle_email(conn, raw))
    return new
