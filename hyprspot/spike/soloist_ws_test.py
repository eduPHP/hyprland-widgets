"""Spike: latência da API WebSocket do Soloist usando libsoup3 (a mesma pilha do app).

Mede, para cada comando, o tempo até o command_result e até o evento que confirma a mudança.
"""
import json
import sys
import time

import gi

gi.require_version("Soup", "3.0")
from gi.repository import GLib, Soup  # noqa: E402

URL = "ws://127.0.0.1:9090"
PLAYLIST = sys.argv[1] if len(sys.argv) > 1 else None

loop = GLib.MainLoop()
session = Soup.Session()
conn = None
pending = None  # (nome, evento esperado, t0, marcas)
results = []
steps = []


def send(cmd, expect, **fields):
    global pending
    msg = {"type": "command", "command": cmd, **fields}
    pending = {"name": cmd, "expect": expect, "t0": time.perf_counter(), "ack": None, "evt": None}
    conn.send_text(json.dumps(msg))


def on_message(_c, _type, data):
    global pending
    ev = json.loads(data.get_data().decode())
    t = ev.get("type")
    if t == "error":
        print("  erro:", ev.get("message"))
    if not pending:
        if t in ("auth_state", "playback_state"):
            st = ev.get("status") or ("logado" if ev.get("logged_in") else "deslogado")
            print(f"  ao conectar: {t} ({st})")
        return
    dt = (time.perf_counter() - pending["t0"]) * 1000
    if t == "command_result" and pending["ack"] is None:
        pending["ack"] = dt
    if t == pending["expect"] and pending["evt"] is None:
        pending["evt"] = dt
        extra = ""
        if t == "track_changed":
            extra = (ev.get("item") or {}).get("decorations", {}).get("identity", {}).get("name", "")
        elif t == "context_changed":
            extra = (ev.get("context") or {}).get("uri", "")
        elif t == "playback_changed":
            extra = ev.get("status")
        elif t == "volume_changed":
            extra = ev.get("volume")
        results.append((pending["name"], pending["ack"], pending["evt"], extra))
        pending = None
        GLib.timeout_add(1500, next_step)


def next_step():
    if not steps:
        print("\n  comando            ack(ms)  evento(ms)  detalhe")
        for name, ack, evt, extra in results:
            print(f"  {name:17} {ack if ack is None else round(ack):>7}  {round(evt):>9}   {extra}")
        loop.quit()
        return False
    steps.pop(0)()
    return False


def on_connected(sess, res):
    global conn
    conn = sess.websocket_connect_finish(res)
    conn.connect("message", on_message)
    steps.extend([
        lambda: send("pause", "playback_changed"),
        lambda: send("play", "playback_changed"),
        lambda: send("skip_next", "track_changed"),
        lambda: send("skip_prev", "track_changed"),
        lambda: send("set_volume", "volume_changed", volume=40),
        lambda: send("seek", "position_sync", position_ms=60000),
    ])
    if PLAYLIST:
        steps.append(lambda: send("play", "context_changed", uri=PLAYLIST))
    steps.append(lambda: send("pause", "playback_changed"))
    GLib.timeout_add(800, next_step)


msg = Soup.Message.new("GET", URL)
t0 = time.perf_counter()
session.websocket_connect_async(msg, None, None, GLib.PRIORITY_DEFAULT, None, on_connected)
GLib.timeout_add_seconds(40, lambda: (print("timeout"), loop.quit()))
loop.run()
