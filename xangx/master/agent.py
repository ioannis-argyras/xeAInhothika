"""The master agent: a manual tool loop with approval gates.

Public functions (blocking; called from a thread pool by the server):
    handle_owner_message(conn, text, channel="web") -> {"reply", "event_ids"}
    handle_trigger(conn, reason, trigger_ids, instruction=None) -> {"reply", "event_ids"}
    decide_approval(conn, approval_id, decision, channel="web") -> {"ok", "message", "event_ids"}
    morning_check(conn) -> event id of check.morning_done
    start_background(db_path=None) -> None
"""

import json
import os
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from xangx.master import tools as tool_catalog
from xangx.master.prompt import SYSTEM_PROMPT
from xangx.screen.describe import load_names
from xangx.shared import append_event, read_events
from xangx.shared import payments, views
from xangx.shared.money import format_eur

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 4096
MAX_ROUNDS = 8
HISTORY_EVENTS = 8
TEXT_LIMIT = 4000
RESULT_LIMIT = 20000

TEST_MODE_NOTE = "(δοκιμαστική λειτουργία χωρίς Claude)"
MSG_REFUSAL = "Δεν μπορώ να το κάνω αυτό."
MSG_PAUSED = "Είμαι σε παύση. Δεν κάνω καμία ενέργεια μέχρι να πατήσεις «Συνέχισε»."
MSG_API_ERROR = "Δεν μπόρεσα να μιλήσω με το Claude αυτή τη στιγμή. Δοκίμασε ξανά σε λίγο."
MSG_TOO_LONG = "Χρειάστηκαν πάρα πολλά βήματα και σταμάτησα. Ρώτα με πιο συγκεκριμένα."
MSG_CUT = "Η απάντηση κόπηκε πριν ολοκληρωθεί, και γι' αυτό δεν έκανα τίποτα. Ξαναδοκίμασε."
TRIGGER_INSTRUCTION = (
    "Κανείς δεν σου μίλησε. Συνέβη: {reason}. Δες την κατάσταση του μαγαζιού και κάνε ό,τι χρειάζεται, "
    "με τα εργαλεία· ό,τι θέλει έγκριση γίνεται κάρτα. Αν δεν χρειάζεται τίποτα, πες το σε μία πρόταση."
)

_background_started = False


# --- Claude client ----------------------------------------------------------

def use_fake_llm():
    return os.environ.get("XANGX_FAKE_LLM") == "1" or not os.environ.get("ANTHROPIC_API_KEY")


def _get_client():
    """The Anthropic client, or the rule-based fake one in test mode."""
    if use_fake_llm():
        return FakeClient()
    import anthropic

    return anthropic.Anthropic()


def _create(client, messages):
    effort = os.environ.get("XANGX_EFFORT") or "low"
    return client.beta.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=tool_catalog.tool_definitions(),
        messages=messages,
        output_config={"effort": effort},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )


# --- Fake Claude (screen demos and tests) -------------------------------------

def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _response(stop_reason, content):
    return SimpleNamespace(stop_reason=stop_reason, content=content, stop_details=None)


class _FakeMessages:
    def create(self, **kwargs):
        messages = kwargs["messages"]
        last = messages[-1]["content"]
        if isinstance(last, list) and last and last[0].get("type") == "tool_result":
            try:
                status = json.loads(last[0]["content"])
            except (ValueError, TypeError):
                status = {}
            return _response("end_turn", [_text_block(_fake_summary(status))])
        return _response(
            "tool_use",
            [SimpleNamespace(type="tool_use", id="fake-1", name="get_store_status", input={})],
        )


class FakeClient:
    """Rule-based stand-in: always calls get_store_status, then summarizes it."""

    def __init__(self):
        self.beta = SimpleNamespace(messages=_FakeMessages())


