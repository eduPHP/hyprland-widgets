#!/usr/bin/env python3
"""Follow the Omarchy theme: map its palette onto hypr-ui's colour tokens.

    hypr-ui/theme.py            write ~/.local/state/hypr-ui/theme.json from the current theme
    hypr-ui/theme.py --reset    remove it (widgets go back to tokens.json)
    hypr-ui/theme.py --print    show the mapping without writing

Run by omarchy-theme as a theme-set hook (~/.config/omarchy/hooks/theme-set.d/hypr-ui). The GTK
widgets and hyprbt watch theme.json and repaint live, so a theme switch needs no restart. With no
theme.json the widgets use tokens.json as before.

The palette is read from colors.json (resolved by omarchy-theme) or colors.toml, in
~/.config/omarchy/current/theme or, on real Omarchy 4, ~/.local/state/omarchy/current/theme.
"""

import json
import os
import sys
import tomllib
from pathlib import Path

HOME = Path.home()
STATE = Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local/state") / "hypr-ui"
OUT = STATE / "theme.json"
THEME_DIRS = [HOME / ".config/omarchy/current/theme", HOME / ".local/state/omarchy/current/theme"]


def mix(a: str, b: str, t: float) -> str:
    a, b = a.lstrip("#"), b.lstrip("#")
    return "#" + "".join(f"{round(int(a[i:i + 2], 16) * (1 - t) + int(b[i:i + 2], 16) * t):02x}" for i in (0, 2, 4))


def luma(c: str) -> float:
    c = c.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def palette() -> tuple[dict, Path] | tuple[None, None]:
    for d in THEME_DIRS:
        if (d / "colors.json").exists():
            return json.loads((d / "colors.json").read_text()), d
        if (d / "colors.toml").exists():
            p = {k: str(v) for k, v in tomllib.loads((d / "colors.toml").read_text()).items()}
            # the few fallbacks hypr-ui needs, for a palette nobody resolved
            p.setdefault("background", p.get("color0", "#1a1b26"))
            p.setdefault("foreground", p.get("color7", "#c0caf5"))
            for k, f in (("bright_foreground", "foreground"), ("light_foreground", "foreground"),
                         ("lighter_background", "background"), ("dark_foreground", "foreground"),
                         ("selection", "lighter_background"), ("accent", "blue")):
                p.setdefault(k, p.get(f, p["foreground"]))
            p.setdefault("dark_background", mix(p["background"], "#000000", 0.25))
            p.setdefault("mode", "light" if luma(p["background"]) > 0.5 else "dark")
            for k in ("red", "green", "yellow", "blue", "magenta", "cyan"):
                p.setdefault(k, p["accent"])
                p.setdefault("bright_" + k, p[k])
            p.setdefault("orange", p["yellow"])
            return p, d
    return None, None


def hex6(v: str) -> str:
    return "#" + v.lstrip("#")[:6].lower()


def tokens(p: dict) -> dict:
    p = {k: hex6(v) if isinstance(v, str) and v.startswith("#") else v for k, v in p.items()}
    light = p.get("mode") == "light"
    bg = p["background"] if light else mix(p["background"], p["dark_background"], 0.5)
    fg = p["foreground"] if light else p["bright_foreground"]
    accent = p["accent"]
    pick = (lambda k: p[k]) if light else (lambda k: p["bright_" + k])  # bright variants wash out on light
    return {
        "bg": bg,
        "surface": mix(p["background"], p["lighter_background"], 0.5),
        "raised": p["lighter_background"],
        "line": p["selection"],
        "frame": mix(p["background"], accent, 0.38),
        "track": mix(p["lighter_background"], p.get("muted", p["dark_foreground"]), 0.4),
        "fg": fg,
        "bright": mix(fg, "#000000" if light else "#ffffff", 0.4 if light else 0.6),
        "muted": mix(p["dark_foreground"], fg, 0.35),
        "faint": p["dark_foreground"],
        "blue": accent,
        "cyan": pick("cyan"),
        "purple": pick("magenta"),
        "green": p["green"],
        "amber": p["yellow"],
        "orange": p["orange"],
        "red": p["red"],
        # text on an accent-filled control: whichever end of the palette reads on the accent
        "ink": (fg if light else bg) if luma(accent) > 0.45 else (bg if light else fg),
    }


def main(argv: list[str]) -> int:
    if "--reset" in argv:
        OUT.unlink(missing_ok=True)
        return 0
    p, d = palette()
    if p is None:
        print("hypr-ui theme: no Omarchy theme found", file=sys.stderr)
        return 1
    data = {"theme": p.get("theme_name") or d.name, "mode": p.get("mode", "dark"), "color": tokens(p),
            "palette": {k: v for k, v in p.items() if isinstance(v, str)}}
    if "--print" in argv:
        print(json.dumps(data["color"], indent=2))
        return 0
    STATE.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2) + "\n"
    if not OUT.exists() or OUT.read_text() != text:
        OUT.write_text(text)  # in place, not renamed over: file watchers keep their watch
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
