"""Small iCalendar reader for Google's secret iCal addresses (or any .ics file).

Handles VEVENT with DTSTART/DTEND/DURATION, all-day dates, TZID, RRULE
(DAILY/WEEKLY/MONTHLY/YEARLY with INTERVAL, COUNT, UNTIL, BYDAY, BYMONTHDAY),
EXDATE and RECURRENCE-ID overrides. Enough for a personal calendar, not a full RFC 5545.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
MAX_STEPS = 4000


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _split(line: str) -> tuple[str, dict, str]:
    # NAME;PARAM=a;PARAM="b:c":VALUE (a colon inside quotes is not the separator)
    in_q = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_q = not in_q
        elif ch == ":" and not in_q:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return line.upper(), {}, ""
    parts = head.split(";")
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return parts[0].upper(), params, value


def _text(v: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def _zone(params: dict, local: tzinfo) -> tzinfo:
    tzid = params.get("TZID")
    if tzid:
        try:
            return ZoneInfo(tzid)
        except Exception:
            pass
    return local


def _dt(value: str, params: dict, local: tzinfo) -> tuple[datetime, bool]:
    """(aware datetime, all_day). All-day dates become local midnight."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or len(value) == 8:
        d = datetime.strptime(value[:8], "%Y%m%d")
        return d.replace(tzinfo=local), True
    if value.endswith("Z"):
        return datetime.strptime(value[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc), False
    return datetime.strptime(value[:15], "%Y%m%dT%H%M%S").replace(tzinfo=_zone(params, local)), False


def _duration(v: str) -> timedelta:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v.strip())
    if not m:
        return timedelta(0)
    sign = -1 if m.group(1) == "-" else 1
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    return sign * timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def parse(text: str, local: tzinfo) -> list[dict]:
    events, cur, depth = [], None, 0
    for line in _unfold(text):
        name, params, value = _split(line)
        if name == "BEGIN":
            if value.upper() == "VEVENT" and cur is None:
                cur, depth = {"exdates": []}, 0
            elif cur is not None:
                depth += 1  # VALARM and friends
            continue
        if cur is not None and depth == 1 and name == "TRIGGER":
            # relative to the start only ("-PT10M"); absolute or end-related alarms are ignored
            if params.get("VALUE") != "DATE-TIME" and params.get("RELATED", "START") == "START":
                mins = int(-_duration(value).total_seconds() // 60)
                if mins >= 0:
                    cur.setdefault("alarms", []).append(mins)
            continue
        if name == "END":
            if cur is not None and depth:
                depth -= 1
            elif cur is not None and value.upper() == "VEVENT":
                if "start" in cur:
                    events.append(cur)
                cur = None
            continue
        if cur is None or depth:
            continue
        try:
            if name == "DTSTART":
                cur["start"], cur["all_day"] = _dt(value, params, local)
            elif name == "DTEND":
                cur["end"], _ = _dt(value, params, local)
            elif name == "DURATION":
                cur["duration"] = _duration(value)
            elif name == "SUMMARY":
                cur["title"] = _text(value)
            elif name == "LOCATION":
                cur["location"] = _text(value)
            elif name == "UID":
                cur["uid"] = value
            elif name == "RRULE":
                cur["rrule"] = dict(p.split("=", 1) for p in value.upper().split(";") if "=" in p)
            elif name == "EXDATE":
                cur["exdates"] += [_dt(v, params, local)[0] for v in value.split(",") if v]
            elif name == "RECURRENCE-ID":
                cur["recurrence_id"], _ = _dt(value, params, local)
            elif name == "STATUS":
                cur["status"] = value.upper()
            elif name == "URL":
                cur["url"] = value
            elif name == "DESCRIPTION":
                m = re.search(r"https://(?:meet\.google\.com|[\w.-]*zoom\.us)/\S+", _text(value))
                if m and "url" not in cur:
                    cur["url"] = m.group(0)
        except ValueError:
            continue
    for e in events:
        if "end" not in e:
            e["end"] = e["start"] + e.get("duration", timedelta(days=1) if e.get("all_day") else timedelta(0))
    return events


def _nth_weekday(year: int, month: int, wd: int, n: int) -> date | None:
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(wd - d.weekday()) % 7 + 7 * (n - 1))
    else:
        nxt = date(year + month // 12, month % 12 + 1, 1)
        d = nxt - timedelta(days=1)
        d -= timedelta(days=(d.weekday() - wd) % 7 + 7 * (-n - 1))
    return d if d.month == month else None


def _month_days(year: int, month: int, rule: dict, start: datetime) -> list[date]:
    out = []
    if "BYDAY" in rule:
        for tok in rule["BYDAY"].split(","):
            m = re.fullmatch(r"([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)", tok)
            if not m:
                continue
            wd = DAYS.index(m.group(2))
            if m.group(1):
                d = _nth_weekday(year, month, wd, int(m.group(1)))
                out += [d] if d else []
            else:
                d = date(year, month, 1) + timedelta(days=(wd - date(year, month, 1).weekday()) % 7)
                while d.month == month:
                    out.append(d)
                    d += timedelta(days=7)
    else:
        days = [int(x) for x in rule.get("BYMONTHDAY", str(start.day)).split(",") if x.lstrip("-").isdigit()]
        last = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)).day
        for n in days:
            n = n if n > 0 else last + 1 + n
            if 1 <= n <= last:
                out.append(date(year, month, n))
    return sorted(out)


