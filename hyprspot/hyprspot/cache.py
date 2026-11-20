"""Cache em disco: lista de playlists (JSON) e capas (LRU por mtime)."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from hyprspot.model import Playlist


def save_playlists(path: Path, items: list[Playlist]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(p) for p in items], ensure_ascii=False))
    tmp.replace(path)


def load_playlists(path: Path) -> list[Playlist]:
    try:
        return [Playlist(**d) for d in json.loads(path.read_text())]
    except (OSError, ValueError, TypeError):
        return []


class ArtCache:
    def __init__(self, root: Path, max_bytes: int = 50 * 1024 * 1024):
        self.root = Path(root)
        self.max_bytes = max_bytes

    def path_for(self, url: str) -> Path:
        return self.root / (hashlib.sha1(url.encode()).hexdigest() + ".jpg")

    def get(self, url: str) -> Path | None:
        p = self.path_for(url)
        if not p.exists():
            return None
        os.utime(p)
        return p

    def put(self, url: str, data: bytes) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        p = self.path_for(url)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
        return p

    def evict(self) -> int:
        files = sorted((f for f in self.root.glob("*.jpg") if f.is_file()), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        freed = 0
        for f in files:
            if total <= self.max_bytes:
                break
            size = f.stat().st_size
            f.unlink(missing_ok=True)
            total -= size
            freed += size
        return freed


def fetch_art(cache: ArtCache, session, url: str, cb: Callable[[Path | None], None]) -> None:
    """Devolve o caminho local da capa, baixando de forma assíncrona se preciso."""
    hit = cache.get(url)
    if hit:
        cb(hit)
        return
    from gi.repository import GLib, Soup

    msg = Soup.Message.new("GET", url)

    def done(sess, res):
        try:
            data = sess.send_and_read_finish(res).get_data()
        except GLib.Error:
            cb(None)
            return
        if msg.get_status() != 200 or not data:
            cb(None)
            return
        cb(cache.put(url, data))

    session.send_and_read_async(msg, GLib.PRIORITY_LOW, None, done)
