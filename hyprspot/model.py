"""Tipos de dados compartilhados."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Track:
    uri: str
    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0
    art_url: str | None = None


@dataclass(frozen=True)
class Playlist:
    uri: str
    name: str
    owner: str
    image_url: str | None
