"""hyprmenu: resident GTK4 layer-shell start menu in the hypr-ui look.
Driven over a unix socket by the `hyprmenu` script.

One card over a light veil: the most-used apps on the left, the Omarchy menu on the right, and a
search over both. The daemon also measures which apps you use (usage.py), so keep it running.
"""

from __future__ import annotations

import getpass
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

WINDOWED = bool(os.environ.get("HYPRMENU_WINDOWED"))
if not WINDOWED:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as Layer  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "hypr-ui/gtk"))  # the repo's copy; ~/.config/hypr-ui links to it
import hypr_ui  # noqa: E402  shared look: tokens, base stylesheet, solo()
import menu as mn  # noqa: E402
import usage as us  # noqa: E402

RUN = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
OPEN_FLAG = RUN / "hyprmenu.open"
FOLLOW_SAVED = RUN / "hyprmenu.follow_mouse"  # the user's input:follow_mouse while the menu holds it at 2
CARD_W, CARD_H = 880, 500
COLS, GRID_N = 4, 8
CONFIRM_S = 3
SAVE_S = 60


def log(*parts) -> None:
    print(GLib.DateTime.new_now_local().format("%H:%M:%S"), "hyprmenu:", *parts, file=sys.stderr, flush=True)


def hyprctl(*args: str) -> str:
    try:
        return subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def hold_focus() -> None:
    """With follow_mouse = 1 Hyprland moves the keyboard to whatever window the cursor passes over, so
    leaving the card took the focus and closed the menu. While it is open, follow_mouse = 2: hovering
    a window no longer focuses it, clicking one still does (and that closes the menu, as before)."""
    if WINDOWED or FOLLOW_SAVED.exists():
        return
    try:
        cur = int(json.loads(hyprctl("-j", "getoption", "input:follow_mouse") or "{}")["int"])
    except (ValueError, KeyError, TypeError):
        return
    if cur != 1:
        return
    FOLLOW_SAVED.write_text(str(cur))
    hyprctl("keyword", "input:follow_mouse", "2")
    log("follow_mouse 1 -> 2 while open")


def release_focus() -> None:
    """Give the user's follow_mouse back (also at daemon start, in case it died with the menu open)."""
    try:
        cur = FOLLOW_SAVED.read_text().strip()
    except OSError:
        return
    if cur.isdigit():
        hyprctl("keyword", "input:follow_mouse", cur)
        log("follow_mouse restored to", cur)
    FOLLOW_SAVED.unlink(missing_ok=True)


def refocus_under_cursor() -> bool:
    x, _, y = hyprctl("cursorpos").strip().partition(", ")
    if x.isdigit() and y.isdigit():
        hyprctl("dispatch", "movecursor", x, y)
    return False


class Replayer:
    """Passes a click on the veil through to what's under it: once the menu is gone, the same button is
    pressed again on a virtual mouse (uinput). The device is made once, at daemon start, because the
    compositor takes a moment to pick up a new one. Without /dev/uinput access the click only closes."""

    def __init__(self):
        self.ui = None
        try:
            from evdev import UInput
            from evdev import ecodes as e

            self.e = e
            self.ui = UInput({e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE], e.EV_REL: [e.REL_X, e.REL_Y]},
                             name="hyprmenu click-through")
        except Exception as ex:  # no python-evdev, or no access to /dev/uinput
            log("click-through off:", ex)

    def click(self, button: int) -> bool:
        if self.ui:
            e = self.e
            code = {1: e.BTN_LEFT, 2: e.BTN_MIDDLE, 3: e.BTN_RIGHT}.get(button, e.BTN_LEFT)
            try:
                self.ui.write(e.EV_KEY, code, 1)
                self.ui.syn()
                self.ui.write(e.EV_KEY, code, 0)
                self.ui.syn()
            except OSError as ex:
                log("click-through:", ex)
        return False

    def close(self):
        if self.ui:
            self.ui.close()


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


