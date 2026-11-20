"""WebApi com um transporte falso (sem rede)."""
import json

import pytest

from hyprspot.auth import TokenStore
from hyprspot.webapi import ERR_AUTH, ERR_OFFLINE, ERR_QUOTA, WebApi


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.bodies = []

    def __call__(self, method, url, headers, body, cb):
        self.calls.append((method, url, cb))
        try:
            self.bodies.append(json.loads(body) if body else None)
        except ValueError:  # o refresh do token manda formulário, não JSON
            self.bodies.append(body)

    def reply(self, i, status=200, data=None, error=None, headers=None):
        raw = json.dumps(data).encode() if data is not None else b""
        self.calls[i][2](status, headers or {}, raw, error)


def api_with(tmp_path, tok=None, now=None):
    store = TokenStore(tmp_path / "token.json")
    if tok is not None:
        store.save(tok, now=now)
    api = WebApi(store, session=object())
    api._transport = FakeTransport()
    return api, store


FRESH = {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "client_id": "c"}
EXPIRED = {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "client_id": "c"}


def test_no_token_reports_auth_without_network(tmp_path):
    api, _ = api_with(tmp_path)
    got = []
    api.search("x", 0, lambda items, err: got.append(err))
    assert got == [ERR_AUTH]
    assert api._transport.calls == []


def test_refresh_transport_error_reports_offline(tmp_path):
    api, _ = api_with(tmp_path, EXPIRED, now=0)
    got = []
    api.search("x", 0, lambda items, err: got.append(err))
    assert api._transport.calls[0][0] == "POST"
    api._transport.reply(0, status=0, error="network down")
    assert got == [ERR_OFFLINE]


def test_refresh_400_reports_auth(tmp_path):
    api, _ = api_with(tmp_path, EXPIRED, now=0)
    got = []
    api.search("x", 0, lambda items, err: got.append(err))
    api._transport.reply(0, status=400, data={"error": "invalid_grant"})
    assert got == [ERR_AUTH]


def test_refresh_exception_releases_waiters(tmp_path):
    api, store = api_with(tmp_path, EXPIRED, now=0)

    def boom(*_a, **_k):
        raise PermissionError("read-only")

    store.apply_refresh = boom
    got = []
    api.search("x", 0, lambda items, err: got.append(("a", err)))
    api.search("y", 0, lambda items, err: got.append(("b", err)))
    assert len(api._transport.calls) == 1  # refresh coalescido
    api._transport.reply(0, status=200, data={"access_token": "n", "expires_in": 3600})
    assert sorted(got) == [("a", ERR_AUTH), ("b", ERR_AUTH)]
    api.search("z", 0, lambda items, err: got.append(("c", err)))
    assert len(api._transport.calls) == 2  # não ficou travado


def test_fresh_token_goes_straight_to_api(tmp_path):
    import time
    api, _ = api_with(tmp_path, FRESH, now=time.time())
    got = []
    api.search("x", 0, lambda items, err: got.append((items, err)))
    assert api._transport.calls[0][0] == "GET"
    api._transport.reply(0, data={"tracks": {"items": []}})
    assert got == [([], None)]


def test_quota_blocks_following_calls(tmp_path):
    import time
    api, _ = api_with(tmp_path, FRESH, now=time.time())
    got = []
    api.search("x", 0, lambda items, err: got.append(err))
    api._transport.reply(0, status=429, data={"error": {"reason": "QUOTA_EXCEEDED"}})
    api.search("y", 0, lambda items, err: got.append(err))
    assert got == [ERR_QUOTA, ERR_QUOTA]
    assert len(api._transport.calls) == 1


# ---------------------------------------------------------------- play_at
DEVICES = {"devices": [{"id": "d1", "name": "outro"}, {"id": "d2", "name": "hyprspot"}]}


def test_play_at_finds_device_and_plays_with_offset(tmp_path):
    api, _ = api_with(tmp_path, FRESH)
    got = []
    api.play_at("hyprspot", "spotify:playlist:p", "spotify:track:t", got.append)
    t = api._transport
    assert t.calls[0][0] == "GET" and t.calls[0][1].endswith("/me/player/devices")
    t.reply(0, data=DEVICES)
    assert t.calls[1][0] == "PUT" and t.calls[1][1].endswith("/me/player/play?device_id=d2")
    assert t.bodies[1] == {"context_uri": "spotify:playlist:p", "offset": {"uri": "spotify:track:t"}}
    t.reply(1, status=204)
    assert got == [None]


def test_play_at_reuses_device_id(tmp_path):
    api, _ = api_with(tmp_path, FRESH)
    got = []
    api.play_at("hyprspot", "spotify:playlist:p", "spotify:track:t", got.append)
    api._transport.reply(0, data=DEVICES)
    api._transport.reply(1, status=204)
    api.play_at("hyprspot", "spotify:playlist:p", "spotify:track:u", got.append)
    assert api._transport.calls[2][0] == "PUT" and len(api._transport.calls) == 3


def test_play_at_without_device_reports_error(tmp_path):
    api, _ = api_with(tmp_path, FRESH)
    got = []
    api.play_at("hyprspot", "spotify:playlist:p", "spotify:track:t", got.append)
    api._transport.reply(0, data={"devices": []})
    assert len(got) == 1 and isinstance(got[0], str)
    assert len(api._transport.calls) == 1


def test_play_at_retries_once_on_404(tmp_path):
    # o id do aparelho muda quando o Soloist reinicia: pede a lista de novo, uma vez
    api, _ = api_with(tmp_path, FRESH)
    got = []
    api.play_at("hyprspot", "spotify:playlist:p", "spotify:track:t", got.append)
    t = api._transport
    t.reply(0, data=DEVICES)
    t.reply(1, status=404, data={"error": {"status": 404}})
    assert t.calls[2][0] == "GET" and t.calls[2][1].endswith("/me/player/devices")
    t.reply(2, data=DEVICES)
    t.reply(3, status=404, data={"error": {"status": 404}})
    assert len(got) == 1 and isinstance(got[0], str) and len(t.calls) == 4


def test_search_also_delivers_playlists(tmp_path):
    api, _ = api_with(tmp_path, FRESH)
    got, pls = [], []
    api.search("rock", 0, lambda items, err: got.append(len(items)), on_playlists=pls.append)
    api._transport.reply(0, data={"tracks": {"items": []}, "playlists": {"items": [
        {"uri": "spotify:playlist:a", "name": "A", "owner": {"display_name": "x"}}]}})
    assert got == [0] and [p.uri for p in pls[0]] == ["spotify:playlist:a"]


@pytest.mark.parametrize("liked,method", [(True, "PUT"), (False, "DELETE")])
def test_set_liked_sends_uri_in_query_string(tmp_path, liked, method):
    # o Spotify responde 400 "Missing required field: uris" se o URI vier no corpo
    api, _ = api_with(tmp_path, FRESH)
    got = []
    api.set_liked("spotify:track:abc", liked, got.append)
    m, url, _cb = api._transport.calls[0]
    assert m == method and url.endswith("/me/library?uris=spotify%3Atrack%3Aabc")
    assert api._transport.bodies[0] is None
    api._transport.reply(0, status=200)
    assert got == [True]
