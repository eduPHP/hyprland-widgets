"""Verificação manual do SoloistClient: imprime conexão e tipos de evento por ~25 s."""
import sys, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from gi.repository import GLib
from hyprspot.soloist import SoloistClient
T = time.monotonic()
c = SoloistClient()
c.connect("connection-changed", lambda _c, ok: print(f"{time.monotonic()-T:5.1f}s connection-changed {ok}", flush=True))
c.connect("event", lambda _c, ev: print(f"{time.monotonic()-T:5.1f}s event {ev.get('type')}", flush=True))
c.start()
loop = GLib.MainLoop(); GLib.timeout_add_seconds(int(sys.argv[1]) if len(sys.argv) > 1 else 25, loop.quit); loop.run()
