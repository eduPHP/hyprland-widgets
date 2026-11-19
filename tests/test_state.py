import json
from pathlib import Path

from hyprspot.state import AppState, Playback, Throttle, apply_event, optimistic, position_now

EV = json.loads((Path(__file__).parent / "fixtures" / "soloist_events.json").read_text())


def test_playback_state_snapshot():
    pb = apply_event(Playback(connected=True), EV["playback_state"])
    ps = EV["playback_state"]
    assert pb.status == ps["status"]
    assert pb.track is not None and pb.track.title
    assert pb.volume == ps["volume"]
    assert pb.shuffle == ps["options"]["shuffle"]
    assert pb.repeat == ps["options"]["repeat"]
    assert pb.is_active == ps["is_active"]
    assert pb.context_uri == (ps.get("context") or {}).get("uri")


def test_playback_changed_only_status():
    base = apply_event(Playback(connected=True), EV["playback_state"])
    pb = apply_event(base, {"type": "playback_changed", "status": "paused"})
    assert pb.status == "paused"
    assert pb.track == base.track and pb.volume == base.volume


def test_track_and_context_changed():
    pb = apply_event(Playback(), EV["track_changed"])
    assert pb.track.title == "Don't Speak"
    pb = apply_event(pb, EV["context_changed"])
    assert pb.context_uri == "spotify:playlist:1fAkePlAyLiStFoCo000002"


def test_options_volume_device():
    pb = apply_event(Playback(), {"type": "options_changed", "options": {"shuffle": True, "repeat": "track"}})
    assert pb.shuffle is True and pb.repeat == "track"
    pb = apply_event(pb, {"type": "volume_changed", "volume": 42})
    assert pb.volume == 42
    pb = apply_event(pb, {"type": "device_changed", "is_active": True, "device_name": "hyprspot"})
    assert pb.is_active is True


def test_position_sync_and_interpolation():
    pb = apply_event(Playback(), {"type": "position_sync",
                                  "position": {"position_ms": 45000, "timestamp_ms": 1000, "speed": 1.0}})
    assert position_now(pb, 3000) == 47000
    still = apply_event(pb, {"type": "position_sync",
                             "position": {"position_ms": 45000, "timestamp_ms": 1000, "speed": 0}})
    assert position_now(still, 9000) == 45000


def test_position_clamped_to_duration():
    pb = apply_event(Playback(), EV["track_changed"])  # 263000 ms
    pb = apply_event(pb, {"type": "position_sync",
                          "position": {"position_ms": 262000, "timestamp_ms": 0, "speed": 1.0}})
    assert position_now(pb, 10_000) == 263000


def test_disconnect_marks_offline():
    pb = apply_event(Playback(connected=True), EV["playback_state"])
    pb = apply_event(pb, {"type": "_disconnected"})
    assert pb.connected is False
    assert pb.status == "idle"
    assert apply_event(pb, {"type": "_connected"}).connected is True


def test_auth_state_logged_out():
    pb = apply_event(Playback(connected=True, logged_in=True), {"type": "auth_state", "logged_in": False})
    assert pb.logged_in is False


def test_unknown_event_is_noop():
    pb = Playback(connected=True)
    assert apply_event(pb, {"type": "queue_changed"}) == pb
    assert apply_event(pb, {"type": "error", "message": "x"}) == pb


def test_optimistic_pause_then_server_confirms():
    pb = apply_event(Playback(connected=True), EV["playback_state"])
    pb = optimistic(pb, "pause")
    assert pb.status == "paused"
    pb = apply_event(pb, {"type": "playback_changed", "status": "paused"})
    assert pb.status == "paused"


def test_optimistic_variants():
    pb = Playback(connected=True, status="paused", volume=50)
    assert optimistic(pb, "play").status == "playing"
    assert optimistic(pb, "set_volume", volume=55).volume == 55
    assert optimistic(pb, "set_shuffle", enabled=True).shuffle is True
    assert optimistic(pb, "skip_next").status == "buffering"
    s = optimistic(pb, "seek", position_ms=30000, now_ms=5000)
    assert s.position_ms == 30000 and s.position_ts_ms == 5000


def test_volume_throttle_keeps_last():
    t = Throttle(100)
    assert t.push(10, 0) == 10
    assert t.push(20, 30) is None
    assert t.push(30, 60) is None
    assert t.flush(100) == 30
    assert t.flush(200) is None
    assert t.push(40, 250) == 40


def test_appstate_emits_only_on_change():
    s = AppState()
    seen = []
    s.connect("changed", lambda _s, what: seen.append(what))
    pb = Playback(connected=True)
    s.set_playback(pb)
    s.set_playback(pb)
    s.set_ui("expanded")
    s.set_ui("expanded")
    assert seen == ["playback", "ui"]


