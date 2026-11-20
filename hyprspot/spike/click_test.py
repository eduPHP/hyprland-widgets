"""Spike descartável, interativo (~25 s): o que o Hyprland faz com cliques de verdade.

Fase 1 (ON_DEMAND): clique no retângulo e depois em outra janela.
Fase 2 (EXCLUSIVE): o retângulo cresce e pega o foco; clique em outra janela.
Tudo vai para spike/click_test.log (is-active + eventos do socket do Hyprland).

Uso: LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libgtk4-layer-shell.so.0 python3 click_test.py
"""
import os
import socket
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

T0 = time.monotonic()
LOG = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "click_test.log"), "w")
INTERESTING = ("activewindow>>", "activewindowv2", "workspace>>", "focusedmon", "openlayer", "closelayer")


def log(msg):
    line = f"{time.monotonic() - T0:6.2f}s  {msg}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def watch_hypr():
    path = os.path.join(os.environ["XDG_RUNTIME_DIR"], "hypr", os.environ["HYPRLAND_INSTANCE_SIGNATURE"], ".socket2.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(path)
    buf = b""
    while True:
        data = s.recv(4096)
        if not data:
            return
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            text = line.decode(errors="replace")
            if text.startswith(INTERESTING):
                log(f"  hypr: {text[:90]}")


def on_activate(app):
    win = Gtk.ApplicationWindow(application=app)
    LS.init_for_window(win)
    LS.set_namespace(win, "hyprspot-spike")
    LS.set_layer(win, LS.Layer.OVERLAY)
    LS.set_anchor(win, LS.Edge.BOTTOM, True)
    LS.set_anchor(win, LS.Edge.RIGHT, True)
    LS.set_margin(win, LS.Edge.BOTTOM, 16)
    LS.set_margin(win, LS.Edge.RIGHT, 16)
    LS.set_keyboard_mode(win, LS.KeyboardMode.ON_DEMAND)
    label = Gtk.Label(label="FASE 1: clique aqui,\ndepois clique em outra janela")
    label.set_size_request(320, 90)
    win.set_child(label)
    win.connect("notify::is-active", lambda w, _p: log(f"is-active -> {w.is_active()}"))
    click = Gtk.GestureClick()
    click.connect("pressed", lambda *_a: log("clique DENTRO do widget"))
    win.add_controller(click)
    win.present()
    log("FASE 1 (ON_DEMAND): clique no widget e depois em outra janela")

    def phase2():
        log("FASE 2 (EXCLUSIVE): clique em outra janela")
        label.set_text("FASE 2: pegou o foco.\nAgora clique em outra janela")
        label.set_size_request(360, 520)
        LS.set_keyboard_mode(win, LS.KeyboardMode.EXCLUSIVE)
        win.present()
        return False

    def finish():
        log("fim")
        app.quit()
        return False

    GLib.timeout_add_seconds(12, phase2)
    GLib.timeout_add_seconds(25, finish)


threading.Thread(target=watch_hypr, daemon=True).start()
app = Gtk.Application(application_id="dev.hyprspot.clicktest")
app.connect("activate", on_activate)
app.run([])
