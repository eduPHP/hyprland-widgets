"""hyprtheme: resident GTK4 layer-shell theme picker in the hypr-ui look.
Driven over a unix socket by the `hyprtheme` script.

The picker previews a theme inside itself only: it repaints in the highlighted theme's colours
and shows that theme's wallpaper behind it. Nothing on the desktop changes until Enter.
"""

from __future__ import annotations

import hashlib
import heapq
import itertools
import os
import random
import socket
import sys
import threading
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

WINDOWED = bool(os.environ.get("HYPRTHEME_WINDOWED"))
if not WINDOWED:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as Layer  # noqa: E402

sys.path.insert(0, os.path.expanduser("~/.config/hypr-ui/gtk"))
import hypr_ui  # noqa: E402  shared look: tokens, base stylesheet, solo()
import themes as th  # noqa: E402

HERE = Path(__file__).resolve().parent
OPEN_FLAG = th.RUN / "hyprtheme.open"
CARD_W, CARD_H = 960, 640
COLS = 4
TILE_H = 132
THUMB_W = 480  # cached wallpaper thumbnails, wide enough for a tile at 2x
TOAST_S = 10
PREVIEW_MS = 300  # the selection must rest this long before the desktop follows it
FONT = hypr_ui.TOKENS["font"]["family"]
FILTERS = [("all", "All"), ("dark", "Dark"), ("light", "Light"), ("favs", "★ Favorites")]
G = {"palette": "\U000f03d8", "search": "\U000f0349", "undo": "\U000f054c"}


def log(*parts) -> None:
    """One line on stderr (the daemon's log), so an apply that goes wrong leaves a trace."""
    print(GLib.DateTime.new_now_local().format("%H:%M:%S"), "hyprtheme:", *parts, file=sys.stderr, flush=True)


def rgb(hexc: str, a: float = 1.0) -> tuple[float, float, float, float]:
    h = hexc.lstrip("#")
    return (*(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)), a)


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


def button(text: str, css: str, cb) -> Gtk.Button:
    b = Gtk.Button(label=text, valign=Gtk.Align.CENTER, focus_on_click=False, can_focus=False)
    b.add_css_class(css)
    b.connect("clicked", lambda *_: cb())
    return b


def keyhint(k: str, text: str) -> Gtk.Box:
    return hbox(4, label(k, "key"), label(text, "hint"))


def rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    cr.arc(x + r, y + r, r, 3.1416, 4.7124)
    cr.close_path()


def paint_cover(cr, pix: GdkPixbuf.Pixbuf | None, x, y, w, h, fallback: tuple[str, str]):
    """Fill the box with the image, cropped to cover it; a gradient of two palette colours without one."""
    cr.save()
    cr.rectangle(x, y, w, h)
    cr.clip()
    if pix is None:
        g = cairo.LinearGradient(x, y, x + w, y + h)
        g.add_color_stop_rgba(0, *rgb(fallback[0]))
        g.add_color_stop_rgba(1, *rgb(fallback[1]))
        cr.set_source(g)
        cr.paint()
    else:
        s = max(w / pix.get_width(), h / pix.get_height())
        cr.translate(x + (w - pix.get_width() * s) / 2, y + (h - pix.get_height() * s) / 2)
        cr.scale(s, s)
        Gdk.cairo_set_source_pixbuf(cr, pix, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()
    cr.restore()


def text(cr, s: str, x, y, px: float, color: str, bold=False, width: int | None = None):
    layout = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription.from_string(FONT)
    fd.set_absolute_size(px * Pango.SCALE)
    fd.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.MEDIUM)
    layout.set_font_description(fd)
    layout.set_text(s, -1)
    if width:
        layout.set_width(width * Pango.SCALE)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
    cr.set_source_rgba(*rgb(color))
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)
    return layout.get_pixel_size()


# ---------- images: wallpaper thumbnails, cached on disk, decoded off the main thread ----------

