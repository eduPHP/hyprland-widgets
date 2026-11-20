"""Peak levels of a sink (its monitor) or a source, from a low-rate `pw-record` pipe. Runs only while shown."""

from __future__ import annotations

import array
import subprocess
import threading

import pw

RATE = 8000
CHUNK_S = 0.04


class Meter:
    def __init__(self):
        self.levels: list[float] = [0.0, 0.0]
        self.target: str | None = None
        self.proc: subprocess.Popen | None = None

    def watch(self, target: str | None, sink: bool) -> None:
        """Follow `target` (a node.name); None stops metering."""
        if target == self.target and self.proc and self.proc.poll() is None:
            return
        self.stop()
        self.target = target
        if not target:
            return
        props = f"{{ node.name={pw.OWN}.meter application.name={pw.OWN} node.dont-reconnect=true"
        props += " stream.capture.sink=true }" if sink else " }"
        self.proc = subprocess.Popen(
            ["pw-record", "--target", target, "-P", props, "--rate", str(RATE), "--channels", "2",
             "--format", "f32", "--latency", "40ms", "--raw", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        size = int(RATE * CHUNK_S) * 2 * 4
        while proc is self.proc:
            buf = proc.stdout.read(size)
            if not buf:
                break
            a = array.array("f", buf[: len(buf) // 8 * 8])
            if a:
                self.levels = [min(1.0, max(map(abs, a[0::2]))), min(1.0, max(map(abs, a[1::2])))]
        if proc is self.proc:
            self.levels = [0.0, 0.0]

    def stop(self) -> None:
        proc, self.proc, self.target = self.proc, None, None
        self.levels = [0.0, 0.0]
        if proc:
            proc.terminate()
