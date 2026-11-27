"""hyprbell history: one JSON object per line in ~/.local/state/hyprbell/history.jsonl, oldest first.
No GTK in here, so `hyprbell --status` stays light."""

from __future__ import annotations

import html
import json
import os
import re
import time
from pathlib import Path

STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "hyprbell"
HISTORY = STATE / "history.jsonl"
SEEN = STATE / "seen"  # time the panel was last opened: anything newer is "new"
RUN = Path(os.environ.get("HYPRBELL_RUNTIME_DIR") or os.environ.get("XDG_RUNTIME_DIR") or "/tmp")

KEEP_DAYS = 30
KEEP_MAX = 1000
SKIP_APPS = {"spotify"}  # track changes; mako hides them too (invisible=1)
DND_MODE = "do-not-disturb"

_TAG = re.compile(r"<[^>]*>")


def plain(text: str) -> str:
    """Notification bodies may carry a little markup (<b>, <a href>); keep only the text."""
    return html.unescape(_TAG.sub("", text or "")).strip()


def load() -> list[dict]:
    out = []
    try:
        with HISTORY.open(encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict) and "ts" in e:
                    out.append(e)
    except OSError:
        pass
    return out


def prune(entries: list[dict], now: float | None = None) -> list[dict]:
    cut = (now or time.time()) - KEEP_DAYS * 86400
    kept = [e for e in entries if e.get("ts", 0) >= cut]
    return kept[-KEEP_MAX:]


def save(entries: list[dict]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, HISTORY)


def append(entry: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def seen() -> float:
    try:
        return float(SEEN.read_text())
    except (OSError, ValueError):
        return 0.0


def mark_seen(ts: float | None = None) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(f"{ts or time.time():.3f}")


def unread(entries: list[dict], since: float | None = None) -> list[dict]:
    since = seen() if since is None else since
    return [e for e in entries if e.get("ts", 0) > since]