class Images:
    """Wallpaper thumbnails (cached in ~/.cache/hyprtheme), loaded by a worker thread in priority
    order. `on_ready(path)` runs on the main thread."""

    def __init__(self, on_ready):
        self.on_ready = on_ready
        self.thumbs: dict[Path, GdkPixbuf.Pixbuf | None] = {}
        self.queue: list = []
        self.queued: set = set()
        self.count = itertools.count()
        self.cv = threading.Condition()
        threading.Thread(target=self._work, daemon=True).start()
        (th.CACHE / "thumbs").mkdir(parents=True, exist_ok=True)

    def thumb(self, path: Path, prio: int = 5) -> GdkPixbuf.Pixbuf | None:
        if path in self.thumbs:
            return self.thumbs[path]
        with self.cv:
            if path not in self.queued:
                self.queued.add(path)
                heapq.heappush(self.queue, (prio, next(self.count), path))
                self.cv.notify()
        return None

    def _work(self):
        while True:
            with self.cv:
                while not self.queue:
                    self.cv.wait()
                _, _, path = heapq.heappop(self.queue)
            try:
                pix = self._thumb(path)
            except (GLib.Error, OSError) as e:
                log(f"{path}: {getattr(e, 'message', e)}")
                pix = None
            GLib.idle_add(self._done, path, pix)

    @staticmethod
    def _thumb(path: Path) -> GdkPixbuf.Pixbuf:
        st = path.stat()
        key = hashlib.sha1(f"{path}:{st.st_mtime_ns}:{st.st_size}:{THUMB_W}".encode()).hexdigest()
        cached = th.CACHE / "thumbs" / f"{key}.png"
        if cached.exists():
            return GdkPixbuf.Pixbuf.new_from_file(str(cached))
        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), THUMB_W, -1, True)
        tmp = cached.with_suffix(".tmp")
        pix.savev(str(tmp), "png", [], [])
        os.replace(tmp, cached)
        return pix

    def _done(self, path, pix):
        with self.cv:
            self.queued.discard(path)
        self.thumbs[path] = pix
        self.on_ready(path)
        return False


# ---------- tiles ----------

class Tile(Gtk.DrawingArea):
    """One theme, drawn from its own palette: a wallpaper crop with a mini bar and terminal on it,
    the name, a favourite star and the six accent swatches. The ring uses the picker's colours."""

    STAR = 18  # clickable square at the right of the name row

    def __init__(self, picker: "Picker", theme: th.Theme):
        super().__init__(focusable=False, hexpand=True)
        self.picker, self.t = picker, theme
        self.set_size_request(-1, TILE_H)
        self.set_draw_func(self._draw)
        click = Gtk.GestureClick()
        click.connect("pressed", self._pressed)
        self.add_controller(click)
        self.set_cursor(Gdk.Cursor.new_from_name("pointer"))

    def _pressed(self, _g, n, x, y):
        w = self.get_width()
        if w - 6 - self.STAR <= x <= w - 4 and y >= TILE_H - 40:
            self.picker.toggle_favorite(self.t)
        elif n >= 2:
            self.picker.highlight(self.t)
            self.picker.apply()
        else:
            self.picker.highlight(self.t)

    def _draw(self, _a, cr, w, h):
        t, c, p = self.t, self.t.colors, self.picker
        sel = p.hi is t
        ring = p.colors["blue"] if sel else p.colors["line"]
        top = 84
        rounded(cr, 1, 1, w - 2, h - 2, 10)
        cr.save()
        cr.clip()
        cr.set_source_rgba(*rgb(c["surface"]))
        cr.paint()
        pix = p.images.thumb(p.wallpaper(t)) if p.wallpaper(t) else None
        paint_cover(cr, pix, 0, 0, w, top, (c["bg"], c["blue"]))
        # mini waybar
        rounded(cr, 6, 6, w - 12, 9, 3)
        cr.set_source_rgba(*rgb(c["bg"]))
        cr.fill()
        for i, col in enumerate((c["blue"], c["faint"], c["faint"])):
            cr.rectangle(10 + i * 8, 8, 5, 5)
            cr.set_source_rgba(*rgb(col))
            cr.fill()
        # mini terminal
        rounded(cr, 10, top - 46, 58, 38, 4)
        cr.set_source_rgba(*rgb(c["bg"]))
        cr.fill_preserve()
        cr.set_source_rgba(*rgb(c["frame"]))
        cr.set_line_width(1)
        cr.stroke()
        for i, (col, frac) in enumerate(((t.swatches[0], 0.7), (t.swatches[2], 0.45), (c["fg"], 0.85),
                                         (t.swatches[4], 0.3))):
            rounded(cr, 15, top - 41 + i * 7, 48 * frac, 3, 1.5)
            cr.set_source_rgba(*rgb(col))
            cr.fill()
        if t.id == p.current:
            cr.save()
            fd_w = 50
            rounded(cr, w - fd_w - 6, top - 20, fd_w, 14, 4)
            cr.set_source_rgba(*rgb(c["blue"]))
            cr.fill()
            text(cr, "CURRENT", w - fd_w - 6 + 6, top - 19, 9, c["ink"], bold=True)
            cr.restore()
        # name, star, swatches
        text(cr, t.title, 9, top + 7, 11, c["fg"], width=w - 18 - self.STAR)
        fav = t.id in p.favs
        self._star(cr, w - 6 - self.STAR / 2 - 2, top + 14, t.swatches[1] if fav else c["faint"], fav)
        sw = (w - 18 - 3 * 5) / 6
        for i, col in enumerate(t.swatches):
            rounded(cr, 9 + i * (sw + 3), h - 15, sw, 6, 2)
            cr.set_source_rgba(*rgb(col))
            cr.fill()
        cr.restore()
        rounded(cr, 1, 1, w - 2, h - 2, 10)
        cr.set_source_rgba(*rgb(ring))
        cr.set_line_width(2)
        cr.stroke()
        if sel:
            rounded(cr, -1, -1, w + 2, h + 2, 12)
            cr.set_source_rgba(*rgb(ring, 0.25))
            cr.set_line_width(2)
            cr.stroke()

    @staticmethod
    def _star(cr, cx, cy, color, filled):
        import math

        cr.new_path()
        for i in range(10):
            r = 6 if i % 2 == 0 else 2.6
            a = -math.pi / 2 + i * math.pi / 5
            cr.line_to(cx + r * math.cos(a), cy + r * math.sin(a))
        cr.close_path()
        cr.set_source_rgba(*rgb(color))
        if filled:
            cr.fill()
        else:
            cr.set_line_width(1.4)
            cr.stroke()


