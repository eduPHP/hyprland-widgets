"""Aviso de versão nova: compara a última release do GitHub com a versão instalada."""
from __future__ import annotations

import json
import re
from typing import Callable

RELEASES_URL = "https://api.github.com/repos/eduPHP/hyprspot/releases/latest"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "hyprspot"}

_VERSION = re.compile(r"v?(\d+(?:\.\d+)*)")


def parse_version(tag: str | None) -> tuple[int, ...] | None:
    """'v1.2.3' ou '1.2.3' → (1, 2, 3); qualquer outra coisa → None."""
    m = _VERSION.fullmatch((tag or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _pad(v: tuple[int, ...], n: int) -> tuple[int, ...]:
    return v + (0,) * (n - len(v))


def newer_release(body, current: str) -> tuple[str, str] | None:
    """(versão, página da release) se a release for mais nova que `current`."""
    if not isinstance(body, dict) or not body.get("html_url"):
        return None
    tag = body.get("tag_name")
    latest, mine = parse_version(tag), parse_version(current)
    if latest is None or mine is None:
        return None
    n = max(len(latest), len(mine))
    if _pad(latest, n) <= _pad(mine, n):
        return None
    return tag.strip().lstrip("v"), body["html_url"]


def check(transport, current: str, on_newer: Callable[[str, str], None]) -> None:
    """Pergunta ao GitHub; chama on_newer(versão, url) só se houver versão nova. Erros são ignorados."""
    def done(status, _headers, raw, error):
        if error or status != 200:
            return
        try:
            body = json.loads(raw)
        except ValueError:
            return
        found = newer_release(body, current)
        if found:
            on_newer(*found)

    transport("GET", RELEASES_URL, dict(HEADERS), None, done)


def notification_text(version: str, current: str, repo_dir: str) -> tuple[str, str]:
    return (f"hyprspot {version} disponível",
            f"Você tem a {current}. Para atualizar: git -C {repo_dir} pull e abra o hyprspot de novo.")
