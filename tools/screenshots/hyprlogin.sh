#!/usr/bin/env bash
# hyprlogin.sh: README screenshots of the login screen, from sddm-greeter --test-mode.
# Each state runs the real theme fullscreen on the laptop panel and grabs it with grim.
#   tools/screenshots/hyprlogin.sh [OUTPUT]     (default eDP-1)
set -euo pipefail
cd "$(dirname "$0")/../.."
out=${1:-eDP-1}
shot() {  # shot NAME [preview args...]
  local name=$1; shift
  hyprlogin/hyprlogin preview --seconds 6 "$@" >/dev/null 2>&1 &
  sleep 4.5
  grim -o "$out" "/tmp/hyprlogin-$name.png"
  wait
  python3 - "$name" <<'PY'
import sys
from PIL import Image
name = sys.argv[1]
im = Image.open(f"/tmp/hyprlogin-{name}.png").convert("RGB")
if name in ("hyprlogin-sessions", "hyprlogin-error"):   # states: just the login stack and its surroundings
    w, h = im.size
    im = im.crop((w // 2 - 330, h // 2 - 130, w // 2 + 330, h // 2 + 270))
    im.save(f"docs/screenshots/{name}.png", optimize=True)
    sys.exit()
im.resize((1600, round(1600 * im.height / im.width)), Image.LANCZOS).save(f"docs/screenshots/{name}.png", optimize=True)
if name == "hyprlogin":
    im.resize((960, round(960 * im.height / im.width)), Image.LANCZOS).save("hyprlogin/theme/preview.png", optimize=True)
PY
  echo "docs/screenshots/$name.png"
}
shot hyprlogin --theme tokyo-night
shot hyprlogin-sessions --theme tokyo-night --state sessions
shot hyprlogin-error --theme tokyo-night --state error
shot hyprlogin-light --theme flexoki-light --state caps
