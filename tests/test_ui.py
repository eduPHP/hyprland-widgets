"""Testes de widgets GTK (precisam de um display Wayland)."""
import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("WAYLAND_DISPLAY"), reason="sem display")


@pytest.fixture
def view():
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.init()
    from hyprspot.state import AppState
    from hyprspot.ui.expanded import ExpandedView

    class NoArt:
        def load(self, url, cb):
            pass

    state = AppState()
    v = ExpandedView(state, lambda *_: None, lambda *_: None, lambda: None, lambda: None, NoArt())
    return state, v


def test_search_error_hint_clears(view):
    state, v = view
    state.set_search("q", [])
    state.set_error("Erro X")
    assert "Erro X" in v.sr_hint.get_label()
    state.set_error(None)
    assert "Erro X" not in v.sr_hint.get_label()


def test_search_error_resets_loading_more(view):
    state, v = view
    v._loading_more = True
    state.set_error("falhou")
    assert v._loading_more is False


def test_playlists_hint_shows_persistent_error(view):
    state, v = view
    state.set_playlists_error("Sem conexão com o Spotify")
    assert v.pl_hint.get_visible()
    assert v.pl_hint.get_label() == "Sem conexão com o Spotify"


def test_q_quits_expanded_when_not_typing():
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gtk
    Gtk.init()
    from hyprspot.state import AppState
    from hyprspot.ui.expanded import ExpandedView

    class NoArt:
        def load(self, url, cb):
            pass

    quits = []
    v = ExpandedView(AppState(), lambda *_: None, lambda *_: None, lambda: None, lambda: None, NoArt(),
                     on_quit=lambda: quits.append(1))
    assert v._on_key(None, Gdk.KEY_q, 0, 0) is True
    assert quits == [1]


def test_compact_key_action():
    from gi.repository import Gdk
    from hyprspot.ui.compact import compact_key_action
    assert compact_key_action(Gdk.KEY_q) == "quit"
    assert compact_key_action(Gdk.KEY_Q) == "quit"
    assert compact_key_action(Gdk.KEY_space) == "play-pause"
    assert compact_key_action(Gdk.KEY_a) is None
