#!/usr/bin/env bash
# Install hyprmenu: put it on PATH and print the Hyprland lines to add.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=~/.local/bin
mkdir -p "$BIN"
dst=$BIN/hyprmenu
if [[ -e $dst && ! -L $dst ]]; then mv "$dst" "$dst.bak.$(date +%Y%m%d-%H%M%S)"; fi
ln -sfn "$HERE/hyprmenu" "$dst"
echo "linked $dst"
cat <<'TXT'
Add to ~/.config/hypr/autostart.conf:
  exec-once = hyprmenu --daemon
and bind (replacing the walker / omarchy-menu binds):
  bindd = SUPER, SPACE, Start menu, exec, hyprmenu
  bindd = SUPER ALT, SPACE, Start menu (Omarchy menu), exec, hyprmenu menu
TXT
