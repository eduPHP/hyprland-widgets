"""hyprtheme's model: the installed Omarchy themes with their palettes, wallpapers and favourites.

Kept free of GTK so it can be tested (and the launcher stays fast). Palettes are resolved by
omarchy-theme and mapped by hypr-ui/theme.py, so a tile shows exactly the colours the widgets
would get after applying the theme.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RUN = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "hyprtheme/state.json"
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "hyprtheme"
SWATCH_KEYS = ("red", "yellow", "green", "cyan", "blue", "magenta")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ot = _load("omarchy_theme", REPO / "omarchy-theme/omarchy_theme.py")
mapping = _load("hypr_ui_theme", REPO / "hypr-ui/theme.py")


def hex6(v: str) -> str:
    return "#" + v.lstrip("#")[:6].lower() if isinstance(v, str) and v.startswith("#") else v


@dataclass
class Theme:
    id: str
    title: str
    mode: str  # dark | light
    dir: Path
    colors: dict  # the hypr-ui colour tokens this theme maps to (bg, surface, fg, blue, ...)
    palette: dict  # the resolved Omarchy palette
    swatches: list[str]
    backgrounds: list[Path]


def load_themes() -> list[Theme]:
    out = []
    for name, d in ot.theme_dirs().items():
        try:
            raw = tomllib.loads((d / "colors.toml").read_text()) if (d / "colors.toml").exists() else {}
            p = ot.resolve(raw, d)
            colors = mapping.tokens(p)
        except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as e:
            print(f"hyprtheme: skipping {name}: {e}", file=sys.stderr)
            continue
        out.append(Theme(name, ot.pretty(name), p.get("mode", "dark"), d, colors,
                         {k: hex6(v) for k, v in p.items()}, [hex6(p[k]) for k in SWATCH_KEYS],
                         ot.backgrounds(name, d)))
    out.sort(key=lambda t: t.title.lower())
    return out


def fuzzy(query: str, text: str) -> int | None:
    """Score `query` as a subsequence of `text` (lower is better), None when it doesn't match.
    Letters that start a word or follow the previous match cost nothing; gaps cost their length."""
    q = "".join(query.lower().split())
    if not q:
        return 0
    t = text.lower()
    score, i, last = 0, 0, -1
    for j, ch in enumerate(t):
        if i < len(q) and ch == q[i]:
            if not (j == 0 or t[j - 1] in " -_" or j == last + 1):
                score += j - last
            last, i = j, i + 1
    return score if i == len(q) else None


def search(themes: list[Theme], query: str, flt: str, favorites: set[str]) -> list[Theme]:
    """Themes for the filter chip (all | dark | light | favs) and the search box, best matches first."""
    keep = []
    for t in themes:
        if flt in ("dark", "light") and t.mode != flt:
            continue
        if flt == "favs" and t.id not in favorites:
            continue
        s = fuzzy(query, t.title)
        if s is not None:
            keep.append((s, t.title.lower(), t))
    return [t for *_, t in sorted(keep, key=lambda k: (k[0], k[1]))]


# --- the picker's own state: favourites ---------------------------------------------

def load_state() -> dict:
    try:
        s = json.loads(STATE.read_text())
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name("state.json.new")
    tmp.write_text(json.dumps(s, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, STATE)


def favorites() -> set[str]:
    return set(load_state().get("favorites", []))


def set_favorite(theme: str, on: bool) -> set[str]:
    s = load_state()
    favs = set(s.get("favorites", []))
    (favs.add if on else favs.discard)(theme)
    s["favorites"] = sorted(favs)
    save_state(s)
    return favs
