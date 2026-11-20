"""Detecta 'clicou fora' enquanto o widget está expandido (modo EXCLUSIVE).

Duas fontes, armadas juntas (ver ledger, Task 1):
  * socket2 do Hyprland: troca de janela ativa / workspace / monitor;
  * click-bind: um `bindn , mouse:272` temporário que chama a ação `click-anywhere`
    do app; o app compara a posição do cursor com o retângulo da layer.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib, GObject  # noqa: E402

FOCUS_EVENTS = ("activewindow>>", "activewindowv2>>", "workspace>>", "workspacev2>>", "focusedmon>>",
                "focusedmonv2>>", "openwindow>>")
ARM_GRACE_S = 0.3
CLICK_EXEC = ("gdbus call --session --dest dev.hyprspot.App --object-path /dev/hyprspot/App "
              "--method org.gtk.Actions.Activate click-anywhere [] {}")


def hyprctl(*args: str) -> str:
    try:
        return subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def hypr_socket_path() -> str | None:
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    run = os.environ.get("XDG_RUNTIME_DIR")
    return os.path.join(run, "hypr", sig, ".socket.sock") if sig and run else None


def hypr_request(cmd: str) -> bytes | None:
    """Pergunta direto ao socket do Hyprland (~0,3 ms; rápido para fazer a cada quadro)."""
    path = hypr_socket_path()
    if not path:
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            s.connect(path)
            s.sendall(cmd.encode())
            data = b""
            while chunk := s.recv(65536):
                data += chunk
        return data
    except OSError:
        return None


def hypr_cursor() -> tuple[float, float] | None:
    """Posição do cursor na tela (coordenadas lógicas)."""
    try:
        pos = json.loads(hypr_request("j/cursorpos") or b"")
        return float(pos["x"]), float(pos["y"])
    except (ValueError, KeyError, TypeError):
        return None


def hypr_usable_area(x: float, y: float) -> tuple[int, int] | None:
    """Largura e altura úteis (lógicas, sem o espaço reservado por painéis) do monitor em (x, y)."""
    try:
        mons = json.loads(hypr_request("j/monitors") or b"")
        areas = []
        for m in mons:
            w, h = m["width"] / m["scale"], m["height"] / m["scale"]
            if m.get("transform", 0) % 2:
                w, h = h, w
            left, top, right, bottom = m.get("reserved") or (0, 0, 0, 0)
            area = (round(w - left - right), round(h - top - bottom))
            if m["x"] <= x < m["x"] + w and m["y"] <= y < m["y"] + h:
                return area
            areas.append((m.get("focused"), area))
        return next((a for f, a in areas if f), areas[0][1] if areas else None)
    except (ValueError, KeyError, TypeError, ZeroDivisionError):
        return None


def cursor_outside_layer(namespace: str = "hyprspot") -> bool | None:
    """True se o cursor está fora da layer; None se não deu para saber."""
    try:
        cur = json.loads(hyprctl("cursorpos", "-j"))
        layers = json.loads(hyprctl("layers", "-j"))
    except ValueError:
        return None
    for mon in layers.values():
        for level in mon.get("levels", {}).values():
            for layer in level:
                if layer.get("namespace") == namespace:
                    x, y, w, h = layer["x"], layer["y"], layer["w"], layer["h"]
                    return not (x <= cur["x"] < x + w and y <= cur["y"] < y + h)
    return None


class FocusWatcher(GObject.Object):
    __gsignals__ = {"focus-lost": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__()
        self.armed = False
        self._armed_at = 0.0
        self._stream: Gio.DataInputStream | None = None
        self._conn: Gio.SocketConnection | None = None
        self._cancel: Gio.Cancellable | None = None
        self._bind_set = False

    # -- API --
    def arm(self) -> None:
        if self.armed:
            return
        self.armed = True
        self._armed_at = time.monotonic()
        self._open_socket()
        if hyprctl("keyword", "bindn", f", mouse:272, exec, {CLICK_EXEC}").strip() == "ok":
            self._bind_set = True

    def disarm(self) -> None:
        if not self.armed:
            return
        self.armed = False
        if self._cancel is not None:
            self._cancel.cancel()
        if self._conn is not None:
            self._conn.close(None)
        self._conn = self._stream = self._cancel = None
        if self._bind_set:
            hyprctl("keyword", "unbind", ", mouse:272")
            self._bind_set = False

    def on_click_anywhere(self) -> None:
        if self.armed and cursor_outside_layer() is True:
            self._lost()

    # -- interno --
    def _lost(self) -> None:
        if self.armed and time.monotonic() - self._armed_at > ARM_GRACE_S:
            self.emit("focus-lost")

    def _open_socket(self) -> None:
        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        rt = os.environ.get("XDG_RUNTIME_DIR")
        if not sig or not rt:
            return
        path = os.path.join(rt, "hypr", sig, ".socket2.sock")
        try:
            self._conn = Gio.SocketClient().connect(Gio.UnixSocketAddress.new(path), None)
        except GLib.Error:
            self._conn = None
            return
        self._stream = Gio.DataInputStream.new(self._conn.get_input_stream())
        self._cancel = Gio.Cancellable()
        self._read()

    def _read(self) -> None:
        if self._stream is None:
            return
        self._stream.read_line_async(GLib.PRIORITY_DEFAULT, self._cancel, self._on_line)

    def _on_line(self, stream, res) -> None:
        try:
            line, _len = stream.read_line_finish_utf8(res)
        except GLib.Error:
            return
        if line is None:
            return
        if line.startswith(FOCUS_EVENTS):
            self._lost()
        self._read()
