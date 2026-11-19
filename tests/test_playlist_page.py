"""Tela da playlist (precisa de um display Wayland)."""
import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("WAYLAND_DISPLAY"), reason="sem display")

PL = "spotify:playlist:p"


class NoArt:
    def load(self, url, cb):
        pass


@pytest.fixture
def page():
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.init()
    from hyprspot.model import Playlist
    from hyprspot.ui.playlist_page import PlaylistPage

    calls = []
    p = PlaylistPage(NoArt(), on_back=lambda: calls.append(("back",)),
                     on_play=lambda shuffle: calls.append(("play", shuffle)),
                     on_pick=lambda uri: calls.append(("pick", uri)))
    p.show(Playlist(PL, "Green Day as melhores", "Rogério", None), "Playlists")
    return p, calls


def tracks(n):
    from hyprspot.model import Track
    return [Track(f"spotify:track:{i}", f"Faixa {i}", "Banda", "", 180_000) for i in range(n)]


def rows(lb):
    out, r = [], lb.get_first_child()
    while r is not None:
        out.append(r)
        r = r.get_next_sibling()
    return out


def test_page_renders_first_block_then_more(page):
    p, _ = page
    p.set_queue(PL, tracks(75), None)
    assert p.rendered() == 30
    p.load_more()
    assert p.rendered() == 60
    p.load_more()
    assert p.rendered() == 75
    p.load_more()
    assert p.rendered() == 75


def test_queue_update_keeps_what_was_already_shown(page):
    # a fila muda a cada faixa; quem já rolou até 60 não pode voltar para 30
    p, _ = page
    p.set_queue(PL, tracks(75), None)
    p.load_more()
    p.set_queue(PL, tracks(74), None)
    assert p.rendered() == 60


def test_page_puts_current_track_on_top_highlighted(page):
    # a fila do Soloist só traz as próximas; a que está tocando entra no topo, em destaque
    from hyprspot.model import Track
    p, _ = page
    cur = Track("spotify:track:atual", "Atual", "Banda")
    p.set_queue(PL, tracks(3), cur)
    rs = rows(p.list)
    assert [r.uri for r in rs][:2] == ["spotify:track:atual", "spotify:track:0"]
    assert [r.uri for r in rs if r.eq.get_visible()] == ["spotify:track:atual"]


def test_current_already_first_is_not_duplicated(page):
    p, _ = page
    tr = tracks(3)
    p.set_queue(PL, tr, tr[0])
    assert len(rows(p.list)) == 3


def test_page_shows_not_playing_for_other_context(page):
    p, _ = page
    p.set_queue("spotify:playlist:outra", tracks(5), None)
    assert not p.list_box.get_visible() and p.not_playing.get_visible()
    p.set_queue(PL, tracks(5), None)
    assert p.list_box.get_visible() and not p.not_playing.get_visible()


def test_page_header_shows_playlist(page):
    p, _ = page
    assert p.name.get_label() == "Green Day as melhores"
    assert p.owner.get_label() == "Playlist · Rogério"
    assert "Playlists" in p.back_btn.get_label()


def test_page_buttons_call_back(page):
    p, calls = page
    p.set_queue(PL, tracks(5), None)
    p.play_btn.emit("clicked")
    p.shuffle_btn.emit("clicked")
    p.back_btn.emit("clicked")
    p.list.emit("row-activated", rows(p.list)[2])
    assert calls == [("play", False), ("play", True), ("back",), ("pick", "spotify:track:2")]
