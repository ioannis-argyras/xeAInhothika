"""MCP door: other agents use the store's tools through POST /mcp.

Streamable HTTP, JSON-RPC 2.0, one request per call, JSON answers (no SSE).
Methods: initialize, notifications/initialized, ping, tools/list, tools/call. Tools only.

The door is closed (404) unless XANGX_MCP_TOKEN is set (24+ characters). Every call needs
`Authorization: Bearer <token>`. A tool that needs the owner's yes is NEVER run here:
the owner gets the same approval card as for the partner, and the caller gets "waiting".
"""

import hmac
import importlib
import json
import os
import secrets
import threading

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from xangx.master import tools as tool_catalog
from xangx.server import access
from xangx.shared import append_event, views
from xangx.shared.db import connect, init_db
from xangx.shared.events import read_events

# Tools that other agents never see (notify_owner writes to the owner's screen and Telegram).
EXCLUDED_TOOLS = frozenset({"notify_owner"})

# Greek titles shown on the owner's screen and in tools/list "title" (descriptions stay English).
TITLES_EL = {
    "get_store_status": "Κατάσταση του μαγαζιού",
    "list_recent_events": "Πρόσφατα γεγονότα",
    "list_open_invoices": "Τιμολόγια που θέλουν προσοχή",
    "process_invoice": "Έλεγχος τιμολογίου",
    "set_payment_reminder": "Υπενθύμιση πληρωμής",
    "request_credit": "Αίτημα πιστωτικού",
    "daily_report": "Αναφορά της ημέρας",
    "propose_order": "Πρόταση παραγγελίας",
    "send_order": "Αποστολή παραγγελίας",
    "delivery_status": "Κατάσταση παραλαβής",
    "compare_suppliers": "Σύγκρινε προμηθευτές",
    "expiring_soon": "Δες τι λήγει σύντομα",
    "monthly_folder": "Φάκελος του μήνα για τον λογιστή",
}

MIN_TOKEN_LENGTH = 24
MAX_BODY_BYTES = 64 * 1024
MAX_STRING = 2000
TEXT_LIMIT = 20000
PROTOCOLS = ("2025-06-18", "2025-03-26")
DEFAULT_PROTOCOL = "2025-06-18"
INSTRUCTIONS = (
    "Αυτό είναι το Xangxothaika, ο ψηφιακός συνεργάτης ενός μίνι μάρκετ· "
    "ό,τι θέλει χρήματα ή email πάει για έγκριση στον ιδιοκτήτη."
)
MSG_PAUSED = "Το μαγαζί είναι σε παύση από τον ιδιοκτήτη."
MSG_WAITING = "Περιμένει έγκριση του ιδιοκτήτη."
NEEDS_APPROVAL_NOTE = " Χρειάζεται έγκριση του ιδιοκτήτη."

router = APIRouter()
_approval_lock = threading.Lock()


# --- Small helpers -------------------------------------------------------------

def _token():
    value = (os.environ.get("XANGX_MCP_TOKEN") or "").strip()
    return value if len(value) >= MIN_TOKEN_LENGTH else None


def _fail_key(request):
    # Own counter (prefix), so a guessing agent cannot lock the owner out of the login page.
    return "mcp:" + access._client_ip(request)


def _bearer(request):
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _rpc_error(request_id, code, message, status=200):
    body = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    return JSONResponse(body, status_code=status)


def _rpc_result(request_id, result, headers=None):
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result}, headers=headers)


def _db():
    conn = connect()
    init_db(conn)
    return conn


def _tool_result(text, structured=None, is_error=False):
    result = {"content": [{"type": "text", "text": text[:TEXT_LIMIT]}], "isError": is_error}
    if structured is not None:
        result["structuredContent"] = structured
    return result


def _error_result(text):
    return _tool_result(text, is_error=True)


# --- tools/list ----------------------------------------------------------------

def _visible_tools():
    tool_catalog.load_role_tools()
    definitions = {d["name"]: d for d in tool_catalog.tool_definitions()}
    listed = []
    for name in sorted(definitions):
        if name in EXCLUDED_TOOLS:
            continue
        t = tool_catalog.TOOLS[name]
        description = definitions[name]["description"]
        if t.needs_approval:
            description += NEEDS_APPROVAL_NOTE
        entry = {"name": name, "description": description, "inputSchema": definitions[name]["input_schema"]}
        if name in TITLES_EL:
            entry["title"] = TITLES_EL[name]
        listed.append(entry)
    return listed


# --- Argument check (no library: the same rules as `strict`) ---------------------

def _type_ok(value, expected):
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def check_arguments(value, schema, where="τα ορίσματα"):
    """Return a Greek error message, or None when `value` fits `schema`."""
    expected = schema.get("type")
    if expected and not _type_ok(value, expected):
        return f"Το «{where}» πρέπει να είναι τύπου {expected}."
    if isinstance(value, str) and len(value) > MAX_STRING:
        return f"Το «{where}» είναι πολύ μεγάλο (το πολύ {MAX_STRING} χαρακτήρες)."
    if "enum" in schema and value not in schema["enum"]:
        return f"Το «{where}» πρέπει να είναι ένα από: {', '.join(map(str, schema['enum']))}."
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                return f"Λείπει το πεδίο «{name}»."
        for name, item in value.items():
            if name not in properties:
                return f"Το πεδίο «{name}» δεν υπάρχει σε αυτό το εργαλείο."
            error = check_arguments(item, properties[name], name)
            if error:
                return error
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            error = check_arguments(item, schema["items"], f"{where}[{i}]")
            if error:
                return error
    return None