class WallThumb(Gtk.DrawingArea):
    """One wallpaper of the highlighted theme in the side column."""

    def __init__(self, picker: "Picker", i: int):
        super().__init__(focusable=False, hexpand=True)
        self.picker, self.i = picker, i
        self.set_size_request(-1, 74)
        self.set_draw_func(self._draw)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda _g, n, *_: picker.pick_wallpaper(i, apply=n >= 2))
        self.add_controller(click)
        self.set_cursor(Gdk.Cursor.new_from_name("pointer"))

    def _draw(self, _a, cr, w, h):
        p, t = self.picker, self.picker.hi
        if t is None or self.i >= len(t.backgrounds):
            return
        sel = self.i == p.wp_index(t)
        rounded(cr, 1, 1, w - 2, h - 2, 7)
        cr.save()
        cr.clip()
        paint_cover(cr, p.images.thumb(t.backgrounds[self.i], 1), 0, 0, w, h, (t.colors["bg"], t.colors["blue"]))
        if not sel:
            cr.set_source_rgba(0, 0, 0, 0.25)
            cr.paint()
        cr.restore()
        if sel:
            rounded(cr, 1, 1, w - 2, h - 2, 7)
            cr.set_source_rgba(*rgb(p.colors["blue"]))
            cr.set_line_width(2)
            cr.stroke()


# ---------- the picker ----------

