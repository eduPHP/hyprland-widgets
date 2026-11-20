"""hyprpower backend: battery, power profiles, backlights, idle, Hyprland monitors.
No GTK here, so the `hyprpower` script and the waybar status can import it cheaply."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

PS = Path("/sys/class/power_supply")
BAT = PS / "BAT0"
BACKLIGHT = Path("/sys/class/backlight/intel_backlight")
KBD = Path("/sys/class/leds/dell::kbd_backlight")
HISTORY = Path("/var/lib/upower")
RUN = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
STATE = Path.home() / ".config/hyprpower/state.json"
MONITORS_CONF = Path.home() / ".config/hypr/monitors.conf"

PROFILES = ["power-saver", "balanced", "performance"]
CHARGE_TYPES = ["Adaptive", "Standard", "Fast", "Custom"]  # Dell; Trickle exists too but is not offered


def sh(*cmd: str, timeout: float = 2.0) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def read(p: Path, default: str = "") -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return default


def read_int(p: Path, default: int = 0) -> int:
    try:
        return int(read(p))
    except ValueError:
        return default


# ---------- settings kept by hyprpower itself ----------

def load_state() -> dict:
    try:
        return {"follow_charger": True, **json.loads(STATE.read_text())}
    except (OSError, ValueError):
        return {"follow_charger": True}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=2))


# ---------- battery ----------

@dataclass
class Battery:
    present: bool = False
    pct: int = 0
    state: str = "unknown"  # charging | discharging | fully-charged | pending-charge | ...
    rate: float = 0.0  # W
    energy: float = 0.0
    full: float = 0.0
    design: float = 0.0
    voltage: float = 0.0
    temp: float | None = None
    cycles: int = 0
    tech: str = ""
    vendor: str = ""
    model: str = ""
    time_to_full: str = ""
    time_to_empty: str = ""
    source: str = "battery"  # battery | ac | usb-c pd
    charge_type: str = ""
    charge_types: list[str] = field(default_factory=list)
    start: int = 0
    end: int = 0
    charge_writable: bool = False

    @property
    def health(self) -> int:
        return round(100 * self.full / self.design) if self.design else 0

    @property
    def plugged(self) -> bool:
        return self.source != "battery"


def _upower() -> dict[str, str]:
    out = sh("upower", "-i", "/org/freedesktop/UPower/devices/battery_BAT0")
    d: dict[str, str] = {}
    for line in out.splitlines():
        if ":" in line and not line.startswith(" " * 4 + "1"):
            k, _, v = line.partition(":")
            d.setdefault(k.strip(), v.strip())
    return d


def _num(s: str) -> float:
    try:
        return float(s.split()[0])
    except (ValueError, IndexError):
        return 0.0


def power_source() -> str:
    for p in PS.iterdir() if PS.exists() else []:
        if p.name.startswith("ucsi") and read(p / "online") == "1":
            return "usb-c pd" if "PD" in read(p / "usb_type") else "usb-c"
    return "ac" if read(PS / "AC/online") == "1" else "battery"


def battery() -> Battery:
    b = Battery()
    if not BAT.exists():
        return b
    u = _upower()
    b.present = True
    b.pct = read_int(BAT / "capacity")
    b.state = u.get("state", read(BAT / "status").lower())
    b.rate = _num(u.get("energy-rate", "0"))
    b.energy = _num(u.get("energy", "0"))
    b.full = _num(u.get("energy-full", "0"))
    b.design = _num(u.get("energy-full-design", "0"))
    b.voltage = _num(u.get("voltage", "0"))
    t = u.get("temperature")
    b.temp = _num(t) if t else None
    b.cycles = read_int(BAT / "cycle_count")
    b.tech = read(BAT / "technology").lower()
    b.vendor = read(BAT / "manufacturer")
    b.model = read(BAT / "model_name")
    b.time_to_full = u.get("time to full", "")
    b.time_to_empty = u.get("time to empty", "")
    b.source = power_source()
    types = read(BAT / "charge_types")
    b.charge_types = [t.strip("[]") for t in types.split()]
    b.charge_type = next((t.strip("[]") for t in types.split() if t.startswith("[")), "")
    b.start = read_int(BAT / "charge_control_start_threshold")
    b.end = read_int(BAT / "charge_control_end_threshold")
    b.charge_writable = os.access(BAT / "charge_types", os.W_OK)
    return b


def short_time(s: str) -> str:
    """'12.6 minutes' -> '13 min', '3.4 hours' -> '3 h 24 min'."""
    if not s:
        return ""
    v = _num(s)
    mins = v * 60 if "hour" in s else v
    h, m = divmod(round(mins), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def history(hours: float = 3.0) -> list[tuple[float, float]]:
    """(unix time, percent) from upower's charge history for the last `hours`."""
    files = sorted(HISTORY.glob("history-charge-*.dat"), key=lambda p: p.stat().st_size, reverse=True)
    model = read(BAT / "model_name").replace(" ", "_")
    files = [f for f in files if model and model in f.name] or files[:1]
    if not files:
        return []
    cut = time.time() - hours * 3600
    pts = []
    try:
        lines = files[0].read_text().splitlines()[-400:]
    except OSError:
        return []
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 2:
            try:
                t, v = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            if t >= cut and v > 0:
                pts.append((t, v))
    return pts


