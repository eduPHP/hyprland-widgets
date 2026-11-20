"""Shared GTK helpers, icons and formatting for the network popover panels."""

from __future__ import annotations

import subprocess
import threading

from gi.repository import GLib, Gtk

WAYBAR_SIGNAL = 9  # custom/wifi "signal" in the waybar config

I_WIFI, I_REFRESH, I_LOCK, I_EYE, I_EYE_OFF, I_WIFI_OFF = "\U000f05a9", "\U000f0450", "\U000f033e", "\U000f0208", "\U000f0209", "\U000f05aa"


def bg(fn, done=None):
    """Run fn in a thread; deliver (result, error) to done on the main loop."""

    def work():
        try:
            res, err = fn(), None
        except Exception as e:  # surfaced in the UI, never fatal
            res, err = None, e
        if done:
            GLib.idle_add(lambda: done(res, err) and False)

    threading.Thread(target=work, daemon=True).start()


def poke_waybar():
    subprocess.run(["pkill", f"-RTMIN+{WAYBAR_SIGNAL}", "-x", "waybar"], capture_output=True)


def fmt_rate(mbps: float) -> str:
    return f"{mbps:.0f}" if mbps >= 10 else f"{mbps:.1f}"


def fmt_age(seconds: int) -> str:
    if seconds < 60:
        return "just now"
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def label(text="", *classes, xalign=0.0, markup=False, hexpand=False, ellipsize=False):
    w = Gtk.Label(xalign=xalign, hexpand=hexpand)
    w.set_markup(text) if markup else w.set_text(text)
    for c in classes:
        w.add_css_class(c)
    if ellipsize:
        w.set_ellipsize(3)  # Pango.EllipsizeMode.END
        w.set_max_width_chars(24)  # else the natural width is the whole text and the popover grows
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
