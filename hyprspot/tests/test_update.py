import json

import pytest

from hyprspot import update as U


@pytest.mark.parametrize("tag,expected", [
    ("v0.2.0", (0, 2, 0)), ("0.2.0", (0, 2, 0)), ("v1.10", (1, 10)), (" v3 ", (3,)),
    ("", None), ("latest", None), ("v1.2-beta", None), (None, None),
])
def test_parse_version(tag, expected):
    assert U.parse_version(tag) == expected


@pytest.mark.parametrize("tag,current,is_newer", [
    ("v0.2.0", "0.1.0", True),
    ("v0.10.0", "0.9.0", True),        # numérico, não alfabético
    ("v0.1.0", "0.1.0", False),
    ("v0.1", "0.1.0", False),          # mesmo número com zeros a menos
    ("v0.1.1", "0.1", True),
    ("v0.0.9", "0.1.0", False),
])
def test_newer_release_compares_numbers(tag, current, is_newer):
    body = {"tag_name": tag, "html_url": "https://github.com/eduPHP/hyprspot/releases/tag/" + tag}
    got = U.newer_release(body, current)
    assert (got is not None) == is_newer
    if is_newer:
        assert got == (tag.lstrip("v"), body["html_url"])


@pytest.mark.parametrize("body", [None, {}, {"tag_name": "nightly", "html_url": "x"}, ["v9"],
                                  {"tag_name": "v9.0.0"}])
def test_newer_release_ignores_odd_answers(body):
    assert U.newer_release(body, "0.1.0") is None


def fake_transport(status, payload, error=None):
    calls = []

    def send(method, url, headers, body, cb):
        calls.append((method, url, headers))
        raw = json.dumps(payload).encode() if isinstance(payload, (dict, list)) else (payload or b"")
        cb(status, {}, raw, error)

    return send, calls


def test_check_calls_back_only_when_newer():
    send, calls = fake_transport(200, {"tag_name": "v0.2.0", "html_url": "https://r/0.2.0"})
    got = []
    U.check(send, "0.1.0", lambda v, url: got.append((v, url)))
    assert got == [("0.2.0", "https://r/0.2.0")]
    method, url, headers = calls[0]
    assert method == "GET" and url == U.RELEASES_URL
    assert headers.get("User-Agent") and "github" in headers.get("Accept", "")


@pytest.mark.parametrize("status,payload,error", [
    (200, {"tag_name": "v0.1.0", "html_url": "u"}, None),   # já está na última
    (404, {"message": "Not Found"}, None),                  # sem release
    (403, {"message": "API rate limit exceeded"}, None),
    (0, None, "network"),
    (200, b"<html>", None),
])
def test_check_is_silent_otherwise(status, payload, error):
    send, _ = fake_transport(status, payload, error)
    got = []
    U.check(send, "0.1.0", lambda v, url: got.append(v))
    assert got == []


def test_notification_text_says_how_to_update():
    title, body = U.notification_text("0.2.0", "0.1.0", "/home/x/.local/share/hyprspot")
    assert title == "hyprspot 0.2.0 disponível"
    assert "0.1.0" in body and "git -C /home/x/.local/share/hyprspot pull" in body
