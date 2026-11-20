"""Integração mínima com o hyprspot (mini player de Spotify) e o Spotify Soloist.

Tudo aqui roda na thread do Worker (subprocessos curtos com timeout) ou dispara
processos destacados; nada bloqueia a UI do painel.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

SOLOIST = Path.home() / ".local" / "bin" / "soloist"
CTL = "hyprspot-ctl"


def _run(cmd: list[str], timeout: float = 2.0) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _name(entity: dict | None) -> str:
    return (((entity or {}).get("decorations") or {}).get("identity") or {}).get("name", "") or ""


def parse_now(raw: str) -> dict:
    """Saída de `soloist ctl now --json` → {status, title, artist}."""
    try:
        ev = json.loads(raw.strip().splitlines()[0]) if raw.strip() else {}
    except (ValueError, IndexError):
        ev = {}
    item = ev.get("item") or {}
    deco = item.get("decorations") or {}
    artists = ", ".join(n for n in (_name((c or {}).get("entity", c)) for c in deco.get("creators") or []) if n)
    return {"status": ev.get("status") or "off", "title": _name(item), "artist": artists}


def state() -> dict:
    """{running, status (playing|paused|buffering|idle|off), title, artist}."""
    owner = _run(["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
                  "--object-path", "/org/freedesktop/DBus", "--method",
                  "org.freedesktop.DBus.NameHasOwner", "dev.hyprspot.App"])
    st = parse_now(_run([str(SOLOIST), "ctl", "now", "--json"])) if SOLOIST.exists() else parse_now("")
    st["running"] = "true" in owner
    return st


def run(action: str) -> None:
    """open-close | play-pause | next | prev — destacado, para não segurar o painel."""
    args = [CTL, action] if action == "open-close" else [CTL, "media", action]
    try:
        subprocess.Popen(args, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ.copy())
    except OSError:
        pass
