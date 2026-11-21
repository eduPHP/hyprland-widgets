#!/usr/bin/env python3
"""Omarchy themes on a non-Omarchy Hyprland desktop.

    omarchy-theme list                 installed themes, current one marked
    omarchy-theme current              name of the current theme
    omarchy-theme set <name> [--background <image> | --keep-background]
    omarchy-theme next | prev          step through the themes
    omarchy-theme bg <image>           set a wallpaper for the current theme
    omarchy-theme bg-next [--restore]  next wallpaper of the current theme
    omarchy-theme undo                 back to the theme and wallpaper before the last change
    omarchy-theme refresh              re-render the current theme (after editing a template)

Installed as omarchy-theme-set, omarchy-theme-list, ... symlinks too; the name picks the command.

A theme is an Omarchy theme folder: colors.toml, backgrounds/, and optional app files
(btop.theme, neovim.lua, ...). Themes are looked up in ~/.config/omarchy/themes (yours) and
then ~/.local/share/omarchy/themes (Omarchy's, fetched by install.sh).

Setting a theme renders every *.tpl template into ~/.config/omarchy/current/theme/ (templates in
~/.config/omarchy/themed/ win over the ones next to this file), writes the resolved palette to
colors.json there, swaps the folder in atomically, sets the wallpaper, pokes the running apps and
runs the ~/.config/omarchy/hooks/theme-set.d/* hooks with the theme name.

The wallpaper picked for each theme is remembered, so switching back to a theme brings its wallpaper
back, and the theme and wallpaper before each change are kept for `undo`
(~/.local/state/omarchy-theme/state.json).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HOME = Path.home()
HERE = Path(__file__).resolve().parent
CONFIG = HOME / ".config/omarchy"
CURRENT = CONFIG / "current"
THEME_OUT = CURRENT / "theme"
THEME_NAME = CURRENT / "theme.name"
BACKGROUND = CURRENT / "background"
USER_THEMES = CONFIG / "themes"
OMARCHY_THEMES = HOME / ".local/share/omarchy/themes"
USER_TEMPLATES = CONFIG / "themed"
TEMPLATES = HERE / "themed"
HOOKS = CONFIG / "hooks"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
STATE = Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local/state") / "omarchy-theme/state.json"


# --- palette -------------------------------------------------------------------------
# A port of Omarchy's omarchy-theme-color: legacy names, ANSI aliases and derived shades,
# so templates can use any key an Omarchy template would.

def mix(a: str, b: str, amount: float) -> str:
    a, b = a.lstrip("#"), b.lstrip("#")
    out = (round(int(a[i:i + 2], 16) * (1 - amount) + int(b[i:i + 2], 16) * amount) for i in (0, 2, 4))
    return "#" + "".join(f"{v:02x}" for v in out)


def is_hex(v: str | None) -> bool:
    return bool(v) and re.fullmatch(r"#[0-9a-fA-F]{6}", v) is not None


def resolve(raw: dict, theme_dir: Path | None = None) -> dict:
    c = {k: str(v) for k, v in raw.items() if isinstance(v, (str, int, float))}

    def alias(key, fallback):
        if not c.get(key) and c.get(fallback):
            c[key] = c[fallback]

    short = {"background": "bg", "dark_background": "dark_bg", "darker_background": "darker_bg",
             "lighter_background": "lighter_bg", "foreground": "fg", "dark_foreground": "dark_fg",
             "light_foreground": "light_fg", "bright_foreground": "bright_fg"}
    for k, s in short.items():
        alias(k, s)
    alias("background", "color0")
    alias("foreground", "color7")
    for k, n in {"red": 1, "green": 2, "yellow": 3, "blue": 4, "magenta": 5, "cyan": 6, "bright_red": 9,
                 "bright_green": 10, "bright_yellow": 11, "bright_blue": 12, "bright_magenta": 13,
                 "bright_cyan": 14}.items():
        alias(k, f"color{n}")
    alias("magenta", "purple")
    alias("bright_magenta", "bright_purple")
    c.setdefault("background", "#1a1b26")
    c.setdefault("foreground", "#c0caf5")
    alias("light_foreground", "color7")
    alias("light_foreground", "foreground")
    alias("bright_foreground", "color15")
    alias("bright_foreground", "foreground")
    alias("lighter_background", "color0")
    alias("lighter_background", "background")
    alias("dark_foreground", "color8")
    alias("dark_foreground", "foreground")
    alias("muted", "color8")
    alias("muted", "dark_foreground")
    alias("selection", "selection_background")
    alias("selection", "color8")
    alias("selection", "background")
    alias("selection_background", "selection")
    alias("selection_foreground", "bright_foreground")
    for k in ("red", "green", "yellow", "blue", "magenta", "cyan"):
        c.setdefault(k, c["foreground"])
    c.setdefault("orange", c["yellow"])
    if not c.get("brown"):
        c["brown"] = mix(c["orange"], "#000000", 0.5)
    if not c.get("dark_background"):
        c["dark_background"] = mix(c["background"], "#000000", 0.25)
    if not c.get("darker_background"):
        c["darker_background"] = mix(c["background"], "#000000", 0.5)
    for k in ("red", "yellow", "green", "cyan", "blue", "magenta"):
        if not c.get(f"bright_{k}"):
            c[f"bright_{k}"] = mix(c[k], "#ffffff", 0.2)
    alias("purple", "magenta")
    alias("bright_purple", "bright_magenta")
    ansi = ["background", "red", "green", "yellow", "blue", "magenta", "cyan", "foreground", "muted",
            "bright_red", "bright_green", "bright_yellow", "bright_blue", "bright_magenta", "bright_cyan",
            "bright_foreground"]
    for n, k in enumerate(ansi):
        alias(f"color{n}", k)
    alias("accent", "color4")
    for k, s in short.items():
        if c.get(k):
            c[s] = c[k]

    mode = c.get("mode") or c.get("theme_type")
    if not mode:
        if theme_dir and (theme_dir / "light.mode").exists():
            mode = "light"
        elif is_hex(c["background"]):
            bg = c["background"].lstrip("#")
            mode = "light" if sum(int(bg[i:i + 2], 16) for i in (0, 2, 4)) > 382 else "dark"
        else:
            mode = "dark"
    c["mode"] = c["theme_type"] = mode

    # Hyprland borders: colors.toml may set hyprland_active_border; Omarchy 4 themes put it in
    # hyprland.lua, which Hyprland 0.53 can't read, so lift the colours out of it.
    lua = theme_dir / "hyprland.lua" if theme_dir else None
    if lua and lua.exists():
        text = lua.read_text()
        for var, key in (("active_border_color", "hyprland_active_border"),
                         ("inactive_border_color", "hyprland_inactive_border")):
            m = re.search(rf"local {var}\s*=\s*(.+)", text)
            if m and key not in c:
                c[key] = lua_gradient(m.group(1))
    c.setdefault("hyprland_active_border", c["accent"])
    c.setdefault("hyprland_inactive_border", "rgba(595959aa)")
    for key in ("hyprland_active_border", "hyprland_inactive_border"):
        c[key + "_hypr"] = hypr_gradient(c[key])
    return c


def lua_gradient(expr: str) -> str:
    """'"rgb(faa968)"' or '{ colors = { "rgb(a)", "rgb(b)" }, angle = 45 }' -> 'rgb(a) rgb(b) 45deg'."""
    colours = re.findall(r'"([^"]+)"', expr)
    angle = re.search(r"angle\s*=\s*(-?\d+)", expr)
    return " ".join(colours) + (f" {angle.group(1)}deg" if angle else "")


def hypr_gradient(spec: str) -> str:
    """A border spec in Hyprland 0.53 syntax: hex colours become rgb(), the rest passes through."""
    parts = []
    for p in spec.split():
        if is_hex(p):
            parts.append(f"rgb({p[1:]})")
        elif re.fullmatch(r"#[0-9a-fA-F]{8}", p):
            parts.append(f"rgba({p[1:]})")
        else:
            parts.append(p)
    return " ".join(parts)


# --- templates -----------------------------------------------------------------------

TOKEN = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def render(text: str, c: dict, name: str) -> str:
    def value(m):
        words = m.group(1).split()
        if words[0] in ("mix", "mix_strip", "mix_rgb") and len(words) == 4:
            a, b, amt = c.get(words[1], words[1]), c.get(words[2], words[2]), words[3]
            if is_hex(a) and is_hex(b):
                v = mix(a, b, float(amt.rstrip("%")) / (100 if amt.endswith("%") or float(amt.rstrip("%")) > 1 else 1))
                return fmt(v, words[0][3:])
        if len(words) == 1:
            key = words[0]
            for suffix in ("_strip", "_rgb"):
                if key.endswith(suffix) and key[: -len(suffix)] in c:
                    return fmt(c[key[: -len(suffix)]], suffix)
            if key in c:
                return c[key]
        print(f"omarchy-theme: {name}: unknown token {m.group(0)}", file=sys.stderr)
        return m.group(0)

    return TOKEN.sub(value, text)


def fmt(v: str, suffix: str) -> str:
    if suffix == "_strip":
        return v.lstrip("#")
    if suffix == "_rgb" and is_hex(v):
        h = v.lstrip("#")
        return ",".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    return v


# --- themes --------------------------------------------------------------------------

def theme_dirs() -> dict[str, Path]:
    found = {}
    for root in (OMARCHY_THEMES, USER_THEMES):  # yours override Omarchy's of the same name
        if root.is_dir():
            for d in sorted(root.iterdir()):
                if (d / "colors.toml").exists() or (d / "alacritty.toml").exists():
                    found[d.name] = d
    return found


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def pretty(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split("-"))


def current() -> str | None:
    try:
        return THEME_NAME.read_text().strip() or None
    except OSError:
        return None


def backgrounds(name: str, theme_dir: Path | None = None) -> list[Path]:
    """Your ~/.config/omarchy/backgrounds/<theme>/ first, then the theme's own."""
    theme_dir = theme_dir or theme_dirs().get(name)
    out = []
    for d in (CONFIG / "backgrounds" / name, theme_dir / "backgrounds" if theme_dir else None):
        if d and d.is_dir():
            out += sorted(p for p in d.iterdir() if p.suffix.lower() in IMAGE_EXT)
    return out