def keyhint(k: str, text: str) -> Gtk.Box:
    return hbox(4, label(k, "key"), label(text, "hint"))


def clear(box: Gtk.Box):
    while (c := box.get_first_child()) is not None:
        box.remove(c)


def icon_image(icon, size: int) -> Gtk.Image:
    img = Gtk.Image(pixel_size=size)
    if isinstance(icon, str):
        img.set_from_icon_name(icon or "application-x-executable-symbolic")
    elif icon is not None:
        img.set_from_gicon(icon)
    else:
        img.set_from_icon_name("application-x-executable")
    return img


def child_env() -> dict:
    """The environment for what the menu starts: without this process's layer-shell preload and with
    the GDK_BACKEND the session had (the daemon forces wayland for itself)."""
    env = dict(os.environ)
    env.pop("LD_PRELOAD", None)
    orig = env.pop("HYPRMENU_SESSION_GDK_BACKEND", None)
    if orig:
        env["GDK_BACKEND"] = orig
    else:
        env.pop("GDK_BACKEND", None)
    return env


def fuzzy(q: str, text: str) -> float:
    """0 for no match; higher for a prefix, then a word start, then a substring, then a subsequence."""
    q, t = q.lower().strip(), text.lower()
    if not q:
        return 1.0
    if t.startswith(q):
        return 4.0
    if any(w.startswith(q) for w in t.replace("-", " ").replace("›", " ").split()):
        return 3.0
    if q in t:
        return 2.0
    j = 0
    for ch in q.replace(" ", ""):
        j = t.find(ch, j)
        if j < 0:
            return 0.0
        j += 1
    return 1.0


# ---------- installed apps ----------

class App:
    def __init__(self, info: Gio.DesktopAppInfo):
        self.info = info
        self.key = info.get_id() or ""
        self.name = info.get_display_name() or info.get_name() or self.key
        self.icon = info.get_icon()
        extra = [info.get_generic_name() or "", *(info.get_keywords() or [])]
        exe = os.path.basename((info.get_executable() or "").split()[0]) if info.get_executable() else ""
        self.search = " ".join([self.name, exe, *extra])
        self.wmclass = (info.get_startup_wm_class() or "").lower()
        self.exe = exe.lower()


class Apps:
    """The desktop entries that should show, and the window-class → app mapping for usage."""

    def __init__(self):
        self.by_key: dict[str, App] = {}
        self.index: dict[str, str] = {}
        self.reload()

    def reload(self):
        by_key, index = {}, {}
        for info in Gio.AppInfo.get_all():
            if not isinstance(info, Gio.DesktopAppInfo) or not info.should_show() or not info.get_id():
                continue
            a = App(info)
            by_key[a.key] = a
            stem = a.key.removesuffix(".desktop").lower()
            for k in (stem, stem.rsplit(".", 1)[-1], a.exe):
                if k:
                    index.setdefault(k, a.key)
        for a in by_key.values():  # an explicit StartupWMClass beats a name guess
            if a.wmclass:
                index[a.wmclass] = a.key
        self.by_key, self.index = by_key, index

    def resolve(self, cls: str) -> str:
        c = (cls or "").lower()
        for k in (c, c.rsplit(".", 1)[-1], c.removesuffix("-bin")):
            if k in self.index:
                return self.index[k]
        return f"class:{cls}"

    def sorted(self) -> list[App]:
        return sorted(self.by_key.values(), key=lambda a: a.name.lower())


# ---------- the card's pieces ----------

