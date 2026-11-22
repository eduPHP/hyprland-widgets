"""hyprpower: resident GTK4 layer-shell power + display popover in the hypr-ui look.
Driven over a unix socket by the `hyprpower` script."""

from __future__ import annotations

import dataclasses
import math
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402
from gi.repository import Gtk4LayerShell as Layer  # noqa: E402

sys.path.insert(0, os.path.expanduser("~/.config/hypr-ui/gtk"))
import hypr_ui  # noqa: E402  shared look: tokens, base stylesheet, popover placement, solo()
import backend as be  # noqa: E402

HERE = Path(__file__).resolve().parent
OPEN_FLAG = be.RUN / "hyprpower.open"
WIDTH = 380
INNER = WIDTH - 24  # card padding
CONFIRM_S = 10
C = hypr_ui.TOKENS["color"]
FONT = hypr_ui.TOKENS["font"]["family"]

# nerd font (material design) glyphs
G = {
    "bat": "\U000f0079", "charging": "\U000f0084", "plug": "\U000f06a5", "coffee": "\U000f0176",
    "bell": "\U000f009a", "monitor": "\U000f0379", "kbd": "\U000f030c", "moon": "\U000f0594",
    "sun": "\U000f05a8", "close": "\U000f0156", "rotate": "\U000f0467", "leaf": "\U000f032a",
    "speed": "\U000f04c5", "scale": "\U000f04c5",
}
PROFILE_HINT = {
    "power-saver": "cpu held back · platform low-power · quiet",
    "balanced": "default · cpu scales with load",
    "performance": "max clocks · fans louder · drains faster",
}
CHARGE_HINT = {
    "Adaptive": "learns your routine · holds the charge lower when you stay plugged in",
    "Standard": "charges to 100% at normal speed",
    "Fast": "charges to 100% quickly · more battery wear",
    "Custom": "charges only between the two limits · best if always plugged in",
    "Trickle": "slow charge · for a battery that is always plugged in",
}
MODE_LABEL = {"extend": "extend", "mirror": "mirror", "internal": "laptop only", "external": "external only"}
SCALES = [1.0, 1.2, 1.25, 1.5, 2.0]


def rgb(name: str) -> tuple[float, float, float]:
    h = C[name].lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


# ---------- small widget helpers (same shape as hyprcal / sound-widget) ----------

def label(text="", *classes, xalign=0.0, hexpand=False, ellipsize=0):
    w = Gtk.Label(label=text, xalign=xalign, hexpand=hexpand)
    for c in classes:
        w.add_css_class(c)
    if ellipsize:
        w.set_ellipsize(Pango.EllipsizeMode.END)
        w.set_max_width_chars(ellipsize)
    return w


def hbox(spacing=0, *children, **kw):
    b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing, **kw)
    for c in children:
        b.append(c)
    return b


def vbox(spacing=0, *children, **kw):
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, **kw)
    for c in children:
        b.append(c)
    return b


def button(text: str, css: str, cb, tip: str = "") -> Gtk.Button:
    b = Gtk.Button(label=text, valign=Gtk.Align.CENTER)
    b.add_css_class(css)
    if tip:
        b.set_tooltip_text(tip)
    b.connect("clicked", lambda *_: cb())
    return b


def keyhint(k: str, text: str) -> Gtk.Box:
    return hbox(4, label(k, "key"), label(text, "hint"))


def section(name: str, meta: Gtk.Label | None = None) -> Gtk.Box:
    row = hbox(0, label(name, "section", hexpand=True))
    if meta:
        row.append(meta)
    return row


def skel(width: int, height: int = 6) -> Gtk.Box:
    """A pulsing placeholder bar (loading skeleton)."""
    b = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
    b.set_size_request(width, height)
    b.add_css_class("skel")
    return b


def glyph(g: str, *classes) -> Gtk.Label:
    w = label(g, "glyph", *classes, xalign=0.5)
    w.set_valign(Gtk.Align.CENTER)
    return w


class Seg(Gtk.Box):
    """Segmented control: the base `.tabs` + `button.tab`, one active."""

    def __init__(self, items: list[tuple[str, str]], on_pick, small=False):
        super().__init__(spacing=2, homogeneous=True)
        self.add_css_class("tabs")
        self.add_css_class("seg")
        if small:
            self.add_css_class("small")
        self.on_pick = on_pick
        self.btns: dict[str, Gtk.Button] = {}
        self.active = None
        self.set_items(items)

    def set_items(self, items: list[tuple[str, str]]):
        for b in list(self.btns.values()):
            self.remove(b)
        self.btns = {}
        for key, text in items:
            b = Gtk.Button(label=text)
            b.add_css_class("tab")
            b.connect("clicked", lambda _b, k=key: self.on_pick(k))
            self.append(b)
            self.btns[key] = b
        self.set_active(self.active)

    def set_active(self, key):
        self.active = key
        for k, b in self.btns.items():
            (b.add_css_class if k == key else b.remove_css_class)("active")
            for c in ("p-power-saver", "p-balanced", "p-performance"):
                b.remove_css_class(c)
            if k == key:
                b.add_css_class(f"p-{k}")


class SwitchRow(Gtk.Box):
    def __init__(self, g: str, title: str, sub: str, on_set):
        super().__init__(spacing=10)
        self.add_css_class("row")
        self.sub = label(sub, "subtitle", ellipsize=40)
        self.sw = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.busy = False
        self.sw.connect("notify::active", lambda s, _p: None if self.busy else on_set(s.get_active()))
        self.append(glyph(g, "row-glyph"))
        self.append(vbox(2, label(title), self.sub, hexpand=True, valign=Gtk.Align.CENTER))
        self.append(self.sw)

    def set(self, on: bool):
        self.busy = True
        self.sw.set_active(on)
        self.busy = False


# ---------- drawn pieces ----------

