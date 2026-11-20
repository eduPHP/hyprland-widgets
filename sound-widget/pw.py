"""PipeWire state from one `pw-dump`, changes through `wpctl`."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

OWN = "sound-widget"  # node.name prefix / application.name of our own filters, loopback and meter


@dataclass
class Node:
    id: int
    name: str  # node.name, stable across restarts
    label: str
    sub: str
    kind: str  # sink | source | play | rec
    vol: int  # percent, wpctl's cubic scale
    muted: bool
    icon: str = "spk"
    available: bool = True
    app: str = ""
    running: bool = False
    target: int | None = None  # device a stream is linked to


@dataclass
class Snapshot:
    sinks: list[Node] = field(default_factory=list)
    sources: list[Node] = field(default_factory=list)
    play: list[Node] = field(default_factory=list)
    rec: list[Node] = field(default_factory=list)
    default_sink: str | None = None
    default_source: str | None = None
    rate: int = 48000
    voice: Node | None = None  # our filtered source, when running

    def sink(self) -> Node | None:
        return next((n for n in self.sinks if n.name == self.default_sink), self.sinks[0] if self.sinks else None)

    def source(self) -> Node | None:
        """The physical mic: the filter's target when the voice filter is the default."""
        name = self.default_source
        if self.voice and name == self.voice.name:
            name = self.voice.app  # app holds the filter's capture target
        return next((n for n in self.sources if n.name == name), self.sources[0] if self.sources else None)


def _vol(props: list[dict]) -> tuple[int, bool]:
    for p in props:
        if "channelVolumes" in p:
            v = max(p["channelVolumes"] or [0.0])
            return round(v ** (1 / 3) * 100), bool(p.get("mute"))
    return 100, False


def _icon(p: dict, sink: bool) -> str:
    text = (p.get("node.description", "") + p.get("device.profile.description", "")).lower()
    if p.get("device.api") == "bluez5":
        return "bt"
    if "hdmi" in text or "displayport" in text:
        return "mon"
    if "headphone" in text or "headset" in text:
        return "hp"
    if sink:
        return "spk"
    return "lap" if "digital" in text or "internal" in text else "mic"


def _device_label(p: dict, card: dict) -> tuple[str, str]:
    """Short name plus a sub line, e.g. ("Speaker", "Built-in · Raptor Lake-P/U/H cAVS")."""
    desc = p.get("node.description") or p.get("node.name", "?")
    if p.get("device.api") == "bluez5":
        codec = p.get("api.bluez5.codec", "")
        return desc, "Bluetooth" + (f" · {codec.upper()}" if codec else "")
    name = p.get("device.profile.description") or desc
    cdesc = card.get("device.description", "")
    if desc.startswith(cdesc) and len(desc) > len(cdesc):
        name = desc[len(cdesc):].strip() or name
    bus = p.get("device.bus", "")
    kind = "USB" if bus == "usb" else "HDMI / DP" if "HDMI" in name or "DisplayPort" in name else "Built-in"
    return name.replace(" Output", ""), f"{kind} · {cdesc}" if cdesc and kind == "Built-in" else kind