def test_playback_state_does_not_force_login():
    pb = apply_event(Playback(connected=True), {"type": "auth_state", "logged_in": False})
    pb = apply_event(pb, {"type": "playback_state", "status": "idle"})
    assert pb.logged_in is False


def test_disconnect_clears_track_and_login():
    pb = apply_event(Playback(connected=True, logged_in=True), EV["playback_state"])
    pb = apply_event(pb, {"type": "_disconnected"})
    assert pb.track is None and pb.logged_in is False


def test_playlists_error_is_persistent_state():
    s = AppState()
    seen = []
    s.connect("changed", lambda _s, what: seen.append(what))
    s.set_playlists_error("Sem conexão com o Spotify")
    assert s.playlists_error == "Sem conexão com o Spotify"
    s.set_playlists_error("Sem conexão com o Spotify")
    assert seen == ["playlists"]


def test_should_refresh_playlists():
    from hyprspot.state import should_refresh_playlists
    assert should_refresh_playlists(None, 1000.0, has_items=False) is True
    assert should_refresh_playlists(900.0, 1000.0, has_items=True) is False
    assert should_refresh_playlists(0.0, 1000.0, has_items=True) is True   # > 600 s
    assert should_refresh_playlists(990.0, 1000.0, has_items=False) is True


# ---------------------------------------------------------------- faixa pulada pelo Soloist
from hyprspot.model import Track  # noqa: E402
from hyprspot.state import SkipDetector, skip_message  # noqa: E402

A = Track("spotify:track:a", "Faixa A", "X")
B = Track("spotify:track:b", "Faixa B", "X")
C = Track("spotify:track:c", "Faixa C", "X")


def test_track_replaced_quickly_without_user_command_is_a_skip():
    # o Soloist às vezes não consegue a licença da faixa e passa para a próxima sozinho
    d = SkipDetector()
    assert d.track_changed(A, 0) is None
    d.user_command(10_000)                       # usuário: "próxima"
    assert d.track_changed(B, 10_050) is None
    assert d.track_changed(C, 10_650) == B       # B durou 600 ms e ninguém pediu: pulada


def test_user_skipping_fast_is_not_reported():
    d = SkipDetector()
    d.track_changed(A, 0)
    d.user_command(1_000)
    d.track_changed(B, 1_050)
    d.user_command(1_400)                        # o próprio usuário pulou B rapidinho
    assert d.track_changed(C, 1_450) is None


def test_track_that_played_long_enough_is_not_a_skip():
    d = SkipDetector()
    d.track_changed(A, 0)
    assert d.track_changed(B, 200_000) is None   # A terminou normalmente
    assert d.track_changed(C, 201_500) is None   # B durou 1,5 s: acima do limite


def test_same_track_again_is_not_a_change():
    d = SkipDetector()
    d.track_changed(A, 0)
    assert d.track_changed(A, 300) is None
    assert d.track_changed(None, 500) is None    # sem faixa (desconectado) não conta


def test_skip_message():
    assert skip_message(["Faixa B"]) == "“Faixa B” indisponível, pulada"
    assert skip_message(["Faixa B", "Faixa C"]) == "2 faixas indisponíveis, puladas"
    assert skip_message([""]) == "Faixa indisponível, pulada"


# ---------------------------------------------------------------- QueueSync
from hyprspot.state import QueueSync  # noqa: E402

QEV = {"type": "queue_changed", "previous": [], "upcoming": [
    {"uid": "1", "source": "context", "item": {"uri": "spotify:track:a", "decorations": {"identity": {"name": "A"}}}}]}


def test_queue_sync_does_not_loop():
    # a resposta do get_queue também chega como queue_changed: não pode pedir de novo
    sent = []
    q = QueueSync(lambda name, f: sent.append((name, f)) or True)
    q.request()
    assert sent == [("get_queue", {"limit": 0})]
    got = q.on_queue_event(QEV)
    assert [t.title for t in got] == ["A"] and len(sent) == 1
    assert q.on_queue_event(QEV) is None                     # espontâneo (máx. 10): pede a fila inteira
    assert len(sent) == 2


def test_queue_sync_request_is_idempotent_while_pending():
    sent = []
    q = QueueSync(lambda name, f: sent.append(name) or True)
    q.request()
    q.request()
    assert sent == ["get_queue"]


def test_queue_sync_retries_when_send_fails():
    # desconectado: o envio falha e não pode ficar "pendente" para sempre
    sent = []
    q = QueueSync(lambda name, f: sent.append(name) or False)
    q.request()
    q.request()
    assert sent == ["get_queue", "get_queue"]


def test_set_queue_emits_once():
    s = AppState()
    seen = []
    s.connect("changed", lambda _s, what: seen.append(what))
    tr = [Track("spotify:track:a", "A", "")]
    s.set_queue("spotify:playlist:p", tr)
    s.set_queue("spotify:playlist:p", tr)
    assert seen == ["queue"] and s.queue == tr and s.queue_context == "spotify:playlist:p"