# --- tools/call ------------------------------------------------------------------

def _write_marker(conn, name, t):
    """One event that says this call came from another agent through MCP.

    agent.planned already exists in the catalog; extra payload fields are allowed.
    The tool run and the approval card point back to it with caused_by.
    """
    summary = TITLES_EL.get(name) or (t.description or name).split(".")[0].strip()[:200]
    event, _ = append_event(
        conn, type="agent.planned", actor="master",
        payload={"steps": [{"tool": name, "summary": summary}], "via": "mcp"},
    )
    return event


def _pending_same(conn, name, args):
    for e in views.pending_approvals(conn):
        if e.payload.get("tool") == name and (e.payload.get("args") or {}) == args:
            return e
    return None


def _waiting(approval_event):
    p = approval_event.payload
    summary = str(p.get("summary") or "")
    structured = {"status": "waiting_for_owner", "approval_id": p.get("approval_id"), "summary": summary}
    return _tool_result(f"{MSG_WAITING} {summary}".strip(), structured)


def _call_sync(name, args):
    agent = importlib.import_module("xangx.master.agent")
    t = tool_catalog.TOOLS[name]
    conn = _db()
    try:
        if views.is_paused(conn):
            return _error_result(MSG_PAUSED)
        if t.needs_approval:
            with _approval_lock:  # two identical calls at once must give one card
                existing = _pending_same(conn, name, args)
                if existing is not None:
                    return _waiting(existing)
                marker = _write_marker(conn, name, t)
                try:
                    event = agent._request_approval(conn, [], t, args, f"A-{marker.id}", marker.id)
                except Exception as exc:
                    return _error_result(f"Δεν μπόρεσα να ετοιμάσω την έγκριση: {type(exc).__name__}.")
            return _waiting(event)
        marker = _write_marker(conn, name, t)
        ok, result = agent._run_tool(conn, [], name, args, marker.id)
        summary = str(result.get("summary") or "")
        if not ok:
            return _error_result(str(result.get("error") or summary or "Το εργαλείο απέτυχε."))
        body = json.dumps(result, ensure_ascii=False, default=str)
        text = f"{summary}\n{body}" if summary else body
        return _tool_result(text, json.loads(body))
    finally:
        conn.close()


def _tools_call(request_id, params):
    if not isinstance(params, dict):
        return _rpc_error(request_id, -32602, "Τα params πρέπει να είναι αντικείμενο.")
    tool_catalog.load_role_tools()
    name = params.get("name")
    if not isinstance(name, str) or name in EXCLUDED_TOOLS or name not in tool_catalog.TOOLS:
        return _rpc_error(request_id, -32602, "Άγνωστο εργαλείο.")
    args = params.get("arguments")
    args = {} if args is None else args
    schema = tool_catalog.TOOLS[name].input_schema
    error = check_arguments(args, {**schema, "type": "object"})
    if error:
        return _rpc_result(request_id, _error_result(error))
    return None  # valid: the caller runs it in a thread


# --- The door --------------------------------------------------------------------

@router.get("/mcp")
async def mcp_get():
    if _token() is None:
        return Response(status_code=404)
    return Response(status_code=405, headers={"Allow": "POST"})


@router.post("/mcp")
async def mcp_post(request: Request):
    expected = _token()
    if expected is None:
        return Response(status_code=404)
    origin = request.headers.get("origin")
    if origin is not None and origin.strip().lower() != "null":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    key = _fail_key(request)
    if access._is_locked(key):
        return JSONResponse({"error": "too many attempts"}, status_code=429, headers={"Retry-After": "300"})
    if not hmac.compare_digest(_bearer(request).encode("utf-8"), expected.encode("utf-8")):
        access._count_fail(key)
        return JSONResponse(
            {"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
        )
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return JSONResponse({"error": "too large"}, status_code=413)
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return JSONResponse({"error": "too large"}, status_code=413)

    try:
        message = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _rpc_error(None, -32700, "Μη έγκυρο JSON.")
    if isinstance(message, list):
        return _rpc_error(None, -32600, "Δεν δεχόμαστε batch, ένα αίτημα τη φορά.")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _rpc_error(None, -32600, "Μη έγκυρο αίτημα JSON-RPC 2.0.")
    if "id" not in message:  # notification, or a response from the client: nothing to answer
        return Response(status_code=202)
    request_id = message["id"]
    method = message.get("method")
    params = message.get("params")
    if not isinstance(method, str):
        return _rpc_error(request_id, -32600, "Λείπει το method.")

    if method == "initialize":
        asked = params.get("protocolVersion") if isinstance(params, dict) else None
        result = {
            "protocolVersion": asked if asked in PROTOCOLS else DEFAULT_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "xangxothaika", "version": "0.1"},
            "instructions": INSTRUCTIONS,
        }
        return _rpc_result(request_id, result, headers={"Mcp-Session-Id": secrets.token_hex(16)})
    if method == "ping":
        return _rpc_result(request_id, {})
    if method == "tools/list":
        return _rpc_result(request_id, {"tools": _visible_tools()})
    if method == "tools/call":
        early = _tools_call(request_id, params)
        if early is not None:
            return early
        result = await run_in_threadpool(_call_sync, params["name"], {} if params.get("arguments") is None else params["arguments"])
        return _rpc_result(request_id, result)
    return _rpc_error(request_id, -32601, "Άγνωστη μέθοδος.")
