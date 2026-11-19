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


class NoArt:
    def load(self, url, cb):
        pass


def test_arrows_skip_tracks_in_compact():
    # com o mouse sobre o compacto: ← volta, → avança (as teclas de mídia ficam com o sistema)
    from gi.repository import Gdk
    from hyprspot.ui.compact import compact_key_action
    assert compact_key_action(Gdk.KEY_Left) == "prev"
    assert compact_key_action(Gdk.KEY_Right) == "next"


def test_arrows_skip_tracks_in_expanded_unless_typing():
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gtk
    Gtk.init()
    from hyprspot.state import AppState
    from hyprspot.ui.expanded import ExpandedView

    sent = []
    v = ExpandedView(AppState(), lambda n, f: sent.append(n), lambda *_: None, lambda: None, lambda: None, NoArt())
    assert v._on_key(None, Gdk.KEY_Right, 0, 0) is True
    assert v._on_key(None, Gdk.KEY_Left, 0, 0) is True
    assert sent == ["skip_next", "skip_prev"]


@pytest.mark.parametrize("which", ["compact", "expanded"])
def test_player_buttons_show_their_shortcut(which):
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.init()
    from hyprspot.state import AppState
    from hyprspot.ui.compact import CompactView
    from hyprspot.ui.expanded import ExpandedView

    noop = lambda *_a, **_k: None  # noqa: E731
    v = (CompactView(AppState(), noop, noop, NoArt()) if which == "compact"
         else ExpandedView(AppState(), noop, noop, noop, noop, NoArt()))
    assert v.prev.get_tooltip_text() == "Faixa anterior (←)"
    assert v.next.get_tooltip_text() == "Próxima faixa (→)"
    assert v.play.get_tooltip_text() == "Tocar (Espaço)"


# ---------------------------------------------------------------- navegação até a tela da playlist
@pytest.fixture
def nav():
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.init()
    from hyprspot.model import Playlist
    from hyprspot.state import AppState
    from hyprspot.ui.expanded import ExpandedView

    calls = []
    state = AppState()
    state.set_playlists([Playlist("spotify:playlist:a", "Rock", "eu", None)])
    v = ExpandedView(state, lambda n, f: calls.append(("cmd", n)), lambda *_: None,
                     lambda: calls.append(("collapse",)), lambda: None, NoArt(),
                     on_open_playlist=lambda p, tab: calls.append(("open", p.uri, tab)),
                     on_play_playlist=lambda uri, sh: calls.append(("play", uri, sh)),
                     on_pick_track=lambda ctx, uri: calls.append(("pick", ctx, uri)))
    return state, v, calls


def test_expanded_is_600_tall():
    from hyprspot.ui.expanded import ExpandedView
    assert (ExpandedView.WIDTH, ExpandedView.HEIGHT) == (360, 600)


def test_clicking_playlist_calls_open(nav):
    state, v, calls = nav
    v.pl_list.emit("row-activated", v.pl_list.get_row_at_index(0))
    assert calls == [("open", "spotify:playlist:a", "playlists")]


def test_escape_goes_back_before_collapsing(nav):
    from gi.repository import Gdk
    state, v, calls = nav
    v.open_playlist(state.playlists[0], "playlists")
    assert v.page_open("playlists")
    assert v._on_key(None, Gdk.KEY_Escape, 0, 0) is True
    assert not v.page_open("playlists") and calls == []
    v._on_key(None, Gdk.KEY_Escape, 0, 0)
    assert calls == [("collapse",)]


def test_switching_tabs_keeps_open_page(nav):
    state, v, _ = nav
    v.open_playlist(state.playlists[0], "playlists")
    v.show_tab("search")
    v.show_tab("playlists")
    assert v.page_open("playlists")


def test_slash_returns_to_search_results(nav):
    from gi.repository import Gdk
    state, v, _ = nav
    v.show_tab("search")
    v.open_playlist(state.playlists[0], "search")
    v.show_tab("playlists")
    v._on_key(None, Gdk.KEY_slash, 0, 0)
    assert state.tab == "search" and not v.page_open("search")


def test_page_buttons_reach_the_app(nav):
    from hyprspot.model import Track
    state, v, calls = nav
    state.set_queue("spotify:playlist:a", [Track("spotify:track:x", "X", "B")])
    v.open_playlist(state.playlists[0], "playlists")
    page = v.pages["playlists"]
    page.shuffle_btn.emit("clicked")
    page.list.emit("row-activated", page.list.get_row_at_index(0))
    assert calls == [("play", "spotify:playlist:a", True), ("pick", "spotify:playlist:a", "spotify:track:x")]


def test_open_page_follows_the_queue(nav):
    from hyprspot.model import Track
    from hyprspot.state import Playback
    state, v, _ = nav
    v.open_playlist(state.playlists[0], "playlists")
    state.set_playback(Playback(connected=True, track=Track("spotify:track:now", "Agora", "B"),
                                context_uri="spotify:playlist:a"))
    state.set_queue("spotify:playlist:a", [Track("spotify:track:n1", "Próxima", "B")])
    page = v.pages["playlists"]
    assert page.rendered() == 2 and page.list_box.get_visible()


# ---------------------------------------------------------------- busca com playlists
def _search_state(state, n_playlists=8):
    from hyprspot.model import Playlist, Track
    pls = [Playlist(f"spotify:playlist:s{i}", f"Lista {i}", "dono", None) for i in range(n_playlists)]
    tracks = [Track(f"spotify:track:t{i}", f"Música {i}", "B") for i in range(3)]
    state.set_search("rock", tracks, pls)
    return pls, tracks


def test_search_shows_at_most_five_playlists(nav):
    state, v, _ = nav
    _search_state(state)
    n, r = 0, v.sr_pl_list.get_first_child()
    while r is not None:
        n, r = n + 1, r.get_next_sibling()
    assert n == 5 and v.sr_pl_box.get_visible()


def test_search_without_playlists_hides_section(nav):
    state, v, _ = nav
    _search_state(state, 0)
    assert not v.sr_pl_box.get_visible()


def test_enter_still_plays_first_track(nav):
    state, v, calls = nav
    _, tracks = _search_state(state)
    sent = []
    v.on_command = lambda n, f: sent.append((n, f))
    v._play_selected_or_first()
    assert sent == [("play", {"uri": tracks[0].uri})]


def test_search_playlist_opens_in_search_tab(nav):
    state, v, calls = nav
    pls, _ = _search_state(state)
    v.sr_pl_list.emit("row-activated", v.sr_pl_list.get_row_at_index(1))
    assert calls == [("open", pls[1].uri, "search")]


def test_more_search_pages_keep_playlists(nav):
    state, v, _ = nav
    pls, tracks = _search_state(state)
    state.set_search("rock", tracks + tracks)          # página seguinte: só músicas
    assert state.search_playlists == pls