def set_charge(kind: str | None = None, start: int | None = None, end: int | None = None) -> str | None:
    """Write Dell charge mode / thresholds. Returns an error string, or None."""
    try:
        if start is not None and end is not None:
            # write in an order the firmware accepts (end must stay >= start + 5)
            cur_end = read_int(BAT / "charge_control_end_threshold")
            first = ("end", end) if end > cur_end else ("start", start)
            second = ("start", start) if first[0] == "end" else ("end", end)
            for name, v in (first, second):
                (BAT / f"charge_control_{name}_threshold").write_text(str(v))
        if kind:
            (BAT / "charge_types").write_text(kind)
        return None
    except PermissionError:
        return "no permission (install the udev rule)"
    except OSError as e:
        return str(e.strerror or e)


# ---------- who is using the battery (cpu time per app) ----------

CLK = os.sysconf("SC_CLK_TCK")
ROOTS = {"systemd", "start-hyprland", "init"}  # an app is a direct child of one of these (or of Hyprland)
WRAPPERS = {"sh", "bash", "dash", "zsh", "fish", "uwsm-app", "uwsm", "setsid", "env", "systemd-run", "disclaimer"}
SHELLS = {"sh", "bash", "dash", "zsh", "fish", "tmux: server", "tmux", "zellij"}
TERMS = {"kitty", "alacritty", "Alacritty", "foot", "ghostty", "wezterm-gui", "gnome-terminal-", "konsole"}
CHROMES = ("chromium", "chrome", "brave")  # name prefixes; every match is one app


def _procs() -> dict[int, tuple[int, str, int]]:
    """pid -> (ppid, comm, cpu ticks) for every process."""
    out = {}
    for d in os.scandir("/proc"):
        if not d.name.isdigit():
            continue
        try:
            with open(f"/proc/{d.name}/stat") as f:
                raw = f.read()
        except OSError:
            continue
        l, r = raw.find("("), raw.rfind(")")
        rest = raw[r + 2:].split()
        try:
            out[int(d.name)] = (int(rest[1]), raw[l + 1:r], int(rest[11]) + int(rest[12]))
        except (ValueError, IndexError):
            continue
    return out


def _cmdline(pid: int) -> list[str]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
    except OSError:
        return []


INTERPRETERS = {"python3", "python", "node", "ruby", "perl", "bun", "deno"}


def _pretty(pid: int, comm: str) -> str:
    """One name per app: every chrome / chromium process (windows, web apps, helpers) is one
    "chrome"; scripts are named after the script, not the interpreter."""
    low = comm.lower()
    for c in CHROMES:
        if low.startswith(c):
            return c
    args = _cmdline(pid)
    if comm in INTERPRETERS:
        script = next((a for a in args[1:] if not a.startswith("-")), "")
        if script:
            return os.path.basename(script)
    if len(comm) == 15 and args:  # comm is cut at 15 characters
        return os.path.basename(args[0]) or comm
    return comm


