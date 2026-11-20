"""VPN and DNS panels of the network popover (the wifi panel lives in widget.Popover)."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from gi.repository import Gio, GLib, Gtk

import dns
import nm
import state
import vpn
from ui import bg, clear, fmt_age, fmt_rate, hbox, label, poke_waybar, vbox

I_SHIELD_ON, I_SHIELD_OFF, I_GLOBE, I_RADIO_OFF, I_RADIO_ON, I_REFRESH = (
    "\U000f0565", "\U000f0498", "\U000f059f", "\U000f043d", "\U000f043e", "\U000f0450",
)


def switch_row(title: str, sub: str, active: bool, on_change, sensitive=True) -> tuple[Gtk.Widget, Gtk.Switch]:
    sw = Gtk.Switch(active=active, valign=Gtk.Align.CENTER, sensitive=sensitive)
    sw.connect("notify::active", lambda s, _p: on_change(s.get_active()))
    sub_lbl = label(sub, "opt-sub", ellipsize=True)
    sub_lbl.set_tooltip_text(sub)
    return hbox(10, vbox(1, label(title, "opt-title"), sub_lbl, hexpand=True), sw), sw


def icon_box(glyph: str, *classes) -> Gtk.Label:
    return label(glyph, "big-icon", *classes, xalign=0.5)


class LatencyBar(Gtk.DrawingArea):
    """Thin pill bar; length = latency relative to the slowest resolver, color from CSS (.lat.*)."""

    def __init__(self):
        super().__init__(content_width=64, content_height=4, valign=Gtk.Align.CENTER)
        self.frac = 0.0
        self.add_css_class("lat")
        self.set_draw_func(self._draw)

    def set(self, ms: int | None, worst: int):
        for c in ("fast", "mid", "slow"):
            self.remove_css_class(c)
        if ms is not None:
            self.add_css_class("fast" if ms < 30 else "mid" if ms < 80 else "slow")
        self.frac = 0.0 if ms is None else max(0.08, min(1.0, ms / max(worst, 60)))
        self.queue_draw()

    def _draw(self, _a, cr, w, h):
        c = self.get_color()
        cr.set_source_rgba(c.red, c.green, c.blue, 0.18)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        if self.frac:
            cr.set_source_rgba(c.red, c.green, c.blue, 1.0)
            cr.rectangle(0, 0, w * self.frac, h)
            cr.fill()


class Panel:
    """Common bits: a scrollable list with keyboard selection."""

    def __init__(self, win):
        self.win = win
        self.box = vbox(10)
        self.sel = 0
        self.rows: list[Gtk.Widget] = []

    def select(self, i: int):
        if not self.rows:
            return
        if self.sel < len(self.rows):
            self.rows[self.sel].remove_css_class("selected")
        self.sel = max(0, min(i, len(self.rows) - 1))
        self.rows[self.sel].add_css_class("selected")

    def on_key(self, name: str) -> bool:
        if name in ("j", "Down"):
            self.select(self.sel + 1)
        elif name in ("k", "Up"):
            self.select(self.sel - 1)
        elif name in ("Return", "KP_Enter", "space"):
            self.activate(self.sel)
        elif name == "r":
            self.refresh()
        else:
            return False
        return True

    @staticmethod
    def _wrap(lbl: Gtk.Label):
        # a wrapping label otherwise asks for its whole one-line width and stretches the popover
        lbl.set_wrap(True)
        lbl.set_max_width_chars(44)
        lbl.set_width_chars(1)

    def clickable(self, widget: Gtk.Widget, i: int):
        g = Gtk.GestureClick()
        g.connect("released", lambda *_: (self.select(i), self.activate(i)))
        widget.add_controller(g)

    # overridden
    def refresh(self): ...
    def activate(self, i: int): ...
    def start(self): ...
    def stop(self): ...
    def dot(self) -> bool: return False


# ------------------------------------------------------------------ VPN


class VpnPanel(Panel):
    def __init__(self, win):
        super().__init__(win)
        self.tuns: list[vpn.Tunnel] = []
        self.lat: dict[str, int | None] = {}
        self.exit: dict = {}
        self.busy: str | None = None
        self.error = ""
        self.last_bytes = None
        self.rate = (0.0, 0.0)
        self.timer = 0
        self.st = state.load()

        self.card = vbox(8)
        self.card.add_css_class("card")
        self.count = label("", "section-meta", xalign=1.0)
        self.list = vbox(2)
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                max_content_height=240)
        sc.set_child(self.list)

        kill, _ = switch_row("kill switch", "needs root firewall rules · not wired yet", False, lambda _v: None,
                             sensitive=False)
        auto, _ = switch_row("auto-connect", "on open wifi networks, via the last tunnel",
                             self.st["vpn_autoconnect"], self._on_auto)
        opts = vbox(10, kill, auto)
        opts.add_css_class("card")

        for w in (self.card, hbox(0, label("TUNNELS", "section", hexpand=True), self.count), sc, opts):
            self.box.append(w)

    def dot(self) -> bool:
        return any(t.active for t in self.tuns)

    def start(self):
        self.refresh()
        self.timer = GLib.timeout_add_seconds(1, self._tick)

    def stop(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0

    def refresh(self):
        def load():
            tuns = vpn.tunnels()
            with ThreadPoolExecutor(8) as ex:
                lat = dict(zip([t.uuid for t in tuns], ex.map(lambda t: vpn.latency_ms(t.endpoint), tuns)))
            prot = vpn.protecting(tuns)
            return tuns, lat, (vpn.exit_info() if prot else {})

        bg(load, self._loaded)

    def _loaded(self, res, err):
        if err:
            self.error = str(err)
        else:
            self.tuns, self.lat, self.exit = res
        self.render()
        self.win.update_dots()

    def _tick(self):
        prot = vpn.protecting(self.tuns)
        if prot and prot.iface:
            try:
                rx, tx = nm.traffic_bytes(prot.iface)
                now = time.monotonic()
                if self.last_bytes:
                    t0, rx0, tx0 = self.last_bytes
                    dt = max(now - t0, 1e-3)
                    self.rate = ((rx - rx0) * 8 / 1e6 / dt, (tx - tx0) * 8 / 1e6 / dt)
                    if hasattr(self, "rate_lbl"):
                        self.rate_lbl.set_markup(self._rate_markup())
                self.last_bytes = (now, rx, tx)
            except OSError:
                pass
        return True

    def _rate_markup(self):
        return (f"<span color='#7aa2f7'>↓ {fmt_rate(self.rate[0])}</span> "
                f"<span color='#bb9af7'>↑ {fmt_rate(self.rate[1])}</span> Mb/s")

    def _default_tunnel(self) -> vpn.Tunnel | None:
        by_uuid = {t.uuid: t for t in self.tuns}
        return by_uuid.get(self.st["last_vpn"]) or next((t for t in self.tuns if t.full), None)

    def render(self):
        clear(self.card)
        prot = vpn.protecting(self.tuns)
        if prot:
            icon = icon_box(I_SHIELD_ON, "ok")
            head = vbox(1, label(f"protected · {prot.kind}", "vpn-state", "ok"),
                       label(prot.name, "ssid-big", ellipsize=True), hexpand=True)
            disc = Gtk.Button(label="disconnect", valign=Gtk.Align.CENTER)
            disc.add_css_class("ghost")
            disc.connect("clicked", lambda *_: self.toggle_tunnel(prot))
            self.card.append(hbox(10, icon, head, disc))
            up = vpn.uptime_s(prot.iface)
            self.rate_lbl = label(self._rate_markup(), "v", markup=True)
            grid = Gtk.Grid(column_spacing=12, row_spacing=6, column_homogeneous=True)
            cells = (("exit ip", self.exit.get("ip") or "—"), ("location", self.exit.get("location") or "—"),
                     ("up for", fmt_age(up) if up is not None else "—"), ("tunnel", None))
            for i, (k, v) in enumerate(cells):
                grid.attach(vbox(1, label(k, "k"), label(v, "v") if v else self.rate_lbl), i % 2, i // 2, 1, 1)
            self.card.append(grid)
        else:
            icon = icon_box(I_SHIELD_OFF, "warn")
            head = vbox(1, label("unprotected", "vpn-state", "warn"), label("vpn off", "ssid-big"), hexpand=True)
            row = hbox(10, icon, head)
            default = self._default_tunnel()
            if default:
                go = Gtk.Button(label=f"connect · {default.badge}", valign=Gtk.Align.CENTER)
                go.add_css_class("primary")
                go.connect("clicked", lambda *_: self.toggle_tunnel(default))
                row.append(go)
            self.card.append(row)
            ssid = GLib.markup_escape_text(self.win.link.ssid) if self.win.link else "your connection"
            hint = "pick a tunnel below, or connect to the last one." if default else "import a wireguard .conf below."
            self.card.append(label(f"traffic leaves straight through <b>{ssid}</b>, {hint}", "card-note",
                                   markup=True))
            self._wrap(self.card.get_last_child())
        if self.error:
            self.card.append(label(self.error, "error-text"))
            self._wrap(self.card.get_last_child())

        n_wg = sum(t.kind != "tailscale" for t in self.tuns)
        kinds = sorted({t.kind for t in self.tuns if t.kind != "tailscale"}) or ["wireguard"]
        self.count.set_text(f"{' · '.join(kinds)} · {n_wg} profile{'s' if n_wg != 1 else ''}")

        clear(self.list)
        self.rows = []
        for i, t in enumerate(self.tuns):
            self.rows.append(self._row(i, t))
            self.list.append(self.rows[-1])
        imp = Gtk.Button(label="+ import wireguard .conf", halign=Gtk.Align.START)
        imp.add_css_class("link")
        imp.connect("clicked", lambda *_: self.import_conf())
        self.list.append(imp)
        self.select(min(self.sel, len(self.rows) - 1))

    def _row(self, i: int, t: vpn.Tunnel) -> Gtk.Widget:
        badge = label(t.badge, "cc", xalign=0.5)
        if t.active:
            badge.add_css_class("up")
        right = hbox(10, valign=Gtk.Align.CENTER)
        if self.busy == t.uuid:
            right.append(hbox(5, Gtk.Spinner(spinning=True), label("connecting", "state", "busy")))
        elif t.active:
            right.append(label("up", "state", "connected"))
        ms = self.lat.get(t.uuid)
        if ms is not None:
            right.append(label(f"{ms} ms", "ms", "fast" if ms < 50 else "mid" if ms < 150 else "slow"))
        elif t.kind == "tailscale" and not t.active:
            right.append(label("off", "state"))
        row = hbox(10, badge, vbox(1, label(t.name, "name", ellipsize=True), label(t.detail, "opt-sub", ellipsize=True),
                                  hexpand=True), right)
        row.add_css_class("row")
        self.clickable(row, i)
        return row

    def activate(self, i: int):
        if i < len(self.tuns):
            self.toggle_tunnel(self.tuns[i])

    def toggle_tunnel(self, t: vpn.Tunnel):
        if self.busy:
            return
        self.busy, self.error = t.uuid, ""
        self.render()
        going_up = not t.active
        if going_up and t.kind != "tailscale":
            self.st["last_vpn"] = t.uuid
            state.save(self.st)
            if self.st["vpn_autoconnect"]:
                bg(lambda: vpn.set_autoconnect(t.uuid))

        def done(_r, err):
            self.busy = None
            self.last_bytes = None
            if err:
                self.error = str(err)
            poke_waybar()
            self.refresh()

        bg(lambda: vpn.up(t) if going_up else vpn.down(t), done)

    def _on_auto(self, on: bool):
        self.st["vpn_autoconnect"] = on
        state.save(self.st)
        target = self.st["last_vpn"] if on else None
        if on and not target:
            self.error = "connect a tunnel once so auto-connect knows which one to use"
            self.render()
            return
        bg(lambda: vpn.set_autoconnect(target),
           lambda n, err: (setattr(self, "error", str(err) if err else ""), self.render()))

    def import_conf(self):
        self.win.toggle()  # the file chooser is a normal window; the popover would sit on top of it
        dlg = Gtk.FileDialog(title="Import WireGuard config")
        f = Gtk.FileFilter(name="WireGuard config")
        f.add_pattern("*.conf")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        dlg.set_filters(store)

        def picked(d, res):
            try:
                path = d.open_finish(res).get_path()
            except GLib.Error:
                return
            bg(lambda: vpn.import_wireguard(path), self._imported)

        dlg.open(None, None, picked)

    def _imported(self, name, err):
        import subprocess

        msg = (f"Imported {name}", "Open the network popover → vpn to connect.") if not err else \
              ("WireGuard import failed", str(err))
        subprocess.Popen(["notify-send", "-a", "Network", "-i", "network-vpn", *msg])


# ------------------------------------------------------------------ DNS


class DnsPanel(Panel):
    def __init__(self, win):
        super().__init__(win)
        self.st = state.load()
        self.lat: dict[str, int | None] = {}
        self.dhcp: list[str] = []
        self.live: list[str] = []
        self.live_tls = False
        self.current = self.st["dns"]
        self.busy: str | None = None
        self.testing = False
        self.error = ""
        self.custom_open = False

        self.card = vbox(8)
        self.card.add_css_class("card")
        test = Gtk.Button(label=f"{I_REFRESH} test latency")
        test.add_css_class("link")
        test.connect("clicked", lambda *_: self.test())
        self.list = vbox(2)
        tls, self.tls_sw = switch_row("dns over tls", "encrypt lookups so the network can't read them",
                                      self.st["dns_tls"], lambda v: self._on_opt("dns_tls", v))
        fb, self.fb_sw = switch_row("fallback to dhcp", "router's servers stay behind yours as a backup",
                                    self.st["dns_fallback"], lambda v: self._on_opt("dns_fallback", v))
        opts = vbox(10, tls, fb)
        opts.add_css_class("card")
        for w in (self.card, hbox(0, label("RESOLVERS", "section", hexpand=True), test), self.list, opts):
            self.box.append(w)

    def dot(self) -> bool:
        return self.live_tls

    def start(self):
        self.refresh()

    def refresh(self):
        dev = self.win.dev

        def load():
            dhcp = dns.dhcp_servers(dev)
            live, tls = dns.link_status(dev)
            return dhcp, live, tls

        bg(load, self._loaded)

    def _loaded(self, res, err):
        if not err:
            self.dhcp, self.live, self.live_tls = res
            self.current = dns.identify(self.live, self.dhcp)
        self.render()
        self.win.update_dots()
        if not self.lat:
            self.test()

    def _servers(self, r: dns.Resolver) -> list[str]:
        if r.key == "auto":
            return self.dhcp
        if r.key == "custom":
            return [s.strip() for s in self.st["dns_custom"].replace(" ", ",").split(",") if s.strip()]
        return r.servers

    def test(self):
        if self.testing:
            return
        self.testing = True
        tls = self.live_tls
        jobs = {r.key: (self._servers(r)[:1] or [None])[0] for r in dns.PRESETS}

        def run():
            with ThreadPoolExecutor(5) as ex:
                res = ex.map(lambda s: dns.latency_ms(s) if s else None, jobs.values())
            out = dict(zip(jobs.keys(), res))
            cur = self.live[0] if self.live else None
            out["_current"] = dns.latency_ms(cur, tls) if cur else None
            return out

        def done(res, err):
            self.testing = False
            if not err:
                self.lat = res
            self.render()

        bg(run, done)

    def render(self):
        clear(self.card)
        r = next(p for p in dns.PRESETS if p.key == self.current)
        ms = self.lat.get("_current")
        head = vbox(1, label("resolving via", "k"), label(r.name, "ssid-big"), hexpand=True)
        avg = vbox(0, label(f"{ms} ms" if ms is not None else "—", "avg", xalign=1.0),
                   label("avg lookup", "k", xalign=1.0), valign=Gtk.Align.CENTER)
        self.card.append(hbox(10, icon_box(I_GLOBE), head, avg))
        first = self.live[0] if self.live else "no server"
        proto = "dns-over-tls :853" if self.live_tls else "plain dns :53"
        mark = "<span color='#9ece6a'>✓</span>" if self.live_tls else "<span color='#e0af68'>!</span>"
        self.card.append(label(f"{mark} {first} · {proto}", "v", markup=True))
        prot = vpn.protecting(self.win.vpn.tuns)
        if prot:
            self.card.append(label("vpn is up · queries ride the tunnel to this resolver", "note"))
            self._wrap(self.card.get_last_child())
        if self.error:
            self.card.append(label(self.error, "error-text"))
            self._wrap(self.card.get_last_child())

        clear(self.list)
        self.rows = []
        known = [v for k, v in self.lat.items() if k != "_current" and v is not None]
        worst = max(known + [1])
        for i, p in enumerate(dns.PRESETS):
            self.rows.append(self._row(i, p, worst))
            self.list.append(self.rows[-1])
            if p.key == "custom" and self.custom_open:
                self.list.append(self._custom_box())
        self.select(min(self.sel, len(self.rows) - 1))

    def _row(self, i: int, p: dns.Resolver, worst: int) -> Gtk.Widget:
        chosen = p.key == self.current
        radio = label(I_RADIO_ON if chosen else I_RADIO_OFF, "radio", "on" if chosen else "off")
        title = hbox(6, label(p.name, "name"))
        if p.tag:
            title.append(label(p.tag, "tag"))
        servers = self._servers(p)
        sub = " · ".join(servers) + (" · from router" if p.key == "auto" and servers else "")
        right = hbox(8, valign=Gtk.Align.CENTER)
        if self.busy == p.key:
            right.append(hbox(5, Gtk.Spinner(spinning=True), label("applying", "state", "busy")))
        else:
            ms = self.lat.get(p.key)
            bar = LatencyBar()
            bar.set(ms, worst)
            right.append(bar)
            right.append(label(f"{ms} ms" if ms is not None else ("…" if self.testing else "—"), "ms-plain",
                               xalign=1.0))
        row = hbox(10, radio, vbox(1, title, label(sub or "—", "opt-sub", ellipsize=True), hexpand=True), right)
        row.add_css_class("row")
        if chosen:
            row.add_css_class("current")
        self.clickable(row, i)
        return row

    def _custom_box(self) -> Gtk.Widget:
        box = vbox(8)
        box.add_css_class("pass")
        box.append(label("resolver address(es), comma separated", "label"))
        entry = Gtk.Entry(text=self.st["dns_custom"], placeholder_text="10.0.0.53, 10.0.0.54", hexpand=True)
        go = Gtk.Button(label="apply")
        go.add_css_class("primary")

        def submit(*_):
            self.st["dns_custom"] = entry.get_text().strip()
            state.save(self.st)
            if self._servers(dns.PRESETS[-1]):
                self.custom_open = False
                self.choose("custom")

        go.connect("clicked", submit)
        entry.connect("activate", submit)
        box.append(hbox(6, entry, go))
        GLib.idle_add(lambda: entry.grab_focus() and False)
        return box

    def activate(self, i: int):
        key = dns.PRESETS[i].key
        if key == "custom":
            self.custom_open = not self.custom_open
            self.render()
        elif key != self.current:
            self.choose(key)

    def choose(self, key: str):
        if self.busy:
            return
        self.busy, self.error = key, ""
        self.st["dns"] = key
        state.save(self.st)
        self.render()
        self._apply()

    def _apply(self):
        r = next(p for p in dns.PRESETS if p.key == self.st["dns"])
        custom = self._servers(dns.PRESETS[-1])
        args = (r, custom, self.st["dns_tls"], self.st["dns_fallback"], self.win.dev)

        def done(_n, err):
            self.busy = None
            if err:
                self.error = str(err)
            self.lat.pop("_current", None)
            GLib.timeout_add(800, lambda: (self.refresh(), self.test()) and False)

        bg(lambda: dns.apply(*args), done)

    def _on_opt(self, key: str, value: bool):
        self.st[key] = value
        state.save(self.st)
        if not self.busy:
            self.busy = self.st["dns"]
            self.render()
            self._apply()

    def on_key(self, name: str) -> bool:
        if name == "r":
            self.test()
            return True
        return super().on_key(name)
