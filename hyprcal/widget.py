"""hyprcal: resident GTK4 layer-shell calendar popover in the hypr-ui look.
Driven over a unix socket by the `hyprcal` script."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402
from gi.repository import Gtk4LayerShell as Layer  # noqa: E402

sys.path.insert(0, os.path.expanduser("~/.config/hypr-ui/gtk"))
import hypr_ui  # noqa: E402  shared look: tokens, base stylesheet, popover placement, solo()
import config  # noqa: E402
import store  # noqa: E402

HERE = Path(__file__).resolve().parent
OPEN_FLAG = config.RUN / "hyprcal.open"
RSTATE = config.CACHE.parent / "reminders.json"
WIDTH = {"popout": 400, "full": 780}  # card widths; the window adds the shadow margin
LEFT_COL = 316

# nerd font (material design) glyphs
G = {
    "cal": "\U000f00ed", "sync": "\U000f0450", "full": "\U000f0293", "shrink": "\U000f0294", "cog": "\U000f0493",
    "close": "\U000f0156", "prev": "\U000f0141", "next": "\U000f0142", "copy": "\U000f018f",
    "bell": "\U000f009e",
}
ANCHOR = config.CACHE.parent / "anchor"  # x of the waybar clock, learned from clicks on it
BAR_MAX_Y = 40  # a click above this is on the bar


def place(window: Gtk.Window, cfg: dict, card_w: int, pos: str | None = None) -> None:
    """Put a hypr-ui card under the bar: under the clock (position = "clock"), centred, or top-right."""
    pos = pos or cfg["general"].get("position", "clock")
    Layer.set_anchor(window, Layer.Edge.RIGHT, pos == "right")
    x = None
    if pos == "clock":
        try:
            x = float(ANCHOR.read_text())
        except (OSError, ValueError):
            x = None
    Layer.set_anchor(window, Layer.Edge.LEFT, x is not None)
    if x is not None:
        mons = Gdk.Display.get_default().get_monitors()
        width = mons.get_item(0).get_geometry().width if mons.get_n_items() else 1920
        gap = hypr_ui.SURFACE["gapRight"]
        left = min(max(gap, x - card_w / 2), width - card_w - gap)
        Layer.set_margin(window, Layer.Edge.LEFT, int(left) - hypr_ui.MARGIN)


def learn_anchor() -> None:
    """Opened by a click on the clock: remember where the clock is."""
    try:
        out = subprocess.run(["hyprctl", "cursorpos"], capture_output=True, text=True, timeout=1).stdout
        x, y = (float(v) for v in out.split(","))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return
    if 0 <= y <= BAR_MAX_Y:
        ANCHOR.parent.mkdir(parents=True, exist_ok=True)
        ANCHOR.write_text(str(x))


# ---------- small widget helpers (same shape as sound-widget) ----------

def label(text="", *classes, xalign=0.0, hexpand=False, ellipsize=0, markup=False):
    w = Gtk.Label(xalign=xalign, hexpand=hexpand)
    w.set_markup(text) if markup else w.set_text(text)
    for c in classes:
        w.add_css_class(c)
    if ellipsize:
        w.set_ellipsize(Pango.EllipsizeMode.END)
        w.set_max_width_chars(ellipsize)  # else the natural width is the whole text and the panel grows
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


def clear(box) -> None:
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def button(text: str, css: str, cb, tip: str = "") -> Gtk.Button:
    b = Gtk.Button(label=text, valign=Gtk.Align.CENTER)
    b.add_css_class(css)
    if tip:
        b.set_tooltip_text(tip)
    b.connect("clicked", lambda *_: cb())
    return b


def pip(color: str) -> Gtk.Box:
    p = Gtk.Box(valign=Gtk.Align.CENTER)
    p.add_css_class("pip")
    p.add_css_class(f"c-{color}")
    return p


def keyhint(k: str, text: str) -> Gtk.Box:
    return hbox(4, label(k, "key"), label(text, "hint"))


def esc(s: str) -> str:
    return GLib.markup_escape_text(s)


def span(minutes: int) -> str:
    h, m = divmod(max(0, minutes), 60)
    return f"{h}h {m:02d}m" if h and m else f"{h}h" if h else f"{m}m"


def copy(text: str) -> None:
    subprocess.Popen(["wl-copy", text], start_new_session=True)


def xdg_open(url: str) -> None:
    subprocess.Popen(["xdg-open", url], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Backdrop(Gtk.Window):
    """Invisible full-screen layer under the popover: a click on it means "clicked outside"."""

    def __init__(self, app, on_click):
        super().__init__(application=app, title="hyprcal-backdrop")
        self.add_css_class("backdrop")
        Layer.init_for_window(self)
        Layer.set_namespace(self, "hyprcal-backdrop")
        Layer.set_layer(self, Layer.Layer.TOP)
        for edge in (Layer.Edge.TOP, Layer.Edge.BOTTOM, Layer.Edge.LEFT, Layer.Edge.RIGHT):
            Layer.set_anchor(self, edge, True)
        Layer.set_keyboard_mode(self, Layer.KeyboardMode.NONE)
        self.set_child(Gtk.Box(hexpand=True, vexpand=True))
        click = Gtk.GestureClick(button=0)
        click.connect("pressed", lambda *_: on_click())
        self.add_controller(click)


class Panel(Gtk.ApplicationWindow):
    def __init__(self, app: "App"):
        super().__init__(application=app, title="hyprcal", resizable=False)
        self.app = app
        Layer.init_for_window(self)
        Layer.set_namespace(self, "hyprcal")
        Layer.set_layer(self, Layer.Layer.OVERLAY)
        hypr_ui.popover(self, Layer)
        Layer.set_keyboard_mode(self, Layer.KeyboardMode.ON_DEMAND)

        self.mode = app.cfg["general"]["default_view"] if app.cfg["general"]["default_view"] in WIDTH else "popout"
        self.filter = "all"
        self.events: list[store.Event] = []
        self.slots: list[store.Slot] = []
        self.data: dict = {}
        self.by_day: dict[date, list[store.Event]] = {}
        self.flash_text = ""
        self.flash_timer = 0
        self.tick_timer = 0
        self.last_minute = None
        today = self.now().date()
        self.sel = today
        self.month = today.replace(day=1)

        self.backdrop = Backdrop(app, lambda: self.get_visible() and self.toggle())
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.place()
        self._build()

    # ---------- config-derived ----------

    @property
    def cfg(self) -> dict:
        return self.app.cfg

    def now(self) -> datetime:
        return datetime.now(self.app.zone)

    def f(self, dt, key: str) -> str:
        return config.fmt(dt, self.cfg["format"][key])

    def place(self):
        place(self, self.cfg, WIDTH[self.mode])

    def week_start(self) -> int:
        return 6 if str(self.cfg["general"].get("week_start", "")).lower().startswith("sun") else 0

    def sources(self) -> list[tuple[str, str]]:
        out = [(c["name"], c["color"]) for c in self.cfg["google"]["calendars"]] if self.cfg["google"]["enabled"] else []
        if self.cfg["calendly"]["enabled"]:
            out.append(("calendly", self.cfg["calendly"]["color"]))
        return out

    # ---------- layout ----------

    def _build(self):
        full = self.mode == "full"
        w = WIDTH[self.mode]
        root = vbox(12)
        root.add_css_class("popover")
        root.add_css_class(self.mode)
        root.set_size_request(w + 2 * hypr_ui.MARGIN, -1)
        self.set_default_size(w + 2 * hypr_ui.MARGIN, -1)
        self.set_child(root)

        # header
        self.subtitle = label("", "subtitle", ellipsize=40)
        root.append(hbox(
            10, label(G["cal"], "app-icon", xalign=0.5),
            vbox(0, label("calendar", "title"), self.subtitle, hexpand=True, valign=Gtk.Align.CENTER),
            button(G["sync"], "icon", self.app.sync, "sync now (r)"),
            button(G["shrink"] if full else G["full"], "icon", self.switch_mode, "compact (f)" if full else "full view (f)"),
            button(G["cog"], "icon", self.edit_config, "edit config.toml (e)"),
            button(G["close"], "icon", self.toggle, "close (esc)")))

        # calendar filter tabs (full view)
        self.tab_btns: dict[str, Gtk.Button] = {}
        if full and len(self.sources()) > 1:
            tabs = hbox(2, homogeneous=True)
            tabs.add_css_class("tabs")
            for name, color in [("all", "")] + self.sources():
                inner = hbox(6, halign=Gtk.Align.CENTER)
                if color:
                    inner.append(pip(color))
                inner.append(label(name, "tab-name"))
                b = Gtk.Button(child=inner)
                b.add_css_class("tab")
                b.connect("clicked", lambda _b, n=name: self.set_filter(n))
                tabs.append(b)
                self.tab_btns[name] = b
            root.append(tabs)

        clock = self._build_clock()
        month = self._build_month()
        agenda = self._build_agenda(full)
        cly = self._build_calendly(full) if self.cfg["calendly"]["enabled"] else None
        if full:
            left = vbox(12, clock, month, hexpand=False)  # explicit: the clock row's spacer would make it expand
            left.set_size_request(LEFT_COL, -1)
            right = vbox(12, agenda, hexpand=True)
            if cly:
                right.append(cly)
            root.append(hbox(18, left, right))
        else:
            root.append(clock)
            root.append(month)
            root.append(agenda)
            if cly:
                root.append(cly)

        hints = ([("h/l", "day"), ("j/k", "week"), ("[ ]", "month"), ("t", "today"), ("tab", "filter"),
                  ("y", "link"), ("f", "compact"), ("esc", "close")] if full else
                 [("h/l", "day"), ("j/k", "week"), ("t", "today"), ("f", "full"), ("esc", "close")])
        if not self.cfg["calendly"]["enabled"]:
            hints = [h for h in hints if h[0] != "y"]
        foot = hbox(10)
        foot.add_css_class("footer")
        for k, t in hints:
            foot.append(keyhint(k, t))
        root.append(foot)
        self.render()

    def _build_clock(self) -> Gtk.Widget:
        self.status = label("", "status", hexpand=True, ellipsize=34, markup=True)
        self.clock = label("", "clock")
        self.secs = label("", "secs")
        base = getattr(Gtk.Align, "BASELINE_FILL", Gtk.Align.BASELINE)
        self.clock.set_valign(base)
        self.secs.set_valign(base)
        self.week = label("", "badge")
        self.week.set_valign(Gtk.Align.CENTER)
        self.date_l = label("", "date", ellipsize=36)
        self.progress = Gtk.ProgressBar(hexpand=True)
        self.progress.add_css_class("day-progress")
        self.dayno = label("", "meta", hexpand=True)
        self.left = label("", "meta", xalign=1.0)
        card = vbox(4,
                    hbox(8, self.status, button("today", "ghost", self.go_today, "jump to today (t)")),
                    hbox(0, self.clock, self.secs, Gtk.Box(hexpand=True), self.week, margin_top=4),
                    self.date_l,
                    vbox(4, self.progress, hbox(0, self.dayno, self.left), margin_top=6))
        card.add_css_class("card")
        card.add_css_class("clock-card")
        return card

    def _build_month(self) -> Gtk.Widget:
        self.month_title = label("", "section", hexpand=True)
        self.grid = Gtk.Grid(column_homogeneous=True, row_spacing=2, column_spacing=2)
        head = hbox(2, self.month_title,
                    button(G["prev"], "icon", lambda: self.shift_month(-1), "previous month ([)"),
                    button(G["next"], "icon", lambda: self.shift_month(1), "next month (])"))
        return vbox(6, head, self.grid)

    def _build_agenda(self, full: bool) -> Gtk.Widget:
        self.ag_title = label("", "section", hexpand=True)
        self.ag_meta = label("", "section-meta", xalign=1.0)
        self.agenda = vbox(2)
        body: Gtk.Widget = self.agenda
        if full:
            sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
            sc.set_child(self.agenda)
            sc.set_min_content_height(240)
            sc.set_max_content_height(420)
            sc.set_propagate_natural_height(True)
            body = sc
        return vbox(6, hbox(0, self.ag_title, self.ag_meta), body)

    def _build_calendly(self, full: bool) -> Gtk.Widget:
        color = self.cfg["calendly"]["color"]
        self.cly_meta = label("", "section-meta", hexpand=True, ellipsize=44 if full else 34)
        link = button("copy link", "primary", self.copy_link, "copy your booking link (y)")
        self.slot_box = None
        self.cly_note = None
        if full:
            self.slot_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6,
                                        min_children_per_line=3, column_spacing=4, row_spacing=4, homogeneous=True)
            self.cly_note = label("", "meta", ellipsize=60)
            card = vbox(8, hbox(8, pip(color), label("calendly", "name"), self.cly_meta, link),
                        self.slot_box, self.cly_note)
        else:
            card = hbox(10, pip(color), vbox(1, label("calendly", "name"), self.cly_meta, hexpand=True,
                                                valign=Gtk.Align.CENTER), link)
        card.add_css_class("card")
        card.add_css_class("calendly")
        return card

    # ---------- data ----------

    def set_data(self, data: dict):
        self.data = data or {}
        self.events, self.slots = store.load(self.data, self.app.zone)
        self.render()

    def visible_events(self) -> list[store.Event]:
        return self.events if self.filter == "all" else [e for e in self.events if e.cal == self.filter]

    def _index(self):
        self.by_day = {}
        for e in self.visible_events():
            d = e.start.date()
            last = (e.end - timedelta(microseconds=1)).date() if e.end > e.start else d
            while d <= last:
                self.by_day.setdefault(d, []).append(e)
                d += timedelta(days=1)

    # ---------- rendering ----------

    def render(self):
        self._index()
        now = self.now()
        cals = len(self.sources())
        place = config.tz_name(self.cfg).rsplit("/", 1)[-1].replace("_", " ").lower()
        self.subtitle.set_text(f"{place} · {cals} calendar{'s' if cals != 1 else ''}")
        for name, b in self.tab_btns.items():
            (b.add_css_class if name == self.filter else b.remove_css_class)("active")
        self.render_clock()
        self.render_month()
        self.render_agenda()
        self.render_calendly()

    def render_status(self):
        errs = self.data.get("errors") or {}
        cfg_err = self.app.cfg_error
        if self.flash_text:
            cls, text = "ok", self.flash_text
        elif cfg_err:
            cls, text = "error", cfg_err
        elif self.app.syncing:
            cls, text = "busy", "syncing"
        elif errs:
            name, msg = next(iter(errs.items()))
            cls, text = "warn", f"{name}: {msg}"
        elif self.data.get("synced"):
            ago = int((self.now() - datetime.fromisoformat(self.data["synced"])).total_seconds() // 60)
            cls, text = "ok", "synced · " + ("now" if ago < 1 else f"{span(ago)} ago")
        else:
            cls, text = "idle", "not synced yet"
        for c in ("ok", "error", "busy", "warn", "idle"):
            (self.status.add_css_class if c == cls else self.status.remove_css_class)(c)
        self.status.set_markup(f"<span size='small'>●</span>  {esc(text)}")
        self.status.set_tooltip_text("\n".join(f"{k}: {v}" for k, v in errs.items()) or None)

    def render_clock(self):
        now = self.now()
        self.clock.set_text(self.f(now, "clock"))
        self.secs.set_visible(bool(self.cfg["format"].get("seconds")))
        self.secs.set_text(now.strftime(":%S"))
        self.date_l.set_text(self.f(now, "date"))
        self.week.set_text(f"w{now.isocalendar().week}")
        mid = datetime.combine(now.date(), time(0)).replace(tzinfo=now.tzinfo)
        done = (now - mid).total_seconds() / 86400
        self.progress.set_fraction(min(1.0, max(0.0, done)))
        year_days = 366 if (now.year % 4 == 0 and now.year % 100) or now.year % 400 == 0 else 365
        self.dayno.set_text(f"day {now.timetuple().tm_yday}/{year_days}")
        self.left.set_text(f"{span(int((1 - done) * 1440))} left")
        self.render_status()

    def render_month(self):
        clear(self.grid)
        today = self.now().date()
        ws = self.week_start()
        wk = bool(self.cfg["general"].get("week_numbers"))
        x0 = 1 if wk else 0
        self.month_title.set_text(config.fmt(self.month, self.cfg["format"]["month_title"]).upper())
        offset = (self.month.weekday() - ws) % 7
        start = self.month - timedelta(days=offset)
        if wk:
            self.grid.attach(label("wk", "wd", "wk", xalign=0.5), 0, 0, 1, 1)
        for i in range(7):
            self.grid.attach(label(config.fmt(start + timedelta(days=i), "%a")[:2].lower(), "wd", xalign=0.5), x0 + i, 0, 1, 1)
        for row in range(6):
            first = start + timedelta(days=row * 7)
            if row == 5 and first.month != self.month.month:
                break  # five rows are enough this month
            if wk:
                monday = first + timedelta(days=(7 - first.weekday()) % 7)
                n = label(str(monday.isocalendar().week), "wk", xalign=0.5)
                n.set_valign(Gtk.Align.CENTER)
                self.grid.attach(n, 0, row + 1, 1, 1)
            for col in range(7):
                d = first + timedelta(days=col)
                evs = self.by_day.get(d, [])
                dots = hbox(2, halign=Gtk.Align.CENTER)
                for color in list(dict.fromkeys(e.color for e in evs))[:3]:
                    dots.append(pip(color))
                b = Gtk.Button(child=vbox(1, label(str(d.day), "num", xalign=0.5), dots, valign=Gtk.Align.CENTER))
                b.add_css_class("day")
                if d.month != self.month.month:
                    b.add_css_class("out")
                if d.weekday() >= 5:
                    b.add_css_class("weekend")
                if d == today:
                    b.add_css_class("today")
                if d == self.sel:
                    b.add_css_class("selected")
                if evs:
                    b.set_tooltip_text("\n".join(
                        ("all day" if e.all_day else self.f(e.start, "agenda_time")) + "  " + e.title for e in evs[:8]))
                b.connect("clicked", lambda _b, d=d: self.select(d))
                self.grid.attach(b, x0 + col, row + 1, 1, 1)

    def _row(self, e: store.Event, badge: str, now: datetime, live_badge=True, chars=26) -> Gtk.Widget:
        if e.all_day:
            t1, t2 = "all day", ""
        else:
            t1, t2 = self.f(e.start, "agenda_time"), self.f(e.end, "agenda_time")
        times = vbox(0, label(t1, "t1"), label(t2, "t2"), valign=Gtk.Align.CENTER)
        times.set_size_request(46, -1)
        bar = Gtk.Box()
        bar.add_css_class("bar")
        bar.add_css_class(f"c-{e.color}")
        meta = [e.cal]
        if e.who:
            meta.append(e.who)
        if e.location:
            meta.append(e.location.split(",")[0])
        if e.people > 1:
            meta.append(f"{e.people} people")
        live = e.live(now)
        b = label("now" if live and live_badge else badge, "badge")
        b.set_valign(Gtk.Align.CENTER)
        if live:
            b.add_css_class("now")
        row = hbox(10, times, bar,
                   vbox(1, label(e.title, "name", ellipsize=chars), label(" · ".join(meta), "meta", ellipsize=chars + 6),
                        hexpand=True, valign=Gtk.Align.CENTER), b)
        row.add_css_class("row")
        if live:
            row.add_css_class("selected")
        elif e.end <= now:
            row.add_css_class("past")
        if e.url:
            row.add_css_class("link")
            row.set_tooltip_text(f"open {e.url.split('/')[2] if '//' in e.url else e.url}")
            click = Gtk.GestureClick()
            click.connect("released", lambda *_a, u=e.url: (xdg_open(u), self.toggle()))
            row.add_controller(click)
        return row

    def render_agenda(self):
        clear(self.agenda)
        now = self.now()
        today = now.date()
        full = self.mode == "full"
        chars = 40 if full else 26
        if not full and self.sel == today:
            # compact view, today: what comes next, across days
            ahead = [e for e in self.visible_events() if e.end > now][:4]
            self.ag_title.set_text("UP NEXT")
            self.ag_meta.set_text(f"{len(self.by_day.get(today, []))} today")
            for e in ahead:
                d = e.start.date()
                mins = int((e.start - now).total_seconds() // 60)
                badge = (f"in {span(mins)}" if 0 <= mins < 60 else "today" if d <= today else
                         "tomorrow" if d == today + timedelta(days=1) else self.f(e.start, "day_title").lower())
                self.agenda.append(self._row(e, badge, now, chars=chars))
            if not ahead:
                self.agenda.append(label("nothing ahead", "empty", xalign=0.5))
            return
        evs = self.by_day.get(self.sel, [])
        self.ag_title.set_text("TODAY" if self.sel == today else self.f(self.sel, "day_title").upper())
        self.ag_meta.set_text(f"{len(evs)} event{'s' if len(evs) != 1 else ''}" if evs else "")
        line_done = self.sel != today
        for e in evs:
            if not line_done and not e.all_day and e.start > now:
                self.agenda.append(self._nowline(now))
                line_done = True
            mins = int((e.end - e.start).total_seconds() // 60)
            self.agenda.append(self._row(e, "all day" if e.all_day else span(mins), now, chars=chars))
        if evs and not line_done:
            self.agenda.append(self._nowline(now))
        if not evs:
            self.agenda.append(label("nothing on this day", "empty", xalign=0.5))

    def _nowline(self, now: datetime) -> Gtk.Widget:
        line = Gtk.Box(hexpand=True, valign=Gtk.Align.CENTER)
        line.add_css_class("nowline")
        return hbox(8, label(self.f(now, "agenda_time"), "now-time"), line, margin_start=8)

    def render_calendly(self):
        if not self.cfg["calendly"]["enabled"] or not hasattr(self, "cly_meta"):
            return
        now = self.now()
        today = now.date()
        open_ = [s for s in self.slots if s.start > now]
        next_book = next((e for e in self.events if e.cal == "calendly" and e.start > now), None)
        nb = ""
        if next_book:
            day = "today" if next_book.start.date() == today else self.f(next_book.start, "day_title").lower()
            nb = f"next {day} {self.f(next_book.start, 'agenda_time')}" + (f" with {next_book.who.split(',')[0]}"
                                                                           if next_book.who else "")
        if self.slot_box is None:  # compact
            n_today = sum(1 for s in open_ if s.start.date() == today)
            parts = [f"{n_today} open today" if self.data.get("slots") is not None else ""]
            self.cly_meta.set_text(" · ".join(p for p in parts + [nb] if p) or "no bookings ahead")
            return
        day_slots = [s for s in open_ if s.start.date() == self.sel][: int(self.cfg["calendly"]["slots_per_day"])]
        self.cly_meta.set_text(f"{len(open_)} open · next 7 days")
        clear(self.slot_box)
        for s in day_slots:
            b = button(self.f(s.start, "agenda_time"), "ghost", lambda s=s: self._copy(s.url or self.booking_url(),
                                                                                        "slot link copied"),
                       f"{s.type} · {s.minutes}m · click to copy its link")
            self.slot_box.append(b)
        self.slot_box.set_visible(bool(day_slots))
        if day_slots:
            self.cly_note.set_text(nb or "click a slot to copy its booking link")
        else:
            later = next((s for s in open_ if s.start.date() > self.sel), None)
            msg = "no open slots this day"
            if later:
                msg += f" · next {self.f(later.start, 'day_title').lower()} {self.f(later.start, 'agenda_time')}"
            self.cly_note.set_text(msg + (f" · {nb}" if nb else ""))

    # ---------- actions ----------

    def booking_url(self) -> str:
        return self.data.get("booking_url") or self.cfg["calendly"].get("booking_url") or ""

    def copy_link(self):
        url = self.booking_url()
        if url:
            self._copy(url, "booking link copied")
        else:
            self.flash("no booking link yet (sync first)")

    def _copy(self, url: str, msg: str):
        copy(url)
        self.flash(msg)

    def flash(self, text: str):
        self.flash_text = text
        if self.flash_timer:
            GLib.source_remove(self.flash_timer)
        self.flash_timer = GLib.timeout_add(1800, self._unflash)
        self.render_status()

    def _unflash(self):
        self.flash_text, self.flash_timer = "", 0
        self.render_status()
        return False

    def select(self, d: date):
        self.sel = d
        if (d.year, d.month) != (self.month.year, self.month.month):
            self.month = d.replace(day=1)
        self.render_month()
        self.render_agenda()
        self.render_calendly()

    def shift_month(self, n: int):
        m = self.month.month - 1 + n
        first = date(self.month.year + m // 12, m % 12 + 1, 1)
        last = (date(first.year + first.month // 12, first.month % 12 + 1, 1) - timedelta(days=1)).day
        self.select(first.replace(day=min(self.sel.day, last)))

    def go_today(self):
        self.select(self.now().date())

    def set_filter(self, name: str):
        self.filter = name
        self.render()

    def next_filter(self):
        names = ["all"] + [n for n, _ in self.sources()]
        self.set_filter(names[(names.index(self.filter) + 1) % len(names)] if self.filter in names else "all")

    def switch_mode(self, mode: str | None = None):
        mode = mode or ("popout" if self.mode == "full" else "full")
        if mode == self.mode:
            return
        self.mode = mode
        if self.filter != "all" and mode == "popout":
            self.filter = "all"
        shown = self.get_visible()
        if shown:
            self.set_visible(False)  # a layer surface only shrinks when it is mapped again
        self.place()
        self._build()
        if shown:
            self.present()

    def edit_config(self):
        config.ensure()
        editor = os.environ.get("EDITOR") or shutil.which("nvim") or shutil.which("vim")
        term = os.environ.get("TERMINAL") or shutil.which("alacritty")
        if editor and term:
            subprocess.Popen([term, "-e", editor, str(config.CONFIG)], start_new_session=True)
        else:
            xdg_open(str(config.CONFIG))
        self.toggle()

    def _on_key(self, _ctrl, keyval, _code, state):
        name = Gdk.keyval_name(keyval) or ""
        d = self.sel
        moves = {"h": -1, "Left": -1, "l": 1, "Right": 1, "j": 7, "Down": 7, "k": -7, "Up": -7}
        if name == "Escape" or name == "q":
            self.toggle()
        elif name in moves:
            self.select(d + timedelta(days=moves[name]))
        elif name in ("bracketleft", "Page_Up", "H"):
            self.shift_month(-1)
        elif name in ("bracketright", "Page_Down", "L"):
            self.shift_month(1)
        elif name in ("t", "Home", "period"):
            self.go_today()
        elif name in ("Tab", "ISO_Left_Tab"):
            self.next_filter()
        elif name == "f":
            self.switch_mode()
        elif name == "y" and self.cfg["calendly"]["enabled"]:
            self.copy_link()
        elif name == "r":
            self.app.sync()
        elif name == "e":
            self.edit_config()
        else:
            return False
        return True

    # ---------- visibility ----------

    def _tick(self):
        now = self.now()
        self.render_clock()
        if now.minute != self.last_minute:
            if self.last_minute is not None:
                self.render_agenda()  # live/past states and the now-line move with the minute
                if now.hour == 0 and now.minute == 0:
                    self.render()
            self.last_minute = now.minute
        return True

    def toggle(self, mode: str | None = None):
        if self.get_visible():
            self.set_visible(False)
            self.backdrop.set_visible(False)
            if self.tick_timer:
                GLib.source_remove(self.tick_timer)
                self.tick_timer = 0
            OPEN_FLAG.unlink(missing_ok=True)
            self.app.check_reminders()
            return
        hypr_ui.solo("hyprcal")
        learn_anchor()
        rebuilt = self.app.reload_config()
        if mode and mode != self.mode:
            self.mode = mode
            rebuilt = True
        self.place()
        if rebuilt:
            self._build()
        self.sel = self.now().date()
        self.month = self.sel.replace(day=1)
        self.set_data(store.read_raw())
        self.app.reminder.set_visible(False)  # the popover shows the same event
        self.backdrop.present()
        self.present()
        self.last_minute = None
        self.tick_timer = GLib.timeout_add(1000, self._tick)
        OPEN_FLAG.touch()
        if self.app.stale():
            self.app.sync()


class Reminder(Gtk.Window):
    """The next event, under the clock, on top of everything until dismissed, snoozed or over.
    Click the card to open the event in Google Calendar."""

    W = 360

    def __init__(self, app: "App"):
        super().__init__(application=app, title="hyprcal-reminder", resizable=False)
        self.app = app
        self.ev: store.Event | None = None
        Layer.init_for_window(self)
        Layer.set_namespace(self, "hyprcal-reminder")
        Layer.set_layer(self, Layer.Layer.OVERLAY)
        hypr_ui.popover(self, Layer)  # top gap, shadow room, click-through margin
        Layer.set_keyboard_mode(self, Layer.KeyboardMode.NONE)

        root = vbox(10)
        root.add_css_class("popover")
        root.add_css_class("reminder")
        root.set_size_request(self.W + 2 * hypr_ui.MARGIN, -1)
        self.set_default_size(self.W + 2 * hypr_ui.MARGIN, -1)
        self.set_child(root)

        self.icon = label(G["bell"], "app-icon", xalign=0.5)
        self.title_l = label("", "title", ellipsize=28)
        self.sub = label("", "subtitle", ellipsize=40)
        self.when = label("", "badge")
        self.when.set_valign(Gtk.Align.CENTER)
        root.append(hbox(10, self.icon, vbox(1, self.title_l, self.sub, hexpand=True, valign=Gtk.Align.CENTER),
                         self.when, button(G["close"], "icon", self.dismiss, "dismiss")))
        self.snooze_b = button("", "ghost", self.snooze)
        self.join_b = button("join", "primary", self.join, "join the call")
        self.more = label("", "hint")
        root.append(hbox(8, self.snooze_b, self.more, Gtk.Box(hexpand=True),
                         label("click to open in google", "hint"), self.join_b))
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self.open_event())
        root.add_controller(click)

    def show_event(self, ev: store.Event, more: int, now: datetime):
        cfg = self.app.cfg
        if self.ev is None or ev.key != self.ev.key:
            for c in list(self.icon.get_css_classes()):
                if c.startswith("c-"):
                    self.icon.remove_css_class(c)
            self.icon.add_css_class(f"c-{ev.color}")
            self.title_l.set_text(ev.title)
            t = config.fmt(ev.start, cfg["format"]["agenda_time"]) + "–" + config.fmt(ev.end, cfg["format"]["agenda_time"])
            self.sub.set_text(" · ".join([t, ev.cal] + ([ev.location.split(",")[0]] if ev.location else [])))
            self.snooze_b.set_label(f"snooze {int(cfg['reminder']['snooze'])}m")
            self.join_b.set_visible(bool(ev.url) and ev.url != ev.link and "calendar.google.com" not in ev.url)
        self.ev = ev
        mins = (ev.start - now).total_seconds() / 60
        if mins > 0.5:
            text = f"in {span(int(mins + 0.99))}"
        elif mins > -1:
            text = "now"
        else:
            text = f"{span(int(-mins))} ago"
        self.when.set_text(text)
        (self.when.add_css_class if mins <= 1 else self.when.remove_css_class)("now")
        self.more.set_text(f"+{more} more" if more else "")
        if not self.get_visible():
            place(self, cfg, self.W, cfg["reminder"].get("position", "center"))
            self.present()

    def hide(self):
        self.ev = None
        self.set_visible(False)

    def dismiss(self):
        if self.ev:
            self.app.reminder_state(self.ev.key, "done")
        self.hide()
        self.app.check_reminders()

    def snooze(self):
        if self.ev:
            until = datetime.now(self.app.zone) + timedelta(minutes=int(self.app.cfg["reminder"]["snooze"]))
            self.app.reminder_state(self.ev.key, until.timestamp())
        self.hide()
        self.app.check_reminders()

    def open_event(self):
        ev = self.ev
        if not ev:
            return
        xdg_open(ev.link or f"https://calendar.google.com/calendar/r/day/{ev.start:%Y/%m/%d}")
        self.dismiss()

    def join(self):
        if self.ev and self.ev.url:
            xdg_open(self.ev.url)
            self.dismiss()


class App(Gtk.Application):
    def __init__(self, sock: Path):
        super().__init__(application_id="dev.edu.HyprCal", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.sock_path = sock
        self.panel: Panel | None = None
        self.reminder: Reminder | None = None
        self.events: list[store.Event] = []
        self.rstate: dict[str, object] = {}  # event key -> "done" or snooze-until timestamp
        self.syncing = False
        self.sync_timer = 0
        self.cfg_mtime = -1.0
        self.cfg, self.cfg_error = config.load()
        self.zone = config.tz(self.cfg)

    def reload_config(self) -> bool:
        """Read config.toml again if it changed; True when the popover must be rebuilt."""
        m = config.mtime()
        if m == self.cfg_mtime:
            return False
        self.cfg_mtime = m
        self.cfg, self.cfg_error = config.load()
        self.zone = config.tz(self.cfg)
        config.apply_locale(self.cfg)
        self._arm_sync()
        return True

    def stale(self) -> bool:
        synced = store.read_raw().get("synced")
        if not synced:
            return True
        age = (datetime.now(self.zone) - datetime.fromisoformat(synced)).total_seconds()
        return age > max(60, int(self.cfg["general"]["refresh"]))

    def _arm_sync(self):
        if self.sync_timer:
            GLib.source_remove(self.sync_timer)
        self.sync_timer = GLib.timeout_add_seconds(max(60, int(self.cfg["general"]["refresh"])),
                                                   lambda: (self.sync(), True)[1])

    def sync(self):
        if self.syncing:
            return
        self.syncing = True
        if self.panel and self.panel.get_visible():
            self.panel.render_status()
        cfg = self.cfg

        def work():
            try:
                data = store.sync(cfg)
            except Exception as e:  # never kill the daemon over a sync
                data = dict(store.read_raw(), errors={"sync": str(e)[:80]})
            GLib.idle_add(lambda: self._synced(data) and False)

        threading.Thread(target=work, daemon=True).start()

    def _synced(self, data: dict):
        self.syncing = False
        self.events, _ = store.load(data, self.zone)
        self.check_reminders()
        if self.panel:
            if self.panel.get_visible():
                self.panel.set_data(data)
            else:
                self.panel.data = data

    # ---------- reminders ----------

    def reminder_state(self, key: str, value) -> None:
        self.rstate[key] = value
        now = datetime.now(self.zone).timestamp()
        live = {e.key for e in self.events if e.end.timestamp() > now - 86400}
        self.rstate = {k: v for k, v in self.rstate.items() if k in live}  # forget events that are over
        try:
            RSTATE.write_text(json.dumps(self.rstate))
        except OSError:
            pass

    def check_reminders(self) -> bool:
        r = self.cfg["reminder"]
        if not r.get("enabled", True) or self.reminder is None:
            if self.reminder:
                self.reminder.hide()
            return True
        now = datetime.now(self.zone)
        ts = now.timestamp()
        lead = int(r.get("lead", 10))

        def waiting(e: store.Event) -> bool:
            st = self.rstate.get(e.key)
            return st != "done" and not (isinstance(st, (int, float)) and st > ts)

        due = [e for e in self.events if not e.all_day and e.end > now
               and e.start - timedelta(minutes=e.lead(lead)) <= now and waiting(e)]
        if not due or (self.panel and self.panel.get_visible()):
            if self.reminder.get_visible() or not due:
                self.reminder.hide()
            return True
        self.reminder.show_event(due[0], len(due) - 1, now)
        return True

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
        p = self.panel
        if c == "toggle":
            default = self.cfg["general"]["default_view"]
            mode = arg if arg in WIDTH else default if default in WIDTH else "popout"
            if p.get_visible() and mode and mode != p.mode:
                p.switch_mode(mode)  # SUPER ALT C while the compact one is open: grow it
            else:
                p.toggle(mode)
        elif c == "open" and not p.get_visible():
            p.toggle(arg if arg in WIDTH else None)
        elif c == "close" and p.get_visible():
            p.toggle()
        elif c in ("snooze", "dismiss", "open-event") and self.reminder.ev:
            {"snooze": self.reminder.snooze, "dismiss": self.reminder.dismiss,
             "open-event": self.reminder.open_event}[c]()
        elif c == "refresh":
            self.reload_config()
            self.sync()
        elif c == "quit":
            self.quit()

    # ---------- lifecycle ----------

    def do_startup(self):
        Gtk.Application.do_startup(self)
        hypr_ui.load_css(HERE / "style.css")
        self.hold()
        self.reload_config()
        self.panel = Panel(self)
        self.reminder = Reminder(self)
        try:
            self.rstate = json.loads(RSTATE.read_text())
        except (OSError, ValueError):
            self.rstate = {}
        self.events, _ = store.load(store.read_raw(), self.zone)
        self._serve()
        self.sync()
        self.check_reminders()
        GLib.timeout_add_seconds(5, self.check_reminders)

    def do_activate(self):
        pass

    def do_shutdown(self):
        self.sock_path.unlink(missing_ok=True)
        OPEN_FLAG.unlink(missing_ok=True)
        Gtk.Application.do_shutdown(self)


def main(sock: Path):
    import signal

    OPEN_FLAG.unlink(missing_ok=True)
    config.ensure()
    app = App(sock)
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: app.quit() or False)
    app.run([])
