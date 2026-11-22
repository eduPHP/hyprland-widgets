"""hypr-ui helper for GTK4 layer-shell widgets.

    import sys; sys.path.insert(0, os.path.expanduser("~/.config/hypr-ui/gtk"))
    import hypr_ui

    hypr_ui.load_css(HERE / "style.css")          # base stylesheet + the widget's own, one provider
    hypr_ui.popover(window, Layer)                # top-right placement, shadow room, click-through margin

The root box of a popover gets the `popover` CSS class; a bottom toast gets `toast`.
"""

import json
import os
import re
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOKENS = json.loads((ROOT / "tokens.json").read_text())
SURFACE = TOKENS["surface"]
MARGIN = SURFACE["margin"]

# --- theme -------------------------------------------------------------------------
# theme.py writes the Omarchy theme's colours here (see hypr-ui/theme.py); they replace the
# tokens.json colours, and the widgets repaint live when the file changes.
THEME_FILE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "hypr-ui/theme.json"
DEFAULT_COLORS = dict(TOKENS["color"])
_theme: dict = {"providers": [], "callbacks": [], "monitor": None, "pending": 0}


def _theme_colors() -> dict:
    try:
        return {k: v for k, v in json.loads(THEME_FILE.read_text())["color"].items() if k in DEFAULT_COLORS}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _apply_theme_colors() -> None:
    """Update TOKENS["color"] in place, so code holding a reference to it sees the new values."""
    TOKENS["color"].clear()
    TOKENS["color"].update(DEFAULT_COLORS)
    TOKENS["color"].update(_theme_colors())


_apply_theme_colors()


def rgb(name: str) -> tuple[float, float, float]:
    """A token colour as cairo floats, read now (so it follows the theme)."""
    h = TOKENS["color"][name].lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgba(name: str, a: float = 1.0) -> tuple[float, float, float, float]:
    return (*rgb(name), a)


def hex_color(name: str) -> str:
    """A token colour as #rrggbb, for Pango markup."""
    return TOKENS["color"][name]


def mix(a: str, b: str, t: float) -> tuple[float, float, float]:
    """Blend two token colours (t=0 -> a, 1 -> b) as cairo floats."""
    return tuple(x * (1 - t) + y * t for x, y in zip(rgb(a), rgb(b)))


def on_theme_changed(callback) -> None:
    """Call `callback()` after the theme changes (for state that caches colours)."""
    _theme["callbacks"].append(callback)


def css_text(own=(), colors: dict | None = None) -> str:
    """The base sheet followed by `own` sheets, with the theme's colours, or `colors` instead
    (for a widget that paints itself in some other theme, like hyprtheme's preview)."""
    colors = {**TOKENS["color"], **(colors or {})}
    text = re.sub(r"@define-color (\w+) [^;]+;",
                  lambda m: f"@define-color {m.group(1)} {colors[m.group(1)]};" if m.group(1) in colors else m.group(0),
                  (ROOT / "gtk/hypr-ui.css").read_text())
    for p in own:
        text += f"\n/* ---- {Path(p).name} ---- */\n" + Path(p).read_text()
    return text


def _redraw(widget) -> None:
    widget.queue_draw()
    child = widget.get_first_child()
    while child is not None:
        _redraw(child)
        child = child.get_next_sibling()


def _reload_theme() -> bool:
    _theme["pending"] = 0
    _apply_theme_colors()
    for provider, own in _theme["providers"]:
        provider.load_from_string(css_text(own))
    for cb in _theme["callbacks"]:
        cb()
    for win in Gtk.Window.list_toplevels():
        _redraw(win)
    return False


def _watch_theme() -> None:
    if _theme["monitor"] is not None:
        return
    THEME_FILE.parent.mkdir(parents=True, exist_ok=True)
    mon = Gio.File.new_for_path(str(THEME_FILE)).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)

    def changed(*_):  # a write comes as several events; settle first
        if not _theme["pending"]:
            _theme["pending"] = GLib.timeout_add(150, _reload_theme)

    mon.connect("changed", changed)
    _theme["monitor"] = mon


def load_css(*own: Path) -> Gtk.CssProvider:
    """One provider holding the base sheet followed by the widget's own sheets, so the
    widget can use every @colour and override any base rule. Follows the theme live."""
    provider = Gtk.CssProvider()
    provider.load_from_string(css_text(own))
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    _theme["providers"].append((provider, own))
    _watch_theme()
    return provider


def _clip_input(window: Gtk.Window) -> None:
    """Only the visible card takes input; the shadow margin passes clicks through."""

    def apply(surface, width, height):
        rect = cairo.RectangleInt(MARGIN, MARGIN, max(0, width - 2 * MARGIN), max(0, height - 2 * MARGIN))
        surface.set_input_region(cairo.Region(rect))

    def on_realize(win):
        surface = win.get_surface()
        surface.connect("layout", apply)

    window.connect("realize", on_realize)


def solo(name: str) -> None:
    """Close every other hypr-ui popover (call as this one opens). Runs in the background."""
    Gio.Subprocess.new([str(ROOT / "bin/hypr-ui-solo"), name], Gio.SubprocessFlags.NONE)


def popover(window: Gtk.Window, layer) -> None:
    """Anchor a popover under the bar at the top-right corner, `gapTop`/`gapRight` px from
    the edges as seen on screen, with the window grown by MARGIN for the shadow."""
    layer.set_anchor(window, layer.Edge.TOP, True)
    layer.set_anchor(window, layer.Edge.RIGHT, True)
    layer.set_margin(window, layer.Edge.TOP, SURFACE["gapTop"] - MARGIN)
    layer.set_margin(window, layer.Edge.RIGHT, SURFACE["gapRight"] - MARGIN)
    _clip_input(window)


def toast(window: Gtk.Window, layer, bottom: int) -> None:
    """Bottom-centre toast whose card sits `bottom` px above the screen edge; fully click-through."""
    layer.set_anchor(window, layer.Edge.BOTTOM, True)
    layer.set_margin(window, layer.Edge.BOTTOM, bottom - MARGIN)
    window.connect("map", lambda w: w.get_surface().set_input_region(cairo.Region()))
