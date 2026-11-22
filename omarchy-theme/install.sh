#!/usr/bin/env bash
# Install the Omarchy theme switcher: fetch Omarchy's themes and put the commands on PATH.
#   ./install.sh            first install (keeps themes already fetched)
#   ./install.sh --update   re-fetch Omarchy's themes
# Wiring your apps to ~/.config/omarchy/current/theme/ is a one-time edit per app; see README.md.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
THEMES=~/.local/share/omarchy/themes
BIN=~/.local/bin

if [[ ! -d $THEMES || ${1:-} == --update ]]; then
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  echo "fetching Omarchy themes..."
  git clone -q --depth 1 --filter=blob:none --sparse https://github.com/basecamp/omarchy.git "$tmp/omarchy"
  git -C "$tmp/omarchy" sparse-checkout set themes
  mkdir -p "$(dirname "$THEMES")"
  rm -rf "$THEMES.new" && cp -r "$tmp/omarchy/themes" "$THEMES.new"
  rm -rf "$THEMES" && mv "$THEMES.new" "$THEMES"
  echo "$(ls "$THEMES" | wc -l) themes in $THEMES"
fi

mkdir -p "$BIN" ~/.config/omarchy/themes ~/.config/omarchy/themed ~/.config/omarchy/hooks/theme-set.d
link() {  # link NAME TARGET, keeping a non-link file that is already there
  local dst=$BIN/$1
  if [[ -e $dst && ! -L $dst ]]; then mv "$dst" "$dst.bak.$(date +%Y%m%d-%H%M%S)"; fi
  ln -sfn "$2" "$dst"
}
link omarchy-theme "$HERE/omarchy_theme.py"
for c in set list current next prev refresh bg bg-next undo; do link "omarchy-theme-$c" "$HERE/omarchy_theme.py"; done
link omarchy-theme-menu "$HERE/bin/omarchy-theme-menu"
link hyprtheme "$HERE/../hyprtheme/hyprtheme"  # the picker; omarchy-theme-menu opens it
# hypr-ui widgets (and hyprspot) follow the theme through this hook
[[ -x $HERE/../hypr-ui/theme.py ]] && ln -sfn "$HERE/../hypr-ui/theme.py" ~/.config/omarchy/hooks/theme-set.d/hypr-ui
echo "commands: omarchy-theme {set,list,current,next,prev,refresh,bg,bg-next,undo}, hyprtheme, omarchy-theme-menu"
[[ -e ~/.config/omarchy/current/theme ]] || echo "now pick one: omarchy-theme set tokyo-night"
