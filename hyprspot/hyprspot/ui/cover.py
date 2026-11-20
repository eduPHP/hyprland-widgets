"""Capas: carregamento assíncrono com cache em disco + memória."""
from __future__ import annotations

from collections import OrderedDict
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from hyprspot.cache import ArtCache, fetch_art  # noqa: E402


class ArtLoader:
    def __init__(self, cache: ArtCache, session, mem_items: int = 96):
        self.cache = cache
        self.session = session
        self._mem: OrderedDict[str, Gdk.Texture] = OrderedDict()
        self._mem_items = mem_items
        self._puts = 0

    def load(self, url: str, cb: Callable[[Gdk.Texture | None], None]) -> None:
        tex = self._mem.get(url)
        if tex is not None:
            self._mem.move_to_end(url)
            cb(tex)
            return

        def got(path):
            if path is None:
                cb(None)
                return
            try:
                tex = Gdk.Texture.new_from_filename(str(path))
            except GLib.Error:
                cb(None)
                return
            self._mem[url] = tex
            while len(self._mem) > self._mem_items:
                self._mem.popitem(last=False)
            self._puts += 1
            if self._puts % 50 == 0:
                self.cache.evict()
            cb(tex)

        fetch_art(self.cache, self.session, url, got)


class Cover(Gtk.Image):
    """Capa quadrada de tamanho fixo, com placeholder #24283b (CSS) enquanto carrega ou sem capa."""

    def __init__(self, size: int, loader: ArtLoader, variant: str = ""):
        super().__init__()
        self.loader = loader
        self._url: str | None = None
        self.set_pixel_size(size)
        self.set_size_request(size, size)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.add_css_class("hs-cover")
        if variant:
            self.add_css_class(variant)

    def set_url(self, url: str | None) -> None:
        if url == self._url:
            return
        self._url = url
        self.clear()
        if not url:
            return

        def done(tex):
            if self._url == url and tex is not None:
                self.set_from_paintable(tex)

        self.loader.load(url, done)
