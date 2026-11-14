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
FONT_DIR = "/static/vendor/"  # the login page loads its font and icons from here, before any login

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
    # Never read X-Forwarded-For here: the visitor writes it, so it can be changed
    # on every request to dodge the limit. Cloudflare always overwrites
    # CF-Connecting-IP; otherwise uvicorn already put the real IP in client.host.
    cf_ip = request.headers.get("cf-connecting-ip", "").strip()
    if cf_ip:
        return cf_ip
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
  /* Η σελίδα είναι αυτόνομη: τα χρώματα και τα μεγέθη είναι από το design/kateuthynseis-v2 (#syndesi). */
  @font-face { font-family:"Ubuntu Sans"; font-style:normal; font-weight:400 700; font-display:swap;
    src:url(/static/vendor/ubuntu-sans/ubuntu-sans-greek.woff2) format("woff2");
    unicode-range:U+0370-0377,U+037A-037F,U+0384-038A,U+038C,U+038E-03A1,U+03A3-03FF; }
  @font-face { font-family:"Ubuntu Sans"; font-style:normal; font-weight:400 700; font-display:swap;
    src:url(/static/vendor/ubuntu-sans/ubuntu-sans-latin.woff2) format("woff2");
    unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD; }
  @font-face { font-family:"Phosphor"; src:url(/static/vendor/phosphor/Phosphor.woff2) format("woff2"); font-display:block; }
  :root { --band:#0A1B33; --band2:#0E2444; --n2:#16305A; --n3:#3A5A86; --page:#E2E7EE; --surf:#FFFFFF;
          --line:#C6CFDB; --ink:#0B1626; --muted:#4B586B; --alert:#A3351B; --accent:#FFC531;
          --accent-h:#FFD055; --on-accent:#0A1B33; --xl:48px;
          --sh-stage:0 1px 2px rgba(2,10,22,.30),0 10px 20px -8px rgba(2,10,22,.35),0 36px 64px -24px rgba(2,10,22,.60); }
  * { box-sizing:border-box; margin:0; padding:0; }
  html, body { background:var(--page); }
  body { font-family:"Ubuntu Sans", system-ui, sans-serif; color:var(--ink); font-size:14px; line-height:1.5;
         -webkit-font-smoothing:antialiased; }
  :focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
  .ph { font-family:"Phosphor"; font-style:normal; font-weight:normal; line-height:1; speak:never; }
  .ph-key:before { content:"\\e2d6"; }
  .ph-arrow-right:before { content:"\\e06c"; }
  .login { min-height:100vh; display:flex; align-items:center; justify-content:center; padding:40px 16px;
           background:linear-gradient(180deg,var(--band) 0%,var(--band2) 100%); }
  .card { width:440px; max-width:100%; background:var(--surf); border:1px solid var(--line); border-radius:18px;
          box-shadow:var(--sh-stage); overflow:hidden; }
  .lb { padding:30px 32px 0; }
  .lb b { display:block; font-size:var(--xl); line-height:1.05; letter-spacing:-.02em; }
  .lb p { margin-top:8px; color:var(--muted); }
  .shelfrow { display:flex; gap:7px; align-items:flex-end; margin:22px 32px 0; border-bottom:3px solid var(--line); }
  .can { width:18px; height:30px; border-radius:7px 7px 4px 4px;
         background:linear-gradient(180deg,#EEF2F7 0%,#B9C6D8 100%); box-shadow:inset 0 3px 0 rgba(255,255,255,.6); }
  .fld { padding:24px 32px 30px; }
  label { display:block; font-weight:700; margin-bottom:8px; }
  .box { display:flex; align-items:center; height:52px; margin-bottom:14px; padding:0 14px;
         border:1px solid var(--line); border-radius:12px; background:var(--surf);
         box-shadow:inset 0 1px 2px rgba(10,27,51,.06); }
  .box:focus-within { outline:2px solid var(--accent); outline-offset:2px; }
  .box i { font-size:20px; margin-right:10px; color:var(--n3); }
  .box input { flex:1; min-width:0; height:100%; border:0; background:transparent; outline:none;
               font:inherit; font-size:16px; color:var(--ink); }
  .btn { display:flex; align-items:center; justify-content:center; gap:8px; width:100%; height:52px; border:0;
         border-radius:999px; background:var(--accent); color:var(--on-accent); font:700 14px/1 "Ubuntu Sans", system-ui, sans-serif;
         cursor:pointer; box-shadow:inset 0 -2px 0 rgba(10,27,51,.14); transition:transform .14s cubic-bezier(.22,1,.36,1), background-color .16s cubic-bezier(.22,1,.36,1); }
  .btn:hover { background:var(--accent-h); }
  .btn:active { transform:scale(.97); }
  .btn i { font-size:18px; }
  .note { margin-top:14px; color:var(--muted); }
  .err { margin:0 0 14px; padding:10px 14px; border-radius:12px; background:#FBEFEB; color:var(--alert); font-weight:700; }
  @media (max-width:720px) { :root { --xl:30px; } .lb { padding:26px 22px 0; } .fld { padding:20px 22px 26px; }
                              .shelfrow { margin:22px 22px 0; } }
  @media (prefers-reduced-motion:reduce) { * { transition:none !important; } }
</style>
</head>
<body>
<main class="login">
  <div class="card">
    <div class="lb">
      <b>Xangxothaika</b>
      <p>Mini Market 24/7 · Παγκράτι</p>
    </div>
    <div class="shelfrow" aria-hidden="true"><i class="can"></i><i class="can"></i><i class="can"></i><i class="can"></i><i class="can"></i><i class="can"></i><i class="can"></i><i class="can"></i></div>
    <form class="fld" method="post" action="/login">
      <label for="code">Κωδικός ομάδας</label>
      <span class="box"><i class="ph ph-key" aria-hidden="true"></i><input id="code" name="code" type="password" autocomplete="current-password" required autofocus></span>
      __ERROR__
      <input type="hidden" name="next" value="__NEXT__">
      <button class="btn" type="submit">Μπες<i class="ph ph-arrow-right" aria-hidden="true"></i></button>
      <p class="note">Τον κωδικό τον δίνει ο ιδιοκτήτης.</p>
    </form>
  </div>
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


def _is_font_file(path):
    """Only the .woff2 font files under /static/vendor/ are open without a login."""
    return path.startswith(FONT_DIR) and path.endswith(".woff2") and ".." not in path


def _wants_page(request):
    return (request.method == "GET" and "text/html" in request.headers.get("accept", "")
            and not request.url.path.startswith("/api/"))


async def access_middleware(request: Request, call_next):
    path = request.url.path
    if path == "/healthz" and request.method == "GET":
        return JSONResponse({"ok": True})

    # The MCP door has its own bearer token (xangx/server/mcp.py), not the team cookie.
    # Exact path only: /mcpx or /mcp/anything stay locked.
    if path == "/mcp":
        return await call_next(request)

    if request.method == "GET" and _is_font_file(path):
        return await call_next(request)

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
