import json
from pathlib import Path

from hyprspot.model import Track
from hyprspot.soloist import backoff_delay, build_command, entity_to_track, repeat_commands

EV = json.loads((Path(__file__).parent / "fixtures" / "soloist_events.json").read_text())


def test_build_command_play_uri():
    assert json.loads(build_command("play", uri="spotify:playlist:x")) == {
        "type": "command", "command": "play", "uri": "spotify:playlist:x"}


def test_build_command_no_fields():
    assert json.loads(build_command("pause")) == {"type": "command", "command": "pause"}


def test_repeat_track_order():
    assert repeat_commands("track") == [("set_repeat_context", {"enabled": False}),
                                        ("set_repeat_track", {"enabled": True})]


def test_repeat_off_and_context():
    assert repeat_commands("off") == [("set_repeat_track", {"enabled": False}),
                                      ("set_repeat_context", {"enabled": False})]
    assert repeat_commands("context") == [("set_repeat_track", {"enabled": False}),
                                          ("set_repeat_context", {"enabled": True})]


def test_entity_to_track_from_fixture():
    t = entity_to_track(EV["track_changed"]["item"])
    assert isinstance(t, Track)
    assert t.title == "Don't Speak"
    assert t.artist == "No Doubt"
    assert t.album == "Tragic Kingdom"
    assert t.duration_ms == 263000
    assert t.uri == "spotify:track:7H8zey5My6uNdD2TxeCb9F"
    assert t.art_url.endswith("ab67616d0000b273ada8d00d23273a097e6df364")  # "large"


def test_entity_without_cover():
    t = entity_to_track({"uri": "spotify:track:x", "entity_type": "track",
                         "decorations": {"identity": {"name": "Só título 🎵"}}})
    assert t.title == "Só título 🎵"
    assert t.art_url is None
    assert t.artist == ""


def test_empty_entity_is_none():
    assert entity_to_track({"uri": "", "entity_type": "", "decorations": {}}) is None
    assert entity_to_track(None) is None


def test_backoff_sequence():
    assert [backoff_delay(i) for i in range(6)] == [0.5, 1, 2, 5, 5, 5]


def test_connection_limits_are_raised():
    from hyprspot.soloist import SoloistClient

    class FakeConn:
        def __init__(self):
            self.calls = {}

        def __getattr__(self, name):
            return lambda *a: self.calls.__setitem__(name, a)

    c = FakeConn()
    SoloistClient._configure(c)
    assert c.calls["set_max_incoming_payload_size"] == (0,)
    assert c.calls["set_keepalive_pong_timeout"] == (30,)


# ---------------------------------------------------------------- fila (get_queue / queue_changed)
def _queue_fixture():
    import json
    from pathlib import Path
    return json.loads((Path(__file__).parent / "fixtures" / "queue_changed.json").read_text())


def test_parse_queue_reads_upcoming_in_order():
    from hyprspot.soloist import parse_queue
    titles = [t.title for t in parse_queue(_queue_fixture())]
    assert titles == ["Last of the American Girls", "When I Come Around", "The American Dream Is Killing Me"]


def test_parse_queue_skips_non_tracks():
    from hyprspot.soloist import parse_queue
    tracks = parse_queue(_queue_fixture())
    assert all(t.uri.startswith("spotify:track:") for t in tracks)
    assert parse_queue({"type": "queue_changed"}) == []


def test_parse_queue_keeps_only_the_playlist_itself():
    # revisão: músicas postas na fila à mão ou do autoplay não são da playlist; clicar nelas falharia
    from hyprspot.soloist import parse_queue
    ev = _queue_fixture()
    ev["upcoming"][0]["source"] = "queue"
    ev["upcoming"][2]["source"] = "autoplay"
    assert [t.title for t in parse_queue(ev)] == ["The American Dream Is Killing Me"]
