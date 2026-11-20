#!/usr/bin/env python3
"""Write a throwaway hyprcal setup with made-up calendars, for README screenshots.

    tools/screenshots/hyprcal_demo.py DIR

Creates DIR/config/hyprcal/config.toml plus local .ics files whose events sit around today, so the
agenda always looks busy. Run hyprcal with XDG_CONFIG_HOME=DIR/config XDG_CACHE_HOME=DIR/cache
XDG_DATA_HOME=DIR/data; no account, token or network access is involved.
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = "Europe/Berlin"  # neutral demo zone; events below are floating times in it

out = Path(sys.argv[1]).resolve()
cfg_dir = out / "config" / "hyprcal"
cfg_dir.mkdir(parents=True, exist_ok=True)
# fontconfig looks for user fonts under the XDG dirs we are replacing; keep the Nerd Font icons.
for xdg, sub in (("data", "fonts"), ("config", "fontconfig")):
    real, link = Path.home() / (".local/share" if xdg == "data" else ".config") / sub, out / xdg / sub
    link.parent.mkdir(parents=True, exist_ok=True)
    if real.exists() and not link.exists():
        link.symlink_to(real)
now = datetime.now(ZoneInfo(TZ))
today = now.date()
monday = today - timedelta(days=today.weekday())

# (calendar, day offset from monday, start "HH:MM" or None for all-day, minutes, title, weekly?)
EVENTS = {
    "work": [
        (0, "09:30", 15, "Stand-up", True), (1, "09:30", 15, "Stand-up", True),
        (2, "09:30", 15, "Stand-up", True), (3, "09:30", 15, "Stand-up", True),
        (4, "09:30", 15, "Stand-up", True),
        (today.weekday(), "rel:-150", 60, "Design review: widgets v2", False),
        (today.weekday(), "rel:75", 45, "1:1 with Ana", False),
        (1, "14:00", 90, "Sprint planning", False),
        (3, "16:00", 60, "Release 0.4 go/no-go", False),
        (9, "10:00", 120, "Quarterly roadmap", False),
    ],
    "personal": [
        (today.weekday(), "rel:-20", 60, "Climbing gym", False),
        (2, "19:30", 120, "Dinner with friends", False),
        (5, "08:00", 90, "Farmers market", False),
        (6, "16:00", 120, "Long run", False),
        (11, "20:00", 150, "Concert", False),
    ],
    "family": [
        (5, None, 0, "Grandma's birthday", False),
        (today.weekday(), "rel:-75", 30, "Call mom", False),
        (8, "17:00", 60, "Parent-teacher meeting", False),
    ],
    "holidays": [
        (12, None, 0, "Founders' Day", False),
        (19, None, 0, "Midsummer", False),
    ],
}
COLORS = {"work": "blue", "personal": "green", "family": "orange", "holidays": "purple"}


def ics(name, events):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//hyprland-widgets//demo//EN", f"X-WR-CALNAME:{name}"]
    for i, (off, start, mins, title, weekly) in enumerate(events):
        d = monday + timedelta(days=off)
        lines += ["BEGIN:VEVENT", f"UID:demo-{name}-{i}@hyprland-widgets.example", "DTSTAMP:20260101T000000Z",
                  f"SUMMARY:{title}"]
        if start is None:
            lines += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}"]
        else:
            if start.startswith("rel:"):  # minutes from now, snapped to a quarter hour
                s = now.replace(tzinfo=None, second=0, microsecond=0) + timedelta(minutes=int(start[4:]))
                s -= timedelta(minutes=s.minute % 15)
            else:
                s = datetime.combine(d, datetime.strptime(start, "%H:%M").time())
            lines += [f"DTSTART:{s:%Y%m%dT%H%M%S}", f"DTEND:{s + timedelta(minutes=mins):%Y%m%dT%H%M%S}"]
        if weekly:
            lines.append("RRULE:FREQ=WEEKLY;COUNT=8")
        lines.append("END:VEVENT")
    return "\r\n".join(lines + ["END:VCALENDAR", ""])


blocks = []
for name, events in EVENTS.items():
    path = cfg_dir / f"{name}.ics"
    path.write_text(ics(name, events))
    blocks.append(f'[[google.calendars]]\nname  = "{name}"\nics   = "{path}"\ncolor = "{COLORS[name]}"\n')

(cfg_dir / "config.toml").write_text(f"""[general]
week_start   = "monday"
week_numbers = true
timezone     = "{TZ}"
default_view = "popout"
position     = "right"

[reminder]
enabled = false

[google]
enabled = true

{chr(10).join(blocks)}
[calendly]
enabled = false
""")
print(cfg_dir / "config.toml")
