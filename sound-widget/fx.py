"""Mic processing as child processes of the daemon.

noise / echo  -> one `pipewire -c` running module-echo-cancel (WebRTC, monitor mode) that exposes a
                 filtered "sound-widget.voice" source and becomes the default mic while it runs.
                 WebRTC always cancels echo; the noise toggle adds its noise suppressor.
monitor       -> `pw-loopback` from the current mic to the default output ("hear yourself").
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import pw

VOICE_CONF = """\
context.properties = {{ log.level = 1 }}
context.spa-libs = {{ audio.convert.* = audioconvert/libspa-audioconvert  support.* = support/libspa-support }}
context.modules = [
  {{ name = libpipewire-module-rt flags = [ ifexists nofail ] }}
  {{ name = libpipewire-module-protocol-native }}
  {{ name = libpipewire-module-client-node }}
  {{ name = libpipewire-module-adapter }}
  {{ name = libpipewire-module-echo-cancel
    args = {{
      library.name = aec/libspa-aec-webrtc
      monitor.mode = true
      aec.args = {{ webrtc.noise_suppression = {noise} webrtc.high_pass_filter = true webrtc.gain_control = false }}
      capture.props = {{ node.name = "{own}.voice.capture" target.object = "{target}" node.dont-reconnect = true }}
      source.props = {{ node.name = "{own}.voice" node.description = "Voice filter" }}
      sink.props = {{ node.name = "{own}.voice.ref" }}
      playback.props = {{ node.name = "{own}.voice.play" }}
    }}
  }}
]
"""


def _die_with_parent():
    ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG: no orphans if the daemon dies


def _spawn(argv: list[str]) -> subprocess.Popen:
    return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=_die_with_parent)


def _stop(proc: subprocess.Popen | None) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(2)
        except subprocess.TimeoutExpired:
            proc.kill()


class Fx:
    def __init__(self):
        self.on = {"noise": False, "echo": False, "monitor": False}
        self.voice: subprocess.Popen | None = None
        self.voice_key: tuple | None = None  # (target, noise) the running filter was started with
        self.loop: subprocess.Popen | None = None
        self.loop_src: str | None = None
        self.conf = Path(tempfile.gettempdir()) / f"{pw.OWN}-{os.getuid()}-voice.conf"

    def apply(self, mic: str | None) -> None:
        """Bring the child processes in line with self.on for physical mic `mic` (a node.name). Blocking."""
        want_voice = (self.on["noise"] or self.on["echo"]) and mic
        key = (mic, self.on["noise"])
        if want_voice and (self.voice_key != key or not self.voice or self.voice.poll() is not None):
            _stop(self.voice)
            self.conf.write_text(VOICE_CONF.format(own=pw.OWN, target=mic, noise=str(self.on["noise"]).lower()))
            self.voice = _spawn(["pipewire", "-c", str(self.conf)])
            self.voice_key = key
            self._make_default(f"{pw.OWN}.voice")
        elif not want_voice and self.voice:
            _stop(self.voice)
            self.voice = self.voice_key = None
            if mic:
                self._make_default(mic)

        src = f"{pw.OWN}.voice" if want_voice else mic
        if self.on["monitor"] and src:
            if self.loop_src != src or not self.loop or self.loop.poll() is not None:
                _stop(self.loop)
                self.loop = _spawn([
                    "pw-loopback", "-n", f"{pw.OWN}.monitor",
                    "--capture-props", f"node.name={pw.OWN}.monitor.in target.object={src} node.dont-reconnect=true",
                    "--playback-props", f"node.name={pw.OWN}.monitor.out media.role=Communication",
                ])
                self.loop_src = src
        elif self.loop:
            _stop(self.loop)
            self.loop = self.loop_src = None

    @staticmethod
    def _make_default(name: str) -> None:
        for _ in range(20):  # the filter's node takes a moment to appear
            node = next((o for o in _nodes() if o[1] == name), None)
            if node:
                pw.set_default(node[0])
                return
            time.sleep(0.1)

    def shutdown(self, mic: str | None) -> None:
        _stop(self.loop)
        if self.voice:
            _stop(self.voice)
            if mic:
                self._make_default(mic)


def _nodes() -> list[tuple[int, str]]:
    import json

    out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout
    return [
        (o["id"], ((o.get("info") or {}).get("props") or {}).get("node.name", ""))
        for o in json.loads(out or "[]")
        if o.get("type", "").endswith("Node")
    ]