def _fake_summary(status):
    pending = status.get("pending_approvals") or []
    low = status.get("low_products") or []
    sales = status.get("sales_today") or {}
    parts = []
    if status.get("paused"):
        parts.append("Το σύστημα είναι σε παύση.")
    if pending:
        parts.append(f"Σε περιμένουν {len(pending)} εγκρίσεις: {pending[0].get('summary')}")
    else:
        parts.append("Δεν περιμένει καμία έγκριση.")
    if low:
        names = ", ".join(f"{p['name']} ({p['on_hand']:g})" for p in low[:5])
        parts.append(f"Σε χαμηλό είναι: {names}. Γιατί: έχουν πέσει κάτω από το όριό τους.")
    else:
        parts.append("Κανένα προϊόν δεν είναι σε χαμηλό.")
    parts.append(
        f"Σήμερα έγιναν {sales.get('sales_count', 0)} πωλήσεις, "
        f"σύνολο {format_eur(sales.get('sales_cents', 0))}."
    )
    parts.append(TEST_MODE_NOTE)
    return " ".join(parts)


# --- Small helpers --------------------------------------------------------------

def _first_sentence(text):
    return text.split(".")[0].strip() if text else ""


def _echo_content(content):
    """Content to send back as the assistant turn.

    After a mid-output fallback, thinking and tool_use blocks before the final
    `fallback` block must be left out (the `fallback` block itself stays).
    """
    content = list(content)
    last_fallback = -1
    for i, block in enumerate(content):
        if getattr(block, "type", None) == "fallback":
            last_fallback = i
    if last_fallback < 0:
        return content
    drop = {"thinking", "redacted_thinking", "tool_use"}
    return [
        b for i, b in enumerate(content)
        if not (i < last_fallback and getattr(b, "type", None) in drop)
    ]


def _text_of(content):
    return "".join(b.text for b in content if getattr(b, "type", None) == "text").strip()


def _history_messages(conn, before_id):
    """The last few owner/agent messages, so "yes, do it" has something to refer to."""
    events = read_events(conn, types=["owner.said", "agent.said"], until_id=before_id - 1)
    messages = []
    for e in events[-HISTORY_EVENTS:]:
        role = "user" if e.type == "owner.said" else "assistant"
        messages.append({"role": role, "content": str(e.payload.get("text", ""))})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


def _write(conn, ids, **kwargs):
    event, _ = append_event(conn, **kwargs)
    ids.append(event.id)
    return event


