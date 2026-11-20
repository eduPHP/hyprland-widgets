"""Sync every source into one cache file, and read it back as Event objects.

The daemon syncs in a thread; `hyprcal --status` (waybar) only reads the cache.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from pathlib import Path

import calendly
import config
import gcal
import ics

PAST_DAYS, FUTURE_DAYS = 62, 124


@dataclass
class Event:
    cal: str
    color: str
    title: str
    start: datetime
    end: datetime
    all_day: bool = False
    location: str = ""
    url: str = ""
    who: str = ""
    people: int = 0
    link: str = ""          # the event's page on calendar.google.com
    alarm: int | None = None  # the event's own reminder, minutes before the start
    key: str = ""

    def lead(self, default: int) -> int:
        return self.alarm if self.alarm is not None else default

    def on(self, d: date, zone: tzinfo) -> bool:
        a = datetime.combine(d, time(0)).replace(tzinfo=zone)
        return self.start < a + timedelta(days=1) and self.end > a

    def live(self, now: datetime) -> bool:
        return not self.all_day and self.start <= now < self.end


@dataclass
class Slot:
    start: datetime
    url: str
    type: str
    minutes: int


def _when(v: str, zone: tzinfo) -> datetime:
    if len(v) == 10:  # all-day date
        return datetime.combine(date.fromisoformat(v), time(0)).replace(tzinfo=zone)
    return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(zone)


def _fetch_ics(src: str) -> str:
    path = Path(os.path.expanduser(src.removeprefix("file://")))
    if not src.startswith(("http://", "https://", "webcal://")):
        return path.read_text()
    req = urllib.request.Request(src.replace("webcal://", "https://"), headers={"User-Agent": "hyprcal"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def google_link(cal_id: str, gid: str) -> str:
    """calendar.google.com/calendar/event?eid=… for an event id on a calendar."""
    if not (cal_id and gid):
        return ""
    short = re.sub(r"@gmail\.com$", "@m", re.sub(r"@group\.calendar\.google\.com$", "@g", cal_id))
    eid = base64.b64encode(f"{gid} {short}".encode()).decode().rstrip("=")
    return f"https://calendar.google.com/calendar/event?eid={eid}"


def ics_calendar_id(url: str) -> str:
    m = re.search(r"/calendar/ical/([^/]+)/", url)
    return urllib.parse.unquote(m.group(1)) if m else ""


def sync(cfg: dict) -> dict:
    """Fetch every enabled source; one failing source never hides the others."""
    zone = config.tz(cfg)
    now = datetime.now(zone)
    lo = datetime.combine(now.date() - timedelta(days=PAST_DAYS), time(0)).replace(tzinfo=zone)
    hi = datetime.combine(now.date() + timedelta(days=FUTURE_DAYS), time(0)).replace(tzinfo=zone)
    old = read_raw()
    out = {"synced": now.isoformat(), "events": [], "slots": [], "booking_url": "", "errors": {}}

    def keep_old(name: str):  # on failure show what we had for this source
        out["events"] += [e for e in old.get("events", []) if e.get("cal") == name]

    if cfg["google"]["enabled"]:
        for cal in cfg["google"]["calendars"]:
            try:
                if cal.get("ics"):
                    raw = ics.expand(ics.parse(_fetch_ics(cal["ics"]), zone), lo, hi, zone)
                    cal_id = ics_calendar_id(cal["ics"])
                    items = [dict(e, start=e["start"].isoformat() if not e["all_day"] else e["start"].date().isoformat(),
                                  end=e["end"].isoformat() if not e["all_day"] else e["end"].date().isoformat(),
                                  link=google_link(cal_id, e.pop("gid", "")))
                             for e in raw]
                else:
                    items = gcal.events(cfg, cal["id"], lo, hi)
                out["events"] += [dict(e, cal=cal["name"], color=cal["color"]) for e in items]
            except gcal.AuthNeeded as e:
                out["errors"][cal["name"]] = str(e)
                keep_old(cal["name"])
            except Exception as e:  # network, parse, HTTP
                out["errors"][cal["name"]] = _short(e)
                keep_old(cal["name"])

    cc = cfg["calendly"]
    if cc["enabled"]:
        try:
            got = calendly.fetch(cc, lo, hi, now)
            out["booking_url"] = got["booking_url"]
            out["slots"] = got["slots"]
            out["events"] += [dict(e, cal="calendly", color=cc["color"]) for e in got["bookings"]]
        except Exception as e:
            out["errors"]["calendly"] = _short(e)
            keep_old("calendly")
            out["slots"], out["booking_url"] = old.get("slots", []), old.get("booking_url", "")
    write_raw(out)
    return out


def _short(e: Exception) -> str:
    code = getattr(e, "code", None)
    if code == 401:
        return "unauthorized (check the token)"
    if code:
        return f"HTTP {code}"
    reason = getattr(e, "reason", None)
    return str(reason or e)[:80] or type(e).__name__


def read_raw() -> dict:
    try:
        return json.loads(config.CACHE.read_text())
    except (OSError, ValueError):
        return {}


def write_raw(data: dict) -> None:
    config.CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(config.CACHE)


def load(data: dict, zone: tzinfo) -> tuple[list[Event], list[Slot]]:
    evs = []
    for e in data.get("events", []):
        try:
            evs.append(Event(cal=e["cal"], color=e.get("color", "blue"), title=e.get("title", ""),
                             start=_when(e["start"], zone), end=_when(e["end"], zone), all_day=bool(e.get("all_day")),
                             location=e.get("location", ""), url=e.get("url", ""), who=e.get("who", ""),
                             people=int(e.get("people") or 0), link=e.get("link", ""),
                             alarm=e["alarm"] if isinstance(e.get("alarm"), int) else None,
                             key=f"{e['cal']}|{e.get('uid', '')}|{e['start']}"))
        except (KeyError, ValueError):
            continue
    # Calendly also writes bookings into Google Calendar: keep one copy (the Calendly one has the invitee)
    booked = {(e.start, e.end) for e in evs if e.cal == "calendly"}
    evs = [e for e in evs if e.cal == "calendly" or (e.start, e.end) not in booked]
    evs.sort(key=lambda e: (e.start, not e.all_day, e.end))
    slots = []
    for s in data.get("slots", []):
        try:
            slots.append(Slot(_when(s["start"], zone), s.get("url", ""), s.get("type", ""), int(s.get("minutes") or 30)))
        except (KeyError, ValueError):
            continue
    return evs, slots
