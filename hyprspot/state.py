"""Estado da aplicação: reducer puro dos eventos do Soloist, otimismo e throttle."""
from __future__ import annotations

import time
from dataclasses import dataclass, replace

import gi

gi.require_version("GObject", "2.0")
from gi.repository import GObject  # noqa: E402

from hyprspot.model import Playlist, Track  # noqa: E402
from hyprspot.soloist import entity_to_track  # noqa: E402


@dataclass(frozen=True)
class Playback:
    connected: bool = False
    logged_in: bool = False
    is_active: bool = False
    status: str = "idle"
    track: Track | None = None
    context_uri: str | None = None
    context_name: str | None = None
    position_ms: int = 0
    position_ts_ms: int = 0
    speed: float = 0.0
    volume: int = 50
    shuffle: bool = False
    repeat: str = "off"
    device_name: str = "hyprspot"


def clock_ms() -> int:
    return int(time.time() * 1000)


def _position(pb: Playback, pos: dict | None) -> Playback:
    if not pos:
        return pb
    return replace(pb, position_ms=int(pos.get("position_ms", 0)),
                   position_ts_ms=int(pos.get("timestamp_ms", 0)), speed=float(pos.get("speed", 0) or 0))


def _options(pb: Playback, opts: dict | None) -> Playback:
    if not opts:
        return pb
    return replace(pb, shuffle=bool(opts.get("shuffle", pb.shuffle)), repeat=opts.get("repeat", pb.repeat))


def apply_event(pb: Playback, ev: dict) -> Playback:
    t = ev.get("type")
    if t == "_connected":
        return replace(pb, connected=True)
    if t == "_disconnected":
        return replace(pb, connected=False, logged_in=False, status="idle", speed=0.0, track=None)
    if t == "auth_state":
        return replace(pb, logged_in=bool(ev.get("logged_in")), is_active=bool(ev.get("is_active", pb.is_active)),
                       device_name=ev.get("device_name") or pb.device_name)
    if t == "playback_state":
        pb = replace(pb, status=ev.get("status", pb.status),
                     track=entity_to_track(ev.get("item")),
                     context_uri=(ev.get("context") or {}).get("uri") or None,
                     volume=int(ev.get("volume", pb.volume)), is_active=bool(ev.get("is_active", pb.is_active)))
        return _options(_position(pb, ev.get("position")), ev.get("options"))
    if t == "playback_changed":
        return replace(pb, status=ev.get("status", pb.status))
    if t == "track_changed":
        return replace(pb, track=entity_to_track(ev.get("item")))
    if t == "context_changed":
        return replace(pb, context_uri=(ev.get("context") or {}).get("uri") or None)
    if t == "volume_changed":
        return replace(pb, volume=int(ev.get("volume", pb.volume)))
    if t == "device_changed":
        return replace(pb, is_active=bool(ev.get("is_active")), device_name=ev.get("device_name") or pb.device_name)
    if t == "options_changed":
        return _options(pb, ev.get("options"))
    if t == "position_sync":
        return _position(pb, ev.get("position"))
    return pb


def position_now(pb: Playback, at_ms: int) -> int:
    pos = pb.position_ms + (at_ms - pb.position_ts_ms) * pb.speed if pb.speed else pb.position_ms
    hi = pb.track.duration_ms if pb.track and pb.track.duration_ms else None
    pos = max(0, int(pos))
    return min(pos, hi) if hi is not None else pos


def optimistic(pb: Playback, command: str, now_ms: int | None = None, **fields) -> Playback:
    if command == "play":
        return replace(pb, status="playing")
    if command == "pause":
        return replace(pb, status="paused")
    if command in ("skip_next", "skip_prev"):
        return replace(pb, status="buffering")
    if command == "set_volume":
        return replace(pb, volume=int(fields["volume"]))
    if command == "set_shuffle":
        return replace(pb, shuffle=bool(fields["enabled"]))
    if command == "seek":
        at = clock_ms() if now_ms is None else now_ms
        return replace(pb, position_ms=int(fields["position_ms"]), position_ts_ms=at)
    return pb


PLAYLISTS_MAX_AGE_S = 600


def should_refresh_playlists(last_ok: float | None, now: float, has_items: bool) -> bool:
    """Recarregar quando nunca deu certo, quando a lista está vazia ou quando passou de 10 min."""
    return last_ok is None or not has_items or now - last_ok > PLAYLISTS_MAX_AGE_S


class Throttle:
    """Deixa passar no máximo um valor por intervalo; flush entrega o último pendente."""

    def __init__(self, interval_ms: int = 100):
        self.interval_ms = interval_ms
        self._last_sent_ms: int | None = None
        self._pending = None
        self._has_pending = False

    def push(self, value, at_ms: int):
        if self._last_sent_ms is None or at_ms - self._last_sent_ms >= self.interval_ms:
            self._last_sent_ms = at_ms
            self._has_pending = False
            return value
        self._pending, self._has_pending = value, True
        return None

    def flush(self, at_ms: int):
        if not self._has_pending:
            return None
        self._has_pending = False
        self._last_sent_ms = at_ms
        return self._pending


class AppState(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self):
        super().__init__()
        self.playback = Playback()
        self.playlists: list[Playlist] = []
        self.search_query = ""
        self.search_results: list[Track] = []
        self.ui_mode = "compact"
        self.tab = "playlists"
        self.error: str | None = None
        self.playlists_error: str | None = None
        self.liked: bool | None = None
        self.soloist_days_left: int | None = None

    def set_playback(self, pb: Playback) -> None:
        if pb != self.playback:
            self.playback = pb
            self.emit("changed", "playback")

    def set_playlists(self, items: list[Playlist]) -> None:
        if items != self.playlists:
            self.playlists = list(items)
            self.emit("changed", "playlists")

    def set_playlists_error(self, msg: str | None) -> None:
        if msg != self.playlists_error:
            self.playlists_error = msg
            self.emit("changed", "playlists")

    def set_search(self, query: str, results: list[Track]) -> None:
        if (query, results) != (self.search_query, self.search_results):
            self.search_query, self.search_results = query, list(results)
            self.emit("changed", "search")

    def set_ui(self, mode: str | None = None, tab: str | None = None) -> None:
        new = (mode or self.ui_mode, tab or self.tab)
        if new != (self.ui_mode, self.tab):
            self.ui_mode, self.tab = new
            self.emit("changed", "ui")

    def set_error(self, msg: str | None) -> None:
        if msg != self.error:
            self.error = msg
            self.emit("changed", "error")

    def set_soloist_days(self, days: int | None) -> None:
        if days != self.soloist_days_left:
            self.soloist_days_left = days
            self.emit("changed", "soloist")

    def set_liked(self, liked: bool | None) -> None:
        if liked != self.liked:
            self.liked = liked
            self.emit("changed", "liked")