def _run_tool(conn, ids, name, args, caused_by):
    """Run a tool and write agent.tool_ran. Returns (ok, result_dict)."""
    started = time.monotonic()
    t = tool_catalog.TOOLS.get(name)
    ok = True
    if t is None:
        ok, result = False, {"error": f"Άγνωστο εργαλείο: {name}"}
    else:
        try:
            result = t.fn(conn, **(args or {}))
            if not isinstance(result, dict):
                result = {"result": result}
        except Exception as exc:  # a failing tool must not stop the loop
            ok, result = False, {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    ms = int((time.monotonic() - started) * 1000)
    summary = result.get("summary") or (
        f"Έτρεξα το {name}" if ok else f"Το {name} απέτυχε: {result.get('error', '')}"
    )
    event_ids = [i for i in result.get("event_ids", []) if isinstance(i, int)]
    _write(
        conn, ids,
        type="agent.tool_ran", actor="master", caused_by=caused_by,
        payload={
            "tool": name, "summary": str(summary)[:300], "ok": ok,
            "ms": ms, "result_event_ids": event_ids,
        },
    )
    return ok, result


def _tool_result_block(tool_use_id, result, is_error=False):
    text = json.dumps(result, ensure_ascii=False, default=str)[:RESULT_LIMIT]
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
    if is_error:
        block["is_error"] = True
    return block


def _request_approval(conn, ids, t, args, approval_id, caused_by):
    info = t.describe(args) if t.describe else {}
    event = _write(
        conn, ids,
        type="approval.requested", actor="master", caused_by=caused_by,
        subject=f"approval:{approval_id}",
        idem_key=f"approval.requested:{approval_id}",
        payload={
            "approval_id": approval_id,
            "action": t.approval_action or t.name,
            "summary": str(info.get("summary") or args.get("summary") or _first_sentence(t.description))[:300],
            "exact": str(info.get("exact") or json.dumps(args, ensure_ascii=False))[:1000],
            "why": str(info.get("why") or args.get("why") or "")[:500],
            "ref": str(info.get("ref") or args.get("ref") or args.get("order_id") or args.get("invoice_id") or ""),
            "tool": t.name,
            "args": args,
        },
    )
    return event


# --- The conversation -------------------------------------------------------------

def handle_owner_message(conn, text, channel="web"):
    """Write owner.said, run the tool loop, write agent.said. Blocking."""
    tool_catalog.load_role_tools()
    ids = []
    said = _write(
        conn, ids,
        type="owner.said", actor="owner",
        payload={"text": str(text)[:TEXT_LIMIT], "channel": channel},
    )
    return _run_loop(conn, ids, said, text, channel)


def handle_trigger(conn, reason, trigger_ids, instruction=None):
    """The partner starts on its own: write agent.woke, run the same loop, write agent.said
    (channel "auto"). With the system paused it only says so. Blocking."""
    tool_catalog.load_role_tools()
    ids = []
    woke = _write(
        conn, ids,
        type="agent.woke", actor="master",
        payload={"reason": str(reason)[:300], "trigger_ids": [i for i in trigger_ids if isinstance(i, int)]},
    )
    text = instruction or TRIGGER_INSTRUCTION.format(reason=reason)
    return _run_loop(conn, ids, woke, text, "auto")


def _run_loop(conn, ids, start, text, channel):
    """The tool loop. `start` is the event that began it (owner.said or agent.woke)."""

    def finish(reply):
        _write(
            conn, ids,
            type="agent.said", actor="master", caused_by=start.id,
            payload={"text": reply[:TEXT_LIMIT], "channel": channel},
        )
        return {"reply": reply, "event_ids": ids}

    if views.is_paused(conn):
        return finish(MSG_PAUSED)

    client = _get_client()
    messages = _history_messages(conn, start.id) + [{"role": "user", "content": str(text)[:TEXT_LIMIT]}]
    planned = False
    approvals = 0

    for _ in range(MAX_ROUNDS):
        try:
            response = _create(client, messages)
        except Exception:  # network, auth, rate limit: tell the owner, keep going later
            return finish(MSG_API_ERROR)

        stop = response.stop_reason
        if stop == "refusal":
            return finish(MSG_REFUSAL)
        content = _echo_content(response.content)
        tool_uses = [b for b in content if getattr(b, "type", None) == "tool_use"]

        if stop == "max_tokens" and tool_uses:
            return finish(MSG_CUT)
        if stop == "pause_turn":
            messages.append({"role": "assistant", "content": content})
            continue
        if stop != "tool_use" or not tool_uses:
            return finish(_text_of(content) or "Έγινε.")

        if not planned:
            planned = True
            steps = [
                {
                    "tool": b.name,
                    "summary": _first_sentence(
                        getattr(tool_catalog.TOOLS.get(b.name), "description", "") or b.name
                    ),
                }
                for b in tool_uses
            ]
            _write(
                conn, ids,
                type="agent.planned", actor="master", caused_by=start.id,
                payload={"steps": steps},
            )

        results = []
        for block in tool_uses:
            t = tool_catalog.TOOLS.get(block.name)
            args = dict(block.input or {})
            if t is not None and t.needs_approval:
                approvals += 1
                approval_id = f"A-{start.id}" + (f"-{approvals}" if approvals > 1 else "")
                _request_approval(conn, ids, t, args, approval_id, start.id)
                results.append(
                    _tool_result_block(
                        block.id,
                        {"status": f"Περιμένει έγκριση του ιδιοκτήτη ({approval_id})"},
                    )
                )
            else:
                ok, result = _run_tool(conn, ids, block.name, args, start.id)
                results.append(_tool_result_block(block.id, result, is_error=not ok))

        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": results})

    return finish(MSG_TOO_LONG)


def decide_approval(conn, approval_id, decision, channel="web"):
    """Record the owner's decision. On "yes", run the stored tool call once."""
    ids = []
    if decision not in ("yes", "no"):
        return {"ok": False, "message": "Η απόφαση πρέπει να είναι «ναι» ή «όχι».", "event_ids": ids}
    tool_catalog.load_role_tools()

    requested = next(
        (
            e for e in read_events(conn, types=["approval.requested"])
            if e.payload.get("approval_id") == approval_id
        ),
        None,
    )
    if requested is None:
        return {"ok": False, "message": "Δεν βρήκα αυτή την έγκριση.", "event_ids": ids}

    if decision == "yes" and views.is_paused(conn):
        return {
            "ok": False,
            "message": "Είμαι σε παύση και δεν έκανα τίποτα. Πάτησε «Συνέχισε» και ξαναδοκίμασε.",
            "event_ids": ids,
        }

    decided, created = append_event(
        conn,
        type="approval.decided", actor="owner", caused_by=requested.id,
        subject=f"approval:{approval_id}",
        idem_key=f"approval.decided:{approval_id}",
        payload={"approval_id": approval_id, "decision": decision, "channel": channel},
    )
    if not created:
        return {
            "ok": False,
            "message": "Έχεις ήδη απαντήσει σε αυτή την έγκριση.",
            "event_ids": [decided.id],
        }
    ids.append(decided.id)

    if decision == "no":
        return {"ok": True, "message": "Εντάξει, δεν το κάνω.", "event_ids": ids}

    name = requested.payload.get("tool")
    if name not in tool_catalog.TOOLS:
        return {"ok": False, "message": f"Δεν βρήκα το εργαλείο «{name}».", "event_ids": ids}
    ok, result = _run_tool(conn, ids, name, requested.payload.get("args") or {}, decided.id)
    message = str(result.get("summary") or ("Έγινε." if ok else result.get("error", "Απέτυχε.")))
    return {"ok": ok, "message": message, "event_ids": ids}


def greek_today():
    """Today's date in Greece, YYYY-MM-DD."""
    now = datetime.now(timezone.utc)
    return (now + tool_catalog.greek_offset(now)).strftime("%Y-%m-%d")


def _tell_payments_due(conn, findings, day_start, today):
    """One notification per invoice per day for payments that are due today or overdue."""
    names = load_names(conn)
    for item in payments.open_payments(conn, day_start, today):
        if item["kind"] != "due":
            continue
        findings.append({"type": "payment_due", "ref": item["invoice_id"]})
        number = names["invoice"].get(item["invoice_id"]) or item["invoice_id"]
        who = names["supplier"].get(item["supplier_id"]) or str(item["supplier_id"])
        amount = format_eur(item["amount_cents"] or 0)
        if item["due_on"] == today:
            text = f"Σήμερα λήγει η πληρωμή {amount} στον {who} ({number})"
        else:
            day = "/".join(reversed(item["due_on"].split("-")))
            text = f"Έληξε στις {day} η πληρωμή {amount} στον {who} ({number})"
        append_event(
            conn, type="notification.sent", actor="master",
            payload={"channel": "all", "text": text},
            idem_key=f"payment-due:{item['invoice_id']}:{today}")


def morning_check(conn):
    """No LLM: collect what needs attention today and write check.morning_done."""
    findings = [
        {"type": "low_stock", "ref": p["product_id"]} for p in tool_catalog.low_products(conn)
    ]
    findings += [
        {"type": "approval_pending", "ref": e.payload.get("approval_id")}
        for e in views.pending_approvals(conn)
    ]
    day_start = tool_catalog.greek_day_start_utc()
    for e in read_events(conn, types=["exception.flagged"]):
        if e.at >= day_start:
            findings.append({"type": "exception", "ref": e.payload.get("ref")})
    _tell_payments_due(conn, findings, day_start, greek_today())
    event, _ = append_event(
        conn, type="check.morning_done", actor="master", payload={"findings": findings}
    )
    return event.id


def start_background(db_path=None):
    """Start the Telegram threads if a token is configured. Safe to call anytime."""
    global _background_started
    if _background_started:
        return
    if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_OWNER_CHAT_ID")):
        return
    from xangx.master import telegram

    telegram.start(db_path)
    _background_started = True
