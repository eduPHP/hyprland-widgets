"""Foco de teclado do compacto ao passar o mouse (precisa de display Wayland)."""
import os
import types

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("WAYLAND_DISPLAY"), reason="sem display")


class FakeLS:
    """Substitui o Gtk4LayerShell: registra os modos de teclado pedidos."""
    Edge = types.SimpleNamespace(TOP="top", BOTTOM="bottom", LEFT="left", RIGHT="right")
    Layer = types.SimpleNamespace(OVERLAY="overlay")
    KeyboardMode = types.SimpleNamespace(NONE="none", EXCLUSIVE="exclusive", ON_DEMAND="on_demand")

    def __init__(self):
        self.modes = []

    def set_keyboard_mode(self, _win, mode):
        self.modes.append(mode)

    def __getattr__(self, _name):
        return lambda *a, **k: None


@pytest.fixture
def win(monkeypatch):
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.init()
    from hyprspot.config import Config
    from hyprspot.ui import window as W

    ls = FakeLS()
    monkeypatch.setattr(W, "LS", ls)
    monkeypatch.setattr(W, "EDGES", {"top": "top", "bottom": "bottom", "left": "left", "right": "right"})
    compact = Gtk.Box()
    compact.set_size_request(320, 64)
    w = W.HyprspotWindow(None, Config(client_id=""), compact, None, lambda m: None)
    w.get_width = lambda: 320
    w.get_height = lambda: 64
    yield w, ls
    w.destroy()


def test_hover_grab_is_released_when_pointer_moves_outside(win):
    # Com o teclado exclusivo, o Hyprland segue mandando o movimento para o widget e
    # não manda "leave": o widget prendia teclado e mouse depois que o cursor saía.
    w, ls = win
    w._grab_keyboard()
    assert ls.modes[-1] == "exclusive"
    w._on_motion(None, 296, 30)                   # ainda dentro
    assert ls.modes[-1] == "exclusive"
    w._on_motion(None, 296, -4)                   # saiu por cima
    assert ls.modes[-1] == "none"


@pytest.mark.parametrize("x,y", [(-1, 30), (320, 30), (100, 64)])
def test_any_edge_releases_the_grab(win, x, y):
    w, ls = win
    w._grab_keyboard()
    w._on_motion(None, x, y)
    assert ls.modes[-1] == "none"


def test_motion_outside_in_expanded_mode_keeps_keyboard(win):
    w, ls = win
    w.mode = "expanded"
    n = len(ls.modes)
    w._on_motion(None, 10, -50)
    assert len(ls.modes) == n


def test_motion_inside_after_edge_event_still_schedules_the_grab(win):
    # Ao entrar, o primeiro movimento pode chegar com y = -1; o seguinte, já dentro,
    # precisa agendar a captura do teclado de novo.
    w, ls = win
    w._hover_enter()
    w._on_motion(None, 296, -1)
    assert w._hover_id == 0
    w._on_motion(None, 296, 5)
    assert w._hover_id != 0


def test_motion_outside_without_grab_does_nothing(win):
    w, ls = win
    n = len(ls.modes)
    w._on_motion(None, 296, -30)
    assert len(ls.modes) == n
