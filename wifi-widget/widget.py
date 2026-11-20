"""GTK4 layer-shell Wi-Fi popover. Launched (and toggled) by the `wifi-widget` script."""

from __future__ import annotations

import os
import time
from collections import deque
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as Layer  # noqa: E402

import sys  # noqa: E402

sys.path.insert(0, os.path.expanduser("~/.config/hypr-ui/gtk"))
import hypr_ui  # noqa: E402  shared look: tokens, base stylesheet, popover placement
import nm  # noqa: E402
import panels  # noqa: E402

HERE = Path(__file__).resolve().parent
OPEN_FLAG = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "wifi-widget.open"
HISTORY = 44  # bars in the traffic graph
SCAN_EVERY_S = 15
LINK_EVERY_S = 2

from ui import (  # noqa: E402
    I_EYE, I_EYE_OFF, I_LOCK, I_REFRESH, I_WIFI, I_WIFI_OFF,
    bg, clear, fmt_age, fmt_rate, hbox, label, poke_waybar, vbox,
)


class SignalBars(Gtk.DrawingArea):
    """Four ascending bars; lit count = strength. Color comes from CSS (.bars)."""

    def __init__(self, bars: int):
        super().__init__(content_width=14, content_height=11, valign=Gtk.Align.CENTER)
        self.bars = bars
        self.add_css_class("bars")
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, w, h):
        c = self.get_color()
        bw, gap = 2.5, 1.3
        for i in range(4):
            bh = h * (i + 1) / 4
            cr.set_source_rgba(c.red, c.green, c.blue, c.alpha if i < max(1, self.bars) else 0.22)
            cr.rectangle(i * (bw + gap), h - bh, bw, bh)
            cr.fill()


class Graph(Gtk.DrawingArea):
    """Throughput history as thin bars; newest bar on the right at full strength."""

    def __init__(self, history: deque):
        super().__init__(content_height=34, hexpand=True)
        self.history = history
        self.add_css_class("graph")
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, w, h):
        c = self.get_color()
        n = self.history.maxlen
        data = list(self.history)
        peak = max(data + [1.0])  # 1 Mb/s floor so an idle link reads as idle, not noisy
        slot = w / n
        bw = max(2.0, slot - 1.6)
        for i, v in enumerate(data):
            x = (n - len(data) + i) * slot
            bh = max(3.0, (v / peak) * (h - 2))
            last = i == len(data) - 1
            cr.set_source_rgba(c.red, c.green, c.blue, 1.0 if last else 0.55)
            cr.rectangle(x, h - bh, bw, bh)
            cr.fill()


class Backdrop(Gtk.Window):
    """Invisible full-screen layer under the popover: a click on it means "clicked outside".

    It keeps clear of exclusive zones (the bar), so clicking the waybar pill still toggles normally.
    """

    def __init__(self, app, on_click):
        super().__init__(application=app, title="wifi-widget-backdrop")
        self.add_css_class("backdrop")
        Layer.init_for_window(self)
        Layer.set_namespace(self, "wifi-widget-backdrop")
        Layer.set_layer(self, Layer.Layer.TOP)
        for edge in (Layer.Edge.TOP, Layer.Edge.BOTTOM, Layer.Edge.LEFT, Layer.Edge.RIGHT):
            Layer.set_anchor(self, edge, True)
        Layer.set_keyboard_mode(self, Layer.KeyboardMode.NONE)
        self.set_child(Gtk.Box(hexpand=True, vexpand=True))
        click = Gtk.GestureClick(button=0)  # any mouse button
        click.connect("pressed", lambda *_: on_click())
        self.add_controller(click)


