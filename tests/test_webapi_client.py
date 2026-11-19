"""WebApi com um transporte falso (sem rede)."""
import json

from hyprspot.auth import TokenStore
from hyprspot.webapi import ERR_AUTH, ERR_OFFLINE, ERR_QUOTA, WebApi


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, body, cb):
        self.calls.append((method, url, cb))

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