def snapshot() -> Snapshot:
    out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout
    objs = json.loads(out or "[]")
    snap = Snapshot()
    devices: dict[int, dict] = {}
    unavailable: dict[int, set[int]] = {}  # device id -> card.profile.device indexes with no jack/monitor
    links: dict[int, int] = {}
    for o in objs:
        t = o.get("type", "")
        if t.endswith("Device"):
            info = o.get("info") or {}
            devices[o["id"]] = info.get("props") or {}
            for r in (info.get("params") or {}).get("EnumRoute", []):
                if r.get("available") == "no":
                    unavailable.setdefault(o["id"], set()).update(r.get("devices") or [])
        elif t.endswith("Metadata"):
            name = (o.get("props") or {}).get("metadata.name")
            for m in o.get("metadata") or []:
                v = m.get("value")
                if name == "default" and isinstance(v, dict):
                    if m["key"] == "default.audio.sink":
                        snap.default_sink = v.get("name")
                    elif m["key"] == "default.audio.source":
                        snap.default_source = v.get("name")
                elif name == "settings" and m["key"] == "clock.rate":
                    snap.rate = int(v)
        elif t.endswith("Link"):
            info = o.get("info") or {}
            a, b = info.get("output-node-id"), info.get("input-node-id")
            if a is not None and b is not None:
                links.setdefault(a, b)
                links.setdefault(b, a)

    for o in objs:
        if not o.get("type", "").endswith("Node"):
            continue
        info = o.get("info") or {}
        p = info.get("props") or {}
        cls = p.get("media.class", "")
        vol, muted = _vol((info.get("params") or {}).get("Props", []))
        running = info.get("state") == "running"
        node_name = p.get("node.name", "")
        if node_name == f"{OWN}.voice":
            cap = next((x for x in objs if ((x.get("info") or {}).get("props") or {}).get("node.name") == f"{OWN}.voice.capture"), None)
            target = (((cap or {}).get("info") or {}).get("props") or {}).get("target.object", "")
            snap.voice = Node(o["id"], node_name, "Voice filter", "WebRTC", "source", vol, muted, "wave", app=target)
            continue
        if node_name.startswith(OWN) or p.get("application.name") == OWN:
            continue
        if cls in ("Audio/Sink", "Audio/Source"):
            card = devices.get(p.get("device.id"), {})
            if card.get("device.api") == "bluez5":  # newer WirePlumber only tags the device, not the public node
                p = {**p, "device.api": "bluez5"}
            label, sub = _device_label(p, card)
            sink = cls == "Audio/Sink"
            avail = p.get("card.profile.device") not in unavailable.get(p.get("device.id"), set())
            n = Node(o["id"], node_name, label, sub, "sink" if sink else "source", vol, muted, _icon(p, sink), avail,
                     running=running)
            (snap.sinks if sink else snap.sources).append(n)
        elif cls in ("Stream/Output/Audio", "Stream/Input/Audio"):
            app = p.get("application.name") or p.get("application.process.binary") or node_name
            media = p.get("media.name", "")
            n = Node(o["id"], node_name, app, media if media != app else "", "play" if cls.endswith("Output/Audio") else "rec",
                     vol, muted, app=p.get("application.process.binary", ""), running=running, target=links.get(o["id"]))
            (snap.play if n.kind == "play" else snap.rec).append(n)

    for lst in (snap.sinks, snap.sources):
        lst.sort(key=lambda n: (not n.available, n.id))
    return snap


def wpctl(*args) -> None:
    subprocess.run(["wpctl", *map(str, args)], capture_output=True, timeout=5)


def set_volume(node: int | str, pct: int) -> None:
    wpctl("set-volume", node, f"{max(0, min(150, pct)) / 100:.2f}")


def step(node: int | str, delta: int, limit: float = 1.0) -> None:
    wpctl("set-volume", "-l", limit, node, f"{abs(delta)}%{'+' if delta > 0 else '-'}")


def set_mute(node: int | str, state: str = "toggle") -> None:
    wpctl("set-mute", node, state)


def set_default(node: int) -> None:
    wpctl("set-default", node)


SINK, SOURCE = "@DEFAULT_AUDIO_SINK@", "@DEFAULT_AUDIO_SOURCE@"


def quick(target: str) -> tuple[int, bool, str]:
    """(volume %, muted, description) of a default device without a full pw-dump (for waybar)."""
    r = subprocess.run(["wpctl", "get-volume", target], capture_output=True, text=True, timeout=3).stdout.split()
    vol = round(float(r[1]) * 100) if len(r) > 1 else 0
    desc = ""
    for line in subprocess.run(["wpctl", "inspect", target], capture_output=True, text=True, timeout=3).stdout.splitlines():
        if "node.description" in line:
            desc = line.split("=", 1)[1].strip().strip('"')
            break
    return vol, "[MUTED]" in r, desc