class CpuSampler:
    """CPU time per app between two calls, apps being the processes the session started
    (a browser and all its child processes count as one)."""

    def __init__(self):
        self.last: tuple[float, dict[int, int]] | None = None
        self.names: dict[int, str] = {}

    def reset(self):
        self.last = None

    def _app(self, pid: int, table: dict) -> str:
        chain = []
        p = pid
        while p in table and len(chain) < 64:
            ppid, comm, _ = table[p]
            if p <= 1 or comm in ROOTS:
                break
            if p == 2 or ppid == 2:
                return "kernel"
            chain.append(p)
            if table.get(ppid, (0, "", 0))[1] == "Hyprland" and comm != "Hyprland":
                break
            p = ppid
        if not chain:
            return table.get(pid, (0, "?", 0))[1]
        top = list(reversed(chain))
        while len(top) > 1 and table[top[0]][1] in WRAPPERS:
            top.pop(0)
        app = top[0]
        name = _pretty(app, table[app][1])
        if table[app][1] in TERMS:
            inner = next((q for q in top[1:] if table[q][1] not in SHELLS), None)
            if inner:
                name = f"{_pretty(inner, table[inner][1])} · {name}"
        return name

    def sample(self) -> list[tuple[str, float]] | None:
        """[(app, % of one core)] busiest first, or None on the first call."""
        now = time.monotonic()
        table = _procs()
        ticks = {pid: t for pid, (_, _, t) in table.items()}
        prev, self.last = self.last, (now, ticks)
        if prev is None:
            return None
        dt = max(0.2, now - prev[0])
        per: dict[str, float] = {}
        for pid, t in ticks.items():
            d = t - prev[1].get(pid, t)  # new processes count from now
            if d <= 0:
                continue
            name = self.names.get(pid)
            if name is None:
                name = self.names[pid] = self._app(pid, table)
            if name == "hyprpower":  # measuring must not show up as a user
                continue
            per[name] = per.get(name, 0.0) + d
        for pid in list(self.names):
            if pid not in table:
                del self.names[pid]
        return sorted(((n, 100.0 * d / CLK / dt) for n, d in per.items()), key=lambda x: -x[1])


# ---------- power profile ----------

def profile() -> str:
    return sh("powerprofilesctl", "get").strip() or "balanced"


