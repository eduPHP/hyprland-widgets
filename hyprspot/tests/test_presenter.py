from hyprspot.model import Track
from hyprspot.presenter import fmt_time, now_playing
from hyprspot.state import Playback

T = Track("spotify:track:x", "Faísca 🎸", "Os Satélites", duration_ms=168000)


def test_offline():
    v = now_playing(Playback(connected=False))
    assert v.title == "Sem conexão"
    assert v.subtitle == "Reconectando ao Soloist…"
    assert v.offline and not v.enabled


def test_logged_out():
    v = now_playing(Playback(connected=True, logged_in=False))
    assert v.title == "Pareamento pendente"
    assert v.subtitle == "Escolha hyprspot no celular"
    assert not v.enabled and not v.offline


def test_nothing_playing():
    v = now_playing(Playback(connected=True, logged_in=True))
    assert (v.title, v.subtitle) == ("Nada tocando", "Clique para escolher")
    assert v.enabled


def test_playing_with_context():
    v = now_playing(Playback(connected=True, logged_in=True, status="playing", track=T), "Rock nacional")
    assert v.title == "Faísca 🎸"
    assert v.subtitle == "Os Satélites"
    assert v.detail == "Os Satélites · Rock nacional"
    assert v.playing and not v.paused


def test_paused_flag():
    v = now_playing(Playback(connected=True, logged_in=True, status="paused", track=T))
    assert v.paused and not v.playing
    assert v.detail == "Os Satélites"


def test_buffering_counts_as_playing():
    assert now_playing(Playback(connected=True, logged_in=True, status="buffering", track=T)).playing


def test_fmt_time():
    assert fmt_time(0) == "0:00"
    assert fmt_time(72_500) == "1:12"
    assert fmt_time(3_725_000) == "1:02:05"


def test_expired_soloist_overrides_offline():
    v = now_playing(Playback(connected=False), soloist_days=0)
    assert (v.title, v.subtitle) == ("Soloist expirou", "Rode hyprspot soloist-update")
    assert v.offline and not v.enabled


def test_valid_soloist_does_not_override():
    assert now_playing(Playback(connected=False), soloist_days=30).title == "Sem conexão"
