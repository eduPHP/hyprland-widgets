"""VPN backend: NetworkManager WireGuard/VPN profiles plus Tailscale. Blocking calls; run off the UI thread."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass

from gi.repository import Gio, GLib

import nm


@dataclass
class Tunnel:
    name: str
    uuid: str
    kind: str  # wireguard | openvpn | vpn | tailscale
    active: bool
    iface: str | None
    full: bool  # carries the default route
    routes: str  # split-tunnel ranges, for display
    endpoint: str | None  # host to ping for latency
    address: str = ""  # tailscale IP

    @property
    def badge(self) -> str:
        if self.kind == "tailscale":
            return "TS"
        if not self.full:
            return "LAN"
        m = re.match(r"([a-z]{2})[-_ ]", self.name.lower())
        return m.group(1).upper() if m else self.name[:2].upper()

    @property
    def detail(self) -> str:
        if self.kind == "tailscale":
            return f"tailscale · {'exit node' if self.full else 'mesh'} · {self.address}"
        return f"{self.kind} · " + ("full tunnel" if self.full else f"split · {self.routes or 'routes'}")


def _settings(uuid: str) -> dict:
    """Full NM settings of a profile (incl. WireGuard peers, which nmcli can't print) via D-Bus."""
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
    path = bus.call_sync(
        "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager/Settings",
        "org.freedesktop.NetworkManager.Settings", "GetConnectionByUuid",
        GLib.Variant("(s)", (uuid,)), None, Gio.DBusCallFlags.NONE, 5000, None,
    ).unpack()[0]
    return bus.call_sync(
        "org.freedesktop.NetworkManager", path,
        "org.freedesktop.NetworkManager.Settings.Connection", "GetSettings",
        None, None, Gio.DBusCallFlags.NONE, 5000, None,
    ).unpack()[0]


def _nm_tunnels() -> list[Tunnel]:
    out = []
    for line in nm._nmcli("-t", "-e", "no", "-f", "TYPE,UUID,DEVICE,ACTIVE,NAME", "con", "show").splitlines():
        ctype, uuid, dev, active, name = line.split(":", 4)
        if ctype not in ("wireguard", "vpn"):
            continue
        s = _settings(uuid)
        ipv4 = s.get("ipv4", {})
        kind, endpoint, routes, full = ctype, None, [], not ipv4.get("never-default", False)
        if ctype == "wireguard":
            peers = s.get("wireguard", {}).get("peers", [])
            for p in peers:
                endpoint = endpoint or p.get("endpoint")
                routes += p.get("allowed-ips", [])
            full = any(r in ("0.0.0.0/0", "::/0") for r in routes)
        else:
            data = s.get("vpn", {}).get("data", {})
            kind = s.get("vpn", {}).get("service-type", "vpn").rsplit(".", 1)[-1]
            endpoint = (data.get("remote") or data.get("gateway") or "").split(",")[0].split(":")[0] or None
        if endpoint and endpoint.count(":") == 1:
            endpoint = endpoint.split(":")[0]  # host:port -> host
        elif endpoint and endpoint.startswith("["):
            endpoint = endpoint[1:].split("]")[0]
        shown = ", ".join(r for r in routes if r not in ("0.0.0.0/0", "::/0"))
        out.append(Tunnel(name, uuid, kind, active == "yes", dev or None, full, shown, endpoint))
    return out


def tailscale_status() -> dict | None:
    if not shutil.which("tailscale"):
        return None
    try:
        r = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5)
        return json.loads(r.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _tailscale() -> Tunnel | None:
    st = tailscale_status()
    if st is None:
        return None
    ips = (st.get("Self") or {}).get("TailscaleIPs") or [""]
    up = st.get("BackendState") == "Running"
    exit_node = bool(st.get("ExitNodeStatus"))
    return Tunnel("tailscale", "tailscale", "tailscale", up, "tailscale0" if up else None, exit_node, "", None, ips[0])


def tunnels() -> list[Tunnel]:
    ts = _tailscale()
    return _nm_tunnels() + ([ts] if ts else [])


def protecting(tuns: list[Tunnel]) -> Tunnel | None:
    """The active tunnel that carries all traffic, if any."""
    return next((t for t in tuns if t.active and t.full), None)


def up(t: Tunnel) -> None:
    if t.kind == "tailscale":
        _tailscale_cmd("up")
    else:
        nm._nmcli("con", "up", "uuid", t.uuid, timeout=60)


def down(t: Tunnel) -> None:
    if t.kind == "tailscale":
        _tailscale_cmd("down")
    else:
        nm._nmcli("con", "down", "uuid", t.uuid)


def _tailscale_cmd(verb: str) -> None:
    r = subprocess.run(["tailscale", verb], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip()
        if "access denied" in msg.lower() or "operator" in msg.lower() or "permission" in msg.lower():
            msg = "needs root once: sudo tailscale set --operator=$USER"
        raise nm.NMError(msg.splitlines()[-1] if msg else f"tailscale {verb} failed")


def latency_ms(host: str | None) -> int | None:
    if not host:
        return None
    r = subprocess.run(["ping", "-n", "-c1", "-W1", host], capture_output=True, text=True)
    m = re.search(r"time=([\d.]+) ms", r.stdout)
    return round(float(m.group(1))) if m else None


def exit_info() -> dict:
    """Public exit IP + location as seen from outside (one request to ipinfo.io)."""
    try:
        with urllib.request.urlopen("https://ipinfo.io/json", timeout=4) as resp:
            d = json.load(resp)
        return {"ip": d.get("ip", ""), "location": ", ".join(x for x in (d.get("city"), d.get("country")) if x)}
    except (OSError, ValueError):
        return {"ip": "", "location": ""}


def uptime_s(iface: str | None) -> int | None:
    """Seconds since the tunnel interface was created (WireGuard handshakes need root to read)."""
    try:
        return int(time.time() - os.stat(f"/sys/class/net/{iface}").st_ctime) if iface else None
    except OSError:
        return None


def import_wireguard(path: str) -> str:
    """Import a wg-quick .conf without leaving it active or autoconnecting. Returns the profile name."""
    out = nm._nmcli("con", "import", "type", "wireguard", "file", path)
    uuid = re.search(r"\(([0-9a-f-]{36})\)", out).group(1)
    # NM brings imported profiles straight up; a full tunnel with a bad endpoint would black-hole traffic
    subprocess.run(["nmcli", "con", "down", "uuid", uuid], capture_output=True)
    nm._nmcli("con", "modify", "uuid", uuid, "connection.autoconnect", "no")
    return nm._nmcli("-g", "connection.id", "con", "show", "uuid", uuid).strip()


def set_autoconnect(vpn_uuid: str | None) -> int:
    """Bring vpn_uuid up together with every saved open Wi-Fi profile (NM secondaries); None clears it."""
    n = 0
    for line in nm._nmcli("-t", "-e", "no", "-f", "TYPE,UUID", "con", "show").splitlines():
        ctype, uuid = line.split(":", 1)
        if ctype != "802-11-wireless":
            continue
        if nm._nmcli("-g", "802-11-wireless-security.key-mgmt", "con", "show", "uuid", uuid).strip():
            continue  # secured network
        nm._nmcli("con", "modify", "uuid", uuid, "connection.secondaries", vpn_uuid or "")
        n += 1
    return n