class Tile(Gtk.Button):
    def __init__(self, win: "Menu", i: int, app: App, meta: str, pinned: bool):
        super().__init__(can_focus=False, focus_on_click=False, hexpand=True)
        self.add_css_class("tile")
        self.app = app
        num = label(str(i + 1), "num")
        pin = label("●" if pinned else "", "pin", xalign=1.0, hexpand=True)
        name = label(app.name, "name", xalign=0.5, ellipsize=14)
        body = vbox(4, hbox(0, num, pin), icon_image(app.icon, 40), name, label(meta, "meta", xalign=0.5))
        self.set_child(body)
        self.set_tooltip_text(f"{app.name}. Right-click or Ctrl+P to {'unpin' if pinned else 'pin'}")
        self.connect("clicked", lambda *_: win.launch_app(app))
        g = Gtk.GestureClick(button=3)
        g.connect("pressed", lambda *_: win.toggle_pin(app))
        self.add_controller(g)


class Row(Gtk.Button):
    """A menu or search row: icon, title (and path), badges, key hint, chevron."""

    def __init__(self, icon, title: str, on_click, path: str = "", keys: str = "", widget=False, sub=False,
                 meta: str = "", icon_size=16):
        super().__init__(can_focus=False, focus_on_click=False)
        self.add_css_class("mrow")
        t = label(title, "t", ellipsize=40)
        parts = [icon_image(icon, icon_size), t]
        if path:
            parts.append(label(path, "path", ellipsize=40))
        box = hbox(10, *parts)
        box.append(Gtk.Box(hexpand=True))
        if widget:
            badge = label("widget", "badge", "widget")
            badge.set_valign(Gtk.Align.CENTER)
            box.append(badge)
        if meta:
            box.append(label(meta, "k"))
        if keys:
            box.append(label(keys, "k"))
        if sub:
            box.append(label("›", "chev"))
        self.set_child(box)
        self.connect("clicked", lambda *_: on_click())


# ---------- the menu window ----------