class BatteryBar(Gtk.DrawingArea):
    def __init__(self):
        super().__init__(content_height=14, hexpand=True)
        self.frac = 0.0
        self.color = "blue"
        self.limit: float | None = None
        self.set_draw_func(self.draw)

    def draw(self, _a, cr, w, h):
        y, bh = (h - 8) / 2, 8
        self._pill(cr, 0, y, w, bh, rgb("track"))
        if self.frac > 0:
            self._pill(cr, 0, y, max(bh, w * self.frac), bh, rgb(self.color))
        if self.limit is not None:
            cr.set_source_rgb(*rgb("amber"))
            x = round(w * self.limit) - 1
            cr.rectangle(x, 0, 2, h)
            cr.fill()

    @staticmethod
    def _pill(cr, x, y, w, h, col):
        r = h / 2
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, math.pi / 2)
        cr.arc(x + r, y + r, r, math.pi / 2, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgb(*col)
        cr.fill()


class Meter(Gtk.DrawingArea):
    """Thin share bar for the battery-users list."""

    def __init__(self):
        super().__init__(content_width=84, content_height=10, valign=Gtk.Align.CENTER)
        self.frac = 0.0
        self.set_draw_func(self.draw)

    def draw(self, _a, cr, w, h):
        y = (h - 4) / 2
        BatteryBar._pill(cr, 0, y, w, 4, rgb("track"))
        if self.frac > 0:
            BatteryBar._pill(cr, 0, y, max(4, w * self.frac), 4, rgb("amber"))


class Spark(Gtk.DrawingArea):
    """Charge level over the last hours (upower history)."""

    def __init__(self, hours: float):
        super().__init__(content_height=34, hexpand=True)
        self.hours = hours
        self.pts: list[tuple[float, float]] = []
        self.set_draw_func(self.draw)

    def draw(self, _a, cr, w, h):
        if len(self.pts) < 2:
            return
        now = time.time()
        t0 = now - self.hours * 3600
        vals = [v for _, v in self.pts]
        lo, hi = max(0.0, min(vals) - 8), min(100.0, max(vals) + 8)  # zoom to the range, so small moves show
        span = max(hi - lo, 10.0)
        xy = [((t - t0) / (now - t0) * w, h - 2 - ((v - lo) / span) * (h - 4)) for t, v in self.pts]
        xy.append((w, xy[-1][1]))
        cr.set_line_width(1.5)
        cr.set_source_rgb(*rgb("blue"))
        cr.move_to(*xy[0])
        for p in xy[1:]:
            cr.line_to(*p)
        cr.stroke_preserve()
        cr.line_to(w, h)
        cr.line_to(xy[0][0], h)
        cr.close_path()
        r, g, b = rgb("blue")
        cr.set_source_rgba(r, g, b, 0.08)
        cr.fill()


class MonitorMap(Gtk.DrawingArea):
    """Miniature of the monitor layout; click a screen to select it."""

    H = 136

    def __init__(self, on_select):
        super().__init__(content_width=INNER, content_height=self.H)
        self.add_css_class("map")
        self.on_select = on_select
        self.mons: list[be.Monitor] = []
        self.mode = "extend"
        self.place = "right"
        self.sel = ""
        self.hits: list[tuple[str, tuple[float, float, float, float]]] = []
        self.set_draw_func(self.draw)
        click = Gtk.GestureClick()
        click.connect("pressed", self._click)
        self.add_controller(click)

    def _click(self, _g, _n, x, y):
        for name, (rx, ry, rw, rh) in reversed(self.hits):
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                self.on_select(name)
                return

    def _rects(self) -> list[tuple[be.Monitor, float, float, float, float, bool]]:
        """(monitor, x, y, w, h, off) in layout units."""
        internal = next((m for m in self.mons if m.internal), None)
        ext = [m for m in self.mons if not m.internal]
        out = []
        if internal is None or not ext:
            for m in self.mons:
                w, h = m.logical
                out.append((m, m.x, m.y, w, h, m.disabled))
            return out
        e = ext[0]
        iw, ih = internal.logical
        if self.mode == "mirror":
            return [(e, 0, 0, iw, ih, False), (internal, 0, 0, iw, ih, False)]
        if self.mode == "extend":
            ew, eh = e.logical
            return [(internal, internal.x, internal.y, iw, ih, False), (e, e.x, e.y, ew, eh, False)]
        # one side off: draw the off one at its usual place, dashed
        ew, eh = e.logical if e.width else (iw, ih)
        pos = be.place_positions(internal, dataclasses.replace(e, width=int(ew), height=int(eh), scale=1.0,
                                                               transform=0), self.place)
        return [(internal, *pos[internal.name], iw, ih, self.mode == "external"),
                (e, *pos[e.name], ew, eh, self.mode == "internal")]

    def draw(self, _a, cr, w, h):
        self.hits = []
        rects = self._rects()
        if not rects:
            return
        minx = min(r[1] for r in rects)
        miny = min(r[2] for r in rects)
        maxx = max(r[1] + r[3] for r in rects)
        maxy = max(r[2] + r[4] for r in rects)
        pad = 14
        k = min((w - 2 * pad) / max(1, maxx - minx), (h - 2 * pad) / max(1, maxy - miny))
        if len(rects) == 1 or self.mode == "mirror":
            k *= 0.8
        ox = (w - (maxx - minx) * k) / 2
        oy = (h - (maxy - miny) * k) / 2
        for i, (m, x, y, rw, rh, off) in enumerate(rects):
            d = 8 if self.mode == "mirror" and i == 0 else 0
            bx, by = ox + (x - minx) * k + 2 + d, oy + (y - miny) * k + 2 + d
            bw, bh = rw * k - 4, rh * k - 4
            self.hits.append((m.name, (bx, by, bw, bh)))
            on = m.name == self.sel
            self._rrect(cr, bx, by, bw, bh, 6)
            cr.set_source_rgb(*(rgb("bg") if off else hypr_ui.mix("bg", "blue", 0.2) if on else rgb("raised")))
            cr.fill_preserve()
            cr.set_line_width(1)
            cr.set_dash([4, 3] if off else [])
            cr.set_source_rgb(*rgb("blue" if on else "frame"))
            cr.stroke()
            cr.set_dash([])
            if self.mode == "mirror" and i == 0:
                continue
            sub = "off" if off else (f"mirror · {be.fmt_scale(m.scale)}×" if self.mode == "mirror"
                                      else f"{m.width}×{m.height} · {be.fmt_scale(m.scale)}×")
            self._text(cr, m.name, bx + bw / 2, by + bh / 2 - 7, 9, rgb("faint" if off else "bright"))
            self._text(cr, sub, bx + bw / 2, by + bh / 2 + 7, 7.5, rgb("muted"))

    @staticmethod
    def _rrect(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    @staticmethod
    def _text(cr, text, cx, cy, size, col):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription(f"{FONT} {size}"))
        layout.set_text(text, -1)
        tw, th = layout.get_pixel_size()
        cr.move_to(cx - tw / 2, cy - th / 2)
        cr.set_source_rgb(*col)
        PangoCairo.show_layout(cr, layout)


# ---------- windows ----------

class Backdrop(Gtk.Window):
    """Invisible full-screen layer under the popover: a click on it means "clicked outside"."""

    def __init__(self, app, on_click):
        super().__init__(application=app, title="hyprpower-backdrop")
        self.add_css_class("backdrop")
        Layer.init_for_window(self)
        Layer.set_namespace(self, "hyprpower-backdrop")
        Layer.set_layer(self, Layer.Layer.TOP)
        for edge in (Layer.Edge.TOP, Layer.Edge.BOTTOM, Layer.Edge.LEFT, Layer.Edge.RIGHT):
            Layer.set_anchor(self, edge, True)
        Layer.set_keyboard_mode(self, Layer.KeyboardMode.NONE)
        self.set_child(Gtk.Box(hexpand=True, vexpand=True))
        click = Gtk.GestureClick(button=0)
        click.connect("pressed", lambda *_: on_click())
        self.add_controller(click)


class Toast(Gtk.Window):
    """Bottom-centre notice (charger in/out → profile), click-through, hides itself."""

    def __init__(self, app):
        super().__init__(application=app, title="hyprpower-toast")
        Layer.init_for_window(self)
        Layer.set_namespace(self, "hyprpower-toast")
        Layer.set_layer(self, Layer.Layer.OVERLAY)
        hypr_ui.toast(self, Layer, bottom=88)
        Layer.set_keyboard_mode(self, Layer.KeyboardMode.NONE)
        self.icon = glyph("", "toast-glyph")
        self.title = label("", "toast-title")
        self.sub = label("", "subtitle", ellipsize=44)
        box = hbox(12, self.icon, vbox(2, self.title, self.sub, hexpand=True, valign=Gtk.Align.CENTER))
        box.add_css_class("toast")
        box.set_size_request(WIDTH + 2 * hypr_ui.MARGIN, -1)
        self.set_child(box)
        self.timer = 0

    def show(self, g: str, color: str, title: str, sub: str):
        for c in ("c-green", "c-blue", "c-orange", "c-amber", "c-red"):
            self.icon.remove_css_class(c)
        self.icon.add_css_class(f"c-{color}")
        self.icon.set_text(g)
        self.title.set_text(title)
        self.sub.set_text(sub)
        self.present()
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(3200, self._hide)

    def _hide(self):
        self.timer = 0
        self.set_visible(False)
        return False


class Identify(Gtk.Window):
    """Big connector name in the middle of one monitor, for a moment."""

    def __init__(self, app, mon: Gdk.Monitor, name: str):
        super().__init__(application=app, title="hyprpower-identify")
        Layer.init_for_window(self)
        Layer.set_namespace(self, "hyprpower-identify")
        Layer.set_layer(self, Layer.Layer.OVERLAY)
        Layer.set_monitor(self, mon)
        Layer.set_keyboard_mode(self, Layer.KeyboardMode.NONE)
        box = vbox(4, label(G["monitor"], "ident-glyph", xalign=0.5), label(name, "ident-name", xalign=0.5))
        box.add_css_class("toast")
        box.add_css_class("ident")
        self.set_child(box)
        self.connect("map", lambda w: w.get_surface().set_input_region(__import__("cairo").Region()))
        self.present()
        GLib.timeout_add(1600, lambda: self.destroy() or False)


class Panel(Gtk.ApplicationWindow):
    def __init__(self, app: "App"):
        super().__init__(application=app, title="hyprpower", resizable=False)
        self.app = app
        Layer.init_for_window(self)
        Layer.set_namespace(self, "hyprpower")
        Layer.set_layer(self, Layer.Layer.OVERLAY)
        hypr_ui.popover(self, Layer)
        Layer.set_keyboard_mode(self, Layer.KeyboardMode.ON_DEMAND)

        self.tab = "power"
        self.bat = be.Battery()
        self.prof = "balanced"
        self.mons: list[be.Monitor] = []
        self.sel = ""
        self.place = "right"
        self.revert_lines: list[str] | None = None
        self.confirm_left = 0
        self.confirm_timer = 0
        self.tick_timer = 0
        self.bright_touched = 0.0
        self.bright_timer = 0
        self.updating = False
        self.gen = 0  # bumped on every opening; readings from an earlier opening are dropped
        self.loading: int | None = None  # gen of the reading in flight
        self.cpu = be.CpuSampler()

        self.backdrop = Backdrop(app, lambda: self.get_visible() and self.toggle())
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self._build()

    # ---------- layout ----------

    def _build(self):
        root = vbox(10)
        root.add_css_class("popover")
        root.set_size_request(WIDTH + 2 * hypr_ui.MARGIN, -1)
        self.set_default_size(WIDTH + 2 * hypr_ui.MARGIN, -1)
        self.set_child(root)

        self.app_icon = label(G["bat"], "app-icon", xalign=0.5)
        self.title = label("power", "title")
        self.subtitle = label("", "subtitle", ellipsize=44)
        root.append(hbox(10, self.app_icon,
                         vbox(0, self.title, self.subtitle, hexpand=True, valign=Gtk.Align.CENTER),
                         button(G["close"], "icon", self.toggle, "close (esc)")))

        self.tabs = Seg([("power", "power"), ("display", "display")], self.set_tab)
        self.tabs.add_css_class("main-tabs")
        root.append(self.tabs)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, vhomogeneous=False,
                               interpolate_size=False)
        self.stack.add_named(self._build_power(), "power")
        self.stack.add_named(self._build_display(), "display")
        root.append(self.stack)

        self.footer = hbox(10)
        self.footer.add_css_class("footer")
        root.append(self.footer)
        self.set_tab("power")

    def _build_power(self) -> Gtk.Widget:
        page = vbox(10)

        # hero
        self.pct = label("", "hero")
        self.pct.set_valign(Gtk.Align.END)
        self.state_l = label("", "state")
        self.time_l = label("", "subtitle")
        self.rate_l = label("", "rate", xalign=1.0)
        self.source_l = label("", "subtitle", xalign=1.0)
        self.bar = BatteryBar()
        self.spark = Spark(3.0)
        hero = vbox(8,
                    hbox(10, self.pct, vbox(1, self.state_l, self.time_l, hexpand=True, valign=Gtk.Align.END),
                         vbox(1, self.rate_l, self.source_l, valign=Gtk.Align.END)),
                    self.bar, self.spark,
                    hbox(0, label("3 h ago", "axis", hexpand=True), label("now", "axis", xalign=1.0)))
        hero.add_css_class("card")
        page.append(hero)

        # stats
        grid = Gtk.Grid(column_spacing=6, column_homogeneous=True)
        self.stats: dict[str, tuple[Gtk.Label, Gtk.Label]] = {}
        for i, key in enumerate(("health", "cycles", "temp", "voltage")):
            v, s = label("–", "stat-val"), label("", "stat-sub", ellipsize=12)
            cell = vbox(1, label(key, "stat-key"), v, s)
            cell.add_css_class("stat")
            grid.attach(cell, i, 0, 1, 1)
            self.stats[key] = (v, s)
        page.append(grid)

        # who is using the battery (only while unplugged)
        self.users_rows: list[tuple[Gtk.Box, Gtk.Label, Meter, Gtk.Label]] = []
        rows = vbox(0)
        for _ in range(3):
            name, meter, val = label("", hexpand=True, ellipsize=26), Meter(), label("", "val", xalign=1.0)
            row = hbox(10, name, meter, val)
            row.add_css_class("row")
            row.add_css_class("user-row")
            rows.append(row)
            self.users_rows.append((row, name, meter, val))
        self.users_note = label("", "hint-line", ellipsize=52)
        self.users_rows_box = rows
        # skeleton shown until the second cpu sample arrives (same row geometry, pulsing)
        self.users_skel = vbox(0)
        for w in (84, 52, 66):
            val = Gtk.Box()  # same 36 px column as the real percentage, bar at its right edge
            val.set_size_request(36, -1)
            bar = skel(22)
            bar.set_halign(Gtk.Align.END)
            bar.set_hexpand(True)
            val.append(bar)
            row = hbox(10, skel(w), Gtk.Box(hexpand=True), skel(84, 4), val)
            row.add_css_class("row")
            row.add_css_class("user-row")
            self.users_skel.append(row)
        self.users_note_skel = hbox(0, skel(180))
        self.users_note_skel.add_css_class("skel-note")
        self.users = vbox(4, section("using the battery", label("share of cpu time · click for htop", "section-meta")),
                          self.users_skel, rows, self.users_note_skel, self.users_note)
        self.users.add_css_class("users")
        self.users.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        self.users.set_tooltip_text("open htop")
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self.open_htop())
        self.users.add_controller(click)
        self.users_loading(True)  # the skeleton shows until the first measurement
        page.append(self.users)

        # profile
        self.profiles = Seg([(p, p) for p in be.PROFILES], self.pick_profile, small=True)
        self.profile_hint = label("", "hint-line", ellipsize=52)
        page.append(vbox(6, section("power profile", label("power-profiles-daemon", "section-meta")),
                         self.profiles, self.profile_hint))

        # charging
        self.charges = Seg([(t, t.lower()) for t in be.CHARGE_TYPES], self.pick_charge, small=True)
        self.charge_hint = label("", "hint-line", ellipsize=52)
        self.start_l = label("", "thr", xalign=0.5)
        self.end_l = label("", "thr", "end", xalign=0.5)
        self.custom = hbox(6,
                           label("start at", "subtitle", hexpand=True),
                           button("−", "step", lambda: self.step_thr(-5, 0)), self.start_l,
                           button("+", "step", lambda: self.step_thr(5, 0)),
                           label("stop at", "subtitle", hexpand=True, xalign=1.0),
                           button("−", "step", lambda: self.step_thr(0, -5)), self.end_l,
                           button("+", "step", lambda: self.step_thr(0, 5)))
        self.custom.add_css_class("custom")
        page.append(vbox(6, section("charging", label("dell charge_types", "section-meta")),
                         self.charges, self.charge_hint, self.custom))

        # automation
        self.awake_row = SwitchRow(G["coffee"], "keep awake", "stop hypridle · no dim, no lock", self.set_awake)
        self.follow_row = SwitchRow(G["plug"], "follow the charger", "battery → power-saver · ac → balanced",
                                    self.set_follow)
        self.alert_row = SwitchRow(G["bell"], "low battery alert", "battery-alert · beeps, voice, red flash",
                                   self.set_alert)
        page.append(vbox(2, section("automation"), self.awake_row, self.follow_row, self.alert_row))
        return page

    def _build_display(self) -> Gtk.Widget:
        page = vbox(10)

        # screen
        self.bright = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 100, 1)
        self.bright.set_draw_value(False)
        self.bright.set_hexpand(True)
        self.bright.connect("value-changed", self._bright_changed)
        self.bright_l = label("", "val", xalign=1.0)
        self.kbd_seg = Seg([], self.pick_kbd, small=True)
        self.kbd_seg.set_size_request(168, -1)
        self.kbd_row = hbox(10, glyph(G["kbd"], "row-glyph"), label("keyboard light", hexpand=True), self.kbd_seg)
        self.kbd_row.add_css_class("row")
        self.night_row = SwitchRow(G["moon"], "night light", "", self.set_night)
        bright_row = hbox(10, glyph(G["sun"], "row-glyph", "c-amber"), self.bright, self.bright_l)
        bright_row.add_css_class("row")
        page.append(vbox(2, section("screen"), bright_row, self.kbd_row, self.night_row))

        # displays
        self.mon_count = label("", "section-meta")
        self.modes = Seg([(k, v.split()[0] if k != "internal" else "laptop") for k, v in MODE_LABEL.items()],
                         self.pick_mode, small=True)
        self.map = MonitorMap(self.select_monitor)
        self.place_seg = Seg([(p, p) for p in ("left", "right", "above", "below")], self.pick_place, small=True)
        self.place_name = label("", "subtitle")
        self.place_row = hbox(8, self.place_name, self.place_seg)
        self.place_seg.set_hexpand(True)
        self.noext = hbox(10, glyph(G["monitor"], "row-glyph"),
                          vbox(2, label("no external display"), label("", "subtitle", ellipsize=44)))
        self.noext.add_css_class("dashed")
        page.append(vbox(6, section("displays", self.mon_count), self.modes, self.map, self.place_row,
                         self.noext))

        # confirm
        self.confirm_l = label("", "subtitle", hexpand=True)
        self.confirm = hbox(8, vbox(1, label("keep this layout?"), self.confirm_l, hexpand=True),
                            button("revert", "ghost", self.revert), button("keep", "primary", self.keep))
        self.confirm.add_css_class("card")
        self.confirm.add_css_class("confirm")
        page.append(self.confirm)

        # selected monitor
        self.mon_title = label("", "mon-title")
        self.mon_desc = label("", "subtitle", ellipsize=34)
        self.mode_dd = Gtk.DropDown.new_from_strings([])
        self.mode_dd.set_hexpand(True)
        self.mode_dd.connect("notify::selected", self._mode_dd_changed)
        self.scale_seg = Seg([(be.fmt_scale(s), be.fmt_scale(s) + "×") for s in SCALES], self.pick_scale, small=True)
        self.scale_seg.set_hexpand(True)
        card = vbox(8,
                    hbox(8, glyph(G["monitor"], "row-glyph", "c-blue"),
                         vbox(1, self.mon_title, self.mon_desc, hexpand=True),
                         button("identify", "ghost", self.identify, "show each screen's name on it")),
                    hbox(8, label("mode", "field"), self.mode_dd,
                         button(G["rotate"], "ghost", self.rotate, "rotate 90°")),
                    hbox(8, label("scale", "field"), self.scale_seg))
        card.add_css_class("card")
        self.mon_card = card
        page.append(card)
        return page

    def _footer(self):
        child = self.footer.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.footer.remove(child)
            child = nxt
        hints = ([("tab", "display"), ("1 2 3", "profile"), ("k", "keep awake"), ("esc", "close")]
                 if self.tab == "power" else
                 [("tab", "power"), ("← →", "brightness"), ("m", "layout"), ("esc", "close")])
        for k, t in hints:
            self.footer.append(keyhint(k, t))

    # ---------- data ----------

    def load(self):
        """Read the slow things in a thread, then render on the main loop."""
        gen = self.gen
        if self.loading == gen:
            return
        self.loading = gen

        def work():
            data = {"bat": be.battery(), "prof": be.profile(), "hist": be.history(3.0), "mons": be.monitors(),
                    "cpu": self.cpu.sample(), "bright": be.brightness(),
                    "awake": be.awake(), "alert": be.alert_on(), "night": be.night()}
            GLib.idle_add(self._loaded, data, gen)

        threading.Thread(target=work, daemon=True).start()

    def load_cpu(self):
        """Just the cpu sample (≈20 ms), for the list to fill in soon after opening."""
        gen = self.gen

        def work():
            d = {"cpu": self.cpu.sample(), "bright": be.brightness()}
            GLib.idle_add(lambda: gen == self.gen and self.render_users(d) and False)

        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, d: dict, gen: int):
        if self.loading == gen:
            self.loading = None
        if gen != self.gen:
            return False  # left over from an earlier opening
        self.bat, self.prof, self.mons = d["bat"], d["prof"], d["mons"]
        self.spark.pts = d["hist"]
        self.updating = True
        try:
            self.render_power(d)
            self.render_display(d)
            self.render_header()
        finally:
            self.updating = False
        return False

    def render_header(self):
        b = self.bat
        if self.tab == "power":
            self.app_icon.set_text(G["charging"] if b.state == "charging" else G["bat"])
            self.title.set_text("power")
            self.subtitle.set_text(f"BAT0 · {self.prof} · {b.state.replace('-', ' ')}")
        else:
            self.app_icon.set_text(G["monitor"])
            self.title.set_text("display")
            on = [m for m in self.mons if not m.disabled]
            mode = be.layout_mode(self.mons)
            if len(self.mons) > 1:
                self.subtitle.set_text(" + ".join(m.name for m in self.mons) + f" · {MODE_LABEL[mode]}")
            elif on:
                m = on[0]
                self.subtitle.set_text(f"{m.name} · {m.width}×{m.height} @ {round(m.refresh)} Hz")

    def render_power(self, d: dict):
        b = self.bat
        self.pct.set_text(f"{b.pct}%")
        for c in ("c-green", "c-amber", "c-red", "c-blue"):
            self.state_l.remove_css_class(c)
        if b.state == "charging":
            col, state = "green", "charging"
            t = be.short_time(b.time_to_full)
            sub = f"full in {t}" if t else "plugged in"
        elif b.state in ("fully-charged", "pending-charge") or (b.plugged and b.state != "discharging"):
            col, state = "green", "plugged in"
            sub = "full" if b.state == "fully-charged" else "not charging · holding charge"
        else:
            col = "red" if b.pct <= 10 else "amber" if b.pct <= 20 else "blue"
            state = "on battery"
            t = be.short_time(b.time_to_empty)
            sub = f"{t} left" if t else "estimating…"
        self.state_l.add_css_class(f"c-{col}")
        self.state_l.set_text(state)
        self.time_l.set_text(sub)
        sign = "+" if b.state == "charging" else "−" if b.state == "discharging" else ""
        self.rate_l.set_text(f"{sign}{b.rate:.1f} W" if b.rate else "0 W")
        for c in ("c-green", "c-amber"):
            self.rate_l.remove_css_class(c)
        self.rate_l.add_css_class("c-green" if sign == "+" else "c-amber")
        self.source_l.set_text(b.source)
        self.bar.frac = b.pct / 100
        self.bar.color = col
        self.bar.limit = b.end / 100 if b.charge_type == "Custom" and b.end else None
        self.bar.queue_draw()
        self.spark.queue_draw()

        hv, hs = self.stats["health"]
        hv.set_text(f"{b.health}%")
        for c in ("c-green", "c-amber", "c-red"):
            hv.remove_css_class(c)
        hv.add_css_class("c-green" if b.health >= 80 else "c-amber" if b.health >= 60 else "c-red")
        hs.set_text(f"{b.full:.1f}/{b.design:.0f} Wh")
        self.stats["cycles"][0].set_text(str(b.cycles))
        self.stats["cycles"][1].set_text(f"{b.vendor} {b.tech}".lower().strip())
        tv, ts = self.stats["temp"]
        tv.set_text(f"{b.temp:.1f}°" if b.temp is not None else "–")
        ts.set_text("" if b.temp is None else "hot" if b.temp >= 45 else "warm" if b.temp >= 38 else "normal")
        self.stats["voltage"][0].set_text(f"{b.voltage:.1f} V")
        self.stats["voltage"][1].set_text(b.model.lower())

        self.render_users(d)

        self.profiles.set_active(self.prof)
        self.profile_hint.set_text(PROFILE_HINT.get(self.prof, ""))

        self.charges.set_active(b.charge_type)
        self.charges.set_sensitive(b.charge_writable)
        hint = CHARGE_HINT.get(b.charge_type, "")
        if not b.charge_writable:
            hint = "read-only · run ./install-charge-rule once to allow changes"
        self.charge_hint.set_text(hint)
        self.custom.set_visible(b.charge_type == "Custom")
        self.custom.set_sensitive(b.charge_writable)
        self.start_l.set_text(f"{b.start}%")
        self.end_l.set_text(f"{b.end}%")

        self.awake_row.set(d["awake"])
        self.follow_row.set(self.app.state.get("follow_charger", True))
        self.alert_row.set_visible(d["alert"] is not None)
        self.alert_row.set(bool(d["alert"]))

    def users_loading(self, on: bool):
        for w in (self.users_skel, self.users_note_skel):
            w.set_visible(on)
        for w in (self.users_rows_box, self.users_note):
            w.set_visible(not on)

    def render_users(self, d: dict):
        b = self.bat
        on_battery = b.state == "discharging" or not b.plugged
        self.users.set_visible(on_battery)
        if not on_battery:
            return
        apps = d.get("cpu")
        self.users_loading(apps is None)
        if apps is None:
            return
        total = sum(v for _, v in apps) or 1.0
        top = [(n, v) for n, v in apps if v >= 0.5][:len(self.users_rows)]
        for i, (row, name, meter, val) in enumerate(self.users_rows):
            row.set_visible(i < len(top))
            if i < len(top):
                n, v = top[i]
                name.set_text(n)
                meter.frac = v / total
                meter.queue_draw()
                val.set_text(f"{round(100 * v / total)}%")
        cores = total / 100
        self.users_note.set_text(f"drawing {b.rate:.1f} W · cpu busy {cores:.1f} cores · screen {d['bright']}%")

    def render_display(self, d: dict | None = None):
        if d is not None:
            if time.monotonic() - self.bright_touched > 1.5:
                self.bright.set_value(be.brightness())
            self.bright_l.set_text(f"{round(self.bright.get_value())}%")
            lvl, mx = be.kbd()
            self.kbd_row.set_visible(mx > 0)
            if mx > 0:
                names = ["off", "low", "high"] if mx == 2 else ["off"] + [str(i) for i in range(1, mx + 1)]
                if len(self.kbd_seg.btns) != len(names):
                    self.kbd_seg.set_items([(str(i), n) for i, n in enumerate(names)])
                self.kbd_seg.set_active(str(lvl))
            has = be.has_sunset()
            self.night_row.sw.set_sensitive(has)
            self.night_row.sub.set_text(f"{be.sunset_tool()} · 4000 K · same as the bar" if has
                                        else "no hyprsunset or wlsunset installed")
            self.night_row.set(d["night"])

        mons = self.mons
        internal = next((m for m in mons if m.internal), None)
        ext = next((m for m in mons if not m.internal), None)
        mode = be.layout_mode(mons)
        if internal and ext and mode == "extend" and not ext.disabled and not internal.disabled:
            self.place = be.placement(internal, ext)
        self.mon_count.set_text(f"{len([m for m in mons if not m.disabled])} on · {len(mons)} connected")
        self.modes.set_active(mode)
        self.modes.set_sensitive(bool(internal and ext))
        self.place_row.set_visible(bool(internal and ext and mode == "extend"))
        if ext:
            self.place_name.set_text(f"{ext.name} is")
        self.place_seg.set_active(self.place)
        self.noext.set_visible(not ext)
        if not ext:
            free = self._free_ports()
            self.noext.get_last_child().get_last_child().set_text(
                "free ports: " + " · ".join(free) if free else "plug in a monitor")

        names = [m.name for m in mons]
        if self.sel not in names:
            self.sel = (ext.name if ext and not ext.disabled else internal.name if internal else
                        names[0] if names else "")
        self.map.mons, self.map.mode, self.map.place, self.map.sel = mons, mode, self.place, self.sel
        self.map.queue_draw()
        self.render_monitor()

    def _free_ports(self) -> list[str]:
        used = {m.name for m in self.mons}
        out = []
        for p in sorted(Path("/sys/class/drm").glob("card*-*")):
            name = p.name.split("-", 1)[1]
            if name not in used and not name.startswith(be.INTERNAL_PREFIXES) and not name.startswith("Writeback"):
                out.append(name)
        return out

    def render_monitor(self):
        m = next((m for m in self.mons if m.name == self.sel), None)
        self.mon_card.set_visible(m is not None)
        if not m:
            return
        self.mon_title.set_text(m.name)
        kind = "built-in" if m.internal else "external"
        bits = [m.desc or m.name, kind, m.inches]
        if m.disabled:
            bits.append("off")
        self.mon_desc.set_text(" · ".join(b for b in bits if b))
        modes = list(dict.fromkeys(m.modes)) or [m.mode]
        shown = [md.replace("x", "×", 1).replace("@", " @ ") + " Hz" for md in modes]
        self._dd_modes = modes
        self.mode_dd.set_model(Gtk.StringList.new(shown))
        cur = min(range(len(modes)), key=lambda i: self._mode_dist(modes[i], m))
        self.mode_dd.set_selected(cur)
        self.scale_seg.set_active(be.fmt_scale(min(SCALES, key=lambda s: abs(s - m.scale))))
        live = not m.disabled and m.mirror_of in ("none", "")
        for w in (self.mode_dd, self.scale_seg):
            w.set_sensitive(live)

    @staticmethod
    def _mode_dist(md: str, m: be.Monitor) -> float:
        try:
            res, hz = md.split("@")
            w, h = (int(v) for v in res.split("x"))
            return abs(w - m.width) * 10 + abs(h - m.height) * 10 + abs(float(hz) - m.refresh)
        except ValueError:
            return 1e9

    # ---------- power actions ----------

    def pick_profile(self, p: str):
        if self.updating:
            return
        be.set_profile(p)
        self.prof = p
        self.profiles.set_active(p)
        self.profile_hint.set_text(PROFILE_HINT.get(p, ""))
        self.render_header()

    def pick_charge(self, kind: str):
        if self.updating:
            return
        err = be.set_charge(kind)
        if err:
            self.charge_hint.set_text(err)
        self.load()

    def step_thr(self, ds: int, de: int):
        b = self.bat
        end = max(55, min(100, b.end + de))
        start = max(50, min(95, b.start + ds))
        if end - start < 5:
            if de:
                start = end - 5
            else:
                end = start + 5
        err = be.set_charge(None, start, end)
        if err:
            self.charge_hint.set_text(err)
        self.load()

    def open_htop(self):
        """htop sorted by cpu, in the same terminal as SUPER RETURN."""
        term = os.environ.get("TERMINAL") or shutil.which("alacritty") or shutil.which("xdg-terminal-exec")
        if not term:
            return
        args = [term, "--class", "htop", "-e", "htop", "--sort-key", "PERCENT_CPU"]
        if os.path.basename(term) == "xdg-terminal-exec":
            args = [term, "htop", "--sort-key", "PERCENT_CPU"]
        subprocess.Popen(args, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.toggle()

    def set_awake(self, on: bool):
        be.set_awake(on)

    def set_follow(self, on: bool):
        self.app.state["follow_charger"] = on
        be.save_state(self.app.state)
        if on:
            self.app.follow(force=True)

    def set_alert(self, on: bool):
        be.set_alert(on)

    # ---------- display actions ----------

    def _bright_changed(self, scale):
        v = round(scale.get_value())
        self.bright_l.set_text(f"{v}%")
        if self.updating:
            return
        self.bright_touched = time.monotonic()
        if not self.bright_timer:
            self.bright_timer = GLib.timeout_add(40, self._push_bright)

    def _push_bright(self):
        self.bright_timer = 0
        be.set_brightness(round(self.bright.get_value()))
        return False

    def nudge_bright(self, d: int):
        self.bright.set_value(max(1, min(100, self.bright.get_value() + d)))

    def pick_kbd(self, key: str):
        if not self.updating:
            be.set_kbd(int(key))
            self.kbd_seg.set_active(key)

    def set_night(self, on: bool):
        be.set_night(on)

    def select_monitor(self, name: str):
        self.sel = name
        self.map.sel = name
        self.map.queue_draw()
        self.updating = True
        try:
            self.render_monitor()
        finally:
            self.updating = False

    def _pair(self):
        internal = next((m for m in self.mons if m.internal), None)
        ext = next((m for m in self.mons if not m.internal), None)
        return internal, ext

    def _apply(self, lines: list[str]):
        if not lines:
            return
        if self.revert_lines is None:
            self.revert_lines = be.current_lines(self.mons)
        be.apply_lines(lines)
        self.confirm_left = CONFIRM_S
        self._confirm_tick(start=True)
        GLib.timeout_add(350, lambda: self.load() or False)

    def _confirm_tick(self, start=False):
        if start:
            if self.confirm_timer:
                GLib.source_remove(self.confirm_timer)
            self.confirm_timer = GLib.timeout_add(1000, self._confirm_tick)
            self.confirm.set_visible(True)
        elif self.confirm_left <= 1:
            self.confirm_timer = 0
            self.revert()
            return False
        else:
            self.confirm_left -= 1
        self.confirm_l.set_text(f"reverting in {self.confirm_left} s")
        return True

    def _end_confirm(self):
        if self.confirm_timer:
            GLib.source_remove(self.confirm_timer)
            self.confirm_timer = 0
        self.revert_lines = None
        self.confirm.set_visible(False)

    def revert(self):
        lines = self.revert_lines
        self._end_confirm()
        if lines:
            be.apply_lines(lines)
            GLib.timeout_add(350, lambda: self.load() or False)

    def keep(self):
        self._end_confirm()
        mons = be.monitors()
        if be.layout_mode(mons) == "extend" and all(not m.disabled for m in mons):
            err = be.persist([m.line() for m in mons])
            self.app.toast.show(G["monitor"], "blue", "layout saved" if not err else "could not save",
                                "written to ~/.config/hypr/monitors.conf" if not err else err)
        else:
            self.app.toast.show(G["monitor"], "blue", "layout kept for this session",
                                "mirror / single-screen layouts are not written to the config")

    def _extend_lines(self, over: dict[str, dict] | None = None, where: str | None = None) -> list[str]:
        """Lines for an extend layout after applying per-monitor overrides, re-placing the external one."""
        over = over or {}
        internal, ext = self._pair()
        mons = [dataclasses.replace(m, **over.get(m.name, {})) for m in self.mons if not m.disabled]
        if internal and ext and len(mons) == 2:
            i2 = next(m for m in mons if m.internal)
            e2 = next(m for m in mons if not m.internal)
            pos = be.place_positions(i2, e2, where or self.place)
            return [m.line(x=pos[m.name][0], y=pos[m.name][1]) for m in (i2, e2)]
        return [m.line() for m in mons]

    def pick_mode(self, mode: str):
        if self.updating:
            return
        internal, ext = self._pair()
        if not (internal and ext) or mode == be.layout_mode(self.mons):
            return
        auto = {"right": "auto-right", "left": "auto-left", "above": "auto-up", "below": "auto-down"}[self.place]
        i_line = internal.line(x=0, y=0) if not internal.disabled else f"{internal.name},preferred,0x0,{be.fmt_scale(internal.scale)}"
        e_on = not ext.disabled and ext.mirror_of in ("none", "")
        e_line = ext.line() if e_on else f"{ext.name},preferred,{auto},1"
        if mode == "extend":
            lines = [i_line, e_line]
        elif mode == "mirror":
            lines = [i_line, f"{ext.name},preferred,auto,1,mirror,{internal.name}"]
        elif mode == "internal":
            lines = [i_line, f"{ext.name},disable"]
        else:
            lines = [e_line, f"{internal.name},disable"]
        self._apply(lines)

    def pick_place(self, where: str):
        if self.updating or where == self.place:
            return
        self.place = where
        self.place_seg.set_active(where)
        self._apply(self._extend_lines(where=where))

    def _mode_dd_changed(self, dd, _p):
        if self.updating:
            return
        i = dd.get_selected()
        modes = getattr(self, "_dd_modes", [])
        m = next((m for m in self.mons if m.name == self.sel), None)
        if not m or i >= len(modes):
            return
        try:
            res, hz = modes[i].split("@")
            w, h = (int(v) for v in res.split("x"))
            hz = float(hz)
        except ValueError:
            return
        if (w, h) == (m.width, m.height) and abs(hz - m.refresh) < 0.05:
            return
        self._apply(self._extend_lines({m.name: {"width": w, "height": h, "refresh": hz}}))

    def pick_scale(self, s: str):
        if self.updating:
            return
        m = next((m for m in self.mons if m.name == self.sel), None)
        if not m or abs(float(s) - m.scale) < 0.01:
            return
        self.scale_seg.set_active(s)
        self._apply(self._extend_lines({m.name: {"scale": float(s)}}))

    def rotate(self):
        m = next((m for m in self.mons if m.name == self.sel), None)
        if m and not m.disabled:
            self._apply(self._extend_lines({m.name: {"transform": (m.transform + 1) % 4}}))

    def identify(self):
        mons = Gdk.Display.get_default().get_monitors()
        for i in range(mons.get_n_items()):
            gm = mons.get_item(i)
            Identify(self.app, gm, gm.get_connector() or f"monitor {i}")

    def cycle_mode(self):
        internal, ext = self._pair()
        if internal and ext:
            order = list(MODE_LABEL)
            cur = be.layout_mode(self.mons)
            self.pick_mode(order[(order.index(cur) + 1) % len(order)])

    # ---------- keys / tabs / visibility ----------

    def set_tab(self, tab: str):
        self.tab = tab
        self.tabs.set_active(tab)
        self.stack.set_visible_child_name(tab)
        self._footer()
        self.render_header()

    def _on_key(self, _ctrl, keyval, _code, state):
        name = Gdk.keyval_name(keyval) or ""
        focus = self.get_focus()
        if name in ("Escape", "q"):
            self.toggle()
        elif name in ("Tab", "ISO_Left_Tab"):
            self.set_tab("display" if self.tab == "power" else "power")
        elif self.tab == "power" and name in ("1", "2", "3"):
            self.pick_profile(be.PROFILES[int(name) - 1])
        elif self.tab == "power" and name == "k":
            self.awake_row.sw.set_active(not self.awake_row.sw.get_active())
        elif self.tab == "display" and name in ("Left", "Right", "h", "l") and not isinstance(focus, Gtk.DropDown):
            self.nudge_bright(-5 if name in ("Left", "h") else 5)
        elif self.tab == "display" and name == "m":
            self.cycle_mode()
        elif name == "Return" and self.confirm.get_visible():
            self.keep()
        else:
            return False
        return True

    def toggle(self, tab: str | None = None):
        if self.get_visible():
            self.set_visible(False)
            self.backdrop.set_visible(False)
            self.gen += 1  # drop readings still in flight
            if self.tick_timer:
                GLib.source_remove(self.tick_timer)
                self.tick_timer = 0
            OPEN_FLAG.unlink(missing_ok=True)
            return
        hypr_ui.solo("hyprpower")
        if tab in ("power", "display"):
            self.set_tab(tab)
        self.confirm.set_visible(self.revert_lines is not None)
        self.gen += 1
        gen = self.gen
        self.cpu.reset()
        self.users_loading(True)  # never show the last opening's numbers
        self.load()
        # second cpu sample → the list fills in (skipped if the popover was closed meanwhile)
        GLib.timeout_add(1000, lambda: (gen == self.gen and self.get_visible() and self.load_cpu()) and False)
        self.backdrop.present()
        self.present()
        self.tick_timer = GLib.timeout_add_seconds(3, lambda: self.load() or True)
        OPEN_FLAG.touch()


class App(Gtk.Application):
    def __init__(self, sock: Path):
        super().__init__(application_id="dev.edu.HyprPower")
        self.sock_path = sock
        self.state = be.load_state()
        self.source = be.power_source()

    def follow(self, force=False) -> bool:
        """Charger plugged / unplugged: switch the power profile (if enabled)."""
        src = be.power_source()
        changed = src != self.source
        self.source = src
        if (changed or force) and self.state.get("follow_charger", True):
            want = "balanced" if src != "battery" else "power-saver"
            if be.profile() != want:
                be.set_profile(want)
                if changed:
                    b = be.battery()
                    t = be.short_time(b.time_to_empty)
                    sub = (f"on battery · {b.pct}%" + (f" · about {t} left" if t else "")
                           if src == "battery" else f"plugged in · {src} · {b.pct}%")
                    self.toast.show(G["leaf"] if want == "power-saver" else G["plug"],
                                    "green" if want == "power-saver" else "blue", want, sub)
            if self.panel.get_visible():
                self.panel.load()
        return True

    def _serve(self):
        self.sock_path.unlink(missing_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self.sock_path))
        srv.listen(8)
        self.srv = srv

        def on_conn(_fd, _cond):
            conn, _ = srv.accept()
            conn.settimeout(1)
            try:
                cmd = conn.recv(256).decode().split()
            except OSError:
                cmd = []
            conn.close()
            if cmd:
                self.command(cmd)
            return True

        GLib.io_add_watch(srv.fileno(), GLib.IO_IN, on_conn)

    def command(self, cmd: list[str]):
        c, arg = cmd[0], cmd[1] if len(cmd) > 1 else None
        p = self.panel
        if c == "toggle":
            if p.get_visible() and arg and arg != p.tab:
                p.set_tab(arg)
            else:
                p.toggle(arg)
        elif c == "open" and not p.get_visible():
            p.toggle(arg)
        elif c == "close" and p.get_visible():
            p.toggle()
        elif c == "quit":
            self.quit()

    def do_startup(self):
        Gtk.Application.do_startup(self)
        hypr_ui.load_css(HERE / "style.css")
        self.hold()
        self.panel = Panel(self)
        self.toast = Toast(self)
        self._serve()
        GLib.timeout_add_seconds(3, self.follow)

    def do_activate(self):
        pass

    def do_shutdown(self):
        self.sock_path.unlink(missing_ok=True)
        OPEN_FLAG.unlink(missing_ok=True)
        Gtk.Application.do_shutdown(self)


def main(sock: Path):
    import signal

    OPEN_FLAG.unlink(missing_ok=True)
    app = App(sock)
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: app.quit() or False)
    app.run([])
