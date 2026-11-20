"""DNS backend: per-Wi-Fi-profile resolvers through NetworkManager + systemd-resolved (no root needed)."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

import nm


@dataclass
class Resolver:
    key: str
    name: str
    tag: str  # small chip next to the name
    servers: list[str]
    sni: str = ""  # TLS server name for dns-over-tls


PRESETS = [
    Resolver("auto", "auto", "dhcp", []),
    Resolver("cloudflare", "Cloudflare", "fast", ["1.1.1.1", "1.0.0.1"], "cloudflare-dns.com"),
    Resolver("quad9", "Quad9", "blocks malware", ["9.9.9.9", "149.112.112.112"], "dns.quad9.net"),
    Resolver("google", "Google", "", ["8.8.8.8", "8.8.4.4"], "dns.google"),
    Resolver("custom", "custom", "yours", []),
]


def dhcp_servers(dev: str) -> list[str]:
    for line in nm._nmcli("-f", "DHCP4", "dev", "show", dev).splitlines():
        if " domain_name_servers = " in line:
            return line.split("=", 1)[1].split()
    return []


def link_status(dev: str) -> tuple[list[str], bool]:
    """(servers resolved actually uses on dev, dns-over-tls on?)"""
    servers = subprocess.run(["resolvectl", "dns", dev], capture_output=True, text=True).stdout
    servers = [s.split("#")[0] for s in servers.split(":", 1)[-1].split()]
    tls = subprocess.run(["resolvectl", "dnsovertls", dev], capture_output=True, text=True).stdout
    return servers, tls.strip().endswith(("yes", "opportunistic"))


def identify(servers: list[str], dhcp: list[str]) -> str:
    if not servers or servers[0] in dhcp:
        return "auto"
    for p in PRESETS:
        if servers[0] in p.servers:
            return p.key
    return "custom"


def apply(res: Resolver, custom: list[str], tls: bool, fallback: bool, dev: str) -> int:
    """Write the resolver into every saved Wi-Fi profile, then reapply on the live device."""
    servers = custom if res.key == "custom" else res.servers
    if res.key == "auto" or not servers:
        dns, ignore_auto = "", "no"
        dot = "opportunistic" if tls else "-1"  # routers rarely speak DoT; don't break lookups
    else:
        dns = " ".join(f"{s}#{res.sni}" if tls and res.sni else s for s in servers)
        ignore_auto = "no" if fallback else "yes"  # fallback = DHCP servers stay behind ours
        dot = "yes" if tls else "no"
    n = 0
    for line in nm._nmcli("-t", "-e", "no", "-f", "TYPE,UUID", "con", "show").splitlines():
        ctype, uuid = line.split(":", 1)
        if ctype == "802-11-wireless":
            nm._nmcli("con", "modify", "uuid", uuid, "ipv4.dns", dns,
                      "ipv4.ignore-auto-dns", ignore_auto, "connection.dns-over-tls", dot)
            n += 1
    subprocess.run(["nmcli", "dev", "reapply", dev], capture_output=True, timeout=20)
    return n


def latency_ms(server: str, tls: bool = False) -> int | None:
    args = ["dig", f"@{server}", "example.com", "+tries=1", "+time=2", "+noall", "+stats"]
    if tls:
        args.append("+tls")
    times = []
    for _ in range(4):  # average 3 answers; a lost UDP packet shouldn't read as "down"
        r = subprocess.run(args, capture_output=True, text=True, timeout=8)
        m = re.search(r"Query time: (\d+) msec", r.stdout)
        if m:
            times.append(int(m.group(1)))
            if len(times) == 3:
                break
    return round(sum(times) / len(times)) if times else None
