"""hyprmenu usage: which apps you use most, measured as time in focus. No GTK in here.

Every Hyprland `activewindow` event closes the stretch of the window that had focus and adds its
seconds to that app's score. Scores decay with a 7-day half-life, so the grid follows what you use
now. A launch from the menu adds a small bonus, and the first run is seeded from GNOME Shell's own
usage scores (~/.local/share/gnome-shell/application_state), which fade out over the following weeks.

State: ~/.local/state/hyprmenu/usage.json
    {"apps": {"<key>": {"score": seconds, "t": unix time of score}}, "pins": [...],
     "recent": [...], "seeded": true}
A key is a desktop-file id ("code.desktop") when the window class maps to one, else "class:<class>".
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HOME = Path.home()
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local/state") / "hyprmenu"
STATE = STATE_DIR / "usage.json"
GNOME_STATE = HOME / ".local/share/gnome-shell/application_state"

HALF_LIFE = 7 * 86400
MAX_STRETCH = 20 * 60  # a window focused longer than this without an event counts this much (idle, locked)
LAUNCH_BONUS = 5 * 60
SEED_TOP = 3 * 3600  # the most-used app in GNOME's history starts with this many seconds
RECENT_MAX = 12


def decayed(entry: dict, now: float) -> float:
    return entry["score"] * 0.5 ** (max(0.0, now - entry["t"]) / HALF_LIFE)


class Usage:
    """Scores per app key. `resolve(cls)` maps a window class to a key (set by the widget)."""

    def __init__(self, path: Path = STATE):
        self.path = path
        self.data = {"apps": {}, "pins": [], "recent": [], "seeded": False}
        try:
            self.data.update(json.loads(path.read_text()))
        except (OSError, ValueError):
            pass
        self.lock = threading.Lock()
        self.dirty = False
        self.resolve = lambda cls: f"class:{cls}"
        self.current: tuple[str, float] | None = None  # (key, focused since)

    # ----- scores -----

    def add(self, key: str, seconds: float, now: float | None = None):
        if not key or seconds <= 0:
            return
        now = now or time.time()
        with self.lock:
            e = self.data["apps"].get(key)
            score = decayed(e, now) if e else 0.0
            self.data["apps"][key] = {"score": score + seconds, "t": now}
            self.dirty = True

    def score(self, key: str, now: float | None = None) -> float:
        e = self.data["apps"].get(key)
        return decayed(e, now or time.time()) if e else 0.0

    def ranked(self, now: float | None = None) -> list[tuple[str, float]]:
        now = now or time.time()
        with self.lock:
            items = [(k, decayed(e, now)) for k, e in self.data["apps"].items()]
        if self.current:  # the window in focus right now counts too
            k, since = self.current
            items = [(a, s + (min(now - since, MAX_STRETCH) if a == k else 0)) for a, s in items]
            if k not in dict(items):
                items.append((k, min(now - since, MAX_STRETCH)))
        return sorted((i for i in items if i[1] > 1), key=lambda i: -i[1])

    def launched(self, key: str):
        self.add(key, LAUNCH_BONUS)
        self.touch_recent(key)

    def touch_recent(self, key: str):
        with self.lock:
            r = [k for k in self.data["recent"] if k != key]
            self.data["recent"] = [key, *r][:RECENT_MAX]
            self.dirty = True

    # ----- pins -----

    def pins(self) -> list[str]:
        return list(self.data["pins"])

    def toggle_pin(self, key: str) -> bool:
        with self.lock:
            pins = self.data["pins"]
            if key in pins:
                pins.remove(key)
                pinned = False
            else:
                pins.append(key)
                pinned = True
            self.dirty = True
        self.save()
        return pinned

    # ----- focus events -----

    def focus(self, cls: str | None, now: float | None = None):
        """The window with class `cls` (None: nothing) got focus."""
        now = now or time.time()
        if self.current:
            key, since = self.current
            self.add(key, min(now - since, MAX_STRETCH), now)
        key = self.resolve(cls) if cls else None
        self.current = (key, now) if key else None
        if key and (not self.data["recent"] or self.data["recent"][0] != key):
            self.touch_recent(key)

    # ----- persistence -----

    def save(self):
        with self.lock:
            if not self.dirty:
                return
            text = json.dumps(self.data, indent=1)
            self.dirty = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text)
        os.replace(tmp, self.path)

    def seed_from_gnome(self, path: Path = GNOME_STATE) -> int:
        """Once: turn GNOME Shell's usage scores into starting seconds, aged from when GNOME last saw
        each app. Returns how many apps were seeded."""
        if self.data.get("seeded"):
            return 0
        self.data["seeded"] = True
        self.dirty = True
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            return 0
        rows = []
        for a in root.iter("application"):
            try:
                rows.append((a.get("id"), float(a.get("score", 0)), float(a.get("last-seen", 0))))
            except ValueError:
                continue
        rows = [r for r in rows if r[0] and r[1] > 0]
        if not rows:
            return 0
        top = max(r[1] for r in rows)
        with self.lock:
            for app_id, score, seen in rows:
                if app_id in self.data["apps"]:
                    continue
                self.data["apps"][app_id] = {"score": SEED_TOP * score / top, "t": seen or time.time()}
        return len(rows)


# ---------- Hyprland events ----------

def hypr_socket2() -> Path | None:
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    run = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    if sig:
        for p in (run / "hypr" / sig / ".socket2.sock", Path("/tmp/hypr") / sig / ".socket2.sock"):
            if p.exists():
                return p
    socks = sorted((run / "hypr").glob("*/.socket2.sock"), key=lambda p: p.stat().st_mtime, reverse=True)
    return socks[0] if socks else None


def active_class() -> str | None:
    """The focused window's class right now, through Hyprland's request socket (no hyprctl needed)."""
    s2 = hypr_socket2()
    if not s2:
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(1)
        s.connect(str(s2.with_name(".socket.sock")))
        s.sendall(b"j/activewindow")
        buf = b""
        while chunk := s.recv(65536):
            buf += chunk
        return (json.loads(buf or b"{}") or {}).get("class") or None
    except (OSError, ValueError):
        return None
    finally:
        s.close()


def listen(on_class, log=lambda *a: None, stop: threading.Event | None = None):
    """Call `on_class(cls or None)` for each focus change, forever (run in a thread). Reconnects
    when Hyprland restarts."""
    stop = stop or threading.Event()
    while not stop.is_set():
        path = hypr_socket2()
        if not path:
            stop.wait(5)
            continue
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(str(path))
            log("listening to", path)
            on_class(active_class())
            buf = b""
            while not stop.is_set():
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    ev, _, payload = line.decode(errors="replace").partition(">>")
                    if ev == "activewindow":
                        cls = payload.split(",", 1)[0]
                        on_class(cls or None)
        except OSError as e:
            log("hyprland events:", e)
        finally:
            s.close()
        stop.wait(2)
