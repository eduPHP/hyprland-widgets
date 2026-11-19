"""Modo compacto: capa, título/artista, três botões e progresso de 2 px."""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from hyprspot.presenter import now_playing  # noqa: E402
from hyprspot.state import AppState, clock_ms, position_now  # noqa: E402
from hyprspot.ui.common import (KEY_NEXT, KEY_PLAY, KEY_PREV, icon_button, label, set_icon,  # noqa: E402
                                toggle_class, with_key)
from hyprspot.ui.cover import ArtLoader, Cover  # noqa: E402

OnCommand = Callable[[str, dict], None]

_COMPACT_KEYS = {"q": "quit", "Q": "quit", "space": "play-pause", "Left": "prev", "Right": "next"}


def compact_key_action(keyval: int) -> str | None:
    """Teclas aceitas com o mouse sobre o widget compacto."""
    from gi.repository import Gdk

    return _COMPACT_KEYS.get(Gdk.keyval_name(keyval) or "")


def context_name(state: AppState) -> str | None:
    uri = state.playback.context_uri
    return next((p.name for p in state.playlists if p.uri == uri), None) if uri else None


class CompactView(Gtk.Overlay):
    WIDTH, HEIGHT = 320, 64

    def __init__(self, state: AppState, on_command: OnCommand, on_expand: Callable[[], None], art: ArtLoader):
        super().__init__()
        self.state = state
        self.on_command = on_command
        self.set_size_request(self.WIDTH, self.HEIGHT)
        self.add_css_class("hs-clip")
        self.set_overflow(Gtk.Overflow.HIDDEN)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, vexpand=True, hexpand=True)
        root.add_css_class("hs-root")
        root.add_css_class("hs-compact")
        self.set_child(root)

        hit = Gtk.Button()
        hit.add_css_class("hs-hit")
        hit.set_hexpand(True)
        hit.set_focus_on_click(False)
        hit.update_property([Gtk.AccessibleProperty.LABEL], ["Expandir o hyprspot"])
        hit.connect("clicked", lambda *_: on_expand())
        inner = Gtk.Box(spacing=10)
        self.cover = Cover(48, art)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER, hexpand=True)
        self.title = label("", "hs-title")
        self.artist = label("", "hs-artist")
        texts.append(self.title)
        texts.append(self.artist)
        inner.append(self.cover)
        inner.append(texts)
        hit.set_child(inner)
        root.append(hit)

        self.prev = icon_button("media-skip-backward-symbolic", with_key("Faixa anterior", KEY_PREV),
                                lambda: on_command("skip_prev", {}))
        self.play = icon_button("media-playback-start-symbolic", with_key("Tocar", KEY_PLAY), self._toggle_play,
                                "hs-play")
        self.next = icon_button("media-skip-forward-symbolic", with_key("Próxima faixa", KEY_NEXT),
                                lambda: on_command("skip_next", {}))
        controls = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        for b in (self.prev, self.play, self.next):
            controls.append(b)
        root.append(controls)

        self.progress = Gtk.ProgressBar(valign=Gtk.Align.END)
        self.progress.add_css_class("hs-progress")
        self.progress.set_can_target(False)
        self.add_overlay(self.progress)

        self.dot = Gtk.Box(halign=Gtk.Align.START, valign=Gtk.Align.START, margin_start=6, margin_top=6)
        self.dot.add_css_class("hs-dot")
        self.dot.set_can_target(False)
        self.add_overlay(self.dot)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        state.connect("changed", self._on_changed)
        self._refresh()
        GLib.timeout_add(250, self._tick)

    def _toggle_play(self) -> None:
        self.on_command("pause" if self.state.playback.status in ("playing", "buffering") else "play", {})

    def _on_scroll(self, _ctrl, _dx, dy) -> bool:
        if not self.state.playback.connected:
            return False
        step = -5 if dy > 0 else 5
        self.on_command("set_volume", {"volume": max(0, min(100, self.state.playback.volume + step))})
        return True

    def _on_changed(self, _state, what: str) -> None:
        if what in ("playback", "playlists", "error", "soloist"):
            self._refresh()

    def _refresh(self) -> None:
        v = now_playing(self.state.playback, context_name(self.state), self.state.soloist_days_left)
        self.title.set_label(v.title)
        self.artist.set_label(v.subtitle)
        toggle_class(self.artist, "hs-error", v.offline or bool(self.state.error))
        if self.state.error and not v.offline:
            self.artist.set_label(self.state.error)
        self.cover.set_url(v.art_url)
        for b in (self.prev, self.play, self.next):
            b.set_sensitive(v.enabled)
        set_icon(self.play, "media-playback-pause-symbolic" if v.playing else "media-playback-start-symbolic")
        self.play.set_tooltip_text(with_key("Pausar" if v.playing else "Tocar", KEY_PLAY))
        self.dot.set_visible(v.offline or bool(self.state.error))
        toggle_class(self.progress, "paused", v.paused)
        self._tick()

    def _tick(self) -> bool:
        pb = self.state.playback
        dur = pb.track.duration_ms if pb.track else 0
        self.progress.set_fraction(position_now(pb, clock_ms()) / dur if dur else 0.0)
        return True
