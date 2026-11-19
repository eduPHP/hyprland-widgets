"""Linhas das listas de playlists e de faixas (Gtk.ListBox)."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from hyprspot.model import Playlist, Track  # noqa: E402
from hyprspot.presenter import fmt_time  # noqa: E402
from hyprspot.ui.common import label, toggle_class  # noqa: E402
from hyprspot.ui.cover import ArtLoader, Cover  # noqa: E402


class ItemRow(Gtk.ListBoxRow):
    """Uma linha de 46 px: capa 34, nome + meta, texto à direita e indicador de 'tocando'."""

    def __init__(self, uri: str, name: str, meta: str, art_url: str | None, right: str, art: ArtLoader):
        super().__init__()
        self.uri = uri
        box = Gtk.Box(spacing=10)
        box.add_css_class("hs-row")
        self._box = box
        cover = Cover(34, art, "small")
        cover.set_url(art_url)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, valign=Gtk.Align.CENTER, hexpand=True)
        texts.append(label(name, "hs-name"))
        texts.append(label(meta, "hs-meta"))
        box.append(cover)
        box.append(texts)
        if right:
            r = Gtk.Label(label=right)
            r.add_css_class("hs-mono")
            box.append(r)
        self.eq = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
        self.eq.add_css_class("hs-eq")
        self.eq.set_visible(False)
        box.append(self.eq)
        self.set_child(box)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{name}, {meta}"])

    def set_active(self, active: bool) -> None:
        toggle_class(self._box, "active", active)
        self.eq.set_visible(active)


def playlist_row(p: Playlist, art: ArtLoader) -> ItemRow:
    return ItemRow(p.uri, p.name, f"Playlist · {p.owner}" if p.owner else "Playlist", p.image_url, "", art)


def track_row(t: Track, art: ArtLoader) -> ItemRow:
    return ItemRow(t.uri, t.title, t.artist, t.art_url, fmt_time(t.duration_ms) if t.duration_ms else "", art)


def make_list() -> Gtk.ListBox:
    lb = Gtk.ListBox()
    lb.add_css_class("hs-list")
    lb.set_selection_mode(Gtk.SelectionMode.SINGLE)
    lb.set_activate_on_single_click(True)
    return lb


def replace_rows(lb: Gtk.ListBox, rows: list[ItemRow]) -> None:
    while (child := lb.get_first_child()) is not None:
        lb.remove(child)
    for r in rows:
        lb.append(r)
