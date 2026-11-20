#!/usr/bin/env python3
"""Run wifi-widget against made-up networks, for README screenshots.

Every backend call (nmcli, resolvectl, ping, ipinfo.io) is replaced with fixed fake data, so the
popover shows no real SSIDs, addresses or location. Nothing on the machine is changed.
    tools/screenshots/wifi_demo.py
"""

import os
import sys
import tempfile
from pathlib import Path

LAYER_SHELL = "/usr/lib/x86_64-linux-gnu/libgtk4-layer-shell.so.0"
if LAYER_SHELL not in os.environ.get("LD_PRELOAD", ""):
    os.environ["LD_PRELOAD"] = ":".join(filter(None, [LAYER_SHELL, os.environ.get("LD_PRELOAD")]))
    os.execv(sys.executable, [sys.executable, os.path.realpath(__file__), *sys.argv[1:]])

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "wifi-widget"))
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="wifi-demo-")  # throwaway state.json

import dns  # noqa: E402
import nm  # noqa: E402
import vpn  # noqa: E402

N = nm.Network
NETS = [
    N("Lighthouse 5G", 92, "WPA2 WPA3", 5180, True, "Lighthouse 5G"),
    N("Coffee Lab", 78, "WPA2", 2437, False, "Coffee Lab"),
    N("Nebula", 71, "WPA2", 5240, False, None),
    N("Studio-Guest", 64, "", 2412, False, None),
    N("Orbit_2.4", 58, "WPA2", 2462, False, None),
    N("Sunset Loft", 47, "WPA2", 2437, False, None),
    N("Pixel Hotspot", 41, "WPA3", 5745, False, "Pixel Hotspot"),
    N("Library-Free", 26, "", 2412, False, None),
]
LINK = nm.Link("wlan0", "Lighthouse 5G", 5180, 866.7, -48, 92, 47 * 60, "36 · 80 MHz",
               "192.168.50.23", "192.168.50.1", "WPA3-SAE")
_traffic = [0, 0]


def traffic(dev):  # bursty download, steady upload
    import math, random

    t = len(_ticks)
    _ticks.append(t)
    _traffic[0] += int(900_000 * (1.2 + math.sin(t / 3) + random.random() * 1.4))
    _traffic[1] += int(120_000 * (1 + random.random()))
    return tuple(_traffic)


_ticks: list[int] = []


nm.wifi_enabled = lambda: True
nm.set_wifi = lambda on: None
nm.wifi_device = lambda: "wlan0"
nm.device_state = lambda dev: ("connected", "Lighthouse 5G")
nm.saved_connections = lambda: {n.ssid: n.ssid for n in NETS if n.saved_as}
nm.scan = lambda rescan=False: NETS
nm.current = lambda: NETS[0]
nm.link_info = lambda dev: LINK
nm.traffic_bytes = traffic
nm.connect = nm.disconnect = nm.forget = lambda *a, **k: None

T = vpn.Tunnel
TUNNELS = [
    T("de-frankfurt", "demo-1", "wireguard", True, "wg0", True, "", "demo"),
    T("us-new-york", "demo-2", "wireguard", False, None, True, "", "demo"),
    T("office", "demo-3", "wireguard", False, None, False, "10.20.0.0/16", "demo"),
    T("tailscale", "demo-4", "tailscale", False, None, False, "", "demo", "100.101.12.7"),
]
vpn.tunnels = lambda: TUNNELS
vpn.latency_ms = lambda host: {"demo": 23}.get(host)
vpn.exit_info = lambda: {"ip": "203.0.113.42", "location": "Frankfurt, DE"}  # TEST-NET-3 address
vpn.uptime_s = lambda iface: 3 * 3600 + 12 * 60
vpn.up = vpn.down = vpn.set_autoconnect = lambda *a, **k: 0

dns.dhcp_servers = lambda dev: ["192.168.50.1"]
dns.link_status = lambda dev: (["1.1.1.1", "1.0.0.1"], True)
dns.latency_ms = lambda server, tls=False: {"1.1.1.1": 9, "9.9.9.9": 14, "8.8.8.8": 17}.get(server, 31)
dns.apply = lambda *a, **k: 0

import widget  # noqa: E402

widget.main()
