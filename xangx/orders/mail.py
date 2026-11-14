"""Order emails to suppliers, with the standard library only (smtplib).

Settings from the environment (.env): SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM.
Without SMTP_HOST the email is written to data/outbox/<order_id>.eml instead (rehearsals).
The only allowed recipient is the supplier's email, or XANGX_DEMO_SUPPLIER_EMAIL when set.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

STORE_NAME = "Mini Market 24/7, Παγκράτι"


class MailError(RuntimeError):
    """The email could not be sent (the order is not marked as sent)."""


def recipient(supplier_email):
    """The demo address wins; otherwise the supplier's own email. Never anything else."""
    to = (os.environ.get("XANGX_DEMO_SUPPLIER_EMAIL") or supplier_email or "").strip()
    if "@" not in to or any(c in to for c in ",;\r\n "):
        raise MailError("Δεν υπάρχει σωστό email για τον προμηθευτή.")
    return to


def outbox_dir():
    files = os.environ.get("XANGX_FILES")
    return Path(files).parent / "outbox" if files else Path("data/outbox")


def build(order_id, to, subject, body):
    msg = EmailMessage()
    sender = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "orders@xangxothaika.invalid"
    msg["From"] = f"{STORE_NAME} <{sender}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(idstring=order_id, domain=sender.split("@")[-1])
    msg.set_content(body)
    return msg


def send(order_id, msg):
    """Send by SMTP, or write to the outbox without SMTP. Returns (message_id, how)."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        out = outbox_dir() / f"{order_id}.eml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(bytes(msg))
        return msg["Message-ID"], "outbox"
    port = int(os.environ.get("SMTP_PORT") or 587)
    user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            server.starttls(context=ssl.create_default_context())
        with server:
            if user:
                server.login(user, password or "")
            server.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(f"Το email δεν έφυγε: {type(exc).__name__}") from exc
    return msg["Message-ID"], "smtp"