# --- state: remembered wallpaper per theme, the previous look for undo ----------------

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


def current_background() -> Path | None:
    try:
        return Path(os.readlink(BACKGROUND)) if BACKGROUND.is_symlink() else None
    except OSError:
        return None


def remembered_background(name: str, theme_dir: Path | None = None) -> Path | None:
    """The wallpaper last used with this theme, if it is still one of its wallpapers, else its first."""
    bgs = backgrounds(name, theme_dir)
    saved = load_state().get("backgrounds", {}).get(name)
    if saved and Path(saved) in bgs:
        return Path(saved)
    return bgs[0] if bgs else None


def remember(name: str | None, background: Path | None, previous: dict | None = None) -> None:
    s = load_state()
    if name and background:
        s.setdefault("backgrounds", {})[name] = str(background)
    if previous is not None:
        s["previous"] = previous
    save_state(s)


def snapshot() -> dict:
    """The look right now, for undo."""
    bg = current_background()
    return {"theme": current(), "background": str(bg) if bg else None}


def set_background(path: Path) -> None:
    CURRENT.mkdir(parents=True, exist_ok=True)
    tmp = BACKGROUND.with_name("background.new")
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(path)
    os.replace(tmp, BACKGROUND)
    subprocess.run(["pkill", "-x", "swaybg"], check=False)
    subprocess.Popen(["swaybg", "-i", str(path), "-m", "fill"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def stage(name: str, theme_dir: Path) -> dict:
    raw = tomllib.loads((theme_dir / "colors.toml").read_text()) if (theme_dir / "colors.toml").exists() else {}
    c = resolve(raw, theme_dir)
    c["theme_name"] = name
    c["theme_title"] = pretty(name)

    nxt = CURRENT / "next-theme"
    shutil.rmtree(nxt, ignore_errors=True)
    nxt.mkdir(parents=True)
    for f in theme_dir.iterdir():
        if f.name == "backgrounds" or (f.is_file() and f.suffix.lower() in IMAGE_EXT):
            (nxt / f.name).symlink_to(f)  # previews and wallpapers stay where they are
        elif f.is_file():
            shutil.copy2(f, nxt / f.name)

    templates = {}
    for d in (TEMPLATES, USER_TEMPLATES):  # yours win
        if d.is_dir():
            templates.update({t.name[:-4]: t for t in d.glob("*.tpl")})
    for out, tpl in sorted(templates.items()):
        if out in {f.name for f in theme_dir.iterdir()}:
            continue  # the theme ships its own file
        (nxt / out).write_text(render(tpl.read_text(), c, tpl.name))

    (nxt / "colors.json").write_text(json.dumps(c, indent=2, sort_keys=True) + "\n")
    old = CURRENT / "old-theme"
    shutil.rmtree(old, ignore_errors=True)
    if THEME_OUT.is_symlink() or THEME_OUT.is_file():
        THEME_OUT.unlink()
    elif THEME_OUT.exists():
        THEME_OUT.rename(old)
    nxt.rename(THEME_OUT)
    shutil.rmtree(old, ignore_errors=True)
    tmp = THEME_NAME.with_name("theme.name.new")
    tmp.write_text(name + "\n")
    os.replace(tmp, THEME_NAME)
    return c


def run(*cmd: str) -> None:
    try:
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def poke_apps(c: dict) -> None:
    """Make the running apps pick the new files up."""
    t = THEME_OUT
    run("hyprctl", "--batch",
        f"keyword general:col.active_border {c['hyprland_active_border_hypr']} ; "
        f"keyword general:col.inactive_border {c['hyprland_inactive_border_hypr']} ; "
        f"keyword group:col.border_active {c['hyprland_active_border_hypr']} ; "
        f"keyword group:col.border_inactive {c['hyprland_inactive_border_hypr']}")
    run("pkill", "-SIGUSR2", "-x", "waybar")  # reload style
    run("makoctl", "reload")
    alacritty = HOME / ".config/alacritty/alacritty.toml"
    if alacritty.exists():
        alacritty.touch()  # live_config_reload only watches the main file reliably
    light = c["mode"] == "light"
    run("gsettings", "set", "org.gnome.desktop.interface", "color-scheme", "prefer-light" if light else "prefer-dark")
    gtk = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
                         capture_output=True, text=True).stdout.strip().strip("'")
    if gtk:
        base = gtk.removesuffix("-dark")
        run("gsettings", "set", "org.gnome.desktop.interface", "gtk-theme", base if light else base + "-dark")
    if (t / "btop.theme").exists():
        run("pkill", "-SIGUSR2", "-x", "btop")


def run_hooks(name: str) -> None:
    hooks = [HOOKS / "theme-set"] + sorted((HOOKS / "theme-set.d").glob("*"))
    for h in hooks:
        if h.is_file() and os.access(h, os.X_OK) and not h.name.endswith(".sample"):
            subprocess.Popen([str(h), name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)


def cmd_set(args: list[str], record: bool = True) -> int:
    keep = "--keep-background" in args
    words, bg = [], None
    it = iter(args)
    for a in it:
        if a == "--background":
            bg = next(it, None)
        elif a.startswith("--background="):
            bg = a.split("=", 1)[1]
        elif not a.startswith("--"):
            words.append(a)
    if not words or (bg is not None and not bg):
        print("usage: omarchy-theme set <name> [--background <image> | --keep-background]", file=sys.stderr)
        return 2
    themes = theme_dirs()
    name = slug(" ".join(words))
    if name not in themes:
        print(f"omarchy-theme: no theme {name!r}. Installed: {', '.join(themes)}", file=sys.stderr)
        return 1
    if bg is not None and not Path(bg).expanduser().is_file():
        print(f"omarchy-theme: no image {bg!r}", file=sys.stderr)
        return 1
    before = snapshot()
    c = stage(name, themes[name])
    if not keep:
        image = Path(bg).expanduser().absolute() if bg else remembered_background(name, themes[name])
        if image:
            set_background(image)
    remember(name, None if keep else current_background(),
             before if record and before["theme"] and before != snapshot() else None)
    poke_apps(c)
    run_hooks(name)
    print(f"theme: {pretty(name)} ({c['mode']})")
    return 0


def cmd_list(_args) -> int:
    cur = current()
    for name in theme_dirs():
        print(("* " if name == cur else "  ") + name)
    return 0


def cmd_step(step: int) -> int:
    names = list(theme_dirs())
    if not names:
        return 1
    cur = current()
    i = names.index(cur) if cur in names else -step
    return cmd_set([names[(i + step) % len(names)]])


def cmd_bg_next(args: list[str]) -> int:
    name = current()
    bgs = backgrounds(name) if name else []
    if not bgs:
        legacy = Path("/usr/share/backgrounds")
        bgs = sorted(p for p in legacy.glob("*") if p.suffix.lower() in IMAGE_EXT)
    if not bgs:
        return 1
    cur = BACKGROUND.resolve() if BACKGROUND.exists() else None
    if "--restore" in args and cur:
        set_background(cur)
        return 0
    i = bgs.index(cur) if cur in bgs else -1
    before = snapshot()
    set_background(bgs[(i + 1) % len(bgs)])
    remember(name, current_background(), before)
    return 0


def cmd_bg(args: list[str]) -> int:
    if len(args) != 1 or not Path(args[0]).expanduser().is_file():
        print("usage: omarchy-theme bg <image>", file=sys.stderr)
        return 2
    before = snapshot()
    set_background(Path(args[0]).expanduser().absolute())
    remember(current(), current_background(), before)
    return 0


def cmd_undo(_args) -> int:
    """Back to the look before the last change. Running it again goes forward again."""
    prev = load_state().get("previous") or {}
    name, bg = prev.get("theme"), prev.get("background")
    if not name:
        print("omarchy-theme: nothing to undo", file=sys.stderr)
        return 1
    if bg and not Path(bg).is_file():
        bg = None
    if name == current():
        if not bg:
            return 1
        return cmd_bg([bg])
    return cmd_set([name] + (["--background", bg] if bg else []))


def cmd_refresh(_args) -> int:
    name = current()
    if not name:
        print("omarchy-theme: no current theme", file=sys.stderr)
        return 1
    return cmd_set([name, "--keep-background"], record=False)


COMMANDS = {
    "set": cmd_set, "list": cmd_list, "current": lambda a: print(current() or "") or 0,
    "next": lambda a: cmd_step(1), "prev": lambda a: cmd_step(-1),
    "bg": cmd_bg, "bg-next": cmd_bg_next, "undo": cmd_undo, "refresh": cmd_refresh,
}


def main(argv: list[str]) -> int:
    prog = Path(argv[0]).name
    if prog.startswith("omarchy-theme-") and prog[len("omarchy-theme-"):] in COMMANDS:
        return COMMANDS[prog[len("omarchy-theme-"):]](argv[1:])
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    return COMMANDS[argv[1]](argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
