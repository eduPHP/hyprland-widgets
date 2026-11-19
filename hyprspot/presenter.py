"""Traduz o estado do player para o que a UI mostra (puro, testável)."""
from __future__ import annotations

from dataclasses import dataclass

from hyprspot.state import Playback


@dataclass(frozen=True)
class NowPlaying:
    title: str
    subtitle: str
    detail: str
    art_url: str | None
    enabled: bool
    offline: bool
    playing: bool
    paused: bool


def now_playing(pb: Playback, context_name: str | None = None, soloist_days: int | None = None) -> NowPlaying:
    if not pb.connected and soloist_days is not None and soloist_days <= 0:
        return NowPlaying("Soloist expirou", "Rode hyprspot soloist-update", "", None, False, True, False, True)
    if not pb.connected:
        return NowPlaying("Sem conexão", "Reconectando ao Soloist…", "", None,
                          False, True, False, True)
    if not pb.logged_in:
        return NowPlaying("Pareamento pendente", "Escolha hyprspot no celular", "", None,
                          False, False, False, True)
    if pb.track is None:
        return NowPlaying("Nada tocando", "Clique para escolher", "", None, True, False, False, True)
    t = pb.track
    detail = " · ".join(x for x in (t.artist, context_name) if x)
    playing = pb.status in ("playing", "buffering")
    return NowPlaying(t.title or "Sem título", t.artist, detail, t.art_url, True, False, playing, not playing)


def fmt_time(ms: int) -> str:
    s = max(0, int(ms)) // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
