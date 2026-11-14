"""Team access code for the whole site, so it can go online through a tunnel.

`XANGX_ACCESS_CODE` empty or unset: no login at all (local work as before).
Set: every request needs the session cookie, except GET/POST /login and GET /healthz.
"""

import hashlib
import hmac
import html
import json
import os
import time
from urllib.parse import parse_qs, parse_qsl, quote, urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

COOKIE = "xangx_session"
MAX_AGE = 7 * 24 * 3600
MAX_FAILS = 5
WINDOW_SECONDS = 300
NEED_CODE = {"detail": "Χρειάζεται κωδικός ομάδας."}

_fails = {}  # client ip -> times of failed attempts


def reset_attempts():
    _fails.clear()


def _code():
    return (os.environ.get("XANGX_ACCESS_CODE") or "").strip()


def _secret(code):
    explicit = os.environ.get("XANGX_SESSION_SECRET") or ""
    if explicit:
        return explicit.encode("utf-8")
    return hmac.new(code.encode("utf-8"), b"xangx-session-secret", hashlib.sha256).digest()


def _token(code):
    return hmac.new(_secret(code), b"ok", hashlib.sha256).hexdigest()


def _is_logged_in(request, code):
    cookie = request.cookies.get(COOKIE) or ""
    return hmac.compare_digest(cookie.encode("utf-8"), _token(code).encode("utf-8"))


def _client_ip(request):
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded.strip():
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _recent_fails(ip):
    now = time.monotonic()
    kept = [t for t in _fails.get(ip, []) if now - t < WINDOW_SECONDS]
    if kept:
        _fails[ip] = kept
    else:
        _fails.pop(ip, None)
    return kept


def _is_locked(ip):
    return len(_recent_fails(ip)) >= MAX_FAILS


def _count_fail(ip):
    _recent_fails(ip)
    _fails.setdefault(ip, []).append(time.monotonic())


def _is_https(request):
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return proto == "https" or request.url.scheme == "https"


def _safe_next(value):
    """Only a relative path on this site; anything else goes to the home page."""
    if (value and value.startswith("/") and not value.startswith("//")
            and "\\" not in value and "\r" not in value and "\n" not in value):
        return value
    return "/"


def _redirect(location, cookie_for=None, request=None):
    response = Response(status_code=303, headers={
        "Location": quote(location, safe="/?=&%:@+,;~-._!*'()"),
        "Cache-Control": "no-store",
    })
    if cookie_for is not None:
        response.set_cookie(
            COOKIE, _token(cookie_for), max_age=MAX_AGE, httponly=True,
            samesite="lax", secure=_is_https(request), path="/",
        )
    return response


PAGE = """<!doctype html>
<html lang="el">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Xangxothaika</title>
<style>
  :root { color-scheme: light dark; --bg:#f6f6f4; --fg:#1c1c1a; --card:#ffffff;
          --line:#c9c9c4; --accent:#2f4f4f; --accent-fg:#ffffff; --err:#a12a2a; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#161615; --fg:#ecebe7; --card:#222221; --line:#4a4a47;
            --accent:#9fc2bd; --accent-fg:#101010; --err:#ff9b9b; } }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         padding:16px; background:var(--bg); color:var(--fg);
         font:16px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { width:100%; max-width:360px; background:var(--card); border:1px solid var(--line);
         border-radius:12px; padding:24px; }
  h1 { margin:0 0 20px; font-size:24px; }
  label { display:block; margin-bottom:6px; font-weight:600; }
  input[type=password] { width:100%; min-height:48px; padding:0 12px; font-size:16px;
         color:var(--fg); background:var(--bg); border:1px solid var(--line); border-radius:8px; }
  button { width:100%; min-height:48px; margin-top:16px; font-size:16px; font-weight:600;
         color:var(--accent-fg); background:var(--accent); border:0; border-radius:8px; cursor:pointer; }
  .err { margin:12px 0 0; color:var(--err); font-weight:600; }
</style>
</head>
<body>
<main>
  <h1>Xangxothaika</h1>
  <form method="post" action="/login">
    <label for="code">Κωδικός ομάδας</label>
    <input id="code" name="code" type="password" autocomplete="current-password" required autofocus>
    <input type="hidden" name="next" value="__NEXT__">
    __ERROR__
    <button type="submit">Μπες</button>
  </form>
</main>
</body>
</html>
"""


def _login_page(next_path, error=None, status=200):
    err = f'<p class="err" role="alert">{html.escape(error)}</p>' if error else ""
    body = PAGE.replace("__NEXT__", html.escape(_safe_next(next_path), quote=True))
    body = body.replace("__ERROR__", err)
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def _locked_page(next_path):
    return _login_page(next_path, "Πάρα πολλές προσπάθειες. Δοκίμασε ξανά σε λίγα λεπτά.", 429)


def _wants_page(request):
    return (request.method == "GET" and "text/html" in request.headers.get("accept", "")
            and not request.url.path.startswith("/api/"))


async def access_middleware(request: Request, call_next):
    path = request.url.path
    if path == "/healthz" and request.method == "GET":
        return JSONResponse({"ok": True})

    code = _code()
    if not code:
        if path == "/login":
            return _redirect("/")
        return await call_next(request)

    ip = _client_ip(request)

    if path == "/login" and request.method == "GET":
        return _login_page(request.query_params.get("next", "/"))

    if path == "/login" and request.method == "POST":
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        given = (form.get("code") or [""])[0]
        next_path = _safe_next((form.get("next") or ["/"])[0])
        if _is_locked(ip):
            return _locked_page(next_path)
        if hmac.compare_digest(given.encode("utf-8"), code.encode("utf-8")):
            return _redirect(next_path, cookie_for=code, request=request)
        _count_fail(ip)
        return _login_page(next_path, "Λάθος κωδικός", 401)

    # Link login for the phone camera: ?code=<code> on any page.
    if request.method == "GET" and "code" in request.query_params:
        if _is_locked(ip):
            return _locked_page("/")
        given = request.query_params.get("code", "")
        if hmac.compare_digest(given.encode("utf-8"), code.encode("utf-8")):
            rest = [(k, v) for k, v in parse_qsl(request.url.query, keep_blank_values=True)
                    if k != "code"]
            target = path + ("?" + urlencode(rest) if rest else "")
            return _redirect(target, cookie_for=code, request=request)
        _count_fail(ip)

    if _is_logged_in(request, code):
        return await call_next(request)

    if _wants_page(request):
        rest = [(k, v) for k, v in parse_qsl(request.url.query, keep_blank_values=True)
                if k != "code"]
        target = path + ("?" + urlencode(rest) if rest else "")
        return _redirect("/login?next=" + quote(target, safe=""))
    return Response(json.dumps(NEED_CODE, ensure_ascii=False), status_code=401,
                    media_type="application/json")
