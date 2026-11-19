"""Spike descartável: layer-shell + foco no Hyprland.

Roda sozinho por ~9 s, mostra um retângulo no canto inferior direito e registra:
  A) compacto com keyboard_mode NONE não rouba o foco;
  B) ON_DEMAND + present() recebe foco? perde ao focar outra janela?
  C) EXCLUSIVE recebe foco? perde ao focar outra janela?

Uso: LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libgtk4-layer-shell.so.0 python3 focus_test.py <endereço-da-janela-de-volta>
"""
import json
import subprocess
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

BACK = sys.argv[1]
T0 = time.monotonic()


def log(msg):
    print(f"{time.monotonic() - T0:5.2f}s  {msg}", flush=True)


def active_window():
    out = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True).stdout
    try:
        return json.loads(out).get("class")
    except json.JSONDecodeError:
        return None


def focus_back():
    subprocess.run(["hyprctl", "dispatch", "focuswindow", f"address:{BACK}"], capture_output=True)


def on_activate(app):
    win = Gtk.ApplicationWindow(application=app)
    LS.init_for_window(win)
    LS.set_namespace(win, "hyprspot-spike")
    LS.set_layer(win, LS.Layer.OVERLAY)
    LS.set_anchor(win, LS.Edge.BOTTOM, True)
    LS.set_anchor(win, LS.Edge.RIGHT, True)
    LS.set_margin(win, LS.Edge.BOTTOM, 16)
    LS.set_margin(win, LS.Edge.RIGHT, 16)
    LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
    win.set_default_size(320, 64)
    label = Gtk.Label(label="hyprspot spike")
    win.set_child(label)
    win.connect("notify::is-active", lambda w, _p: log(f"  is-active -> {w.is_active()}"))
    win.present()

    def step(name, fn, delay):
        def run():
            log(name)
            fn()
            return False

        GLib.timeout_add(int(delay * 1000), run)

    def check(tag):
        return lambda: log(f"  [{tag}] is-active={win.is_active()} activewindow={active_window()} size={win.get_width()}x{win.get_height()}")

    step("A: compacto, keyboard NONE", check("A"), 1.0)

    def expand(mode):
        def fn():
            LS.set_keyboard_mode(win, mode)
            win.set_default_size(360, 520)
            label.set_size_request(360, 520)
            win.present()
        return fn

    step("B: ON_DEMAND + present()", expand(LS.KeyboardMode.ON_DEMAND), 1.5)
    step("B: checagem", check("B"), 2.2)
    step("B: foca outra janela via hyprctl", focus_back, 3.0)
    step("B: checagem após focar outra", check("B2"), 3.6)

    def collapse():
        LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
        label.set_size_request(320, 64)
        win.set_default_size(320, 64)

    step("recolhe (NONE)", collapse, 4.0)
    step("recolhido: checagem", check("R"), 4.6)
    step("C: EXCLUSIVE + present()", expand(LS.KeyboardMode.EXCLUSIVE), 5.0)
    step("C: checagem", check("C"), 5.7)
    step("C: foca outra janela via hyprctl", focus_back, 6.3)
    step("C: checagem após focar outra", check("C2"), 7.0)
    step("recolhe (NONE)", collapse, 7.5)

    def grab_then_demand():
        expand(LS.KeyboardMode.EXCLUSIVE)()
        GLib.timeout_add(250, lambda: (LS.set_keyboard_mode(win, LS.KeyboardMode.ON_DEMAND), log("  D: trocou para ON_DEMAND"), False)[-1])

    step("D: EXCLUSIVE -> 250ms -> ON_DEMAND", grab_then_demand, 8.0)
    step("D: checagem", check("D"), 8.8)
    step("D: foca outra janela via hyprctl", focus_back, 9.3)
    step("D: checagem após focar outra", check("D2"), 10.0)
    step("fim", lambda: (collapse(), focus_back(), app.quit()), 10.5)


app = Gtk.Application(application_id="dev.hyprspot.spike")
app.connect("activate", on_activate)
app.run([])
