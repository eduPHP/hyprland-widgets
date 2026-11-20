"""Small persisted preferences for the widget (~/.config/wifi-widget/state.json)."""

import json
import os
from pathlib import Path

PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "wifi-widget" / "state.json"

DEFAULTS = {
    "last_vpn": None,  # uuid of the last tunnel brought up from the widget
    "vpn_autoconnect": False,
    "dns": "auto",  # auto | cloudflare | quad9 | google | custom
    "dns_custom": "",
    "dns_tls": False,
    "dns_fallback": True,
}


def load() -> dict:
    try:
        return {**DEFAULTS, **json.loads(PATH.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(PATH)
