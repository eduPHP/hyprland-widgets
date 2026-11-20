"""Google Calendar over its REST API with a loopback OAuth sign-in (stdlib only).

`hyprcal auth google` opens the consent page; the refresh token is kept in
~/.local/share/hyprcal/google-token.json (mode 600). Scope is read-only.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import config

TOKEN = config.DATA / "google-token.json"
SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"


class AuthNeeded(Exception):
    pass


def _client(cfg: dict) -> tuple[str, str]:
    path = Path(os.path.expanduser(cfg["google"]["client_file"]))
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise AuthNeeded(f"no OAuth client: see {path.name} in the README, or use ics =") from None
    c = data.get("installed") or data.get("web") or data
    return c["client_id"], c.get("client_secret", "")


def _post(url: str, fields: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _save(tok: dict) -> None:
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(tok, f)


def auth(cfg: dict) -> None:
    """Interactive sign-in: local redirect server + PKCE, then store the refresh token."""
    cid, secret = _client(cfg)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            ok = "code" in got and got.get("state") == state
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(("hyprcal is signed in. You can close this tab." if ok else
                              "hyprcal sign-in failed. Check the terminal.").encode())

        def log_message(self, *_):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "access_type": "offline", "prompt": "consent"})
    print(f"Opening the Google sign-in page. If nothing opens, visit:\n\n  {url}\n")
    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    srv.timeout = 5
    deadline = time.time() + 300
    while "code" not in got and "error" not in got:
        if time.time() > deadline:
            raise SystemExit("timed out waiting for the browser")
        srv.handle_request()
    srv.server_close()
    if got.get("state") != state or "code" not in got:
        raise SystemExit(f"sign-in failed: {got.get('error', 'state mismatch')}")
    tok = _post(TOKEN_URL, {"client_id": cid, "client_secret": secret, "code": got["code"],
                            "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect})
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    _save(tok)
    print(f"Signed in. Token saved to {TOKEN}")


def _access(cfg: dict) -> str:
    _client(cfg)  # a missing OAuth client is the first thing to fix, so report it first
    try:
        tok = json.loads(TOKEN.read_text())
    except (OSError, ValueError):
        raise AuthNeeded("not signed in: run `hyprcal auth google`") from None
    if tok.get("expires_at", 0) > time.time():
        return tok["access_token"]
    if not tok.get("refresh_token"):
        raise AuthNeeded("sign-in expired: run `hyprcal auth google`")
    cid, secret = _client(cfg)
    try:
        new = _post(TOKEN_URL, {"client_id": cid, "client_secret": secret,
                                "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
    except urllib.error.HTTPError as e:
        if e.code in (400, 401):
            raise AuthNeeded("sign-in revoked: run `hyprcal auth google`") from None
        raise
    tok.update(new, expires_at=time.time() + new.get("expires_in", 3600) - 60)
    _save(tok)
    return tok["access_token"]


def _get(cfg: dict, path: str, params: dict | None = None) -> dict:
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_access(cfg)}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def calendars(cfg: dict) -> list[dict]:
    return _get(cfg, "/users/me/calendarList", {"minAccessRole": "reader"}).get("items", [])


def events(cfg: dict, cal_id: str, lo: datetime, hi: datetime) -> list[dict]:
    """Expanded occurrences in [lo, hi) as plain dicts (times as ISO strings)."""
    out, page = [], None
    while True:
        params = {"timeMin": lo.isoformat(), "timeMax": hi.isoformat(), "singleEvents": "true",
                  "orderBy": "startTime", "maxResults": 2500,
                  "fields": "nextPageToken,items(id,status,summary,location,start,end,htmlLink,hangoutLink,"
                            "conferenceData/entryPoints,attendees(self,responseStatus),eventType,reminders)"}
        if page:
            params["pageToken"] = page
        data = _get(cfg, f"/calendars/{urllib.parse.quote(cal_id, safe='')}/events", params)
        for it in data.get("items", []):
            if it.get("status") == "cancelled" or it.get("eventType") == "workingLocation":
                continue
            me = next((a for a in it.get("attendees", []) if a.get("self")), None)
            if me and me.get("responseStatus") == "declined":
                continue
            s, e = it.get("start", {}), it.get("end", {})
            video = it.get("hangoutLink") or next(
                (p["uri"] for p in it.get("conferenceData", {}).get("entryPoints", []) if p.get("entryPointType") == "video"), "")
            out.append({"uid": it["id"], "title": it.get("summary") or "(no title)",
                        "start": s.get("dateTime") or s.get("date"), "end": e.get("dateTime") or e.get("date"),
                        "all_day": "date" in s, "location": it.get("location", ""),
                        "url": video or it.get("htmlLink", ""), "link": it.get("htmlLink", ""),
                        "people": len(it.get("attendees", [])),
                        "alarm": max((r["minutes"] for r in it.get("reminders", {}).get("overrides", [])
                                      if r.get("method") == "popup"), default=None)})
        page = data.get("nextPageToken")
        if not page:
            return out