class Picker(Gtk.ApplicationWindow):
    def __init__(self, app: "App"):
        super().__init__(application=app, title="hyprtheme", resizable=False)
        self.app = app
        if WINDOWED:
            self.set_default_size(1440, 900)
        else:
            Layer.init_for_window(self)
            Layer.set_namespace(self, "hyprtheme")
            hypr_ui.layer(self, Layer, Layer.Layer.OVERLAY)
            for e in (Layer.Edge.TOP, Layer.Edge.BOTTOM, Layer.Edge.LEFT, Layer.Edge.RIGHT):
                Layer.set_anchor(self, e, True)  # whole screen, below waybar (its exclusive zone is kept)
            # the rest of the desktop stays usable: clicks outside the card go through to it, and
            # the picker holds the keyboard only while it has focus
            Layer.set_keyboard_mode(self, Layer.KeyboardMode.ON_DEMAND)
            self.connect("realize", lambda w: w.get_surface().connect("layout", self._clip_input))

        self.themes: list[th.Theme] = []
        self.by_id: dict[str, th.Theme] = {}
        self.visible_list: list[th.Theme] = []
        self.tiles: dict[str, Tile] = {}
        self.hi: th.Theme | None = None
        self.current: str | None = None
        self.current_bg: Path | None = None
        self.wp: dict[str, int] = {}  # wallpaper picked per theme in this session
        self.favs: set[str] = set()
        self.filter = "all"
        self.colors = dict(hypr_ui.TOKENS["color"])
        self.images = Images(self._image_ready)
        # live preview: the look when the picker opened, the one the desktop shows now, and
        # whether omarchy-theme is busy (previews wait for the last one, the newest wins)
        self.orig: tuple[str | None, Path | None] = (None, None)
        self.live: tuple[str | None, Path | None] = (None, None)
        self.busy = False
        self.preview_timer = 0

        dim = Gtk.Box(hexpand=True, vexpand=True)
        dim.add_css_class("dim")  # a light veil over the desktop, which shows the preview itself
        self.card = self._build_card()
        root = Gtk.Overlay()
        root.set_child(dim)
        root.add_overlay(self.card)
        self.set_child(root)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("close-request", lambda *_: self.cancel() or True)

    # ----- layout -----

    def _build_card(self) -> Gtk.Box:
        self.sub = label("", "subtitle", ellipsize=40)
        self.name = label("", "title", ellipsize=16)
        self.state = label("", "badge")
        self.spin = Gtk.Spinner(valign=Gtk.Align.CENTER, visible=False)
        title = vbox(2, hbox(8, self.name, self.state, self.spin), self.sub, valign=Gtk.Align.CENTER)
        title.set_size_request(320, -1)
        icon = label(G["palette"], "app-icon", xalign=0.5)
        self.entry = Gtk.Entry(placeholder_text="type to filter, fuzzy", hexpand=True, valign=Gtk.Align.CENTER)
        self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "system-search-symbolic")
        self.entry.connect("changed", lambda *_: self.refilter())
        chips = hbox(2, valign=Gtk.Align.CENTER)
        chips.add_css_class("tabs")
        self.chips = {}
        for key, text_ in FILTERS:
            b = button(text_, "tab", lambda k=key: self.set_filter(k))
            b.add_css_class("chip")
            chips.append(b)
            self.chips[key] = b
        header = hbox(12, icon, title, self.entry, chips)
        header.add_css_class("picker-header")

        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=COLS,
                                max_children_per_line=COLS, homogeneous=True, column_spacing=12, row_spacing=12,
                                valign=Gtk.Align.START, can_focus=False)
        self.grid.add_css_class("grid")
        self.empty = label("No theme matches. Esc clears the filter.", "empty", xalign=0.5)
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True, vexpand=True,
                                         can_focus=False)
        self.scroll.set_child(vbox(0, self.grid, self.empty))

        self.wp_note = label("", "section-meta", xalign=1.0, hexpand=True)
        self.walls = vbox(8)
        wscroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, can_focus=False)
        wscroll.set_child(self.walls)
        self.wscroll = wscroll
        self.term = label("", "term")
        self.term.set_use_markup(True)
        actions = hbox(8, button("Cancel", "ghost", self.cancel), button("Apply", "primary", self.apply),
                       homogeneous=True)
        side = vbox(12, hbox(0, label("WALLPAPER", "section"), self.wp_note), wscroll, self.term, actions)
        side.add_css_class("side")
        side.set_size_request(260, -1)
        side.set_hexpand(False)  # the wallpaper thumbs expand inside it, not the column itself

        self.count = label("", "hint", xalign=1.0, hexpand=True)
        footer = hbox(14, keyhint("←↑↓→", "preview"), keyhint("tab", "wallpaper"),
                      keyhint("enter", "apply"), keyhint("^f", "favorite"), keyhint("^r", "random"),
                      keyhint("^1-4", "filter"), keyhint("esc", "revert and close"), self.count)
        footer.add_css_class("footer")

        card = vbox(0, header, hbox(0, self.scroll, side, vexpand=True), footer,
                    halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        card.add_css_class("picker")
        card.set_size_request(CARD_W, CARD_H)
        return card

    # ----- data -----

    def wp_index(self, t: th.Theme) -> int:
        if t.id not in self.wp:
            bg = self.current_bg if t.id == self.current else th.ot.remembered_background(t.id, t.dir)
            self.wp[t.id] = t.backgrounds.index(bg) if bg in t.backgrounds else 0
        return self.wp[t.id]

    def wallpaper(self, t: th.Theme) -> Path | None:
        return t.backgrounds[self.wp_index(t)] if t.backgrounds else None

    def reload(self):
        self.themes = th.load_themes()
        self.by_id = {t.id: t for t in self.themes}
        self.current = th.ot.current()
        self.current_bg = th.ot.current_background()
        self.favs = th.favorites()
        self.wp = {}
        self.tiles = {t.id: Tile(self, t) for t in self.themes}
        for t in self.themes:  # the tiles' wallpapers first, then everything else in the background
            if self.wallpaper(t):
                self.images.thumb(self.wallpaper(t), 2)

    def refilter(self, keep: th.Theme | None = None):
        self.visible_list = th.search(self.themes, self.entry.get_text(), self.filter, self.favs)
        self.grid.remove_all()
        for t in self.visible_list:
            self.grid.append(self.tiles[t.id])
        self.empty.set_visible(not self.visible_list)
        self.count.set_text(f"{len(self.visible_list)} of {len(self.themes)} themes")
        keep = keep or self.hi
        if self.visible_list and (self.entry.get_text() or keep not in self.visible_list):
            keep = self.visible_list[0]  # typing jumps to the best match
        if keep is not None:
            self.highlight(keep)

    def set_filter(self, key: str):
        self.filter = key
        for k, b in self.chips.items():
            (b.add_css_class if k == key else b.remove_css_class)("active")
        self.refilter()

    # ----- preview -----

    def highlight(self, t: th.Theme):
        prev, self.hi = self.hi, t
        self.colors = {**hypr_ui.TOKENS["color"], **t.colors}
        self.app.paint(self.colors)
        n = len(t.backgrounds)
        self.sub.set_text(f"{t.mode} \u00b7 {n} wallpaper{'s' * (n != 1)}")
        self.name.set_text(t.title)
        self.term.set_markup(self._term_markup(t))
        self._fill_walls(t, rebuild=prev is not t)
        self._schedule_preview()
        self._status()
        for x in (prev, t):
            if x is not None and x.id in self.tiles:
                self.tiles[x.id].queue_draw()
        GLib.idle_add(self._scroll_to, t)

    def _term_markup(self, t: th.Theme) -> str:
        c, sw = t.colors, t.swatches
        esc = GLib.markup_escape_text
        return "\n".join([
            f"<span color='{sw[2]}'>~/code</span> <span color='{c['blue']}'>❯</span> git status",
            f"<span color='{sw[0]}'>modified:</span> picker.py",
            f"<span color='{sw[2]}'>new file:</span> theme.json",
            f"<span color='{c['muted']}'># {esc(t.title)}</span>",
        ])

    def _fill_walls(self, t: th.Theme, rebuild: bool):
        if rebuild:
            while (ch := self.walls.get_first_child()) is not None:
                self.walls.remove(ch)
            for i, bg in enumerate(t.backgrounds):
                self.walls.append(WallThumb(self, i))
                self.images.thumb(bg, 1)
            self.wscroll.get_vadjustment().set_value(0)
        else:
            ch = self.walls.get_first_child()
            while ch is not None:
                ch.queue_draw()
                ch = ch.get_next_sibling()
        n, i = len(t.backgrounds), self.wp_index(t)
        if not n:
            self.wp_note.set_text("none")
            return
        remembered = th.ot.remembered_background(t.id, t.dir)
        note = f"{i + 1} of {n}"
        if t.id == self.current and self.wallpaper(t) == self.current_bg:
            note += " · in use"
        elif self.wallpaper(t) == remembered and t.id in (th.ot.load_state().get("backgrounds") or {}):
            note += " · remembered"
        self.wp_note.set_text(note)

    # ----- live preview: the whole desktop follows the selection -----

    def want(self) -> tuple[str | None, Path | None]:
        return (self.hi.id, self.wallpaper(self.hi)) if self.hi else (None, None)

    def _schedule_preview(self):
        if self.preview_timer:
            GLib.source_remove(self.preview_timer)
        # only once the selection rests, so holding an arrow key doesn't reload waybar on every step
        self.preview_timer = GLib.timeout_add(PREVIEW_MS, self._preview)

    def _preview(self):
        self.preview_timer = 0
        want = self.want()
        if not self.get_visible() or want[0] is None or want == self.live or self.busy:
            return False  # a busy run re-checks when it finishes
        args = ["set", want[0], "--preview"] + (["--background", str(want[1])] if want[1] else [])
        self._run(args, want, after=self._preview)
        return False

    def _run(self, args: list[str], look, after=None, done=None):
        self.busy = True
        self._status()

        def finished(ok):
            self.busy = False
            if ok:
                self.live = look
            self._status()
            if done:
                done(ok)
            elif after:
                after()

        self.app.run_theme(args, done=finished)

    def _status(self):
        """The badge next to the name: what the desktop is showing, and a spinner while it changes."""
        self.spin.set_visible(self.busy)
        self.spin.set_spinning(self.busy)
        t = self.hi
        if t is None:
            return
        if self.busy:
            text, hot = "applying\u2026", True
        elif self.want() == self.orig:
            text, hot = "in use", False
        elif self.want() == self.live:
            text, hot = "previewing \u00b7 enter keeps it, esc reverts", True
        else:
            text, hot = "preview in a moment", True
        self.state.set_text(text)
        (self.state.add_css_class if hot else self.state.remove_css_class)("preview")

    def _clip_input(self, surface, width, height):
        """Only the card takes input; the veil around it passes clicks to the desktop."""
        a = self.card.get_allocation()
        if a.width <= 1:
            a = Gdk.Rectangle()
            a.x, a.y = (width - CARD_W) // 2, (height - CARD_H) // 2
            a.width, a.height = CARD_W, CARD_H
        surface.set_input_region(cairo.Region(cairo.RectangleInt(a.x, a.y, a.width, a.height)))

    def _image_ready(self, path: Path):
        t = self.hi
        for tile in self.tiles.values():
            if tile.t.backgrounds and self.wallpaper(tile.t) == path:
                tile.queue_draw()
        if t and path in t.backgrounds:
            self._fill_walls(t, rebuild=False)

    def _scroll_to(self, t: th.Theme):
        tile = self.tiles.get(t.id)
        if tile is None or tile.get_parent() is None:
            return False
        ok, b = tile.compute_bounds(self.grid)
        if not ok:
            return False
        adj = self.scroll.get_vadjustment()
        y, h = b.get_y(), b.get_height()
        if y < adj.get_value():
            adj.set_value(max(0, y - 6))
        elif y + h > adj.get_value() + adj.get_page_size():
            adj.set_value(y + h - adj.get_page_size() + 6)
        return False

    # ----- actions -----

    def move(self, step: int):
        if not self.visible_list:
            return
        i = self.visible_list.index(self.hi) if self.hi in self.visible_list else 0
        self.highlight(self.visible_list[max(0, min(len(self.visible_list) - 1, i + step))])

    def pick_wallpaper(self, i: int, apply=False):
        t = self.hi
        if not t or not t.backgrounds:
            return
        self.wp[t.id] = i % len(t.backgrounds)
        self.highlight(t)
        if apply:
            self.apply()

    def toggle_favorite(self, t: th.Theme | None = None):
        t = t or self.hi
        if t is None:
            return
        self.favs = th.set_favorite(t.id, t.id not in self.favs)
        if self.filter == "favs":
            self.refilter()
        self.tiles[t.id].queue_draw()

    def random(self):
        pool = [t for t in self.visible_list if t is not self.hi]
        if pool:
            self.highlight(random.choice(pool))

    def apply(self):
        """Keep the highlighted theme: the full switch (GTK light/dark included), remembered, undoable."""
        t = self.hi
        log("apply", t.id if t else None, "from:", self.orig[0], "live:", self.live[0])
        if t is None:
            return
        want, orig = self.want(), self.orig
        prev = self.by_id.get(orig[0])
        self._close()
        if want == orig and self.live == orig:
            self.app.toast.pop(f"Already on {t.title}", None, t.colors)
            return
        args = ["set", t.id] + (["--background", str(want[1])] if want[1] else [])
        if orig[0]:
            args += ["--from", orig[0]] + (["--from-background", str(orig[1])] if orig[1] else [])
        self.app.toast.pop(f"Applying {t.title}\u2026", None, t.colors, busy=True)
        self._when_idle(lambda: self._run(args, want, done=lambda ok: self.app.toast.pop(
            f"Switched to {t.title}" if ok else f"Could not switch to {t.title}",
            f"back to {prev.title}" if ok and prev and prev is not t else None, t.colors)))

    def cancel(self):
        """Close without keeping anything: the desktop goes back to how it was when the picker opened."""
        if not self.get_visible():
            return
        orig = self.orig
        self._close()
        if self.live == orig and not self.busy or orig[0] is None:
            return
        o = self.by_id.get(orig[0])
        self.app.toast.pop(f"Restoring {o.title if o else orig[0]}\u2026", None, o.colors if o else None, busy=True)
        args = ["set", orig[0], "--preview"] + (["--background", str(orig[1])] if orig[1] else [])
        self._when_idle(lambda: self._run(args, orig, done=lambda ok: self.app.toast.dismiss() if ok else
                                          self.app.toast.pop("Could not restore the theme", None)))

    def _when_idle(self, fn):
        """Run `fn` once the omarchy-theme run in flight (a preview) has finished."""
        if self.busy:
            GLib.timeout_add(100, lambda: self._when_idle(fn) or False)
        else:
            fn()

    def _close(self):
        if self.preview_timer:
            GLib.source_remove(self.preview_timer)
            self.preview_timer = 0
        self.close_picker()

    def close_picker(self):
        self.set_visible(False)
        OPEN_FLAG.unlink(missing_ok=True)
        self.app.paint(None)

    def open(self):
        log("open, current:", th.ot.current())
        hypr_ui.solo("hyprtheme")
        self.app.toast.dismiss()
        self.reload()
        self.orig = self.live = (self.current, self.current_bg)
        self.entry.set_text("")
        self.hi = None
        self.filter = "all"
        for k, b in self.chips.items():
            (b.add_css_class if k == "all" else b.remove_css_class)("active")
        self.present()
        self.refilter(keep=self.by_id.get(self.current) or (self.themes[0] if self.themes else None))
        self.entry.grab_focus()
        OPEN_FLAG.touch()

    def toggle(self):
        self.cancel() if self.get_visible() else self.open()

    def _on_key(self, _ctrl, keyval, _code, state):
        name = Gdk.keyval_name(keyval) or ""
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if name in ("Escape", "Return", "KP_Enter"):
            log("key", name)
        if name == "Escape":
            if self.entry.get_text():
                self.entry.set_text("")
            else:
                self.cancel()
        elif name in ("Return", "KP_Enter"):
            self.apply()
        elif name in ("Left", "Right", "Up", "Down"):
            self.move({"Left": -1, "Right": 1, "Up": -COLS, "Down": COLS}[name])
        elif name in ("Page_Up", "Page_Down"):
            self.move((-COLS if name == "Page_Up" else COLS) * 3)
        elif name == "Tab":
            self.pick_wallpaper(self.wp_index(self.hi) + 1) if self.hi else None
        elif name == "ISO_Left_Tab":
            self.pick_wallpaper(self.wp_index(self.hi) - 1) if self.hi else None
        elif ctrl and name == "f":
            self.toggle_favorite()
        elif ctrl and name == "r":
            self.random()
        elif ctrl and name in ("1", "2", "3", "4"):
            self.set_filter(FILTERS[int(name) - 1][0])
        else:
            return False
        return True


# ---------- undo toast ----------

class Toast(Gtk.Window):
    """Bottom-centre notice after a switch, with an undo button (the theme counterpart of hyprpower's
    keep-or-revert). Only the card takes clicks; it never takes the keyboard."""

    def __init__(self, app: "App"):
        super().__init__(application=app, title="hyprtheme-toast")
        self.app = app
        if not WINDOWED:
            Layer.init_for_window(self)
            Layer.set_namespace(self, "hyprtheme-toast")
            hypr_ui.layer(self, Layer, Layer.Layer.OVERLAY)
            Layer.set_anchor(self, Layer.Edge.BOTTOM, True)
            Layer.set_margin(self, Layer.Edge.BOTTOM, 88 - hypr_ui.MARGIN)
            Layer.set_keyboard_mode(self, Layer.KeyboardMode.NONE)
            self.connect("realize", lambda w: w.get_surface().connect("layout", self._clip))
        self.icon = label(G["palette"], "toast-glyph")
        self.spin = Gtk.Spinner(valign=Gtk.Align.CENTER, visible=False)
        self.title = label("", "toast-title")
        self.left = label("", "subtitle")
        self.undo_b = button("", "primary", self.undo)
        box = hbox(12, self.icon, self.spin, vbox(2, self.title, self.left, hexpand=True, valign=Gtk.Align.CENTER),
                   self.undo_b)
        box.add_css_class("toast")
        self.set_child(box)
        self.timer = 0
        self.secs = 0

    @staticmethod
    def _clip(surface, width, height):
        m = hypr_ui.MARGIN
        surface.set_input_region(cairo.Region(cairo.RectangleInt(m, m, max(0, width - 2 * m), max(0, height - 2 * m))))

    def pop(self, title: str, undo_to: str | None, colors: dict | None = None, busy: bool = False):
        """Show the toast; `busy` shows a spinner and stays until the next pop (the result)."""
        self.app.paint(colors)
        self.title.set_text(title)
        self.undo_b.set_visible(undo_to is not None)
        if undo_to:
            self.undo_b.set_label(f"Undo \u00b7 {undo_to}")
        self.icon.set_visible(not busy)
        self.spin.set_visible(busy)
        self.spin.set_spinning(busy)
        if busy:
            if self.timer:
                GLib.source_remove(self.timer)
                self.timer = 0
            self.left.set_text("")
        else:
            self.secs = TOAST_S
            self._tick(start=True)
        self.present()

    def _tick(self, start=False):
        if start:
            if self.timer:
                GLib.source_remove(self.timer)
            self.timer = GLib.timeout_add(1000, self._tick)
        else:
            self.secs -= 1
            if self.secs <= 0:
                self.dismiss()
                return False
        self.left.set_text(f"undo stays here {self.secs} s" if self.undo_b.get_visible() else "")
        return True

    def dismiss(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        self.set_visible(False)

    def undo(self):
        self.dismiss()
        self.app.undo()


# ---------- app ----------

class App(Gtk.Application):
    def __init__(self, sock: Path):
        super().__init__(application_id="dev.edu.HyprTheme")
        self.sock_path = sock
        self.provider: Gtk.CssProvider | None = None
        self.painted: dict | None = None

    def paint(self, colors: dict | None):
        """Repaint every hyprtheme surface in `colors`, or in the desktop's current theme for None."""
        if colors is None:
            hypr_ui._apply_theme_colors()  # the theme may have changed since the last paint
        want = {**hypr_ui.TOKENS["color"], **(colors or {})}
        if want == self.painted:
            return
        self.painted = want
        self.provider.load_from_string(hypr_ui.css_text([HERE / "style.css"], want))

    def run_theme(self, args: list[str], done=None):
        """Run omarchy-theme in the background (with this process's layer-shell preload removed)."""
        cmd = [sys.executable, str(th.ot.__file__), *args]
        log("run", " ".join(cmd))
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        launcher.unsetenv("LD_PRELOAD")
        try:
            proc = launcher.spawnv(cmd)
        except GLib.Error as e:
            log("could not start omarchy-theme:", e.message)
            if done:
                done(False)
            return

        def finished(p, res):
            try:
                _ok, out, _err = p.communicate_utf8_finish(res)
            except GLib.Error as e:
                out = e.message
            ok = p.get_if_exited() and p.get_exit_status() == 0
            log("omarchy-theme", "ok" if ok else f"failed ({p.get_exit_status()})", (out or "").strip())
            if done:
                done(ok)

        proc.communicate_utf8_async(None, None, finished)

    def undo(self):
        prev = (th.ot.load_state().get("previous") or {}).get("theme")
        title = th.ot.pretty(prev) if prev else "the last theme"
        colors = next((t.colors for t in th.load_themes() if t.id == prev), None) if prev else None
        self.run_theme(["undo"], done=lambda ok: self.toast.pop(
            f"Back to {title}" if ok else "Nothing to undo", None, colors if ok else None))

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
                self.command(cmd[0])
            return True

        GLib.io_add_watch(srv.fileno(), GLib.IO_IN, on_conn)

    def command(self, c: str):
        p = self.picker
        if c == "toggle":
            p.toggle()
        elif c == "open" and not p.get_visible():
            p.open()
        elif c == "close":
            p.cancel()
        elif c == "undo":
            self.undo()
        elif c == "quit":
            self.quit()

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self.paint(None)
        self.hold()
        self.picker = Picker(self)
        self.toast = Toast(self)
        self._serve()
        log("daemon started, python", sys.version.split()[0], "gtk", f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}")
        GLib.idle_add(lambda: self.picker.reload() or False)  # thumbnails are ready by the first open

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
