"""hyprcal config: ~/.config/hyprcal/config.toml laid over DEFAULTS."""

from __future__ import annotations

import copy
import locale
import os
import shutil
import tomllib
from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
XDG = lambda var, default: Path(os.environ.get(var) or Path.home() / default)  # noqa: E731
CONFIG_DIR = XDG("XDG_CONFIG_HOME", ".config") / "hyprcal"
CONFIG = CONFIG_DIR / "config.toml"
DATA = XDG("XDG_DATA_HOME", ".local/share") / "hyprcal"
CACHE = XDG("XDG_CACHE_HOME", ".cache") / "hyprcal" / "events.json"
RUN = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))

COLORS = ("blue", "cyan", "purple", "green", "amber", "orange", "red")

DEFAULTS = {
    "general": {"week_start": "monday", "week_numbers": True, "timezone": "local", "refresh": 300,
                "default_view": "popout", "position": "clock"},
    "format": {"locale": "", "clock": "%H:%M", "seconds": True, "date": "%A, %-d %B", "agenda_time": "%H:%M",
               "month_title": "%B %Y", "day_title": "%a %-d %b", "waybar": "%A %H:%M"},
    "google": {"enabled": True, "client_file": "~/.config/hyprcal/google-client.json", "calendars": []},
    "reminder": {"enabled": True, "lead": 10, "snooze": 5, "position": "center"},
    "calendly": {"enabled": False, "token": "", "token_cmd": "", "booking_url": "", "event_types": [],
                 "color": "purple", "show_bookings": True, "show_open_slots": True, "slots_per_day": 6},
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def ensure() -> None:
    """First run: copy the commented example so there is something to edit."""
    if not CONFIG.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy(HERE / "config.example.toml", CONFIG)


def mtime() -> float:
    try:
        return CONFIG.stat().st_mtime
    except OSError:
        return 0.0


def load() -> tuple[dict, str | None]:
    """The merged config and, if the file could not be read, why (the defaults are used then)."""
    err = None
    try:
        user = tomllib.loads(CONFIG.read_text())
    except FileNotFoundError:
        user = {}
    except (OSError, tomllib.TOMLDecodeError) as e:
        user, err = {}, f"config.toml: {e}"
    cfg = _merge(DEFAULTS, user)
    cals = []
    for i, c in enumerate(cfg["google"].get("calendars") or []):
        if not isinstance(c, dict) or c.get("enabled") is False or not (c.get("id") or c.get("ics")):
            continue
        name = str(c.get("name") or c.get("id") or f"calendar {i + 1}")
        color = c.get("color") if c.get("color") in COLORS else COLORS[i % len(COLORS)]
        cals.append({"name": name, "id": c.get("id"), "ics": c.get("ics"), "color": color})
    cfg["google"]["calendars"] = cals
    if cfg["calendly"].get("color") not in COLORS:
        cfg["calendly"]["color"] = "purple"
    return cfg, err


def tz(cfg: dict) -> tzinfo:
    name = cfg["general"].get("timezone") or "local"
    if name != "local":
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return local_tz()


def local_tz() -> tzinfo:
    key = os.environ.get("TZ", "").lstrip(":")
    if not key:
        try:
            key = os.path.realpath("/etc/localtime").split("zoneinfo/", 1)[1]
        except IndexError:
            key = ""
    try:
        return ZoneInfo(key) if key else datetime.now().astimezone().tzinfo
    except Exception:
        return datetime.now().astimezone().tzinfo


def tz_name(cfg: dict) -> str:
    z = tz(cfg)
    return getattr(z, "key", None) or datetime.now(z).strftime("%Z")


def apply_locale(cfg: dict) -> None:
    try:
        locale.setlocale(locale.LC_TIME, cfg["format"].get("locale") or "")
    except locale.Error:
        locale.setlocale(locale.LC_TIME, "")


def fmt(dt, pattern: str) -> str:
    """strftime that never raises on a bad pattern from the config."""
    try:
        return dt.strftime(pattern)
    except (ValueError, TypeError):
        return dt.strftime("%H:%M")