class Menu(Gtk.ApplicationWindow):
    def __init__(self, app: "Daemon"):
        super().__init__(application=app, title="hyprmenu")  # resizable: the compositor sizes it to the screen
        self.app = app
        if WINDOWED:
            self.set_default_size(1280, 800)
        else:
            Layer.init_for_window(self)
            Layer.set_namespace(self, "hyprmenu")
            hypr_ui.layer(self, Layer, Layer.Layer.OVERLAY)
            for e in (Layer.Edge.TOP, Layer.Edge.BOTTOM, Layer.Edge.LEFT, Layer.Edge.RIGHT):
                Layer.set_anchor(self, e, True)  # whole screen, below waybar (its exclusive zone is kept)
            # the veil takes the clicks around the card: a click there closes the menu and focuses the
            # window under the cursor. On another monitor, clicking a window takes the focus away, and
            # the menu closes when it loses focus
            Layer.set_keyboard_mode(self, Layer.KeyboardMode.ON_DEMAND)

        self.root_node = mn.tree()
        self.stack: list[mn.Node] = [self.root_node]
        self.side = "apps"
        self.ai = self.mi = self.si = 0
        self.tiles: list[Tile] = []
        self.rows: list[Row] = []
        self.results: list[tuple] = []
        self.result_rows: list[Row] = []
        self.all_apps = False
        self.confirm: tuple[str, int] | None = None
        self.was_active = False

        dim = Gtk.Box(hexpand=True, vexpand=True)
        dim.add_css_class("dim")
        veil_click = Gtk.GestureClick(button=0)
        veil_click.connect("pressed", lambda g, *_: g.set_state(Gtk.EventSequenceState.CLAIMED))
        veil_click.connect("released", self._veil_clicked)
        dim.add_controller(veil_click)
        self.card = self._build_card()
        root = Gtk.Overlay()
        root.set_child(dim)
        root.add_overlay(self.card)
        self.set_child(root)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("close-request", lambda *_: self.close_menu() or True)
        self.connect("notify::is-active", self._active_changed)

    # ----- layout -----

    def _build_card(self) -> Gtk.Box:
        self.entry = Gtk.Entry(placeholder_text="search apps and settings", hexpand=True, valign=Gtk.Align.CENTER)
        self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "system-search-symbolic")
        self.entry.connect("changed", lambda *_: self.refresh_view())
        self.entry.connect("activate", lambda *_: self.activate())
        self.who = label("", "subtitle")
        avatar = label((getpass.getuser()[:1] or "?").upper(), "app-icon", xalign=0.5)
        header = hbox(12, self.entry, self.who, avatar)
        header.add_css_class("menu-header")

        # home: apps | menu
        self.grid = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
        self.recent = hbox(6)
        self.recent_box = vbox(8, label("RECENT", "section"), self.recent)
        apps_col = vbox(10, hbox(0, label("MOST USED", "section"),
                                 label("by focus time, last weeks", "section-meta", xalign=1.0, hexpand=True)),
                        self.grid, self.recent_box, hexpand=True)
        apps_col.add_css_class("apps")
        self.crumb = hbox(6)
        self.menu_rows = vbox(1)
        mscroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, can_focus=False,
                                     propagate_natural_height=True)
        mscroll.set_child(self.menu_rows)
        menu_col = vbox(8, self.crumb, mscroll)
        menu_col.add_css_class("side")
        menu_col.set_size_request(290, -1)
        home = hbox(0, apps_col, menu_col)

        # results: search, or every app
        self.res_box = vbox(1)
        self.res_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, can_focus=False)
        self.res_scroll.set_child(self.res_box)
        self.res_scroll.add_css_class("results")

        self.view = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.NONE)
        self.view.add_named(home, "home")
        self.view.add_named(self.res_scroll, "results")

        # footer: power row, key hints (or a status line)
        power = hbox(4)
        for icon, text_, cmd, confirm in (("system-lock-screen-symbolic", "Lock", "omarchy-system-lock", False),
                                          ("weather-clear-night-symbolic", "Suspend", "systemctl suspend", False),
                                          ("system-reboot-symbolic", "Restart", "systemctl reboot", True),
                                          ("system-shutdown-symbolic", "Shut down", "systemctl poweroff", True)):
            b = Gtk.Button(can_focus=False, focus_on_click=False)
            b.add_css_class("ghost")
            b.add_css_class("power")
            if confirm:
                b.add_css_class("danger")
            b.set_child(hbox(6, icon_image(icon, 13), label(text_)))
            b.connect("clicked", lambda _b, t=text_, c=cmd, k=confirm: self.run_action(t, c, k))
            power.append(b)
        self.hints = hbox(12, keyhint("↑↓←→", "move"), keyhint("tab", "apps / menu"), keyhint("enter", "open"),
                          keyhint("alt 1-8", "launch"), keyhint("^p", "pin"), keyhint("esc", "back / close"),
                          halign=Gtk.Align.END, hexpand=True)
        self.status = label("", "status", xalign=1.0, hexpand=True)
        self.status.set_visible(False)
        footer = hbox(12, power, self.hints, self.status)
        footer.add_css_class("footer")

        card = vbox(0, header, self.view, footer, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        card.add_css_class("startmenu")
        card.set_size_request(CARD_W, CARD_H)
        return card

    def _veil_clicked(self, gesture, _n, _x, _y):
        """A click outside the card closes the menu, then goes through to what's under the cursor (on
        release, so the replayed press isn't fighting a button still held down)."""
        button = gesture.get_current_button()
        log("click outside the card, closing")
        self.close_menu()
        if WINDOWED:
            return

        def through():
            refocus_under_cursor()  # pointer focus back on the window under the cursor
            GLib.timeout_add(30, self.app.replayer.click, button)
            return False

        GLib.timeout_add(40, through)  # let the compositor unmap the menu first

    # ----- apps side -----

    def top_apps(self) -> list[tuple[App, float, bool]]:
        apps = self.app.apps.by_key
        out, seen = [], set()
        for k in self.app.usage.pins():
            if k in apps and k not in seen:
                out.append((apps[k], self.app.usage.score(k), True))
                seen.add(k)
        for k, secs in self.app.usage.ranked():
            if len(out) >= GRID_N:
                break
            if k in apps and k not in seen:
                out.append((apps[k], secs, False))
                seen.add(k)
        return out[:GRID_N]

    def fill_apps(self):
        clear(self.grid)
        self.tiles = []
        top = self.top_apps()
        for i, (a, secs, pinned) in enumerate(top):
            meta = f"{secs / 3600:.1f}h" if secs >= 360 else ("pinned" if pinned else "new")
            t = Tile(self, i, a, meta, pinned)
            self.grid.attach(t, i % COLS, i // COLS, 1, 1)
            self.tiles.append(t)
        if not top:
            self.grid.attach(label("Nothing measured yet. Apps show up here as you use them.", "empty"), 0, 0, COLS, 1)
        clear(self.recent)
        shown = {a.key for a, _, _ in top}
        recent = [self.app.apps.by_key[k] for k in self.app.usage.data["recent"]
                  if k in self.app.apps.by_key and k not in shown][:5]
        for a in recent:
            b = Gtk.Button(can_focus=False, focus_on_click=False)
            b.add_css_class("chip")
            b.set_child(hbox(6, icon_image(a.icon, 14), label(a.name, ellipsize=14)))
            b.connect("clicked", lambda _b, a=a: self.launch_app(a))
            self.recent.append(b)
        self.recent_box.set_visible(bool(recent))
        self.ai = min(self.ai, max(0, len(self.tiles) - 1))

    # ----- menu side -----

    def fill_menu(self):
        node = self.stack[-1]
        clear(self.crumb)
        if len(self.stack) > 1:
            back = Gtk.Button(label="‹ back", can_focus=False, focus_on_click=False)
            back.add_css_class("link")
            back.connect("clicked", lambda *_: self.back())
            self.crumb.append(back)
            self.crumb.append(label(" › ".join(n.title for n in self.stack[1:]), "section-meta", ellipsize=30))
        else:
            self.crumb.append(label("OMARCHY MENU", "section"))
        clear(self.menu_rows)
        self.rows = []
        for n in node.visible_children():
            r = Row(n.icon, n.title, lambda n=n: self.open_node(n), keys=n.keys, widget=n.widget,
                    sub=bool(n.children))
            self.menu_rows.append(r)
            self.rows.append(r)
        self.mi = min(self.mi, max(0, len(self.rows) - 1))

    def open_node(self, n: mn.Node):
        if n.children:
            self.stack.append(n)
            self.mi = 0
            self.side = "menu"
            self.fill_menu()
            self.mark()
        elif n.special == "apps":
            self.all_apps = True
            self.si = 0
            self.refresh_view()
        else:
            self.run_action(n.title, n.run, n.special == "confirm")

    def back(self):
        if len(self.stack) > 1:
            child = self.stack.pop()
            self.fill_menu()
            vis = self.stack[-1].visible_children()
            self.mi = vis.index(child) if child in vis else 0
            self.mark()

    # ----- results (search, or every app) -----

    def search(self, q: str) -> list[tuple]:
        """Apps and menu entries matching `q`. A loose match (letters in order) counts only against
        a name, so long keyword lists don't drown an exact hit. The better group comes first."""
        u = self.app.usage

        def score(name: str, extra: str) -> float:
            s = fuzzy(q, name) * 2
            if not s and fuzzy(q, extra) >= 2:
                s = fuzzy(q, extra)
            return s

        apps = [(s + min(u.score(a.key) / 36000, 0.9), a) for a in self.app.apps.by_key.values()
                if (s := score(a.name, a.search))]
        apps.sort(key=lambda x: (-x[0], x[1].name.lower()))
        acts = [(s, n) for n in mn.leaves(self.root_node) if (s := score(n.title, " ".join(n.path)))]
        acts.sort(key=lambda x: -x[0])
        a_rows = [("app", a) for _, a in apps[:7]]
        n_rows = [("node", n) for _, n in acts[:7]]
        if acts and (not apps or acts[0][0] > apps[0][0]):
            return n_rows + a_rows
        return a_rows + n_rows

    def fill_results(self):
        q = self.entry.get_text().strip()
        if self.all_apps:
            self.results = [("app", a) for a in self.app.apps.sorted() if fuzzy(q, a.search)]
        else:
            self.results = self.search(q)
        clear(self.res_box)
        self.result_rows = []
        section = None
        for kind, item in self.results:
            want = ("ALL APPS" if self.all_apps else "APPS") if kind == "app" else "SETTINGS AND ACTIONS"
            if want != section:
                self.res_box.append(label(want, "section", "res-section"))
                section = want
            if kind == "app":
                secs = self.app.usage.score(item.key)
                r = Row(item.icon, item.name, lambda a=item: self.launch_app(a), icon_size=20,
                        meta=f"{secs / 3600:.1f}h" if secs >= 360 else "")
            else:
                r = Row(item.icon, item.title, lambda n=item: self.open_node(n),
                        path=" › ".join(item.path[:-1]), keys=item.keys, widget=item.widget)
            self.res_box.append(r)
            self.result_rows.append(r)
        if not self.results:
            msg = f"Nothing matches “{q}”. Enter runs it as a command." if q else "No apps found."
            self.res_box.append(label(msg, "empty"))
        self.si = min(self.si, max(0, len(self.results) - 1))

    def refresh_view(self):
        searching = self.all_apps or bool(self.entry.get_text().strip())
        if searching:
            self.si = 0
            self.fill_results()
            self.view.set_visible_child_name("results")
        else:
            self.view.set_visible_child_name("home")
        self.mark()

    def mark(self):
        """Show the selection: the tile, the menu row or the result row under the keyboard."""
        searching = self.view.get_visible_child_name() == "results"
        for i, t in enumerate(self.tiles):
            (t.add_css_class if not searching and self.side == "apps" and i == self.ai else t.remove_css_class)("sel")
        for i, r in enumerate(self.rows):
            (r.add_css_class if not searching and self.side == "menu" and i == self.mi else r.remove_css_class)("sel")
        for i, r in enumerate(self.result_rows):
            (r.add_css_class if searching and i == self.si else r.remove_css_class)("sel")
        if searching and self.result_rows:
            GLib.idle_add(self._scroll_to, self.result_rows[self.si])

    def _scroll_to(self, row):
        ok, b = row.compute_bounds(self.res_box)
        if ok:
            adj = self.res_scroll.get_vadjustment()
            top, bottom = b.get_y() - 24, b.get_y() + b.get_height() + 4
            if top < adj.get_value():
                adj.set_value(max(0, top))
            elif bottom > adj.get_value() + adj.get_page_size():
                adj.set_value(bottom - adj.get_page_size())
        return False

    # ----- doing things -----

    def launch_app(self, a: App):
        log("launch", a.key)
        ctx = Gdk.Display.get_default().get_app_launch_context()
        ctx.unsetenv("LD_PRELOAD")
        backend = child_env().get("GDK_BACKEND")
        ctx.setenv("GDK_BACKEND", backend) if backend else ctx.unsetenv("GDK_BACKEND")
        try:
            a.info.launch([], ctx)
        except GLib.Error as e:
            log("launch failed:", e.message)
            return self.say(f"Couldn't start {a.name}: {e.message}")
        self.app.usage.launched(a.key)
        self.close_menu()

    def run_action(self, title: str, cmd: str | None, confirm=False):
        if not cmd:
            return
        if confirm:
            if not self.confirm or self.confirm[0] != title:
                if self.confirm:
                    GLib.source_remove(self.confirm[1])
                tid = GLib.timeout_add_seconds(CONFIRM_S, self._confirm_expired)
                self.confirm = (title, tid)
                return self.say(f"Press {title} again to confirm", hot=True)
            GLib.source_remove(self.confirm[1])
            self.confirm = None
        log("run", cmd)
        try:
            subprocess.Popen(["sh", "-c", cmd], env=child_env(), start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            return self.say(f"Couldn't run it: {e}")
        self.close_menu()

    def _confirm_expired(self):
        self.confirm = None
        self.say(None)
        return False

    def say(self, text: str | None, hot=False):
        self.status.set_text(text or "")
        self.status.set_visible(bool(text))
        self.hints.set_visible(not text)
        (self.status.add_css_class if hot else self.status.remove_css_class)("hot")

    def toggle_pin(self, a: App):
        pinned = self.app.usage.toggle_pin(a.key)
        self.fill_apps()
        self.mark()
        self.say(f"Pinned {a.name}" if pinned else f"Unpinned {a.name}")

    def activate(self):
        if self.view.get_visible_child_name() == "results":
            if self.results:
                kind, item = self.results[self.si]
                self.launch_app(item) if kind == "app" else self.open_node(item)
            elif (q := self.entry.get_text().strip()):
                self.run_action(q, q)
        elif self.side == "apps" and self.tiles:
            self.launch_app(self.tiles[self.ai].app)
        elif self.side == "menu" and self.rows:
            self.open_node(self.stack[-1].visible_children()[self.mi])

    # ----- open / close -----

    def _who(self) -> str:
        try:
            up = int(float(Path("/proc/uptime").read_text().split()[0]))
        except (OSError, ValueError, IndexError):
            return f"{getpass.getuser()}@{socket.gethostname()}"
        return f"{getpass.getuser()}@{socket.gethostname()} · up {up // 3600}h {up % 3600 // 60}m"

    def open(self, side="apps"):
        log("open", side)
        hypr_ui.solo("hyprmenu")
        self.app.apps.reload()
        self.root_node = mn.tree()
        self.stack = [self.root_node]
        self.side, self.ai, self.mi, self.si = side, 0, 0, 0
        self.all_apps = False
        self.confirm = None
        self.was_active = False
        self.say(None)
        self.who.set_text(self._who())
        self.fill_apps()
        self.fill_menu()
        self.entry.set_text("")
        self.refresh_view()
        hold_focus()
        self.present()
        self.entry.grab_focus()
        OPEN_FLAG.touch()

    def close_menu(self):
        if self.confirm:
            GLib.source_remove(self.confirm[1])
            self.confirm = None
        self.set_visible(False)
        release_focus()
        OPEN_FLAG.unlink(missing_ok=True)
        self.app.usage.save()

    def toggle(self, side="apps"):
        if not self.get_visible():
            self.open(side)
        elif side == "menu" and self.side != "menu" and self.view.get_visible_child_name() == "home":
            self.side = "menu"
            self.mark()
        else:
            self.close_menu()

    def _active_changed(self, *_):
        if self.is_active():
            self.was_active = True
        elif self.was_active and self.get_visible() and not WINDOWED:
            log("lost focus, closing")
            self.close_menu()

    # ----- keys -----

    def _on_key(self, _ctrl, keyval, _code, state):
        name = Gdk.keyval_name(keyval) or ""
        alt = bool(state & Gdk.ModifierType.ALT_MASK)
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        results = self.view.get_visible_child_name() == "results"
        if alt and name in "12345678" and len(name) == 1:
            i = int(name) - 1
            if i < len(self.tiles):
                self.launch_app(self.tiles[i].app)
            return True
        if name == "Escape":
            if self.entry.get_text():
                self.entry.set_text("")
            elif self.all_apps:
                self.all_apps = False
                self.refresh_view()
            elif self.side == "menu" and len(self.stack) > 1:
                self.back()
            else:
                self.close_menu()
            return True
        if name in ("Return", "KP_Enter"):
            self.activate()
            return True
        if results:
            n = len(self.results)
            step = {"Down": 1, "Up": -1, "Page_Down": 8, "Page_Up": -8}.get(name)
            if step and n:
                self.si = max(0, min(n - 1, self.si + step))
                self.mark()
                return True
            return False
        if ctrl and name == "p" and self.side == "apps" and self.tiles:
            self.toggle_pin(self.tiles[self.ai].app)
            return True
        if name in ("Tab", "ISO_Left_Tab"):
            self.side = "menu" if self.side == "apps" else "apps"
            self.mark()
            return True
        if self.side == "apps":
            n = len(self.tiles)
            if name == "Right" and (self.ai % COLS == COLS - 1 or self.ai == n - 1):
                self.side = "menu"
            elif name in ("Left", "Right", "Up", "Down") and n:
                step = {"Left": -1, "Right": 1, "Up": -COLS, "Down": COLS}[name]
                if 0 <= self.ai + step < n:
                    self.ai += step
            else:
                return False
        else:
            n = len(self.rows)
            if name == "Down" and n:
                self.mi = min(n - 1, self.mi + 1)
            elif name == "Up" and n:
                self.mi = max(0, self.mi - 1)
            elif name == "Left":
                if len(self.stack) > 1:
                    self.back()
                else:
                    self.side = "apps"
            elif name == "Right" and n:
                node = self.stack[-1].visible_children()[self.mi]
                if node.children:
                    self.open_node(node)
            else:
                return False
        self.mark()
        return True


# ---------- the daemon ----------

class Daemon(Gtk.Application):
    def __init__(self, sock: Path):
        super().__init__(application_id="dev.edu.HyprMenu")
        self.sock_path = sock
        self.stop = threading.Event()

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
        m = self.menu
        if c == "toggle":
            m.toggle("apps")
        elif c == "menu":
            m.toggle("menu")
        elif c == "open" and not m.get_visible():
            m.open()
        elif c == "close" and m.get_visible():
            m.close_menu()
        elif c == "save":
            self.usage.save()
        elif c == "quit":
            self.quit()

    def _focus(self, cls):
        self.usage.focus(cls)
        return False

    def do_startup(self):
        Gtk.Application.do_startup(self)
        hypr_ui.load_css(HERE / "style.css")  # follows the Omarchy theme live
        self.hold()
        self.apps = Apps()
        self.usage = us.Usage()
        self.usage.resolve = self.apps.resolve
        n = self.usage.seed_from_gnome()
        if n:
            log(f"seeded usage from GNOME's history ({n} apps)")
        self.usage.save()
        threading.Thread(target=us.listen, args=(lambda c: GLib.idle_add(self._focus, c), log, self.stop),
                         daemon=True).start()
        GLib.timeout_add_seconds(SAVE_S, lambda: self.usage.save() or True)
        self.menu = Menu(self)
        self.replayer = Replayer() if not WINDOWED else None
        self._serve()
        log("daemon started, python", sys.version.split()[0], "gtk",
            f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}",
            "display", type(Gdk.Display.get_default()).__name__,
            "layer-shell", "off (windowed)" if WINDOWED else Layer.is_supported(),
            "apps", len(self.apps.by_key))

    def do_activate(self):
        pass

    def do_shutdown(self):
        self.stop.set()
        self.usage.focus(None)  # count the stretch in progress
        self.usage.save()
        self.sock_path.unlink(missing_ok=True)
        OPEN_FLAG.unlink(missing_ok=True)
        release_focus()
        if self.replayer:
            self.replayer.close()
        Gtk.Application.do_shutdown(self)


def main(sock: Path):
    import signal

    OPEN_FLAG.unlink(missing_ok=True)
    release_focus()
    app = Daemon(sock)
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: app.quit() or False)
    app.run([])
