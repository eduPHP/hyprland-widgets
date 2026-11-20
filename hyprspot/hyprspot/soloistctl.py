"""Ciclo de vida do Spotify Soloist: validade de 90 dias e atualização do build."""
from __future__ import annotations

import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from datetime import date, datetime
from pathlib import Path

from hyprspot.config import CACHE_DIR
from hyprspot.onboarding import build_url

VALID_DAYS = 90
WARN_DAYS = 14
SOLOIST_BIN = Path.home() / ".local" / "bin" / "soloist"
_VERSION_RE = re.compile(r"^soloist\s+(\S+)\s+build\s+\d+\s+\((\d{8})\)")


def parse_version(text: str) -> tuple[str, date] | None:
    m = _VERSION_RE.match(text.strip())
    if not m:
        return None
    try:
        return m.group(1), datetime.strptime(m.group(2), "%Y%m%d").date()
    except ValueError:
        return None


def days_left(build: date, today: date) -> int:
    return VALID_DAYS - (today - build).days


def warn_level(days: int) -> str:
    if days <= 0:
        return "expired"
    if days <= WARN_DAYS:
        return "soon"
    return "ok"


def installed_version(binary: Path = SOLOIST_BIN) -> tuple[str, date] | None:
    try:
        out = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_version(out)


def run_status() -> int:
    v = installed_version()
    if v is None:
        print(f"Soloist não encontrado em {SOLOIST_BIN}")
        return 1
    d = days_left(v[1], date.today())
    msg = {"ok": "válido", "soon": "expira em breve", "expired": "EXPIRADO"}[warn_level(d)]
    print(f"Soloist {v[0]} (build {v[1].isoformat()}): {msg}, {max(d, 0)} dias restantes")
    return 0 if d > 0 else 2


def run_update() -> int:
    """Baixa o build oficial, confere que roda e substitui ~/.local/bin/soloist."""
    url = build_url(platform.machine())
    if url is None:
        print(f"O Spotify não publica o Soloist para {platform.machine()} (só x86_64 e arm64).")
        return 1
    dl_root = CACHE_DIR / "soloist-dl"
    dl_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=dl_root))
    try:
        archive = work / "soloist.tar.gz"
        print(f"Baixando {url} …")
        urllib.request.urlretrieve(url, archive)
        with tarfile.open(archive) as tar:
            member = tar.getmember("soloist")
            if not member.isfile():
                print("Pacote inesperado: 'soloist' não é um arquivo.")
                return 1
            tar.extract(member, work, filter="data")
        new_bin = work / "soloist"
        new_bin.chmod(0o755)
        v = installed_version(new_bin)
        if v is None:
            print("O build baixado não respondeu a --version; nada foi alterado.")
            return 1
        SOLOIST_BIN.parent.mkdir(parents=True, exist_ok=True)
        tmp = SOLOIST_BIN.with_suffix(".new")
        shutil.copy2(new_bin, tmp)
        tmp.replace(SOLOIST_BIN)
        print(f"Instalado Soloist {v[0]} (build {v[1].isoformat()}, válido por {days_left(v[1], date.today())} dias).")
        subprocess.run(["systemctl", "--user", "restart", "soloist.service"], check=False)
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)
