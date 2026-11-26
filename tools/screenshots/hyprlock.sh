#!/usr/bin/env bash
# Screenshots of the hyprlock-theme lock screen without locking your session.
#
#   tools/screenshots/hyprlock.sh [OUT_DIR [NAME:THEME[:caps|fail|typing] ...]]
#   (THEME "installed" uses ~/.config/hypr/hyprlock.conf as it is)
#
# Starts a second, nested Hyprland (its window is parked on a hidden special workspace), gives it two
# headless monitors the size of edu-dev's (1920x1080 and 2560x1080), locks *that* compositor with
# hyprlock and grabs each monitor with grim. Wrong-password shots type a made-up password into it.
# Your own session is never locked. The avatar is your initial on a tile (AVATAR=~/.face for your photo),
# since these go in the public README.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
OUT=${1:-$REPO/docs/screenshots}
WORK=${XDG_RUNTIME_DIR:-/tmp}/hyprlock-shots
THEME_CMD=$REPO/hyprlock-theme/hyprlock-theme
mkdir -p "$WORK" "$OUT"

cat > "$WORK/nest.conf" <<'EOF'
monitor = , 1280x720, auto, 1
misc {
    disable_hyprland_logo = true
    disable_splash_rendering = true
    allow_session_lock_restore = true
}
animations {
    enabled = false
}
debug {
    suppress_errors = true
}
ecosystem {
    no_update_news = true
    no_donation_nag = true
}
EOF
grep -q '^input' "$WORK/nest.conf" || printf 'input {\n    kb_layout = %s\n}\n' \
  "$(hyprctl getoption input:kb_layout -j | jq -r .str)" >> "$WORK/nest.conf"

OUTER_WL=$WAYLAND_DISPLAY OUTER_SIG=$HYPRLAND_INSTANCE_SIGNATURE
Hyprland -c "$WORK/nest.conf" > "$WORK/nest.log" 2>&1 &
NPID=$!
cleanup() { kill "$NPID" 2>/dev/null || true; }
trap cleanup EXIT

win=
for _ in $(seq 1 50); do
  sleep 0.2
  win=$(hyprctl clients -j | jq -r ".[] | select(.pid==$NPID) | .address")
  [[ -n $win ]] && break
done
[[ -n $win ]] && hyprctl dispatch movetoworkspacesilent "special:hyprlock-shots,address:$win" >/dev/null

sig= sock=
for _ in $(seq 1 25); do
  read -r sig sock < <(hyprctl instances -j | jq -r ".[] | select(.pid==$NPID) | \"\(.instance) \(.wl_socket)\"") || true
  [[ -n $sig ]] && break
  sleep 0.2
done
export WAYLAND_DISPLAY=$sock HYPRLAND_INSTANCE_SIGNATURE=$sig
hyprctl output create headless LAP >/dev/null
hyprctl output create headless UW >/dev/null
hyprctl keyword monitor LAP,1920x1080@60,0x0,1 >/dev/null
hyprctl keyword monitor UW,2560x1080@60,1920x0,1 >/dev/null
hyprctl keyword monitor WAYLAND-1,disable >/dev/null
sleep 0.5
hyprctl dismissnotify -1 >/dev/null   # the nested instance's "started without start-hyprland" notice

# shot NAME THEME [caps|fail] -> NAME.png (laptop) and NAME-wide.png (ultrawide)
shot() {
  local name=$1 theme=$2 state=${3:-} conf="$WORK/$1.conf" demo=()
  [[ $state == caps ]] && demo=(--demo caps)
  if [[ $theme == installed ]]; then conf=$HOME/.config/hypr/hyprlock.conf   # exactly what locks your screen
  else "$THEME_CMD" sync --quiet --theme "$theme" --out "$conf" --avatar "${AVATAR:-initial}" "${demo[@]}"; fi
  hyprlock --config "$conf" --no-fade-in --immediate-render > "$WORK/$name.log" 2>&1 &
  local lp=$!
  sleep 2.5
  case $state in
    caps) wtype -k Caps_Lock; sleep 0.6 ;;
    fail) wtype "not-my-password"; wtype -k Return; sleep 4 ;;
    typing) wtype "correct horse"; sleep 0.6 ;;
  esac
  grim -o LAP "$WORK/$name.png"
  grim -o UW "$WORK/$name-wide.png"
  [[ $state == caps ]] && wtype -k Caps_Lock
  kill "$lp" 2>/dev/null || true
  wait "$lp" 2>/dev/null || true
  sleep 0.5
  python3 - "$WORK" "$OUT" "$name" "$state" <<'PY'
import sys
from PIL import Image
work, out, name, state = sys.argv[1:]
im = Image.open(f"{work}/{name}.png").convert("RGB")
w, h = im.size
if state in ("fail", "caps"):            # a state: just the stack around the field
    im.crop((w // 2 - 380, h // 2 - 200, w // 2 + 380, h // 2 + 260)).save(f"{out}/{name}.png", optimize=True)
else:
    im.resize((1600, round(1600 * h / w)), Image.LANCZOS).save(f"{out}/{name}.png", optimize=True)
if not state:                            # the plain shot also gets its ultrawide twin
    wide = Image.open(f"{work}/{name}-wide.png").convert("RGB")
    wide.resize((1600, round(1600 * wide.height / wide.width)), Image.LANCZOS).save(f"{out}/{name}-wide.png", optimize=True)
PY
  echo "  $OUT/$name.png"
}

# arguments after OUT_DIR: NAME:THEME[:STATE] ..., else the README set
shift || true
specs=("$@")
((${#specs[@]})) || specs=(hyprlock:tokyo-night hyprlock-fail:tokyo-night:fail hyprlock-caps:nord:caps hyprlock-light:flexoki-light:typing)
for spec in "${specs[@]}"; do
  IFS=: read -r name theme state <<< "$spec"
  shot "$name" "$theme" "$state"
done
