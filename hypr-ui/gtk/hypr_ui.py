"""hypr-ui helper for GTK4 layer-shell widgets.

    import sys; sys.path.insert(0, os.path.expanduser("~/.config/hypr-ui/gtk"))
    import hypr_ui

    hypr_ui.load_css(HERE / "style.css")          # base stylesheet + the widget's own, one provider
    hypr_ui.popover(window, Layer)                # top-right placement, shadow room, click-through margin
    hypr_ui.layer(window, Layer, Layer.Layer.OVERLAY)  # set the layer; steps down under a screenshot overlay

The root box of a popover gets the `popover` CSS class; a bottom toast gets `toast`.
"""

import json
import os
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOKENS = json.loads((ROOT / "tokens.json").read_text())
SURFACE = TOKENS["surface"]
MARGIN = SURFACE["margin"]


def load_css(*own: Path) -> Gtk.CssProvider:
    """One provider holding the base sheet followed by the widget's own sheets, so the
    widget can use every @colour and override any base rule."""
    text = (ROOT / "gtk/hypr-ui.css").read_text()
    for p in own:
        text += f"\n/* ---- {Path(p).name} ---- */\n" + Path(p).read_text()
    provider = Gtk.CssProvider()
    provider.load_from_string(text)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
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


# --- screenshot overlay -----------------------------------------------------------
# Flameshot draws its overlay as a fullscreen window, which Hyprland puts above the
# TOP layer but below OVERLAY. ~/.local/bin/flameshot-gui creates this flag once the
# overlay window is up (the capture is already taken, so the widgets are in it) and
# removes it when the overlay closes; meanwhile OVERLAY surfaces step down to TOP so
# the overlay covers them.
CAPTURE_FLAG = Path(os.environ.get("XDG_RUNTIME_DIR") or GLib.get_user_runtime_dir()) / "hypr-ui-capture"
_capture: dict = {"monitor": None, "windows": []}


def _capture_apply(*_):
    down = CAPTURE_FLAG.exists()
    for win, ls, normal in _capture["windows"]:
        want = ls.Layer.TOP if down and normal == ls.Layer.OVERLAY else normal
        if ls.get_layer(win) != want:
            ls.set_layer(win, want)


def layer(window: Gtk.Window, ls, normal) -> None:
    """Put a layer-shell window on `normal`, stepping down to TOP while a screenshot
    overlay is up (see CAPTURE_FLAG). Use instead of `ls.set_layer`."""
    ls.set_layer(window, normal)
    entry = (window, ls, normal)
    _capture["windows"].append(entry)
    window.connect("destroy", lambda *_: entry in _capture["windows"] and _capture["windows"].remove(entry))
    if _capture["monitor"] is None:
        mon = Gio.File.new_for_path(str(CAPTURE_FLAG)).monitor_file(Gio.FileMonitorFlags.NONE, None)
        mon.connect("changed", _capture_apply)
        _capture["monitor"] = mon
    _capture_apply()