class Popover(Gtk.ApplicationWindow):
    PANELS = ("wifi", "vpn", "dns")

    def __init__(self, app):
        super().__init__(application=app, title="wifi-widget", resizable=False)
        Layer.init_for_window(self)
        Layer.set_namespace(self, "wifi-widget")
        hypr_ui.layer(self, Layer, Layer.Layer.OVERLAY)
        hypr_ui.popover(self, Layer)
        Layer.set_keyboard_mode(self, Layer.KeyboardMode.ON_DEMAND)

        self.dev = nm.wifi_device() or "wlan0"
        self.nets: list[nm.Network] = []
        self.link: nm.Link | None = None
        self.radio = True
        self.sel = 0
        self.expanded: str | None = None  # ssid whose passphrase box is open
        self.busy: str | None = None  # ssid being connected
        self.errors: dict[str, str] = {}
        self.scanned_at = 0.0
        self.scanning = False
        self.history: deque[float] = deque(maxlen=HISTORY)
        self.last_bytes: tuple[float, int, int] | None = None
        self.rate = (0.0, 0.0)
        self.timers: list[int] = []
        self._syncing_switch = False
        self.rows: list[Gtk.Widget] = []

        self.backdrop = Backdrop(app, lambda: self.get_visible() and self.toggle())

        self._build()
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)  # before GTK's Tab focus-nav
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # ---------- layout ----------

    def _build(self):
        root = vbox(10)
        root.add_css_class("popover")
        root.set_size_request(360 + 2 * hypr_ui.MARGIN, -1)  # card 360 + shadow margin
        self.set_default_size(360 + 2 * hypr_ui.MARGIN, -1)  # wrapped labels otherwise let GTK trade height for extra width
        self.set_child(root)

        # header: icon, title/subtitle, rescan, radio switch
        icon = label(I_WIFI, "app-icon", xalign=0.5)
        self.subtitle = label("", "subtitle", ellipsize=True)
        titles = vbox(0, label("network", "title"), self.subtitle, hexpand=True, valign=Gtk.Align.CENTER)
        refresh = Gtk.Button(label=I_REFRESH, valign=Gtk.Align.CENTER, tooltip_text="refresh (r)")
        refresh.add_css_class("icon")
        refresh.connect("clicked", lambda *_: self._refresh_panel())
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=True)
        self.switch.connect("notify::active", self._on_switch)
        root.append(hbox(10, icon, titles, refresh, self.switch))

        # segmented panel switcher: wifi / vpn / dns, each with a status dot
        tabs = hbox(4, homogeneous=True)
        tabs.add_css_class("tabs")
        self.tab_btns: dict[str, Gtk.Button] = {}
        self.tab_dots: dict[str, Gtk.Label] = {}
        for name in self.PANELS:
            dot = label("●", "tab-dot")
            btn = Gtk.Button(child=hbox(6, label(name, "tab-name"), dot, halign=Gtk.Align.CENTER))
            btn.add_css_class("tab")
            btn.connect("clicked", lambda _b, n=name: self.show_panel(n))
            tabs.append(btn)
            self.tab_btns[name], self.tab_dots[name] = btn, dot
        root.append(tabs)

        self.panel_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120,
                                     vhomogeneous=False)
        root.append(self.panel_stack)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150)
        self.panel_stack.add_named(self.stack, "wifi")
        self.vpn = panels.VpnPanel(self)
        self.dns = panels.DnsPanel(self)
        self.panel_stack.add_named(self.vpn.box, "vpn")
        self.panel_stack.add_named(self.dns.box, "dns")
        self.panel = "wifi"

        # radio off page
        turn_on = Gtk.Button(label="turn on", halign=Gtk.Align.CENTER)
        turn_on.add_css_class("primary")
        turn_on.connect("clicked", lambda *_: self.switch.set_active(True))
        off = vbox(
            6,
            label(I_WIFI_OFF, "off-icon", xalign=0.5),
            label("radio is off", "off-title", xalign=0.5),
            label("airplane mode, or the switch above", "off-sub", xalign=0.5),
            turn_on,
        )
        off.add_css_class("card")
        off.set_margin_top(0)
        off.set_size_request(-1, 120)
        off.set_valign(Gtk.Align.FILL)
        self.stack.add_named(off, "off")

        # radio on page: connection card + networks
        on = vbox(10)
        self.card = vbox(6)
        self.card.add_css_class("card")
        on.append(self.card)

        self.found = label("", "section-meta", xalign=1.0)
        on.append(hbox(0, label("NETWORKS", "section", hexpand=True), self.found))

        self.list = vbox(2)
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True, max_content_height=300
        )
        scroller.set_child(self.list)
        self.scroller = scroller
        on.append(scroller)
        self.stack.add_named(on, "on")
        self.stack.set_visible_child_name("on")

        # footer key hints
        foot = hbox(7)
        foot.add_css_class("footer")
        for k, h in (("tab", "panel"), ("j/k", "move"), ("⏎", "connect"), ("r", "rescan"), ("d", "forget")):
            foot.append(hbox(4, label(k, "key"), label(h, "hint")))
        foot.append(Gtk.Box(hexpand=True))
        foot.append(label("esc", "key"))
        root.append(foot)

    def _render_card(self):
        child = self.card.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.card.remove(child)
            child = nxt

        link = self.link
        if not link:
            self.card.append(label("not connected", "status"))
            self.card.append(label("pick a network below", "ssid-big"))
            self.subtitle.set_text(f"{self.dev} · not connected")
            return

        self.subtitle.set_text(f"{link.device} · {link.band} · {link.bitrate_mbps:.0f} Mb/s link")
        status = hbox(5, label("●", "dot"), label(f"connected · {fmt_age(link.connected_s)}", "status", "ok"))
        status.add_css_class("status")
        disc = Gtk.Button(label="disconnect", valign=Gtk.Align.START)
        disc.add_css_class("ghost")
        disc.connect("clicked", lambda *_: self.disconnect())
        self.card.append(hbox(0, vbox(2, status, label(link.ssid, "ssid-big", ellipsize=True), hexpand=True), disc))

        self.graph = Graph(self.history)
        self.card.append(self.graph)
        self.rate_down = label("", "down")
        self.rate_up = label("", "up")
        self.card.append(
            hbox(
                14,
                self.rate_down,
                self.rate_up,
                label(f"{link.signal}% · {link.dbm} dBm", "meta", xalign=1.0, hexpand=True),
            )
        )
        self._render_rates()

        grid = Gtk.Grid(column_spacing=12, row_spacing=6, column_homogeneous=True, margin_top=4)
        for i, (k, v) in enumerate(
            (("ipv4", link.ipv4), ("gateway", link.gateway), ("channel", link.channel), ("security", link.security))
        ):
            grid.attach(vbox(1, label(k, "k"), label(v or "—", "v")), i % 2, i // 2, 1, 1)
        self.card.append(grid)

    def _render_rates(self):
        if self.link and hasattr(self, "rate_down"):
            self.rate_down.set_text(f"↓ {fmt_rate(self.rate[0])} Mb/s")
            self.rate_up.set_text(f"↑ {fmt_rate(self.rate[1])} Mb/s")
            self.graph.queue_draw()

    def _render_list(self):
        if self.expanded:
            return  # don't rebuild under someone typing a passphrase
        child = self.list.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.list.remove(child)
            child = nxt
        self.rows = []
        self.sel = min(self.sel, max(0, len(self.nets) - 1))
        for i, net in enumerate(self.nets):
            row = self._make_row(i, net)
            self.rows.append(row)
            self.list.append(row)
        if not self.nets:
            self.list.append(label("scanning…" if self.scanning else "no networks in range", "section-meta"))
        self._render_found()

    def _render_found(self):
        if self.scanning and not self.nets:
            self.found.set_text("scanning…")
        else:
            age = int(time.monotonic() - self.scanned_at) if self.scanned_at else 0
            self.found.set_text(f"{len(self.nets)} found · {age}s ago" + (" · scanning" if self.scanning else ""))

    def _make_row(self, i: int, net: nm.Network) -> Gtk.Widget:
        bars = SignalBars(net.bars)
        if net.signal < 25:
            bars.add_css_class("weak")
        elif not net.in_use:
            bars.add_css_class("strong")

        right = hbox(8, valign=Gtk.Align.CENTER)
        if self.busy == net.ssid:
            spin = Gtk.Spinner(spinning=True)
            right.append(hbox(5, spin, label("associating", "state", "busy")))
        elif net.ssid in self.errors and self.expanded != net.ssid:
            right.append(label("failed", "state", "error"))
        elif net.in_use:
            right.append(label("connected", "state", "connected"))
        elif net.saved_as:
            right.append(label("saved", "state"))
        elif not net.secure:
            right.append(label("open", "state"))
        if net.secure:
            right.append(label(I_LOCK, "lock"))
        right.append(label("5G" if net.band == "5 GHz" else "2.4", "badge", xalign=0.5))

        top = hbox(10, label(str(i + 1), "idx"), bars, label(net.ssid, "name", hexpand=True, ellipsize=True), right)
        top.add_css_class("row")
        if net.in_use:
            top.add_css_class("connected")
        if i == self.sel:
            top.add_css_class("selected")

        click = Gtk.GestureClick()
        click.connect("released", lambda *_: (self._select(i), self.activate_row(i)))
        top.add_controller(click)

        box = vbox(0, top)
        box.top = top
        if self.expanded == net.ssid:
            box.append(self._make_pass(net))
        return box

    def _make_pass(self, net: nm.Network) -> Gtk.Widget:
        sec = net.security.split()[-1] if net.security else "open"
        sec = {"WPA2": "WPA2-PSK", "WPA3": "WPA3-SAE", "WPA1": "WPA-PSK"}.get(sec, sec)
        ssid_m = GLib.markup_escape_text(net.ssid)
        box = vbox(8)
        box.add_css_class("pass")
        box.append(label(f"passphrase for <b>{ssid_m}</b> · {sec}", "label", markup=True))

        entry = Gtk.Entry(visibility=False, placeholder_text="min. 8 characters", hexpand=True)
        entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        eye = Gtk.Button(label=I_EYE)
        eye.add_css_class("eye")

        def toggle_eye(*_):
            entry.set_visibility(not entry.get_visibility())
            eye.set_label(I_EYE_OFF if entry.get_visibility() else I_EYE)

        eye.connect("clicked", toggle_eye)
        box.append(hbox(6, entry, eye))

        if net.ssid in self.errors:
            box.append(label(self.errors[net.ssid], "error-text", ellipsize=True))

        cancel = Gtk.Button(label="cancel")
        cancel.add_css_class("link")
        cancel.connect("clicked", lambda *_: self.collapse())
        go = Gtk.Button(label="connect ⏎", sensitive=False)
        go.add_css_class("primary")
        entry.connect("changed", lambda e: go.set_sensitive(len(e.get_text()) >= 8))

        def submit(*_):
            if len(entry.get_text()) >= 8:
                self.connect_net(net, entry.get_text())

        go.connect("clicked", submit)
        entry.connect("activate", submit)
        box.append(hbox(4, Gtk.Box(hexpand=True), cancel, go))
        GLib.idle_add(lambda: entry.grab_focus() and False)
        return box

    # ---------- data ----------

    def refresh_all(self, rescan=False):
        def load():
            radio = nm.wifi_enabled()
            nets = nm.scan(rescan=rescan) if radio else []
            link = nm.link_info(self.dev) if radio else None
            return radio, nets, link

        self.scanning = True
        self._render_found()
        bg(load, self._loaded)

    def _loaded(self, res, err):
        self.scanning = False
        if err:
            self.found.set_text(f"error: {err}")
            return
        self.radio, self.nets, self.link = res
        self.scanned_at = time.monotonic()
        self._syncing_switch = True
        self.switch.set_active(self.radio)
        self._syncing_switch = False
        self.stack.set_visible_child_name("on" if self.radio else "off")
        self.update_dots()
        if self.vpn.tuns:
            self.vpn.render()  # its "traffic leaves through <ssid>" line depends on the wifi link
        if not self.radio:
            self.subtitle.set_text(f"{self.dev} · radio off")
        else:
            self._render_card()
            self._render_list()

    def scan(self, rescan=False):
        self.refresh_all(rescan=rescan)

    def _tick(self):
        now = time.monotonic()
        try:
            rx, tx = nm.traffic_bytes(self.dev)
        except OSError:
            return True
        if self.last_bytes:
            t0, rx0, tx0 = self.last_bytes
            dt = max(now - t0, 1e-3)
            self.rate = ((rx - rx0) * 8 / 1e6 / dt, (tx - tx0) * 8 / 1e6 / dt)
            self.history.append(self.rate[0] + self.rate[1])
        self.last_bytes = (now, rx, tx)
        self._render_rates()
        self._render_found()
        return True

    def _link_tick(self):
        if self.radio and not self.busy:
            bg(lambda: nm.link_info(self.dev), self._link_loaded)
        return True

    def _link_loaded(self, link, err):
        if err:
            return
        was = self.link.ssid if self.link else None
        self.link = link
        if (link.ssid if link else None) != was:
            self.refresh_all()
        elif link:
            self._render_card()

    def _scan_tick(self):
        if self.radio and not self.busy and not self.expanded:
            self.refresh_all(rescan=False)
        return True

    # ---------- actions ----------

    def _select(self, i: int):
        if not self.rows:
            return
        self.rows[self.sel].top.remove_css_class("selected") if self.sel < len(self.rows) else None
        self.sel = max(0, min(i, len(self.rows) - 1))
        self.rows[self.sel].top.add_css_class("selected")
        # keep the selection visible
        adj = self.scroller.get_vadjustment()
        ok, rect = self.rows[self.sel].compute_bounds(self.list)
        if ok:
            y, h = rect.get_y(), rect.get_height()
            if y < adj.get_value():
                adj.set_value(y)
            elif y + h > adj.get_value() + adj.get_page_size():
                adj.set_value(y + h - adj.get_page_size())

    def activate_row(self, i: int):
        if self.busy or i >= len(self.nets):
            return
        net = self.nets[i]
        if net.in_use:
            return
        if net.saved_as or not net.secure:
            self.connect_net(net)
        else:
            self.expand(net.ssid)

    def expand(self, ssid: str):
        self.expanded = None
        self._render_list()
        self.expanded = ssid
        idx = next((j for j, n in enumerate(self.nets) if n.ssid == ssid), None)
        if idx is not None:
            self._select(idx)
            self.rows[idx].append(self._make_pass(self.nets[idx]))

    def collapse(self):
        self.expanded = None
        self._render_list()
        self.grab_focus()

    def connect_net(self, net: nm.Network, password: str | None = None):
        self.busy = net.ssid
        self.errors.pop(net.ssid, None)
        self.expanded = None
        self._render_list()

        def done(_res, err):
            self.busy = None
            if isinstance(err, nm.NeedsPassword) and net.secure:
                # ask (again) inline; say why when a passphrase was actually tried
                if password or net.saved_as:
                    self.errors[net.ssid] = "wrong passphrase?" if password else "saved passphrase was rejected"
                self.expand(net.ssid)
            elif err:
                self.errors[net.ssid] = str(err)
                self._render_list()
            poke_waybar()
            self.refresh_all()

        bg(lambda: nm.connect(net, password), done)

    def disconnect(self):
        cur = next((n for n in self.nets if n.in_use), None)
        if cur:
            bg(lambda: nm.disconnect(cur), lambda *_: (poke_waybar(), self.refresh_all()))

    def forget_selected(self):
        if self.sel >= len(self.nets):
            return
        net = self.nets[self.sel]
        if net.saved_as:
            bg(lambda: nm.forget(net), lambda *_: (poke_waybar(), self.refresh_all()))

    def _on_switch(self, sw, _pspec):
        if self._syncing_switch:
            return
        on = sw.get_active()
        self.stack.set_visible_child_name("on" if on else "off")
        bg(lambda: nm.set_wifi(on), lambda *_: (poke_waybar(), GLib.timeout_add(1500, self._after_radio)))

    def _after_radio(self):
        self.refresh_all(rescan=self.switch.get_active())
        return False

    def _on_key(self, _ctrl, keyval, _code, _state):
        name = Gdk.keyval_name(keyval) or ""
        typing = isinstance(self.get_focus(), Gtk.Text)
        if name == "Escape":
            self.collapse() if self.expanded else self.toggle()
            return True
        if name in ("Tab", "ISO_Left_Tab"):
            i = self.PANELS.index(self.panel) + (-1 if name == "ISO_Left_Tab" else 1)
            self.show_panel(self.PANELS[i % len(self.PANELS)])
            return True
        if typing:
            return False
        if self.panel != "wifi":
            return getattr(self, self.panel).on_key(name)
        if name in ("j", "Down"):
            self._select(self.sel + 1)
        elif name in ("k", "Up"):
            self._select(self.sel - 1)
        elif name in ("Return", "KP_Enter", "space"):
            self.activate_row(self.sel)
        elif name == "r":
            self.scan(rescan=True)
        elif name == "d":
            self.forget_selected()
        else:
            return False
        return True

    # ---------- panels ----------

    def show_panel(self, name: str):
        self.panel = name
        self.panel_stack.set_visible_child_name(name)
        for n, btn in self.tab_btns.items():
            (btn.add_css_class if n == name else btn.remove_css_class)("active")
        self.grab_focus()

    def update_dots(self):
        states = {"wifi": self.link is not None, "vpn": self.vpn.dot(), "dns": self.dns.dot()}
        for n, on in states.items():
            (self.tab_dots[n].add_css_class if on else self.tab_dots[n].remove_css_class)("on")

    def _refresh_panel(self):
        if self.panel == "wifi":
            self.scan(rescan=True)
        else:
            getattr(self, self.panel).refresh()

    # ---------- visibility ----------

    def toggle(self):
        if self.get_visible():
            self.set_visible(False)
            self.backdrop.set_visible(False)
            self.expanded = None
            self.vpn.stop()
            self.dns.custom_open = False
            for t in self.timers:
                GLib.source_remove(t)
            self.timers = []
            OPEN_FLAG.unlink(missing_ok=True)
        else:
            hypr_ui.solo("wifi")
            self.sel = 0
            self.errors.clear()
            self.last_bytes = None
            self.history.extend([0.0] * HISTORY)
            self.backdrop.present()
            self.show_panel("wifi")
            self.present()
            self.refresh_all()
            self.vpn.start()
            self.dns.start()
            GLib.timeout_add(300, lambda: self.scan(rescan=True) and False)
            self.timers = [
                GLib.timeout_add_seconds(1, self._tick),
                GLib.timeout_add_seconds(LINK_EVERY_S, self._link_tick),
                GLib.timeout_add_seconds(SCAN_EVERY_S, self._scan_tick),
            ]
            OPEN_FLAG.touch()
        poke_waybar()


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.edu.WifiWidget")
        self.win: Popover | None = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        hypr_ui.load_css(HERE / "style.css")
        self.hold()  # stay resident so the next toggle is instant
        # `close` is activated over D-Bus by hypr-ui-solo when another popover opens
        close = Gio.SimpleAction.new("close", None)
        close.connect("activate", lambda *_: self.win and self.win.get_visible() and self.win.toggle())
        self.add_action(close)

    def do_activate(self):
        if self.win is None:
            self.win = Popover(self)
        self.win.toggle()


def main():
    OPEN_FLAG.unlink(missing_ok=True)
    App().run([])