def set_profile(p: str) -> None:
    subprocess.Popen(["powerprofilesctl", "set", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------- backlights (logind: no video group needed) ----------

def _set_brightness(subsystem: str, dev: str, value: int) -> None:
    subprocess.Popen(["busctl", "call", "org.freedesktop.login1", "/org/freedesktop/login1/session/auto",
                      "org.freedesktop.login1.Session", "SetBrightness", "ssu", subsystem, dev, str(value)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def brightness() -> int:
    mx = read_int(BACKLIGHT / "max_brightness", 1) or 1
    return round(100 * read_int(BACKLIGHT / "brightness") / mx)


def set_brightness(pct: int) -> None:
    mx = read_int(BACKLIGHT / "max_brightness", 1)
    _set_brightness("backlight", BACKLIGHT.name, max(mx // 100, round(mx * pct / 100)))


def kbd() -> tuple[int, int]:
    """(level, max) of the keyboard backlight; max 0 when there is none."""
    if not KBD.exists():
        return 0, 0
    return read_int(KBD / "brightness"), read_int(KBD / "max_brightness")


def set_kbd(level: int) -> None:
    _set_brightness("leds", KBD.name, level)


# ---------- idle / alerts / night light ----------

ACTIONS_BAR = shutil.which("actions-bar") or str(Path.home() / ".local/bin/actions-bar")


def _actions_bar(*args: str) -> bool:
    """Flip a toggle through the waybar actions strip, so its indicators stay in sync."""
    if not os.access(ACTIONS_BAR, os.X_OK):
        return False
    subprocess.Popen([ACTIONS_BAR, *args], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def awake() -> bool:
    """'keep awake' = hypridle is not running (same switch as the bar's awake indicator)."""
    return not sh("pgrep", "-x", "hypridle").strip()


def set_awake(on: bool) -> None:
    if on == awake():
        return
    if _actions_bar("toggle", "awake"):
        return
    if on:
        subprocess.run(["pkill", "-x", "hypridle"])
    else:
        subprocess.Popen(["hypridle"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def alert_on() -> bool | None:
    """battery-alert user service: True/False, None when it is not installed."""
    if not (Path.home() / ".config/systemd/user/battery-alert.service").exists():
        return None
    return sh("systemctl", "--user", "is-active", "battery-alert").strip() == "active"


def set_alert(on: bool) -> None:
    subprocess.Popen(["systemctl", "--user", "start" if on else "stop", "battery-alert"])


def sunset_tool() -> str:
    """hyprsunset or wlsunset, whichever is installed (same order as actions-bar)."""
    return "hyprsunset" if shutil.which("hyprsunset") else "wlsunset" if shutil.which("wlsunset") else ""


def has_sunset() -> bool:
    return bool(sunset_tool())


def night() -> bool:
    return bool(sh("pgrep", "-x", "hyprsunset|wlsunset").strip())


def set_night(on: bool) -> None:
    if on == night():
        return
    if _actions_bar("toggle", "nightlight"):
        return
    if not on:
        subprocess.run(["pkill", "-x", "hyprsunset"])
        subprocess.run(["pkill", "-x", "wlsunset"])
        return
    cmd = (["hyprsunset", "-t", "4000"] if sunset_tool() == "hyprsunset" else
           ["wlsunset", "-t", "4000", "-T", "6500", "-S", "23:59", "-s", "00:00", "-d", "60"])
    subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------- monitors ----------

INTERNAL_PREFIXES = ("eDP", "LVDS", "DSI")


@dataclass
class Monitor:
    name: str
    desc: str
    width: int
    height: int
    refresh: float
    x: int
    y: int
    scale: float
    transform: int
    disabled: bool
    mirror_of: str
    modes: list[str]
    phys: tuple[int, int]

    @property
    def internal(self) -> bool:
        return self.name.startswith(INTERNAL_PREFIXES)

    @property
    def mode(self) -> str:
        return f"{self.width}x{self.height}@{self.refresh:.2f}"

    @property
    def logical(self) -> tuple[float, float]:
        w, h = self.width / self.scale, self.height / self.scale
        return (h, w) if self.transform % 2 else (w, h)

    @property
    def inches(self) -> str:
        w, h = self.phys
        return f'{math.hypot(w, h) / 25.4:.1f}″' if w and h else ""

    def line(self, **over) -> str:
        """A `monitor=` value reproducing this monitor, with fields overridden."""
        d = {"mode": self.mode, "x": self.x, "y": self.y, "scale": self.scale, "transform": self.transform, **over}
        s = f"{self.name},{d['mode']},{d['x']}x{d['y']},{fmt_scale(d['scale'])}"
        if d["transform"]:
            s += f",transform,{d['transform']}"
        return s


def fmt_scale(s: float) -> str:
    return f"{s:.2f}".rstrip("0").rstrip(".")


def monitors() -> list[Monitor]:
    try:
        data = json.loads(sh("hyprctl", "-j", "monitors", "all") or "[]")
    except ValueError:
        return []
    out = []
    for m in data:
        out.append(Monitor(
            name=m["name"], desc=m.get("description", ""), width=m["width"], height=m["height"],
            refresh=m.get("refreshRate", 60.0), x=m["x"], y=m["y"], scale=m.get("scale", 1.0),
            transform=m.get("transform", 0), disabled=m.get("disabled", False),
            mirror_of=m.get("mirrorOf", "none"),
            modes=[s.removesuffix("Hz") for s in m.get("availableModes", [])],
            phys=(m.get("physicalWidth", 0), m.get("physicalHeight", 0))))
    out.sort(key=lambda m: (not m.internal, m.name))
    return out


def layout_mode(mons: list[Monitor]) -> str:
    """extend | mirror | internal | external (only meaningful with an external display)."""
    ext = [m for m in mons if not m.internal]
    internal = next((m for m in mons if m.internal), None)
    if not ext or not internal:
        return "extend"
    if any(m.mirror_of not in ("none", "") for m in mons):
        return "mirror"
    if internal.disabled:
        return "external"
    if all(m.disabled for m in ext):
        return "internal"
    return "extend"


def placement(internal: Monitor, ext: Monitor) -> str:
    """Where `ext` sits relative to `internal`: left | right | above | below."""
    iw, ih = internal.logical
    ew, eh = ext.logical
    cx_i, cy_i = internal.x + iw / 2, internal.y + ih / 2
    cx_e, cy_e = ext.x + ew / 2, ext.y + eh / 2
    dx, dy = cx_e - cx_i, cy_e - cy_i
    if abs(dx) / max(iw, 1) >= abs(dy) / max(ih, 1):
        return "right" if dx >= 0 else "left"
    return "below" if dy >= 0 else "above"


def place_positions(internal: Monitor, ext: Monitor, where: str) -> dict[str, tuple[int, int]]:
    """Positions for an extend layout, normalised so the top-left is 0x0.
    Side by side: tops aligned (like Hyprland's auto-right). Stacked: centred."""
    iw, ih = internal.logical
    ew, eh = ext.logical
    if where == "right":
        p = {internal.name: (0, 0), ext.name: (iw, 0)}
    elif where == "left":
        p = {internal.name: (0, 0), ext.name: (-ew, 0)}
    elif where == "above":
        p = {internal.name: (0, 0), ext.name: ((iw - ew) / 2, -eh)}
    else:
        p = {internal.name: (0, 0), ext.name: ((iw - ew) / 2, ih)}
    mx = min(x for x, _ in p.values())
    my = min(y for _, y in p.values())
    return {k: (round(x - mx), round(y - my)) for k, (x, y) in p.items()}


def apply_lines(lines: list[str]) -> None:
    """Apply `monitor=` values at runtime (until the config is reloaded)."""
    if not lines:
        return
    batch = " ; ".join(f"keyword monitor {line}" for line in lines)
    sh("hyprctl", "--batch", batch, timeout=4)


def current_lines(mons: list[Monitor]) -> list[str]:
    """Lines that restore the present state exactly (used to revert)."""
    out = []
    for m in mons:
        if m.disabled:
            out.append(f"{m.name},disable")
        elif m.mirror_of not in ("none", ""):
            out.append(f"{m.name},{m.mode},auto,{fmt_scale(m.scale)},mirror,{m.mirror_of}")
        else:
            out.append(m.line())
    return out


def persist(lines: list[str]) -> str | None:
    """Write extend-layout lines into monitors.conf (replacing those monitors' lines).
    Mirror / disable states are never persisted: a disabled laptop panel in the config
    would leave no screen when the external one is unplugged."""
    try:
        text = MONITORS_CONF.read_text() if MONITORS_CONF.exists() else ""
    except OSError as e:
        return str(e)
    bak = MONITORS_CONF.with_name(MONITORS_CONF.name + ".bak.hyprpower")
    if MONITORS_CONF.exists() and not bak.exists():
        shutil.copy2(MONITORS_CONF, bak)
    names = {line.split(",", 1)[0] for line in lines}
    kept, placed = [], set()
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("monitor") and "=" in s:
            name = s.split("=", 1)[1].split(",", 1)[0].strip()
            if name in names:
                if name not in placed:
                    kept.append("monitor=" + next(l for l in lines if l.startswith(name + ",")))
                    placed.add(name)
                continue
        kept.append(raw)
    for line in lines:
        name = line.split(",", 1)[0]
        if name not in placed:
            kept.append("monitor=" + line)
    try:
        MONITORS_CONF.write_text("\n".join(kept).rstrip("\n") + "\n")
    except OSError as e:
        return str(e)
    return None
