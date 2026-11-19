"""Protocolo e cliente da API WebSocket local do Spotify Soloist."""
from __future__ import annotations

import json

from hyprspot.model import Track

BACKOFF_S = (0.5, 1, 2, 5)
COVER_PREF = ("large", "default", "xlarge", "small")


def build_command(name: str, **fields) -> str:
    return json.dumps({"type": "command", "command": name, **fields})


def repeat_commands(mode: str) -> list[tuple[str, dict]]:
    if mode == "track":
        return [("set_repeat_context", {"enabled": False}), ("set_repeat_track", {"enabled": True})]
    return [("set_repeat_track", {"enabled": False}),
            ("set_repeat_context", {"enabled": mode == "context"})]


def backoff_delay(attempt: int) -> float:
    return BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]


def _name(entity: dict | None) -> str:
    return ((entity or {}).get("decorations") or {}).get("identity", {}).get("name", "") or ""


def _unwrap(ref: dict | None) -> dict | None:
    """parent/creators vêm como {"entity": {...}}."""
    if not ref:
        return None
    return ref.get("entity", ref)


def _cover(decorations: dict) -> str | None:
    covers = (decorations.get("visual_identity") or {}).get("cover") or []
    by_size = {c.get("size"): c.get("url") for c in covers if c.get("url")}
    for size in COVER_PREF:
        if by_size.get(size):
            return by_size[size]
    return None


def entity_to_track(entity: dict | None) -> Track | None:
    if not entity or not entity.get("uri"):
        return None
    deco = entity.get("decorations") or {}
    artists = ", ".join(n for n in (_name(_unwrap(c)) for c in deco.get("creators") or []) if n)
    return Track(
        uri=entity["uri"],
        title=_name(entity),
        artist=artists,
        album=_name(_unwrap(deco.get("parent"))),
        duration_ms=int((deco.get("playback") or {}).get("duration_ms") or 0),
        art_url=_cover(deco),
    )


def parse_queue(ev: dict) -> list[Track]:
    """Próximas faixas de um `queue_changed`, na ordem em que vão tocar (só músicas)."""
    out = []
    for entry in ev.get("upcoming") or []:
        if (entry or {}).get("source", "context") != "context":
            continue  # posta na fila à mão ou autoplay: não é da playlist
        t = entity_to_track((entry or {}).get("item"))
        if t is not None and t.uri.startswith("spotify:track:"):
            out.append(t)
    return out


# ---------------------------------------------------------------------------
# Cliente (GLib main loop + libsoup3). Sem teste unitário: verificado à mão.
# ---------------------------------------------------------------------------
import gi  # noqa: E402

gi.require_version("Soup", "3.0")
from gi.repository import GLib, GObject, Soup  # noqa: E402


class SoloistClient(GObject.Object):
    __gsignals__ = {
        "event": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "connection-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self, url: str = "ws://127.0.0.1:9090"):
        super().__init__()
        self.url = url
        self._session = Soup.Session()
        self._conn: Soup.WebsocketConnection | None = None
        self._attempt = 0
        self._retry_id = 0
        self._stopped = True

    @property
    def connected(self) -> bool:
        return self._conn is not None and self._conn.get_state() == Soup.WebsocketState.OPEN

    def start(self) -> None:
        self._stopped = False
        self._connect()

    def stop(self) -> None:
        self._stopped = True
        if self._retry_id:
            GLib.source_remove(self._retry_id)
            self._retry_id = 0
        if self._conn is not None:
            self._conn.close(Soup.WebsocketCloseCode.NORMAL, None)

    def send(self, name: str, **fields) -> bool:
        if not self.connected:
            return False
        self._conn.send_text(build_command(name, **fields))
        return True

    # -- interno --
    def _connect(self) -> None:
        msg = Soup.Message.new("GET", self.url)
        self._session.websocket_connect_async(msg, None, None, GLib.PRIORITY_DEFAULT, None, self._on_connected)

    def _on_connected(self, session, result) -> None:
        try:
            conn = session.websocket_connect_finish(result)
        except GLib.Error:
            self._schedule_retry()
            return
        self._configure(conn)
        conn.connect("message", self._on_message)
        conn.connect("closed", self._on_closed)
        self._conn = conn
        self._attempt = 0
        self.emit("connection-changed", True)

    @staticmethod
    def _configure(conn) -> None:
        conn.set_keepalive_interval(15)
        conn.set_keepalive_pong_timeout(30)   # detecta um Soloist travado (não só encerrado)
        conn.set_max_incoming_payload_size(0)  # queue_changed de playlists longas passa de 128 KiB

    def _on_message(self, _conn, _type, data) -> None:
        try:
            ev = json.loads(data.get_data().decode())
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(ev, dict):
            self.emit("event", ev)

    def _on_closed(self, _conn) -> None:
        self._conn = None
        self.emit("connection-changed", False)
        self._schedule_retry()

    def _schedule_retry(self) -> None:
        if self._stopped or self._retry_id:
            return
        delay = backoff_delay(self._attempt)
        self._attempt += 1

        def retry():
            self._retry_id = 0
            if not self._stopped:
                self._connect()
            return False

        self._retry_id = GLib.timeout_add(int(delay * 1000), retry)
