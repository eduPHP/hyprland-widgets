#!/usr/bin/env bash
# Install hyprbell: put it on PATH and print the Hyprland and waybar lines to add.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=~/.local/bin
mkdir -p "$BIN"
dst=$BIN/hyprbell
if [[ -e $dst && ! -L $dst ]]; then mv "$dst" "$dst.bak.$(date +%Y%m%d-%H%M%S)"; fi
ln -sfn "$HERE/hyprbell" "$dst"
echo "linked $dst"
cat <<'TXT'
Add to ~/.config/hypr/autostart.conf:
  exec-once = hyprbell --daemon
and bind:
  bindd = SUPER CTRL, N, Notification history, exec, hyprbell

waybar (~/.config/waybar/config.jsonc), next to the clock:
  "modules-center": [ ..., "custom/hyprcal", "custom/hyprbell" ],
  "custom/hyprbell": {
    "exec": "hyprbell --status",
    "return-type": "json",
    "restart-interval": 5,
    "tooltip": true,
    "on-click": "hyprbell",
    "on-click-right": "hyprbell dnd"
  },
and in ~/.config/waybar/style.css:
  #custom-hyprbell { padding: 0 7px; margin: 3px 0 3px 2px; border-radius: 5px; }
  #custom-hyprbell.unread { color: @accent; }
  #custom-hyprbell.dnd { color: @yellow; }
  #custom-hyprbell.open { background-color: alpha(@accent, 0.18); color: @accent; }
TXT
