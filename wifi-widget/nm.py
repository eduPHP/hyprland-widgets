"""NetworkManager backend for the Wi-Fi widget (wraps nmcli, no UI code).

All calls are blocking; the UI should run them off the main thread.
Run `python3 nm.py` for a quick CLI dump of what the widget will see.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


class NMError(Exception):
    pass


@dataclass
class Network:
    ssid: str
    signal: int  # 0-100
    security: str  # "" for open networks, e.g. "WPA2" / "WPA2 WPA3"
    freq_mhz: int
    in_use: bool
    saved_as: str | None  # NetworkManager connection name, if saved

    @property
    def secure(self) -> bool:
        return bool(self.security)

    @property
    def band(self) -> str:
        return "5 GHz" if self.freq_mhz >= 4900 else "2.4 GHz"

    @property
    def bars(self) -> int:  # 0-4, for signal icons
        return min(4, self.signal // 20)


def _nmcli(*args: str, timeout: float = 45) -> str:
    try:
        r = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise NMError("NetworkManager timed out") from e
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().removeprefix("Error: ")
        raise NMError(msg or f"nmcli exited with {r.returncode}")
    return r.stdout


def _split(line: str, n: int) -> list[str]:
    # `-t -e no` output: split on the first n-1 colons so the last field (SSID) may contain ':'
    return line.split(":", n - 1)


def wifi_enabled() -> bool:
    return _nmcli("radio", "wifi").strip() == "enabled"


def set_wifi(on: bool) -> None:
    _nmcli("radio", "wifi", "on" if on else "off")


def saved_connections() -> dict[str, str]:
    """ssid -> connection name, for saved Wi-Fi profiles."""
    out = {}
    for line in _nmcli("-t", "-e", "no", "-f", "NAME,TYPE", "con", "show").splitlines():
        name, _, ctype = line.rpartition(":")
        if ctype != "802-11-wireless":
            continue
        ssid = _nmcli("-g", "802-11-wireless.ssid", "con", "show", name).strip()
        if ssid:
            out[ssid] = name
    return out


def scan(rescan: bool = False) -> list[Network]:
    """Visible networks, one per SSID (strongest AP), connected first then by signal."""
    saved = saved_connections()
    raw = _nmcli(
        "-t", "-e", "no", "-f", "IN-USE,SIGNAL,FREQ,SECURITY,SSID",
        "dev", "wifi", "list", "--rescan", "yes" if rescan else "no",
    )
    best: dict[str, Network] = {}
    for line in raw.splitlines():
        in_use, signal, freq, security, ssid = _split(line, 5)
        if not ssid:
            continue  # hidden network
        sec = "" if security in ("", "--") else security
        net = Network(ssid, int(signal), sec, int(freq.split()[0]), in_use == "*", saved.get(ssid))
        cur = best.get(ssid)
        if cur is None or net.in_use or (not cur.in_use and net.signal > cur.signal):
            best[ssid] = net
    return sorted(best.values(), key=lambda n: (not n.in_use, -n.signal))


def current() -> Network | None:
    return next((n for n in scan() if n.in_use), None)


class NeedsPassword(NMError):
    pass


def connect(net: Network, password: str | None = None) -> None:
    """Connect to a network. Raises NeedsPassword when a secret is required."""
    if net.saved_as:
        if password:
            _nmcli("con", "modify", "id", net.saved_as, "wifi-sec.psk", password)
        try:
            _nmcli("con", "up", "id", net.saved_as)
        except NMError as e:
            raise NeedsPassword(str(e)) from e
        return

    if net.secure and not password:
        raise NeedsPassword("Password required")
    args = ["dev", "wifi", "connect", net.ssid]
    if password:
        args += ["password", password]
    try:
        _nmcli(*args)
    except NMError:
        # nmcli may leave a half-made profile behind after a failed first attempt
        if net.ssid in saved_connections():
            subprocess.run(["nmcli", "con", "delete", "id", net.ssid], capture_output=True)
        raise


def disconnect(net: Network) -> None:
    if net.saved_as:
        _nmcli("con", "down", "id", net.saved_as)


def forget(net: Network) -> None:
    if net.saved_as:
        _nmcli("con", "delete", "id", net.saved_as)


@dataclass
class Link:
    device: str
    ssid: str
    freq_mhz: int
    bitrate_mbps: float
    dbm: int
    signal: int  # 0-100, NetworkManager's scale
    connected_s: int
    channel: str  # "44 · 80 MHz"
    ipv4: str
    gateway: str
    security: str

    @property
    def band(self) -> str:
        return "5 GHz" if self.freq_mhz >= 4900 else "2.4 GHz"


_KEY_MGMT = {"wpa-psk": "WPA2-PSK", "sae": "WPA3-SAE", "wpa-eap": "WPA2-EAP", "owe": "OWE", "none": "WEP"}


def wifi_device() -> str | None:
    for line in _nmcli("-t", "-f", "DEVICE,TYPE", "dev").splitlines():
        dev, _, typ = line.rpartition(":")
        if typ == "wifi":
            return dev
    return None


def device_state(dev: str) -> tuple[str, str]:
    """(state, connection name), e.g. ("connected", "Esuardo") or ("connecting (configuring)", "x")."""
    out = _nmcli("-g", "GENERAL.STATE,GENERAL.CONNECTION", "dev", "show", dev).splitlines()
    state = out[0].split(" ", 1)[1].strip("()") if out and " " in out[0] else ""
    return state, (out[1] if len(out) > 1 else "")


def link_info(dev: str) -> Link | None:
    try:
        link = subprocess.run(["iw", "dev", dev, "link"], capture_output=True, text=True, timeout=5).stdout
        info = subprocess.run(["iw", "dev", dev, "info"], capture_output=True, text=True, timeout=5).stdout
        sta = subprocess.run(["iw", "dev", dev, "station", "dump"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not link.startswith("Connected"):
        return None

    def field(text: str, key: str) -> str:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith(key):
                return line[len(key):].strip()
        return ""

    chan = field(info, "channel ")  # "6 (2437 MHz), width: 20 MHz, center1: 2437 MHz"
    num = chan.split(" ", 1)[0] if chan else "?"
    width = chan.split("width:", 1)[1].split(",")[0].strip() if "width:" in chan else ""

    ip = gw = sec = conn = ""
    for line in _nmcli("-g", "IP4.ADDRESS,IP4.GATEWAY,GENERAL.CONNECTION", "dev", "show", dev).splitlines():
        if not ip and "/" in line:
            ip = line.split("/")[0]
        elif not gw and line.count(".") == 3:
            gw = line
        elif line:
            conn = line
    if conn:
        try:
            km = _nmcli("-g", "802-11-wireless-security.key-mgmt", "con", "show", conn).strip()
            sec = _KEY_MGMT.get(km, km.upper() or "open")
        except NMError:
            pass

    dbm = int(field(link, "signal:").split()[0] or 0)
    return Link(
        device=dev,
        ssid=field(link, "SSID:"),
        freq_mhz=int(float(field(link, "freq:") or 0)),
        bitrate_mbps=float((field(link, "tx bitrate:") or "0").split()[0]),
        dbm=dbm,
        signal=max(0, min(100, 2 * (dbm + 100))),  # same mapping NetworkManager uses
        connected_s=int((field(sta, "connected time:") or "0").split()[0]),
        channel=f"{num} · {width}" if width else num,
        ipv4=ip,
        gateway=gw,
        security=sec,
    )


def traffic_bytes(dev: str) -> tuple[int, int]:
    base = f"/sys/class/net/{dev}/statistics/"
    with open(base + "rx_bytes") as rx, open(base + "tx_bytes") as tx:
        return int(rx.read()), int(tx.read())


if __name__ == "__main__":
    print("wifi:", "on" if wifi_enabled() else "off")
    for n in scan():
        flags = ("✓ " if n.in_use else "  ") + ("🔒" if n.secure else "  ")
        saved = " (saved)" if n.saved_as and not n.in_use else ""
        print(f"{flags} {'▂▄▆█'[: n.bars] or '·':4} {n.signal:3}%  {n.band:7}  {n.ssid}{saved}")
