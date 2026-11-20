"""Calendly API v2 with a Personal Access Token: bookings, open slots, the booking link."""

from __future__ import annotations

import json
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.calendly.com"


def _token(cc: dict) -> str:
    if cc.get("token"):
        return cc["token"].strip()
    if cc.get("token_cmd"):
        r = subprocess.run(cc["token_cmd"], shell=True, capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0]
        raise RuntimeError(f"token_cmd failed: {(r.stderr or 'no output').strip()[:80]}")
    raise RuntimeError("set token or token_cmd in [calendly]")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def fetch(cc: dict, lo: datetime, hi: datetime, now: datetime) -> dict:
    tok = _token(cc)

    def get(path_or_url: str, params: dict | None = None) -> dict:
        url = path_or_url if path_or_url.startswith("http") else API + path_or_url
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)

    me = get("/users/me")["resource"]
    out = {"booking_url": cc.get("booking_url") or me.get("scheduling_url", ""), "bookings": [], "slots": []}

    if cc.get("show_bookings", True):
        params = {"user": me["uri"], "status": "active", "sort": "start_time:asc", "count": 100,
                  "min_start_time": _iso(lo), "max_start_time": _iso(hi)}
        items = get("/scheduled_events", params).get("collection", [])
        for i, ev in enumerate(items):
            who = ""
            if ev["end_time"] >= _iso(now) and i < 25:  # names only for what is still ahead
                try:
                    inv = get(ev["uri"] + "/invitees", {"count": 5, "status": "active"}).get("collection", [])
                    who = ", ".join(x.get("name", "") for x in inv if x.get("name"))
                except Exception:
                    pass
            loc = ev.get("location") or {}
            out["bookings"].append({"uid": ev["uri"].rsplit("/", 1)[-1], "title": ev.get("name") or "calendly",
                                    "start": ev["start_time"], "end": ev["end_time"], "all_day": False,
                                    "location": loc.get("location") or "", "url": loc.get("join_url") or "",
                                    "who": who})

    if cc.get("show_open_slots", True):
        types = get("/event_types", {"user": me["uri"], "active": "true", "count": 100}).get("collection", [])
        wanted = set(cc.get("event_types") or [])
        types = [t for t in types if not wanted or t.get("slug") in wanted][:4]
        start = max(now, lo) + timedelta(minutes=2)
        end = min(start + timedelta(days=7) - timedelta(minutes=1), hi)  # the API allows a 7-day window
        for t in types:
            if end <= start:
                break
            av = get("/event_type_available_times", {"event_type": t["uri"], "start_time": _iso(start),
                                                     "end_time": _iso(end)}).get("collection", [])
            out["slots"] += [{"start": a["start_time"], "url": a.get("scheduling_url") or t.get("scheduling_url", ""),
                              "type": t.get("name", ""), "minutes": t.get("duration", 30)}
                             for a in av if a.get("status") == "available"]
        out["slots"].sort(key=lambda s: s["start"])
    return out
