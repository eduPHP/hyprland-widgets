import json
import socket
import threading

from hyprspot import focus


def serve_once(path, reply: bytes, seen: list):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        seen.append(conn.recv(256))
        conn.sendall(reply)
        conn.close()
        srv.close()

    threading.Thread(target=run, daemon=True).start()


def test_hypr_cursor_reads_position_from_socket(tmp_path, monkeypatch):
    sock = tmp_path / ".socket.sock"
    seen = []
    serve_once(sock, json.dumps({"x": 1300.5, "y": 852}).encode(), seen)
    monkeypatch.setattr(focus, "hypr_socket_path", lambda: str(sock))
    assert focus.hypr_cursor() == (1300.5, 852.0)
    assert seen == [b"j/cursorpos"]


def test_hypr_cursor_is_none_without_hyprland(tmp_path, monkeypatch):
    monkeypatch.setattr(focus, "hypr_socket_path", lambda: str(tmp_path / "nada.sock"))
    assert focus.hypr_cursor() is None
    monkeypatch.setattr(focus, "hypr_socket_path", lambda: None)
    assert focus.hypr_cursor() is None


MONITORS = [
    {"name": "eDP-1", "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.2, "transform": 0,
     "reserved": [0, 26, 0, 0], "focused": True},
    {"name": "HDMI-A-1", "x": 1600, "y": 0, "width": 1080, "height": 1920, "scale": 1.0, "transform": 1,
     "reserved": [0, 0, 0, 30], "focused": False},
]


def test_usable_area_discounts_scale_and_reserved_space(monkeypatch):
    monkeypatch.setattr(focus, "hypr_request", lambda cmd: json.dumps(MONITORS).encode())
    assert focus.hypr_usable_area(800, 400) == (1600, 874)       # 1920/1.2 × 1080/1.2 − Waybar
    # girado 90°: largura e altura trocam
    assert focus.hypr_usable_area(1700, 100) == (1920, 1050)


def test_usable_area_falls_back_to_focused_monitor(monkeypatch):
    monkeypatch.setattr(focus, "hypr_request", lambda cmd: json.dumps(MONITORS).encode())
    assert focus.hypr_usable_area(-500, -500) == (1600, 874)
    monkeypatch.setattr(focus, "hypr_request", lambda cmd: None)
    assert focus.hypr_usable_area(0, 0) is None
