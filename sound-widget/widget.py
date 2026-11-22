"""Resident GTK4 layer-shell sound panel + OSD. Driven over a unix socket by the `sound-widget` script."""

from __future__ import annotations

import json
import math
import os
import queue
import socket
import subprocess
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
import cairo  # noqa: E402
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402
from gi.repository import Gtk4LayerShell as Layer  # noqa: E402

import sys  # noqa: E402

sys.path.insert(0, os.path.expanduser("~/.config/hypr-ui/gtk"))
import hypr_ui  # noqa: E402  shared look: tokens, base stylesheet, popover/toast placement
import fx  # noqa: E402
import pw  # noqa: E402
import spot  # noqa: E402
from meter import Meter  # noqa: E402

HERE = Path(__file__).resolve().parent
RUN = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
OPEN_FLAG = RUN / "sound-widget.open"
STATE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sound-widget" / "state.json"
WAYBAR_SIGNAL = 10  # custom/sound + custom/mic "signal" in the waybar config
OSD_MS = 1500

# nerd font (material design) glyphs
G = {
    "spk": "\U000f057e", "spkLow": "\U000f057f", "spkMute": "\U000f075f",
    "mic": "\U000f036c", "micOff": "\U000f036d",
    "hp": "\U000f02cb", "mon": "\U000f0379", "bt": "\U000f00af", "lap": "\U000f0322",
    "wave": "\U000f147d", "echo": "\U000f04c3", "tune": "\U000f062e", "close": "\U000f0156",
    "spotify": "\U000f04c7", "play": "\U000f040a", "pause": "\U000f03e4", "next": "\U000f04ad",
    "prev": "\U000f04ae", "power": "\U000f0425",
}
MONO_COLORS = 6  # .mono.c0 … .c5 in style.css

KEYS = [  # (key shown, label, action, input?) — F1–F7 belong to workspaces, so the panel uses letters
    ("m", "mute", "mute", False),
    ("−", "vol −", "down", False),
    ("+", "vol +", "up", False),
    ("i", "mic", "mic-mute", True),
    ("o", "output", "next-out", False),
    ("p", "input", "next-in", True),
    ("tab", "tab", "tab", False),
]
PROCS = [
    ("noise", "Noise suppression", "WebRTC · fans & keyboard", "wave"),
    ("echo", "Echo cancellation", "WebRTC AEC on speakers", "echo"),
    ("monitor", "Hear yourself", "loop mic to output", "hp"),
]


def vol_glyph(v: int, muted: bool) -> str:
    return G["spkMute"] if muted or v == 0 else G["spkLow"] if v < 40 else G["spk"]


def mic_glyph(muted: bool) -> str:
    return G["micOff"] if muted else G["mic"]


def poke_waybar():
    subprocess.run(["pkill", f"-RTMIN+{WAYBAR_SIGNAL}", "-x", "waybar"], capture_output=True)


def label(text="", *classes, xalign=0.0, hexpand=False, ellipsize=False, markup=False):
    w = Gtk.Label(xalign=xalign, hexpand=hexpand)
    w.set_markup(text) if markup else w.set_text(text)
    for c in classes:
        w.add_css_class(c)
    if ellipsize:
        w.set_ellipsize(Pango.EllipsizeMode.END)
        w.set_max_width_chars(22)  # else the natural width is the whole text and the panel grows
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


