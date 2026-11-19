"""Verificação manual da WebApi: playlists, busca, curtidas."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gi.repository import GLib
from hyprspot.auth import TokenStore
from hyprspot.config import TOKEN_PATH
from hyprspot.webapi import WebApi
api = WebApi(TokenStore(TOKEN_PATH)); loop = GLib.MainLoop(); left = [4]
def done():
    left[0] -= 1
    if not left[0]: loop.quit()
api.list_playlists(lambda items, err: (print("playlists:", err or [p.name for p in items][:3]), done()))
api.search("bossa nova", 0, lambda items, err: (print("busca:", err or [f"{t.title} — {t.artist}" for t in items][:3]), done()))
api.user_id(lambda u: (print("user_id ok:", bool(u)), done()))
api.search("bossa nova", 10, lambda items, err: (print("busca p2:", err or len(items)), done()))
GLib.timeout_add_seconds(20, loop.quit); loop.run()
