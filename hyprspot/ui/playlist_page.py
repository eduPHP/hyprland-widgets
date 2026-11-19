"""Tela da playlist: voltar, capa, nome, Tocar/Aleatório e a fila "A seguir" (do Soloist)."""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from hyprspot.model import Playlist, Track  # noqa: E402
from hyprspot.ui.common import label  # noqa: E402
from hyprspot.ui.cover import ArtLoader, Cover  # noqa: E402
from hyprspot.ui.lists import make_list, track_row  # noqa: E402

PAGE_BLOCK = 30          # linhas desenhadas por vez
ROW_H = 46
LOAD_MORE_MARGIN = 2 * ROW_H  # desenha o próximo bloco quando faltam menos de 2 linhas


class PlaylistPage(Gtk.Box):
    def __init__(self, art: ArtLoader, on_back: Callable[[], None], on_play: Callable[[bool], None],
                 on_pick: Callable[[str], None]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=10, margin_end=10)
        self.art = art
        self.playlist: Playlist | None = None
        self._tracks: list[Track] = []
        self._current: str | None = None
        self._rendered = 0
        self._want = 0          # quantas linhas o usuário já carregou nesta playlist (não encolhe)
        self._starting = False  # acabou de mandar tocar: ainda não é o contexto

        self.back_btn = Gtk.Button(label="←")
        self.back_btn.add_css_class("hs-back")
        self.back_btn.set_halign(Gtk.Align.START)
        self.back_btn.set_focus_on_click(False)
        self.back_btn.connect("clicked", lambda *_: on_back())
        self.append(self.back_btn)

        head = Gtk.Box(spacing=12)
        head.add_css_class("hs-phead")
        self.cover = Cover(76, art, "big")
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER, hexpand=True)
        self.name = label("", "hs-ptitle")
        self.owner = label("", "hs-artist")
        btns = Gtk.Box(spacing=8, margin_top=6)
        self.play_btn = self._pill("media-playback-start-symbolic", "Tocar", "main", lambda: on_play(False))
        self.shuffle_btn = self._pill("media-playlist-shuffle-symbolic", "Aleatório", None, lambda: on_play(True))
        btns.append(self.play_btn)
        btns.append(self.shuffle_btn)
        for w in (self.name, self.owner, btns):
            texts.append(w)
        head.append(self.cover)
        head.append(texts)
        self.append(head)

        self.section = label("A seguir", "hs-section")
        self.append(self.section)

        self.not_playing = label("Esta playlist não está tocando", "hs-hint", ellipsize=False)
        self.not_playing.set_wrap(True)
        self.not_playing.set_visible(False)
        self.append(self.not_playing)

        self.list = make_list()
        self.list.connect("row-activated", lambda _l, row: on_pick(row.uri))
        self.list_box = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.list_box.set_child(self.list)
        self.list_box.get_vadjustment().connect("value-changed", self._on_scroll)
        self.append(self.list_box)

    def _pill(self, icon: str, text: str, kind: str | None, cb: Callable[[], None]) -> Gtk.Button:
        b = Gtk.Button()
        b.add_css_class("hs-pill")
        if kind:
            b.add_css_class(kind)
        b.set_focus_on_click(False)
        inner = Gtk.Box(spacing=6)
        img = Gtk.Image.new_from_icon_name(icon)
        img.set_pixel_size(13)
        inner.append(img)
        inner.append(Gtk.Label(label=text))
        b.set_child(inner)
        b.update_property([Gtk.AccessibleProperty.LABEL], [text])
        b.connect("clicked", lambda *_: cb())
        return b

    # -- conteúdo --
    def show(self, playlist: Playlist, back_label: str, starting: bool = False) -> None:
        self.playlist = playlist
        self._starting = starting
        self._want = 0
        self.back_btn.set_label(f"←  {back_label}")
        self.name.set_label(playlist.name)
        self.owner.set_label(f"Playlist · {playlist.owner}" if playlist.owner else "Playlist")
        self.cover.set_url(playlist.image_url)
        self._tracks, self._rendered = [], 0
        self._clear()
        self.list_box.get_vadjustment().set_value(0)

    def set_queue(self, context_uri: str | None, tracks: list[Track], current: Track | None,
                  playing_uri: str | None = None) -> None:
        """`context_uri`: de qual contexto é a fila; `playing_uri`: o que está tocando (padrão: o mesmo)."""
        if self.playlist is None:
            return
        uri = self.playlist.uri
        playing = context_uri if playing_uri is None else playing_uri
        if playing == uri:
            self._starting = False
        ready = playing == uri and context_uri == uri
        self.list_box.set_visible(ready)
        self.section.set_visible(ready)
        self.not_playing.set_visible(not ready)
        if not ready:
            loading = self._starting or playing == uri  # mandou tocar, ou a fila ainda é da anterior
            self.not_playing.set_label("Carregando…" if loading else "Esta playlist não está tocando")
            return
        # a fila do Soloist só traz as próximas: a faixa atual entra no topo
        items = list(tracks)
        if current is not None and (not items or items[0].uri != current.uri):
            items.insert(0, current)
        keep = max(PAGE_BLOCK, self._rendered, self._want)
        if items != self._tracks:
            adj = self.list_box.get_vadjustment()
            pos = adj.get_value()
            self._tracks = items
            self._clear()
            self._render_until(min(keep, len(items)))
            if pos:  # a lista é refeita a cada faixa: mantém a rolagem onde estava
                GLib.idle_add(lambda: (adj.set_value(min(pos, adj.get_upper() - adj.get_page_size())), False)[1])
        self._current = current.uri if current else None
        self._mark_first_current()

    def rendered(self) -> int:
        return self._rendered

    def load_more(self) -> None:
        self._render_until(min(self._rendered + PAGE_BLOCK, len(self._tracks)))
        self._want = max(self._want, self._rendered)

    # -- interno --
    def _clear(self) -> None:
        while (child := self.list.get_first_child()) is not None:
            self.list.remove(child)
        self._rendered = 0

    def _render_until(self, n: int) -> None:
        for t in self._tracks[self._rendered:n]:
            self.list.append(track_row(t, self.art))
        self._rendered = max(self._rendered, n)
        self._mark_first_current()

    def _mark_first_current(self) -> None:
        """Destaca só a primeira linha da faixa atual (ela pode se repetir mais adiante na fila)."""
        found = False
        row = self.list.get_first_child()
        while row is not None:
            hit = not found and bool(self._current) and row.uri == self._current
            row.set_active(hit)
            found = found or hit
            row = row.get_next_sibling()

    def _on_scroll(self, adj: Gtk.Adjustment) -> None:
        if adj.get_value() + adj.get_page_size() >= adj.get_upper() - LOAD_MORE_MARGIN:
            self.load_more()