def clear(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def mono(name: str) -> Gtk.Label:
    w = label((name[:2] or "?").title(), "mono", xalign=0.5)
    w.add_css_class(f"c{sum(map(ord, name)) % MONO_COLORS}")
    return w


def esc(s: str) -> str:
    return GLib.markup_escape_text(s)


class Worker:
    """One background thread for every PipeWire call, so key repeats apply in order."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()

    def run(self, fn, done=None):
        self.q.put((fn, done))

    def busy(self) -> bool:
        return not self.q.empty()

    def _loop(self):
        while True:
            fn, done = self.q.get()
            try:
                res, err = fn(), None
            except Exception as e:  # shown or ignored by the caller, never fatal
                res, err = None, e
            if done:
                GLib.idle_add(lambda d=done, r=res, e=err: d(r, e) and False)


class Segments(Gtk.DrawingArea):
    """32-segment peak meter; lit color from CSS, amber/red caps at the top like the design."""

    N = 32

    def __init__(self, height: int):
        super().__init__(content_height=height, hexpand=True)
        self.level = 0.0
        self.add_css_class("seg")
        self.set_draw_func(self._draw)

    def _draw(self, _a, cr, w, h):
        c = self.get_color()
        gap = 2.0
        sw = (w - gap * (self.N - 1)) / self.N
        for i in range(self.N):
            on = i / self.N < self.level
            if not on:
                cr.set_source_rgba(*hypr_ui.rgba("fg", 0.07))
            elif i >= 28:
                cr.set_source_rgb(*hypr_ui.rgb("red"))
            elif i >= 23:
                cr.set_source_rgb(*hypr_ui.rgb("amber"))
            else:
                cr.set_source_rgba(c.red, c.green, c.blue, c.alpha)
            cr.rectangle(i * (sw + gap), 0, sw, h)
            cr.fill()


class Bar(Gtk.DrawingArea):
    """Thin rounded level/progress bar; fill color from CSS."""

    def __init__(self, height=4):
        super().__init__(content_height=height, hexpand=True, valign=Gtk.Align.CENTER)
        self.frac = 0.0
        self.add_css_class("bar")
        self.set_draw_func(self._draw)

    def _pill(self, cr, w, h):
        r = h / 2
        cr.new_sub_path()
        cr.arc(w - r, r, r, -math.pi / 2, math.pi / 2)
        cr.arc(r, r, r, math.pi / 2, 3 * math.pi / 2)
        cr.close_path()

    def _draw(self, _a, cr, w, h):
        cr.set_source_rgba(*hypr_ui.rgba("fg", 0.08))
        self._pill(cr, w, h)
        cr.fill()
        fw = max(0.0, min(1.0, self.frac)) * w
        if fw > 0:
            c = self.get_color()
            cr.set_source_rgba(c.red, c.green, c.blue, c.alpha)
            self._pill(cr, max(fw, h), h)
            cr.fill()


class Radio(Gtk.DrawingArea):
    def __init__(self, on: bool):
        super().__init__(content_width=16, content_height=16, valign=Gtk.Align.CENTER)
        self.on = on
        self.add_css_class("radio")
        self.set_draw_func(self._draw)

    def _draw(self, _a, cr, w, h):
        c = self.get_color()
        if self.on:
            cr.set_source_rgba(c.red, c.green, c.blue, 1)
            cr.arc(w / 2, h / 2, w / 2, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgb(*hypr_ui.rgb("ink"))
            cr.set_line_width(2)
            cr.move_to(w * 0.28, h * 0.52)
            cr.line_to(w * 0.44, h * 0.68)
            cr.line_to(w * 0.73, h * 0.36)
            cr.stroke()
        else:
            cr.set_source_rgba(*hypr_ui.rgba("fg", 0.22))
            cr.set_line_width(1.5)
            cr.arc(w / 2, h / 2, w / 2 - 0.75, 0, 2 * math.pi)
            cr.stroke()


class Backdrop(Gtk.Window):
    """Invisible full-screen layer under the panel: a click on it means "clicked outside"."""

    def __init__(self, app, on_click):
        super().__init__(application=app, title="sound-widget-backdrop")
        self.add_css_class("backdrop")
        Layer.init_for_window(self)
        Layer.set_namespace(self, "sound-widget-backdrop")
        Layer.set_layer(self, Layer.Layer.TOP)
        for edge in (Layer.Edge.TOP, Layer.Edge.BOTTOM, Layer.Edge.LEFT, Layer.Edge.RIGHT):
            Layer.set_anchor(self, edge, True)
        Layer.set_keyboard_mode(self, Layer.KeyboardMode.NONE)
        self.set_child(Gtk.Box(hexpand=True, vexpand=True))
        click = Gtk.GestureClick(button=0)
        click.connect("pressed", lambda *_: on_click())
        self.add_controller(click)


class Osd(Gtk.Window):
    """Bottom-centre volume / device toast, click-through, hides itself after OSD_MS."""

    def __init__(self, app):
        super().__init__(application=app, title="sound-widget-osd")
        Layer.init_for_window(self)
        Layer.set_namespace(self, "sound-widget-osd")
        Layer.set_layer(self, Layer.Layer.OVERLAY)
        hypr_ui.toast(self, Layer, bottom=88)  # card 88 px above the screen edge, click-through
        Layer.set_keyboard_mode(self, Layer.KeyboardMode.NONE)
        self.icon = label("", "osd-icon", xalign=0.5)
        self.icon.set_valign(Gtk.Align.CENTER)
        self.title = label("", "osd-title", markup=True, ellipsize=True)
        self.title.set_max_width_chars(34)
        self.bar = Bar(6)
        self.val = label("", "osd-val", xalign=1.0)
        box = hbox(14, self.icon, vbox(8, self.title, self.bar, hexpand=True, valign=Gtk.Align.CENTER), self.val)
        box.add_css_class("toast")
        box.add_css_class("osd")
        box.set_size_request(360 + 2 * hypr_ui.MARGIN, -1)
        self.set_child(box)
        self.timer = 0

    def show(self, snap: pw.Snapshot, out: bool, device_change: bool):
        node = snap.sink() if out else snap.source()
        if not node:
            return
        box = self.get_child()
        for c in ("out", "in", "muted"):
            box.remove_css_class(c)
        box.add_css_class("out" if out else "in")
        if node.muted:
            box.add_css_class("muted")
        eyebrow = ("Output" if out else "Input") + (" →" if device_change else "")
        self.title.set_markup(f"<span size='small' letter_spacing='800' foreground='{hypr_ui.hex_color('muted')}'>{eyebrow.upper()}</span>  {esc(node.label)}")
        self.icon.set_text(G[node.icon] if device_change else vol_glyph(node.vol, node.muted) if out else mic_glyph(node.muted))
        self.bar.frac = 0 if node.muted else node.vol / 100
        self.bar.queue_draw()
        self.val.set_text("MUTED" if node.muted else f"{node.vol}%")
        self.present()
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(OSD_MS, self._hide)

    def _hide(self):
        self.timer = 0
        self.set_visible(False)
        return False


class Panel(Gtk.ApplicationWindow):
    def __init__(self, app: "App"):
        super().__init__(application=app, title="sound-widget", resizable=False)
        self.app = app
        Layer.init_for_window(self)
        Layer.set_namespace(self, "sound-widget")
        Layer.set_layer(self, Layer.Layer.OVERLAY)
        hypr_ui.popover(self, Layer)
        Layer.set_keyboard_mode(self, Layer.KeyboardMode.ON_DEMAND)

        self.tab = "out"
        self.sel = 0
        self.snap = pw.Snapshot()
        self.meter = Meter()
        self.shown = [0.0, 0.0]  # meter values with decay
        self.timers: list[int] = []
        self.sig_dev = self.sig_apps = None
        self.app_scales: dict[int, tuple[Gtk.Scale, Gtk.Label]] = {}
        self.touched: dict[int, float] = {}  # node id -> monotonic time the user last moved its slider
        self.pending_vol: dict[int, int] = {}
        self.syncing = False
        self.dev_rows: list[Gtk.Widget] = []

        self.backdrop = Backdrop(app, lambda: self.get_visible() and self.toggle())
        self._build()
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # ---------- layout ----------

    def _build(self):
        root = vbox(12)
        root.add_css_class("popover")
        root.set_size_request(400 + 2 * hypr_ui.MARGIN, -1)  # card 400 + shadow margin
        self.set_default_size(400 + 2 * hypr_ui.MARGIN, -1)
        self.set_child(root)

        self.subtitle = label("pipewire", "subtitle")
        mixer = Gtk.Button(label=G["tune"], valign=Gtk.Align.CENTER, tooltip_text="advanced mixer (pavucontrol)")
        mixer.add_css_class("icon")
        mixer.connect("clicked", lambda *_: (self.toggle(), subprocess.Popen(["pavucontrol"], start_new_session=True)))
        close = Gtk.Button(label=G["close"], valign=Gtk.Align.CENTER, tooltip_text="hide (esc)")
        close.add_css_class("icon")
        close.connect("clicked", lambda *_: self.toggle())
        root.append(hbox(10, label(G["spk"], "app-icon", xalign=0.5),
                         vbox(0, label("sound", "title"), self.subtitle, hexpand=True, valign=Gtk.Align.CENTER),
                         mixer, close))

        # direction tabs
        tabs = hbox(8, homogeneous=True)
        self.dir = {}
        for key, name in (("out", "Output"), ("in", "Input")):
            icon = label("", "dir-icon", xalign=0.5)
            icon.set_valign(Gtk.Align.CENTER)
            val = label("", "dir-val", xalign=1.0)
            dev = label("", "dir-dev", ellipsize=True)
            dev.set_max_width_chars(14)
            btn = Gtk.Button(child=hbox(10, icon, vbox(1, hbox(6, label(name, "dir-name", hexpand=True), val), dev,
                                                       hexpand=True, valign=Gtk.Align.CENTER)))
            btn.add_css_class("dir")
            btn.add_css_class(key)
            btn.connect("clicked", lambda _b, k=key: self.show_tab(k))
            tabs.append(btn)
            self.dir[key] = (btn, icon, val, dev)
        root.append(tabs)

        # hero card: mute button, name, big value, slider, meter
        self.mute_btn = Gtk.Button(label="", valign=Gtk.Align.CENTER)
        self.mute_btn.add_css_class("mute")
        self.mute_btn.connect("clicked", lambda *_: self.act("mute" if self.tab == "out" else "mic-mute"))
        self.hero_name = label("", "hero-name", hexpand=True, ellipsize=True)
        self.hero_val = label("", "hero-val", xalign=1.0)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.add_css_class("hero")
        self.scale.connect("value-changed", self._on_hero_scale)
        self.meter_box = vbox(4)
        card = vbox(12, hbox(14, self.mute_btn, vbox(8, hbox(12, self.hero_name, self.hero_val), self.scale,
                                                     hexpand=True, valign=Gtk.Align.CENTER)), self.meter_box)
        card.add_css_class("card")
        self.card = card
        root.append(card)

        # device list
        self.dev_head = label("", "section", hexpand=True)
        self.dev_hint = label("", "section-meta", xalign=1.0)
        self.devs = vbox(2)
        root.append(vbox(4, hbox(0, self.dev_head, self.dev_hint), self.devs))

        # output tab: hyprspot strip + applications · input tab: processing + recording apps
        self.apps_count = label("", "section-meta", xalign=1.0)
        self.apps = vbox(2)
        self.out_extra = vbox(4, self._build_spot(),
                              hbox(0, label("APPLICATIONS", "section", hexpand=True), self.apps_count, margin_top=8),
                              self.apps)
        self.procs: dict[str, Gtk.Switch] = {}
        procs = vbox(2)
        for key, name, sub, icon in PROCS:
            sw = Gtk.Switch(valign=Gtk.Align.CENTER, active=self.app.fx.on[key])
            sw.connect("notify::active", lambda s, _p, k=key: self._on_proc(k, s.get_active()))
            ic = label(G[icon], "proc-icon", xalign=0.5)
            ic.set_valign(Gtk.Align.CENTER)
            row = hbox(12, ic, vbox(1, label(name, "name"), label(sub, "sub", ellipsize=True),
                                    hexpand=True, valign=Gtk.Align.CENTER), sw)
            row.add_css_class("proc")
            row.ic = ic
            procs.append(row)
            self.procs[key] = sw
            sw.row = row
        self.rec_count = label("", "section-meta", xalign=1.0)
        self.recs = vbox(2)
        self.rec_bars: list[Bar] = []
        self.in_extra = vbox(4, hbox(0, label("PROCESSING", "section", hexpand=True), label("webrtc", "section-meta")),
                             procs, hbox(0, label("RECORDING APPS", "section", hexpand=True), self.rec_count,
                                         margin_top=8), self.recs)
        self.extra = Gtk.Stack(vhomogeneous=False, transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=120)
        self.extra.add_named(self.out_extra, "out")
        self.extra.add_named(self.in_extra, "in")
        root.append(self.extra)

        # key strip
        grid = hbox(6, homogeneous=True)
        self.caps: dict[str, Gtk.Button] = {}
        for k, l, a, inp in KEYS:
            b = Gtk.Button(child=vbox(1, label(k, "cap-key", xalign=0.5), label(l, "cap-label", xalign=0.5)))
            b.add_css_class("cap")
            if inp:
                b.add_css_class("in")
            b.connect("clicked", lambda _b, a=a: self.act(a))
            grid.append(b)
            self.caps[a] = b
        foot = vbox(8, grid, label("⇧ −/+ mic gain · j/k ⏎ pick · esc hides · media keys work too", "foot-hint", xalign=0.5))
        foot.add_css_class("footer")
        root.append(foot)

    def _build_spot(self) -> Gtk.Widget:
        """Faixa mínima do hyprspot: abrir/fechar, anterior, play/pause, próxima."""
        self.spot_title = label("", "name", hexpand=True, ellipsize=True)
        self.spot_title.set_max_width_chars(24)
        self.spot_sub = label("", "sub", ellipsize=True)
        self.spot_sub.set_max_width_chars(24)
        self.spot_meta = label("", "section-meta", xalign=1.0)

        def btn(glyph: str, tip: str, action: str) -> Gtk.Button:
            b = Gtk.Button(label=glyph, valign=Gtk.Align.CENTER, tooltip_text=tip)
            b.add_css_class("icon")
            b.add_css_class("spot-btn")
            b.connect("clicked", lambda *_: self._spot_act(action))
            return b

        self.spot_power = btn(G["power"], "abrir hyprspot", "open-close")
        self.spot_prev = btn(G["prev"], "anterior", "prev")
        self.spot_play = btn(G["play"], "tocar", "play-pause")
        self.spot_next = btn(G["next"], "próxima", "next")
        ic = label(G["spotify"], "proc-icon", xalign=0.5)
        ic.set_valign(Gtk.Align.CENTER)
        row = hbox(8, ic, vbox(1, self.spot_title, self.spot_sub, hexpand=True, valign=Gtk.Align.CENTER),
                   self.spot_prev, self.spot_play, self.spot_next, self.spot_power)
        row.add_css_class("proc")
        row.add_css_class("spot")
        self.spot_row = row
        self._set_spot({"running": False, "status": "off", "title": "", "artist": ""})
        return vbox(4, hbox(0, label("HYPRSPOT", "section", hexpand=True), self.spot_meta), row)

    def _set_spot(self, st, err=None):
        if err or not st:
            return
        playing = st["status"] in ("playing", "buffering")
        has_track = st["status"] in ("playing", "paused", "buffering")
        fallback = "Spotify" if has_track else ("nada tocando" if st["status"] != "off" else "Soloist parado")
        self.spot_title.set_text(st["title"] or fallback)
        self.spot_sub.set_text(st["artist"] or ("hyprspot aberto" if st["running"] else "hyprspot fechado"))
        self.spot_meta.set_text("aberto" if st["running"] else "fechado")
        self.spot_play.set_label(G["pause"] if playing else G["play"])
        self.spot_play.set_tooltip_text("pausar" if playing else "tocar")
        self.spot_power.set_tooltip_text("fechar hyprspot" if st["running"] else "abrir hyprspot")
        (self.spot_power.add_css_class if st["running"] else self.spot_power.remove_css_class)("on")
        (self.spot_row.add_css_class if playing else self.spot_row.remove_css_class)("playing")
        for b in (self.spot_prev, self.spot_next):
            b.set_sensitive(has_track)
        self.spot_play.set_sensitive(st["status"] != "off")

    def _spot_act(self, action: str):
        spot.run(action)
        GLib.timeout_add(450, lambda: (self.app.worker.run(spot.state, self._set_spot), False)[1])

    # ---------- rendering ----------

    def render(self):
        s, out = self.snap, self.tab == "out"
        sink, src = s.sink(), s.source()
        self.subtitle.set_text(f"pipewire · {s.rate / 1000:g} kHz" + (" · voice filter on" if s.voice else ""))
        for key, node in (("out", sink), ("in", src)):
            btn, icon, val, dev = self.dir[key]
            (btn.add_css_class if self.tab == key else btn.remove_css_class)("active")
            muted = bool(node and node.muted)
            (val.add_css_class if muted else val.remove_css_class)("muted")
            icon.set_text(vol_glyph(node.vol, muted) if key == "out" and node else mic_glyph(muted))
            val.set_text("—" if not node else "muted" if muted else f"{node.vol}%")
            dev.set_text(node.label if node else "no device")

        node = sink if out else src
        for c in ("out", "in", "muted"):
            self.card.remove_css_class(c)
        self.card.add_css_class(self.tab)
        if node and node.muted:
            self.card.add_css_class("muted")
        self.mute_btn.set_label(vol_glyph(node.vol, node.muted) if out and node else mic_glyph(bool(node and node.muted)))
        self.mute_btn.set_tooltip_text(("Unmute " if node and node.muted else "Mute ") + ("output" if out else "microphone"))
        self.hero_name.set_text(node.label if node else "no device")
        self.hero_val.set_text("—" if not node else "MUTED" if node.muted else f"{node.vol}%")
        if node and time.monotonic() - self.touched.get(node.id, 0) > 1.5:
            self.syncing = True
            self.scale.set_value(min(100, node.vol))
            self.syncing = False

        self._render_devices()
        if out:
            self._render_apps()
        else:
            self._render_recs()
        for key, sw in self.procs.items():
            (sw.row.add_css_class if sw.get_active() else sw.row.remove_css_class)("on")

    def _device_list(self) -> list[pw.Node]:
        """Devices offered in the list: unplugged HDMI / DP outputs are hidden unless one is the current default."""
        s = self.snap
        if self.tab != "out":
            return s.sources
        cur = s.sink()
        return [n for n in s.sinks if n.available or n.icon != "mon" or (cur and n.id == cur.id)]

    def _render_devices(self):
        s, out = self.snap, self.tab == "out"
        lst = self._device_list()
        cur = s.sink() if out else s.source()
        self.dev_head.set_text("OUTPUT DEVICES" if out else "INPUT DEVICES")
        self.dev_hint.set_text("o → next" if out else "p → next")
        sig = (self.tab, cur and cur.id, tuple((n.id, n.label, n.sub, n.vol, n.muted, n.available) for n in lst))
        if sig == self.sig_dev:
            return
        self.sig_dev = sig
        clear(self.devs)
        self.dev_rows = []
        self.sel = min(self.sel, max(0, len(lst) - 1))
        for i, n in enumerate(lst):
            on = cur is not None and n.id == cur.id
            ic = label(G.get(n.icon, G["spk"]), "dev-icon", xalign=0.5)
            ic.set_valign(Gtk.Align.CENTER)
            sub = n.sub + ("" if n.available else " · unplugged")
            row = hbox(12, ic, vbox(2, label(n.label, "name", ellipsize=True), label(sub, "sub", ellipsize=True),
                                    hexpand=True, valign=Gtk.Align.CENTER),
                       label("muted" if n.muted else f"{n.vol}%", "dev-vol"), Radio(on))
            row.add_css_class("row")
            row.add_css_class(self.tab)
            if on:
                row.add_css_class("current")
            if not n.available:
                row.add_css_class("na")
            if i == self.sel:
                row.add_css_class("selected")
            click = Gtk.GestureClick()
            click.connect("released", lambda *_a, i=i: (self._select(i), self.pick(i)))
            row.add_controller(click)
            self.devs.append(row)
            self.dev_rows.append(row)
        if not lst:
            self.devs.append(label("no devices", "section-meta"))

    def _render_apps(self):
        s = self.snap
        self.apps_count.set_text(f"{len(s.play)} stream{'s' * (len(s.play) != 1)}")
        sig = tuple((n.id, n.label, n.sub) for n in s.play)
        if sig != self.sig_apps:
            self.sig_apps = sig
            clear(self.apps)
            self.app_scales = {}
            for n in s.play:
                sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
                sc.set_draw_value(False)
                sc.set_hexpand(True)
                sc.add_css_class("mini")
                sc.set_value(min(100, n.vol))
                sc.connect("value-changed", lambda w, nid=n.id: self._on_app_scale(nid, w))
                vl = label("", "dev-vol", xalign=1.0)
                name = label(f"<b>{esc(n.label)}</b>  <span foreground='{hypr_ui.hex_color('muted')}'>{esc(n.sub)}</span>", markup=True,
                             hexpand=True, ellipsize=True)
                name.set_max_width_chars(30)
                row = hbox(12, mono(n.label), vbox(4, hbox(8, name, vl), sc, hexpand=True, valign=Gtk.Align.CENTER))
                row.add_css_class("app")
                self.apps.append(row)
                self.app_scales[n.id] = (sc, vl)
            if not s.play:
                self.apps.append(label("nothing playing", "empty"))
        sink = s.sink()
        for n in s.play:
            sc, vl = self.app_scales.get(n.id, (None, None))
            if not sc:
                continue
            vl.set_text("muted" if n.muted else f"{n.vol}%")
            (sc.add_css_class if (n.muted or (sink and sink.muted)) else sc.remove_css_class)("muted")
            if time.monotonic() - self.touched.get(n.id, 0) > 1.5:
                self.syncing = True
                sc.set_value(min(100, n.vol))
                self.syncing = False

    def _render_recs(self):
        s = self.snap
        self.rec_count.set_text(f"{len(s.rec)} capturing")
        clear(self.recs)
        self.rec_bars = []
        src = s.source()
        silent = bool(src and src.muted)
        for n in s.rec:
            bar = Bar(4)
            bar.add_css_class("in")
            name = label(f"<b>{esc(n.label)}</b>  <span foreground='{hypr_ui.hex_color('muted')}'>{esc(n.sub)}</span>", markup=True,
                         hexpand=True, ellipsize=True)
            name.set_max_width_chars(30)
            row = hbox(12, mono(n.label), vbox(6, hbox(8, name, label("silent" if silent else "live", "dev-vol")), bar,
                                               hexpand=True, valign=Gtk.Align.CENTER))
            row.add_css_class("app")
            self.recs.append(row)
            self.rec_bars.append(bar)
        if not s.rec:
            self.recs.append(label("no app is using the mic", "empty"))

    def _build_meter(self):
        clear(self.meter_box)
        self.segs = []
        rows = (("L", 5), ("R", 5)) if self.tab == "out" else (("IN", 12),)
        for name, h in rows:
            seg = Segments(h)
            seg.add_css_class(self.tab)
            self.segs.append(seg)
            self.meter_box.append(hbox(8, label(name, "seg-label"), seg))

    def _meter_tick(self):
        s = self.snap
        node = s.sink() if self.tab == "out" else s.source()
        gain = 0.0 if not node or node.muted else ((node.vol / 100) ** 3 if self.tab == "out" else 1.0)
        raw = self.meter.levels
        if self.tab == "in":
            raw = [max(raw)] * 2
        for i in range(2):
            v = math.sqrt(raw[i] * gain)  # sqrt reads closer to loudness than linear peak
            self.shown[i] = v if v > self.shown[i] else self.shown[i] * 0.82
        for i, seg in enumerate(self.segs):
            seg.level = self.shown[i]
            seg.queue_draw()
        for bar in self.rec_bars:
            bar.frac = self.shown[0]
            bar.queue_draw()
        return True

    def _follow_meter(self):
        s = self.snap
        if self.tab == "out":
            node = s.sink()
            self.meter.watch(node.name if node else None, sink=True)
        else:
            node = s.source()
            self.meter.watch(node.name if node else None, sink=False)

    # ---------- data ----------

    def refresh(self):
        self.app.worker.run(pw.snapshot, self.set_snapshot)
        if self.tab == "out":
            self.app.worker.run(spot.state, self._set_spot)

    def set_snapshot(self, snap, err=None):
        if err or snap is None:
            return
        self.snap = snap
        if self.get_visible():
            self.render()
            self._follow_meter()

    def _tick(self):
        if not self.app.worker.busy():
            self.refresh()
        return True

    # ---------- actions ----------

    def act(self, action: str, step: int = 5):
        cap = self.caps.get(action)
        if cap:
            cap.add_css_class("pressed")
            GLib.timeout_add(220, lambda: cap.remove_css_class("pressed") and False)
        if action == "tab":
            self.show_tab("in" if self.tab == "out" else "out")
            return
        self.app.act(action, step)

    def pick(self, i: int):
        out = self.tab == "out"
        lst = self._device_list()
        if i < len(lst):
            self.app.pick(lst[i], out)

    def _select(self, i: int):
        if not self.dev_rows:
            return
        if self.sel < len(self.dev_rows):
            self.dev_rows[self.sel].remove_css_class("selected")
        self.sel = max(0, min(i, len(self.dev_rows) - 1))
        self.dev_rows[self.sel].add_css_class("selected")

    def _on_hero_scale(self, sc):
        if self.syncing:
            return
        node = self.snap.sink() if self.tab == "out" else self.snap.source()
        if node:
            self.set_vol(node.id, int(sc.get_value()))
            self.hero_val.set_text(f"{int(sc.get_value())}%")

    def _on_app_scale(self, nid: int, sc):
        if not self.syncing:
            self.set_vol(nid, int(sc.get_value()))
            self.app_scales[nid][1].set_text(f"{int(sc.get_value())}%")

    def set_vol(self, nid: int, pct: int):
        """Coalesce slider drags: at most one queued wpctl call per node, always with the latest value."""
        self.touched[nid] = time.monotonic()
        first = nid not in self.pending_vol
        self.pending_vol[nid] = pct
        if first:
            self.app.worker.run(lambda: pw.set_volume(nid, self.pending_vol.pop(nid)), lambda *_: poke_waybar())

    def _on_proc(self, key: str, on: bool):
        if self.syncing:
            return
        self.app.set_fx(key, on)
        (self.procs[key].row.add_css_class if on else self.procs[key].row.remove_css_class)("on")

    def _on_key(self, _ctrl, keyval, _code, state):
        name = Gdk.keyval_name(keyval) or ""
        out = self.tab == "out"
        m = {
            "Escape": "close", "Tab": "tab", "ISO_Left_Tab": "tab",
            "m": "mute", "i": "mic-mute", "o": "next-out", "p": "next-in",
            "minus": "down", "KP_Subtract": "down", "equal": "up", "plus": "mic-up", "KP_Add": "up",
            "underscore": "mic-down",
            "Left": "down" if out else "mic-down", "Right": "up" if out else "mic-up",
            "XF86AudioMute": "mute", "XF86AudioRaiseVolume": "up", "XF86AudioLowerVolume": "down",
            "XF86AudioMicMute": "mic-mute",
        }
        a = m.get(name)
        if a == "close":
            self.toggle()
        elif a:
            self.act(a)
        elif name in ("j", "Down"):
            self._select(self.sel + 1)
        elif name in ("k", "Up"):
            self._select(self.sel - 1)
        elif name in ("Return", "KP_Enter", "space"):
            self.pick(self.sel)
        else:
            return False
        return True

    # ---------- visibility ----------

    def show_tab(self, tab: str):
        if tab != self.tab:
            self.tab = tab
            self.sel = 0
            self.sig_dev = None
            self.shown = [0.0, 0.0]
        self.extra.set_visible_child_name(tab)
        self._build_meter()
        self.render()
        self._follow_meter()

    def open(self, tab: str):
        if not self.get_visible():
            self.toggle(tab)
        else:
            self.show_tab(tab)

    def toggle(self, tab: str = "out"):
        if self.get_visible():
            self.set_visible(False)
            self.backdrop.set_visible(False)
            self.meter.stop()
            for t in self.timers:
                GLib.source_remove(t)
            self.timers = []
            OPEN_FLAG.unlink(missing_ok=True)
        else:
            hypr_ui.solo("sound")
            self.sig_dev = self.sig_apps = None
            self.syncing = True
            for key, sw in self.procs.items():
                sw.set_active(self.app.fx.on[key])
            self.syncing = False
            self.backdrop.present()
            self.present()
            self.tab = ""
            self.show_tab(tab)
            self.refresh()
            self.timers = [GLib.timeout_add(50, self._meter_tick), GLib.timeout_add_seconds(1, self._tick)]
            OPEN_FLAG.touch()
        poke_waybar()


class App(Gtk.Application):
    def __init__(self, sock: Path):
        super().__init__(application_id="dev.edu.SoundWidget", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.sock_path = sock
        self.worker = Worker()
        self.fx = fx.Fx()
        self.panel: Panel | None = None
        self.osd: Osd | None = None

    # ---------- state ----------

    def _load_fx(self):
        try:
            saved = json.loads(STATE.read_text())
        except (OSError, ValueError):
            saved = {}
        for k in ("noise", "echo"):  # "hear yourself" is never restored at login
            self.fx.on[k] = bool(saved.get(k))

    def _save_fx(self):
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({k: self.fx.on[k] for k in ("noise", "echo")}, indent=2))

    def _mic_name(self, snap: pw.Snapshot) -> str | None:
        src = snap.source()
        return src.name if src else None

    # ---------- actions (all on the worker thread) ----------

    def act(self, action: str, step: int = 5):
        def work():
            s = pw.snapshot()
            sink, src = s.sink(), s.source()
            out = not action.startswith("mic") and action != "next-in"
            if action in ("up", "down") and sink:
                pw.step(sink.id, step if action == "up" else -step)
                if action == "up" and sink.muted:
                    pw.set_mute(sink.id, "0")
            elif action == "mute" and sink:
                pw.set_mute(sink.id)
            elif action in ("mic-up", "mic-down") and src:
                pw.step(src.id, step if action == "mic-up" else -step)
                if action == "mic-up" and src.muted:
                    pw.set_mute(src.id, "0")
            elif action == "mic-mute" and src:
                pw.set_mute(src.id)
            elif action in ("next-out", "next-in"):
                lst = [n for n in (s.sinks if out else s.sources) if n.available]
                cur = sink if out else src
                if len(lst) > 1:
                    i = next((j for j, n in enumerate(lst) if cur and n.id == cur.id), -1)
                    self._set_device(lst[(i + 1) % len(lst)], out, s)
            return pw.snapshot(), out, action.startswith("next")

        self.worker.run(work, self._after)

    def pick(self, node: pw.Node, out: bool):
        self.worker.run(lambda: (self._set_device(node, out, pw.snapshot()), (pw.snapshot(), out, True))[1], self._after)

    def _set_device(self, node: pw.Node, out: bool, snap: pw.Snapshot):
        if not out and snap.voice:
            self.fx.apply(node.name)  # restart the filter on the new mic; it stays the default source
        else:
            pw.set_default(node.id)
            if not out and self.fx.on["monitor"]:
                self.fx.apply(node.name)

    def set_fx(self, key: str, on: bool):
        self.fx.on[key] = on
        self._save_fx()
        self.worker.run(lambda: (self.fx.apply(self._mic_name(pw.snapshot())), pw.snapshot())[1],
                        lambda snap, err: self.panel.set_snapshot(snap, err))

    def _after(self, res, err):
        if err or not res:
            return
        snap, out, dev = res
        if self.panel:
            self.panel.set_snapshot(snap)
        self.osd.show(snap, out, dev)
        poke_waybar()

    # ---------- socket ----------

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
        c, arg = cmd[0], cmd[1] if len(cmd) > 1 else ""
        if c == "toggle":
            self.panel.toggle()
        elif c == "close":
            if self.panel.get_visible():
                self.panel.toggle()
        elif c in ("open-out", "open-in"):
            self.panel.open(c[5:])
        elif c in ("up", "down", "mute", "mic-up", "mic-down", "mic-mute", "next-out", "next-in"):
            self.act(c, int(arg) if arg.isdigit() else 5)
        elif c == "quit":
            self.quit()

    # ---------- lifecycle ----------

    def do_startup(self):
        Gtk.Application.do_startup(self)
        hypr_ui.load_css(HERE / "style.css")
        self.hold()
        self._load_fx()
        self.panel = Panel(self)
        self.osd = Osd(self)
        self._serve()
        if self.fx.on["noise"] or self.fx.on["echo"]:
            self.worker.run(lambda: self.fx.apply(self._mic_name(pw.snapshot())))

    def do_activate(self):
        pass

    def do_shutdown(self):
        try:
            self.fx.shutdown(self._mic_name(pw.snapshot()))
        except Exception:
            pass
        self.panel.meter.stop()
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
