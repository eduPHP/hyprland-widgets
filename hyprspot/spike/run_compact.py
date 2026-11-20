"""Verificação manual do modo compacto ligado ao Soloist real (sem a Web API)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Soup", "3.0")
from gi.repository import Gtk, Soup
from hyprspot.cache import ArtCache
from hyprspot.config import CACHE_DIR, load_config
from hyprspot.soloist import SoloistClient
from hyprspot.state import AppState, apply_event, optimistic
from hyprspot.ui.common import load_css
from hyprspot.ui.compact import CompactView
from hyprspot.ui.cover import ArtLoader
from hyprspot.ui.window import HyprspotWindow

def activate(app):
    load_css()
    state, client = AppState(), SoloistClient()
    client.connect("event", lambda _c, ev: state.set_playback(apply_event(state.playback, ev)))
    client.connect("connection-changed", lambda _c, ok: state.set_playback(apply_event(state.playback, {"type": "_connected" if ok else "_disconnected"})))
    def cmd(name, fields):
        state.set_playback(optimistic(state.playback, name, **fields)); client.send(name, **fields)
    art = ArtLoader(ArtCache(CACHE_DIR / "art"), Soup.Session())
    win = HyprspotWindow(app, load_config(), CompactView(state, cmd, lambda: print("expand"), art), None, print)
    win.present()
    if "--fake" in sys.argv:
        from hyprspot.model import Track
        from hyprspot.state import Playback
        state.set_playback(Playback(connected=True, logged_in=True, status="playing", track=Track("spotify:track:x", "Uma faixa com um título enorme que não cabe 🎸🔥 日本語のタイトル", "Artista Com Nome Muito Longo, Outro Artista", duration_ms=200000)))
    else:
        client.start()
    from gi.repository import GLib
    def dbg():
        cv = win.compact
        r = cv.get_child()
        print("win", win.get_width(), win.get_height(), "overlay", cv.get_width(), cv.get_height(), "root", r.get_width(), r.get_height(), "prog", cv.progress.get_width(), cv.progress.get_height(), "root_min", r.measure(Gtk.Orientation.VERTICAL, 320)[:2], "prog_min", cv.progress.measure(Gtk.Orientation.VERTICAL, 320)[:2], "cover", cv.cover.get_height(), flush=True)
        return False
    GLib.timeout_add(2000, dbg)
    app._keep = (state, client, win)

app = Gtk.Application(application_id="dev.hyprspot.Spike")
app.connect("activate", activate); app.run([])