def _occurrences(e: dict, lo: datetime, until_bound: datetime, local: tzinfo):
    """Yield start datetimes of a recurring event, in the event's own zone, up to until_bound.
    Without COUNT it jumps straight to periods near `lo`, so old daily events stay cheap."""
    rule, start = e["rrule"], e["start"]
    zone = start.tzinfo
    t = start.timetz().replace(tzinfo=None)
    freq = rule.get("FREQ", "DAILY")
    step = max(1, int(rule.get("INTERVAL", "1") or 1))
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    until = None
    if rule.get("UNTIL"):
        try:
            until, _ = _dt(rule["UNTIL"], {}, local)
            if len(rule["UNTIL"]) == 8:
                until += timedelta(days=1)
        except ValueError:
            pass
    stop = min(until_bound, until) if until else until_bound
    at = lambda d: datetime.combine(d, t).replace(tzinfo=zone)  # noqa: E731
    n, i0 = 0, 0
    if count is None and lo > start:
        gap = {"DAILY": (lo.date() - start.date()).days,
               "WEEKLY": (lo.date() - start.date()).days // 7,
               "MONTHLY": (lo.year - start.year) * 12 + lo.month - start.month,
               "YEARLY": lo.year - start.year}.get(freq, 0)
        i0 = max(0, gap // step - 1)
    for i in range(i0, i0 + MAX_STEPS):
        if freq == "DAILY":
            batch = [at(start.date() + timedelta(days=i * step))]
        elif freq == "WEEKLY":
            week0 = start.date() - timedelta(days=start.weekday())
            wk = week0 + timedelta(weeks=i * step)
            days = [DAYS.index(x[-2:]) for x in rule.get("BYDAY", DAYS[start.weekday()]).split(",") if x[-2:] in DAYS]
            batch = [at(wk + timedelta(days=d)) for d in sorted(set(days))]
        elif freq == "MONTHLY":
            mi = start.month - 1 + i * step
            batch = [at(d) for d in _month_days(start.year + mi // 12, mi % 12 + 1, rule, start)]
        elif freq == "YEARLY":
            y = start.year + i * step
            try:
                batch = [at(start.date().replace(year=y))]
            except ValueError:  # 29 Feb
                batch = []
        else:
            return
        for dt in batch:
            if dt < start:
                continue
            if dt >= stop or (count is not None and n >= count):
                return
            n += 1
            yield dt


def expand(events: list[dict], lo: datetime, hi: datetime, local: tzinfo) -> list[dict]:
    """Every occurrence overlapping [lo, hi) as {title, start, end, all_day, location, url, uid}."""
    overrides = {(e.get("uid"), e["recurrence_id"].timestamp()) for e in events if "recurrence_id" in e}
    out = []
    for e in events:
        if e.get("status") == "CANCELLED" and "recurrence_id" not in e:
            continue
        length = e["end"] - e["start"]
        if "rrule" in e and "recurrence_id" not in e:
            ex = {x.timestamp() for x in e["exdates"]}
            starts = [s for s in _occurrences(e, lo - length, hi, local)
                      if s.timestamp() not in ex and (e.get("uid"), s.timestamp()) not in overrides]
        else:
            if e.get("status") == "CANCELLED":
                continue
            starts = [e["start"]]
        recurring = "rrule" in e or "recurrence_id" in e
        for s in starts:
            end = s + length
            if end > lo and s < hi:
                orig = e.get("recurrence_id", s)
                out.append({"uid": e.get("uid", ""), "title": e.get("title", "(no title)"), "start": s, "end": end,
                            "all_day": bool(e.get("all_day")), "location": e.get("location", ""),
                            "url": e.get("url", ""), "alarm": max(e["alarms"]) if e.get("alarms") else None,
                            "gid": _google_id(e.get("uid", ""), orig, bool(e.get("all_day")) if recurring else None)})
    return out


def _google_id(uid: str, start: datetime, all_day: bool | None) -> str:
    """Google's event id for this occurrence ("" for events Google did not create).
    Recurring instances are "<id>_<original start>", the form calendar.google.com links use."""
    if not uid.endswith("@google.com"):
        return ""
    gid = uid[: -len("@google.com")]
    if all_day is None:
        return gid
    return f"{gid}_{start:%Y%m%d}" if all_day else f"{gid}_{start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"


def day_bounds(d: date, zone: tzinfo) -> tuple[datetime, datetime]:
    a = datetime.combine(d, time(0)).replace(tzinfo=zone)
    return a, a + timedelta(days=1)
