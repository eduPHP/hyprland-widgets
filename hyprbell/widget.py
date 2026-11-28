"""hyprbell: the resident process. Logs every notification (monitor.py) and shows the history in a
GTK4 layer-shell panel at the right edge of the screen, in the hypr-ui look.
Driven over a unix socket by the `hyprbell` script."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402
from gi.repository import Gtk4LayerShell as Layer  # noqa: E402

sys.path.insert(0, os.path.expanduser("~/.config/hypr-ui/gtk"))
import hypr_ui  # noqa: E402  shared look: tokens, base stylesheet, solo()
import store  # noqa: E402
from monitor import Monitor  # noqa: E402

HERE = Path(__file__).resolve().parent
OPEN_FLAG = store.RUN / "hyprbell.open"
WIDTH = 440  # card width; the window adds the shadow margin
MAX_ROWS = 250  # rows drawn at once; search reaches the rest

# nerd font (material design) glyphs
G = {"bell": "\U000f009a", "off": "\U000f009b", "trash": "\U000f0a7a", "close": "\U000f0156",
     "search": "\U000f0349"}
TILE_COLORS = ("blue", "cyan", "purple", "green", "amber", "orange")
MAKO = ("org.freedesktop.Notifications", "/fr/emersion/Mako", "fr.emersion.Mako")
WAYBAR_ACTIONS_SIGNAL = 11


# ---------- small widget helpers (same shape as hyprcal) ----------

def label(text="", *classes, xalign=0.0, hexpand=False, ellipsize=0, wrap=False, lines=0):
    w = Gtk.Label(label=text, xalign=xalign, hexpand=hexpand)
    for c in classes:
        w.add_css_class(c)
    if wrap:
        w.set_wrap(True)
        w.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        w.set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
    if lines:
        w.set_lines(lines)
        w.set_ellipsize(Pango.EllipsizeMode.END)
    if ellipsize:
        w.set_ellipsize(Pango.EllipsizeMode.END)
        w.set_max_width_chars(ellipsize)  # else the natural width is the whole text and the panel grows
    if wrap or lines:
        w.set_max_width_chars(ellipsize or 44)
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


def keyed(key: str, text: str, css: str, cb) -> Gtk.Button:
    """An action button with its key on it, like the design: [o] open."""
    b = Gtk.Button(child=hbox(6, label(key, "key"), label(text)), valign=Gtk.Align.CENTER)
    b.add_css_class("act")
    if css:
        b.add_css_class(css)
    b.connect("clicked", lambda *_: cb())
    return b


def keyhint(k: str, text: str) -> Gtk.Box:
    return hbox(4, label(k, "key"), label(text, "hint"))


def run(*argv: str) -> None:
    try:
        subprocess.Popen(list(argv), start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# ---------- what an entry looks like ----------

def tile_text(app: str) -> str:
    words = [w for w in app.replace("-", " ").replace("_", " ").replace(".", " ").split() if w]
    if not words:
        return "?"
    if len(words) > 1 and words[0].lower() in ("com", "org", "io", "dev", "net"):
        words = words[-1:]
    return (words[0][0] + (words[1][0] if len(words) > 1 else words[0][1:2])).capitalize()


def tile_color(e: dict) -> str:
    if e.get("urgency") == 2:
        return "red"
    return TILE_COLORS[zlib.crc32(e.get("app", "").lower().encode()) % len(TILE_COLORS)]


def when(ts: float, now: float) -> str:
    s = max(0, int(now - ts))
    if s < 60:
        return "now"
    if s < 3600:
        return f"{s // 60}m"
    d = datetime.fromtimestamp(ts)
    if d.date() == datetime.fromtimestamp(now).date():
        return f"{s // 3600}h"
    return d.strftime("%H:%M")


def day_name(d: date, today: date) -> str:
    if d == today:
        return "today"
    if d == today - timedelta(days=1):
        return "yesterday"
    return d.strftime("%a %d %b").lower()


def icon_file(e: dict) -> Path | None:
    p = e.get("icon", "")
    if p.startswith("file://"):
        p = p[7:]
    if p.startswith("/"):
        path = Path(p)
        return path if path.is_file() else None
    return None


def is_screenshot(e: dict) -> Path | None:
    p = icon_file(e)
    if p and e.get("summary", "").lower().startswith("screenshot") and p.suffix.lower() in (".png", ".jpg", ".jpeg"):
        return p
    return None


def mako_call(method: str, args: GLib.Variant | None = None, reply: str | None = None):
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        r = bus.call_sync(MAKO[0], MAKO[1], MAKO[2], method, args,
                          GLib.VariantType(reply) if reply else None, Gio.DBusCallFlags.NONE, 1500, None)
        return r.unpack() if r is not None else None
    except GLib.Error:
        return None


def dnd_on() -> bool:
    r = mako_call("ListModes", None, "(as)")
    return bool(r) and store.DND_MODE in r[0]


def set_dnd(on: bool) -> None:
    r = mako_call("ListModes", None, "(as)")
    modes = [m for m in (r[0] if r else ["default"]) if m != store.DND_MODE]
    if on:
        modes.append(store.DND_MODE)
    mako_call("SetModes", GLib.Variant("(as)", (modes,)))
    run("pkill", f"-RTMIN+{WAYBAR_ACTIONS_SIGNAL}", "-x", "waybar")  # actions-bar's dnd icon


def window_for(e: dict) -> str | None:
    """Address of an open window that belongs to the app that sent `e`."""
    names = {n.lower() for n in (e.get("entry", ""), e.get("app", "")) if n}
    names |= {n.rsplit(".", 1)[-1] for n in names}
    try:
        clients = json.loads(subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True,
                                            timeout=1).stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    for c in clients:
        cls = {c.get("class", "").lower(), c.get("initialClass", "").lower()} - {""}
        if cls & names or {x.rsplit(".", 1)[-1] for x in cls} & names:
            return c.get("address")
    return None


def app_info(e: dict) -> Gio.DesktopAppInfo | None:
    for n in (e.get("entry", ""), e.get("app", ""), e.get("app", "").lower()):
        if n:
            try:
                info = Gio.DesktopAppInfo.new(n if n.endswith(".desktop") else n + ".desktop")
            except TypeError:
                info = None
            if info:
                return info
    return None


# ---------- the panel ----------

class Backdrop(Gtk.Window):
    """Invisible full-screen layer under the panel: a click on it means "clicked outside"."""

    def __init__(self, app, on_click):
        super().__init__(application=app, title="hyprbell-backdrop")
        self.add_css_class("backdrop")
        Layer.init_for_window(self)
        Layer.set_namespace(self, "hyprbell-backdrop")
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
        super().__init__(application=app, title="hyprbell")  # resizable: the compositor sizes it to full height
        self.app = app
        Layer.init_for_window(self)
        Layer.set_namespace(self, "hyprbell")
        Layer.set_layer(self, Layer.Layer.OVERLAY)
        # docked to the right screen edge, under the bar, full height
        m, s = hypr_ui.MARGIN, hypr_ui.SURFACE
        for edge in (Layer.Edge.TOP, Layer.Edge.RIGHT, Layer.Edge.BOTTOM):
            Layer.set_anchor(self, edge, True)
        Layer.set_margin(self, Layer.Edge.TOP, s["gapTop"] - m)
        Layer.set_margin(self, Layer.Edge.RIGHT, s["gapRight"] - m)
        Layer.set_margin(self, Layer.Edge.BOTTOM, s["gapRight"] - m)
        hypr_ui._clip_input(self)
        Layer.set_keyboard_mode(self, Layer.KeyboardMode.ON_DEMAND)

        self.filter = "all"
        self.sel = 0
        self.open_key: str | None = None  # key of the expanded row's newest entry
        self.groups: list[dict] = []
        self.rows: list[Gtk.Widget] = []
        self.seen_before = 0.0
        self.fresh: set[str] = set()
        self.undo: list[dict] | None = None
        self.confirm_clear = 0
        self.flash_timer = 0
        self.dnd = False

        self.backdrop = Backdrop(app, lambda: self.get_visible() and self.toggle())
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self._build()

    # ---------- layout ----------

    def _build(self):
        root = vbox(10, vexpand=True)
        root.add_css_class("popover")
        root.add_css_class("bell-panel")
        root.set_size_request(WIDTH, -1)
        self.set_default_size(WIDTH + 2 * hypr_ui.MARGIN, -1)
        self.set_child(root)

        self.subtitle = label("", "subtitle", ellipsize=40)
        self.dnd_btn = button(G["off"], "icon", lambda: self.set_dnd(not self.dnd), "do not disturb (shift d)")
        self.clear_btn = button(G["trash"], "icon", self.clear_all, "clear all (shift x)")
        root.append(hbox(
            10, label(G["bell"], "app-icon", xalign=0.5),
            vbox(0, label("notifications", "title"), self.subtitle, hexpand=True, valign=Gtk.Align.CENTER),
            self.dnd_btn, self.clear_btn,
            button(G["close"], "icon", self.toggle, "close (esc)")))

        self.dnd_switch = Gtk.Switch(active=True, valign=Gtk.Align.CENTER)
        self.dnd_switch.connect("notify::active", lambda sw, _p: not sw.get_active() and self.set_dnd(False))
        txt = label("", "dnd-text", hexpand=True, wrap=True)
        txt.set_markup(f"<b>do not disturb</b>  <span color='{hypr_ui.hex_color('muted')}'>· popups are silenced, "
                       "everything still lands here</span>")
        self.dnd_text = txt
        self.dnd_card = hbox(10, label(G["off"], "dnd-icon"), txt, self.dnd_switch)
        self.dnd_card.add_css_class("dnd-card")
        root.append(self.dnd_card)

        self.search = Gtk.Entry(placeholder_text="search app, title or text", hexpand=True)
        self.search.connect("changed", lambda *_: self._searched())
        self.search_row = hbox(0, self.search)
        self.search_row.add_css_class("search-row")
        root.append(self.search_row)

        self.chips = hbox(4)
        chips_scroll = Gtk.ScrolledWindow(child=self.chips, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                          hscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        chips_scroll.add_css_class("chips")
        self.chips_scroll = chips_scroll
        root.append(chips_scroll)

        self.list = vbox(2)
        self.list.add_css_class("list")
        self.viewport = Gtk.Viewport(child=self.list, scroll_to_focus=False)
        self.scroll = Gtk.ScrolledWindow(child=self.viewport, vexpand=True,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroll.add_css_class("list-scroll")
        root.append(self.scroll)

        foot = vbox(4)
        foot.add_css_class("footer")
        for hints in ([("j/k", "move"), ("⏎", "expand"), ("o", "open"), ("c", "copy"), ("d", "delete")],
                      [("/", "search"), ("tab", "app"), ("⇧d", "dnd"), ("esc", "close")]):
            line = hbox(10)
            for k, t in hints:
                line.append(keyhint(k, t))
            foot.append(line)
        root.append(foot)

    # ---------- data ----------

    def visible_entries(self) -> list[dict]:
        q = self.search.get_text().strip().lower() if self.search_row.get_visible() else ""
        out = []
        for e in reversed(self.app.entries):
            if self.filter != "all" and e.get("app") != self.filter:
                continue
            if q and q not in f"{e.get('app', '')} {e.get('summary', '')} {store.plain(e.get('body', ''))}".lower():
                continue
            out.append(e)
        return out

    def grouped(self, entries: list[dict]) -> list[dict]:
        """Newest first; the same title from the same app on the same day folds into one row."""
        groups: list[dict] = []
        by: dict[tuple, dict] = {}
        for e in entries:
            k = (datetime.fromtimestamp(e["ts"]).date(), e.get("app", ""), e.get("summary", ""))
            g = by.get(k)
            if g:
                g["earlier"].append(e)
            else:
                g = by[k] = {"head": e, "earlier": [], "day": k[0]}
                groups.append(g)
        return groups

    # ---------- render ----------

    def render(self):
        now = time.time()
        entries = self.app.entries
        today = datetime.now().date()
        n_today = sum(1 for e in entries if datetime.fromtimestamp(e["ts"]).date() == today)
        if not self.flash_timer:
            self.subtitle.set_text(f"{n_today} today · {len(entries)} kept · {store.KEEP_DAYS} days"
                                   if entries else "nothing logged yet")
        self.dnd_card.set_visible(self.dnd)
        if self.dnd_switch.get_active() != self.dnd:
            self.dnd_switch.set_active(self.dnd)
        (self.dnd_btn.add_css_class if self.dnd else self.dnd_btn.remove_css_class)("on")
        (self.clear_btn.add_css_class if self.confirm_clear else self.clear_btn.remove_css_class)("warn")
        self.render_chips()
        self.render_list(now)

    def render_chips(self):
        clear(self.chips)
        counts: dict[str, int] = {}
        for e in self.app.entries:
            counts[e.get("app", "")] = counts.get(e.get("app", ""), 0) + 1
        if self.filter != "all" and self.filter not in counts:
            self.filter = "all"
        self.chips_scroll.set_visible(bool(counts))
        for name, n in [("all", len(self.app.entries))] + sorted(counts.items(), key=lambda kv: -kv[1]):
            b = Gtk.Button(child=hbox(6, label(name or "?"), label(str(n), "count")))
            b.add_css_class("chip")
            if name == self.filter:
                b.add_css_class("sel")
            b.connect("clicked", lambda _b, f=name: self.set_filter(f))
            self.chips.append(b)

    def render_list(self, now: float | None = None):
        now = now or time.time()
        clear(self.list)
        self.rows = []
        self.groups = self.grouped(self.visible_entries())
        if not self.groups:
            if self.app.entries:
                self.list.append(self._empty(f'nothing matches "{self.search.get_text().strip()}"',
                                             "esc clears the search", big=False))
            else:
                self.list.append(self._empty("all quiet", "new notifications will show up here",
                                             f"kept for {store.KEEP_DAYS} days"))
            return
        if self.open_key and not any(g["head"].get("key") == self.open_key for g in self.groups):
            self.open_key = None
        self.sel = max(0, min(self.sel, len(self.groups) - 1))
        today = datetime.now().date()
        shown = self.groups[:MAX_ROWS]
        last_day = None
        for i, g in enumerate(shown):
            if g["day"] != last_day:
                last_day = g["day"]
                n = sum(1 for x in shown if x["day"] == last_day)
                self.list.append(hbox(0, label(day_name(last_day, today).upper(), "section", hexpand=True),
                                      label(str(n), "section-meta"), css_classes=["day"]))
            row = self._row(g, i, now)
            self.rows.append(row)
            self.list.append(row)
        if len(self.groups) > MAX_ROWS:
            self.list.append(label(f"{len(self.groups) - MAX_ROWS} older · / to search them", "hint", xalign=0.5))

    def _empty(self, title: str, text: str, small: str = "", big=True) -> Gtk.Widget:
        box = vbox(6, valign=Gtk.Align.START)
        box.add_css_class("empty")
        if big:
            box.append(label(G["bell"], "empty-icon", xalign=0.5, hexpand=False))
            box.get_first_child().set_halign(Gtk.Align.CENTER)
        box.append(label(title, "empty-title", xalign=0.5))
        box.append(label(text, "empty-text", xalign=0.5))
        if small:
            box.append(label(small, "hint", xalign=0.5))
        return box

    def _row(self, g: dict, i: int, now: float) -> Gtk.Widget:
        e = g["head"]
        is_open = e.get("key") == self.open_key
        live = self.app.is_live(e)

        tile = Gtk.Overlay(valign=Gtk.Align.START)
        tile.set_child(label(tile_text(e.get("app", "")), "tile", f"c-{tile_color(e)}", xalign=0.5))
        if e["ts"] > self.seen_before:
            dot = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.START)
            dot.add_css_class("dot")
            tile.add_overlay(dot)

        body = store.plain(e.get("body", ""))
        main = vbox(2, hexpand=True)
        main.append(hbox(8, label(e.get("summary") or e.get("app", ""), "sum", hexpand=True, ellipsize=34),
                         label(when(e["ts"], now), "tm")))
        if body:
            main.append(label(body, "body", wrap=True, lines=0 if is_open else 2))
        if is_open and g["earlier"]:
            earlier = vbox(3)
            earlier.add_css_class("earlier")
            for x in g["earlier"]:
                earlier.append(hbox(6, label(datetime.fromtimestamp(x["ts"]).strftime("%H:%M"), "tm"),
                                    label(store.plain(x.get("body", "")) or x.get("summary", ""), "body",
                                          wrap=True, hexpand=True)))
            main.append(earlier)

        meta = hbox(6, label(e.get("app", ""), "meta"))
        if e.get("urgency") == 2:
            meta.append(label("critical", "badge", "crit"))
        if live:
            meta.append(label("on screen", "badge", "live"))
        if e.get("dnd"):
            meta.append(label("dnd", "badge", "dnd"))
        if g["earlier"]:
            meta.append(label(f"×{len(g['earlier']) + 1}", "badge", "stack"))
        main.append(meta)

        if is_open:
            main.append(self._actions(e, live))

        row = hbox(10, tile, main)
        row.add_css_class("row")
        row.add_css_class("note")
        if i == self.sel:
            row.add_css_class("selected")
        if e.get("urgency") == 2:
            row.add_css_class("crit")
        if e.get("key") in self.fresh:
            row.add_css_class("fresh")
        click = Gtk.GestureClick()
        click.connect("released", lambda *_a, idx=i: self.click_row(idx))
        row.add_controller(click)
        return row

    def _actions(self, e: dict, live: bool) -> Gtk.Widget:
        acts = hbox(6)
        acts.add_css_class("acts")
        if live and e.get("actions"):
            first = True
            for key, text in e["actions"]:
                acts.append(keyed("o" if first else "", text or key, "primary" if first else "",
                                  lambda k=key: self.invoke(e, k)))
                first = False
        elif is_screenshot(e):
            acts.append(keyed("o", "annotate in satty", "primary", lambda: self.open_entry(e)))
        elif window_for(e) or app_info(e):
            acts.append(keyed("o", f"open {e.get('app', 'app')}", "", lambda: self.open_entry(e)))
        acts.append(keyed("c", "copy", "", lambda: self.copy_entry(e)))
        acts.append(keyed("d", "delete", "danger", self.delete_selected))
        for b in list_children(acts):
            if isinstance(b, Gtk.Button):
                k = b.get_child().get_first_child()
                if not k.get_text():
                    k.set_visible(False)
        return acts

    def restyle_selection(self):
        for i, r in enumerate(self.rows):
            (r.add_css_class if i == self.sel else r.remove_css_class)("selected")
        if 0 <= self.sel < len(self.rows):
            self.viewport.scroll_to(self.rows[self.sel], None)

    def flash(self, text: str):
        self.subtitle.set_text(text)
        if self.flash_timer:
            GLib.source_remove(self.flash_timer)
        self.flash_timer = GLib.timeout_add(2200, self._unflash)

    def _unflash(self):
        self.flash_timer = 0
        self.render()
        return False

    # ---------- actions ----------

    def current(self) -> dict | None:
        return self.groups[self.sel] if 0 <= self.sel < len(self.groups) else None

    def click_row(self, i: int):
        g = self.groups[i] if i < len(self.groups) else None
        if not g:
            return
        self.sel = i
        key = g["head"].get("key")
        self.open_key = None if self.open_key == key else key
        self.render_list()
        self.restyle_selection()

    def expand(self):
        if self.current():
            self.click_row(self.sel)

    def move(self, d: int):
        if not self.groups:
            return
        self.sel = max(0, min(len(self.groups) - 1, min(self.sel, MAX_ROWS - 1) + d))
        self.sel = min(self.sel, len(self.rows) - 1)
        if self.open_key:  # the expanded row follows the selection
            self.open_key = self.groups[self.sel]["head"].get("key")
            self.render_list()
        self.restyle_selection()

    def set_filter(self, name: str):
        self.filter = name
        self.sel = 0
        self.open_key = None
        self.render()
        for b in list_children(self.chips):
            if "sel" in b.get_css_classes():
                adj = self.chips_scroll.get_hadjustment()
                ok, rect = b.compute_bounds(self.chips)
                if ok:
                    adj.clamp_page(rect.get_x(), rect.get_x() + rect.get_width())

    def next_filter(self, d: int = 1):
        names = [b.get_child().get_first_child().get_text() for b in list_children(self.chips)]
        if names:
            i = names.index(self.filter) if self.filter in names else 0
            self.set_filter(names[(i + d) % len(names)])

    def _searched(self):
        self.sel = 0
        self.open_key = None
        self.render_list()

    def show_search(self):
        self.search_row.set_visible(True)
        self.search.grab_focus()

    def hide_search(self):
        self.search.set_text("")
        self.search_row.set_visible(False)
        self.set_focus(None)
        self.render_list()

    def open_current(self):
        g = self.current()
        if not g:
            return
        e = g["head"]
        if self.app.is_live(e) and e.get("actions"):
            keys = [k for k, _ in e["actions"]]
            self.invoke(e, "default" if "default" in keys else keys[0])
        else:
            self.open_entry(e)

    def invoke(self, e: dict, action: str):
        mako_call("InvokeAction", GLib.Variant("(us)", (e.get("id", 0), action)))
        self.toggle()

    def open_entry(self, e: dict):
        shot = is_screenshot(e)
        if shot:
            run("satty", "-f", str(shot), "-o", str(shot))
            return self.toggle()
        addr = window_for(e)
        if addr:
            run("hyprctl", "dispatch", "focuswindow", f"address:{addr}")
            return self.toggle()
        info = app_info(e)
        if info:
            try:
                info.launch([], None)
            except GLib.Error:
                return self.flash(f"could not open {e.get('app', '')}")
            return self.toggle()
        self.flash(f"nothing to open for {e.get('app', '')}")

    def copy_entry(self, e: dict | None = None):
        e = e or (self.current() or {}).get("head")
        if not e:
            return
        body = store.plain(e.get("body", ""))
        run("wl-copy", e.get("summary", "") + ("\n" + body if body else ""))
        self.flash("copied title and text")

    def delete_selected(self):
        g = self.current()
        if not g:
            return
        gone = [g["head"], *g["earlier"]]
        self.undo = gone
        self.app.remove(gone)
        self.open_key = None
        self.render()
        self.flash("deleted · u to undo")

    def undo_delete(self):
        if self.undo:
            self.app.restore(self.undo)
            self.undo = None
            self.render()
            self.flash("restored")

    def clear_all(self):
        if not self.app.entries:
            return
        if not self.confirm_clear:
            self.confirm_clear = GLib.timeout_add(3000, self._cancel_clear)
            self.render()
            self.flash(f"press again to delete all {len(self.app.entries)}")
            return
        GLib.source_remove(self.confirm_clear)
        self.confirm_clear = 0
        self.undo = list(self.app.entries)
        self.app.remove(self.undo)
        self.render()
        self.flash("history cleared · u to undo")

    def _cancel_clear(self):
        self.confirm_clear = 0
        self.render()
        return False

    def set_dnd(self, on: bool):
        if on == self.dnd:
            return
        set_dnd(on)
        self.dnd = on
        self.render()
        self.flash("do not disturb on · still logged here" if on else "do not disturb off")

    # ---------- keys ----------

    def _on_key(self, _ctrl, keyval, _code, state):
        name = Gdk.keyval_name(keyval) or ""
        typing = self.search.has_focus() or (self.search_row.get_visible() and self.search.get_focus_child() is not None)
        if name == "Escape":
            if self.search.get_text():
                self.search.set_text("")
            elif self.search_row.get_visible():
                self.hide_search()
            elif self.open_key:
                self.open_key = None
                self.render_list()
                self.restyle_selection()
            else:
                self.toggle()
            return True
        if name in ("Down", "Up") or (not typing and name in ("j", "k")):
            self.move(1 if name in ("Down", "j") else -1)
            return True
        if name in ("Return", "KP_Enter"):
            if typing:
                self.set_focus(None)
            self.expand()
            return True
        if name in ("Tab", "ISO_Left_Tab"):
            self.next_filter(-1 if name == "ISO_Left_Tab" else 1)
            return True
        if typing:
            return False
        actions = {"space": self.expand, "o": self.open_current, "c": self.copy_entry, "d": self.delete_selected,
                   "Delete": self.delete_selected, "u": self.undo_delete, "slash": self.show_search,
                   "D": lambda: self.set_dnd(not self.dnd), "X": self.clear_all, "q": self.toggle,
                   "g": lambda: self.move(-10**6), "G": lambda: self.move(10**6)}
        fn = actions.get(name)
        if fn:
            fn()
            return True
        return False

    # ---------- visibility ----------

    def arrived(self, e: dict):
        """A new notification while the panel is open: show it at the top, highlighted."""
        if not self.get_visible():
            return
        store.mark_seen()
        key = e.get("key")
        self.fresh.add(key)
        if self.sel or self.open_key:
            self.sel += 1
        self.render()
        GLib.timeout_add(1600, self._unfresh, key)

    def _unfresh(self, key):
        self.fresh.discard(key)
        for r in self.rows:
            r.remove_css_class("fresh")
        return False

    def toggle(self):
        if self.get_visible():
            self.set_visible(False)
            self.backdrop.set_visible(False)
            OPEN_FLAG.unlink(missing_ok=True)
            return
        hypr_ui.solo("hyprbell")
        self.seen_before = store.seen()
        store.mark_seen()
        self.dnd = dnd_on()
        self.filter, self.sel, self.open_key, self.undo = "all", 0, None, None
        self.search.set_text("")
        self.search_row.set_visible(False)
        self.render()
        self.backdrop.present()
        self.present()
        self.set_focus(None)
        if self.rows:
            self.viewport.scroll_to(self.rows[0], None)
        OPEN_FLAG.touch()


def list_children(box) -> list:
    out = []
    c = box.get_first_child()
    while c:
        out.append(c)
        c = c.get_next_sibling()
    return out


# ---------- the app: logger + panel ----------

class App(Gtk.Application):
    def __init__(self, sock: Path):
        super().__init__(application_id="dev.edu.Hyprbell", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.sock_path = sock
        self.entries: list[dict] = []
        self.live: set[int] = set()
        self.save_timer = 0
        self.monitor = Monitor(self.on_notify, self.on_closed)

    # ---------- history ----------

    def is_live(self, e: dict) -> bool:
        return bool(e.get("id")) and not e.get("closed") and e["id"] in self.live

    def _save_soon(self):
        if not self.save_timer:
            self.save_timer = GLib.timeout_add(800, self._save)

    def _save(self):
        self.save_timer = 0
        store.save(self.entries)
        return False

    def on_notify(self, n: dict):
        if n["app"].lower() in store.SKIP_APPS:
            return
        e = {k: n[k] for k in ("ts", "id", "app", "icon", "summary", "body", "actions", "urgency", "entry")}
        e["key"] = f"{int(n['ts'] * 1000):x}{n['id']:x}"
        if dnd_on():
            e["dnd"] = True
        if n["id"]:
            self.live.add(n["id"])
        old = None
        if n["replaces"]:  # an update to a notification still on screen: keep one row
            old = next((x for x in reversed(self.entries) if x.get("id") == n["replaces"] and not x.get("closed")), None)
        if old is not None:
            old.update({k: v for k, v in e.items() if k != "key"})
            self._save_soon()
            e = old
        else:
            self.entries.append(e)
            if len(self.entries) > store.KEEP_MAX + 50:
                self.entries = store.prune(self.entries)
                self._save_soon()
            else:
                store.append(e)
        self.panel.arrived(e)

    def on_closed(self, nid: int):
        self.live.discard(nid)
        changed = False
        for e in reversed(self.entries):
            if e.get("id") == nid and not e.get("closed"):
                e["closed"] = True
                changed = True
                break
        if changed:
            self._save_soon()
            if self.panel.get_visible():
                self.panel.render_list()
                self.panel.restyle_selection()

    def remove(self, gone: list[dict]):
        keys = {e.get("key") for e in gone}
        self.entries = [e for e in self.entries if e.get("key") not in keys]
        self._save()

    def restore(self, back: list[dict]):
        keys = {e.get("key") for e in self.entries}
        self.entries = sorted(self.entries + [e for e in back if e.get("key") not in keys], key=lambda e: e["ts"])
        self._save()

    def _sync_live(self):
        """At start: which notifications are still on screen (mako ids reset when mako restarts)."""
        r = mako_call("ListNotifications", None, "(aa{sv})")
        self.live = {int(n.get("id", 0)) for n in (r[0] if r else [])}
        for e in self.entries:
            if not e.get("closed") and e.get("id") not in self.live:
                e["closed"] = True

    def _daily(self):
        pruned = store.prune(self.entries)
        if len(pruned) != len(self.entries):
            self.entries = pruned
            self._save()
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
        c = cmd[0]
        p = self.panel
        if c == "toggle":
            p.toggle()
        elif c == "open" and not p.get_visible():
            p.toggle()
        elif c == "close" and p.get_visible():
            p.toggle()
        elif c == "key" and p.get_visible() and len(cmd) > 1:  # for tools/screenshots: press keys by name
            for name in cmd[1:]:
                kv = Gdk.keyval_from_name(name)
                if kv:
                    p._on_key(None, kv, 0, 0)
        elif c == "search" and p.get_visible():  # for tools/screenshots: hyprbell search <text>
            p.show_search()
            p.search.set_text(" ".join(cmd[1:]))
        elif c == "quit":
            self.quit()

    # ---------- lifecycle ----------

    def do_startup(self):
        Gtk.Application.do_startup(self)
        hypr_ui.load_css(HERE / "style.css")
        self.hold()
        self.entries = store.prune(store.load())
        self._sync_live()
        store.save(self.entries)
        self.panel = Panel(self)
        self._serve()
        if not os.environ.get("HYPRBELL_NO_MONITOR"):  # tools/screenshots runs a second, demo copy
            self.monitor.start()
        GLib.timeout_add_seconds(3600, self._daily)

    def do_activate(self):
        pass

    def do_shutdown(self):
        if self.save_timer:
            self._save()
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
