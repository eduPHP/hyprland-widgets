#!/usr/bin/env bash
# Instalador do hyprspot para um computador novo (Hyprland + conta Spotify Premium).
#
#   git clone https://github.com/eduPHP/hyprspot.git ~/.local/share/hyprspot && ~/.local/share/hyprspot/setup.sh
#
# Etapas: dependências do sistema (com sudo) → atalhos no Hyprland e serviço do Soloist →
# download do Soloist oficial → tela de configuração (chave, app da Web API, login, pareamento).
#
#   ./setup.sh                  instala tudo e abre a tela de configuração
#   ./setup.sh -y               não pergunta antes de usar o sudo
#   ./setup.sh --check          só confere as dependências
#   ./setup.sh --print-deps PM  lista os pacotes para apt | pacman | dnf
#   ./setup.sh --remove         desfaz (atalhos, autostart e links)
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
YES=0

if [ -t 1 ]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'; else B= G= Y= R= N=; fi
step() { printf '\n%s==> %s%s\n' "$B" "$1" "$N"; }
ok() { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$1"; }
die() { printf '%sERRO:%s %s\n' "$R" "$N" "$1" >&2; exit 1; }

deps_for() {
  case "$1" in
    apt) echo "python3 python3-gi gir1.2-gtk-4.0 gir1.2-gtk4layershell-1.0 gir1.2-soup-3.0 libglib2.0-bin xdg-utils" ;;
    pacman) echo "python python-gobject gtk4 gtk4-layer-shell libsoup3 glib2 xdg-utils" ;;
    dnf) echo "python3 python3-gobject gtk4 gtk4-layer-shell libsoup3 glib2 xdg-utils" ;;
    *) return 1 ;;
  esac
}

detect_pm() {
  if command -v apt-get >/dev/null; then echo apt
  elif command -v pacman >/dev/null; then echo pacman
  elif command -v dnf >/dev/null; then echo dnf
  fi
}

deps_ok() {  # Python ≥ 3.11 com GTK 4, gtk4-layer-shell e libsoup 3, mais gdbus e xdg-open
  command -v python3 >/dev/null && command -v gdbus >/dev/null && command -v xdg-open >/dev/null || return 1
  python3 - <<'EOF' >/dev/null 2>&1
import tomllib  # noqa: F401  (Python 3.11+)
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Soup", "3.0")
from gi.repository import Gtk, Gtk4LayerShell, Soup  # noqa: F401
EOF
}

install_deps() {
  local pm pkgs
  pm="$(detect_pm)"
  if [ -z "$pm" ]; then
    die "não reconheci o gerenciador de pacotes. Instale à mão: Python 3.11+, PyGObject, GTK 4, gtk4-layer-shell (com o typelib), libsoup 3, gdbus e xdg-utils. Depois rode de novo."
  fi
  pkgs="$(deps_for "$pm")"
  echo "  Pacotes ($pm): $pkgs"
  if [ "$YES" != 1 ]; then
    read -rp "  Instalar com sudo agora? [S/n] " ans
    case "${ans:-s}" in s | S | y | Y) ;; *) die "cancelado. Instale os pacotes acima e rode de novo." ;; esac
  fi
  # shellcheck disable=SC2086
  case "$pm" in
    apt) sudo apt-get update && sudo apt-get install -y $pkgs ;;
    pacman) sudo pacman -S --needed --noconfirm $pkgs ;;
    dnf) sudo dnf install -y $pkgs ;;
  esac
}

case "${1:-}" in
  -h | --help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  --print-deps) deps_for "${2:-}" || die "use apt, pacman ou dnf"; exit 0 ;;
  --check) if deps_ok; then ok "dependências instaladas"; exit 0; else warn "faltam dependências"; exit 1; fi ;;
  --remove) exec "$ROOT/install.sh" --remove ;;
  -y | --yes) YES=1 ;;
  "") ;;
  *) die "opção desconhecida: $1 (veja --help)" ;;
esac

[ "$(id -u)" -ne 0 ] || die "rode como o seu usuário, sem sudo (o script pede sudo só para os pacotes)."

printf '%shyprspot%s · mini player de Spotify para Hyprland\n' "$B" "$N"

step "Conferindo o Hyprland"
command -v hyprctl >/dev/null || die "não encontrei o hyprctl. O hyprspot precisa do Hyprland 0.53 ou mais novo."
[ -f "$HOME/.config/hypr/hyprland.conf" ] || die "não encontrei ~/.config/hypr/hyprland.conf."
ok "$(hyprctl version 2>/dev/null | head -1 || echo Hyprland)"

step "Dependências do sistema"
if deps_ok; then
  ok "já instaladas"
else
  install_deps
  deps_ok || die "as dependências continuam faltando. Confira se o Python é 3.11+ e se o typelib do gtk4-layer-shell foi instalado."
  ok "instaladas"
fi

step "Atalhos do Hyprland e serviço do Soloist"
"$ROOT/install.sh" | sed '1d'

step "Spotify Soloist (player oficial, baixado do Spotify)"
if "$ROOT/bin/hyprspot" soloist-status >/dev/null 2>&1; then
  ok "$("$ROOT/bin/hyprspot" soloist-status)"
else
  "$ROOT/bin/hyprspot" soloist-update || die "não consegui baixar o Soloist. Confira a conexão e rode de novo."
fi

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) warn "~/.local/bin não está no PATH; os atalhos funcionam, mas no terminal use $HOME/.local/bin/hyprspot" ;;
esac

step "Configuração da conta"
if [ -n "${WAYLAND_DISPLAY:-}" ]; then
  echo "  Abrindo a tela de configuração (chave do Soloist, app da Web API, login e pareamento)…"
  "$ROOT/bin/hyprspot" setup || true
  printf '\n%sPronto.%s Super+Alt+M abre o hyprspot. Para reabrir a configuração: hyprspot setup\n' "$G" "$N"
else
  warn "sem sessão gráfica. Entre no Hyprland e rode: hyprspot setup"
fi
