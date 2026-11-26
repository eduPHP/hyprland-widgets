#!/usr/bin/env bash
# Install hyprlogin as the SDDM theme. Needs root:
#
#   sudo ./install.sh                  install and make it the active theme
#   sudo ./install.sh --keymap br      also give the greeter (weston) your keyboard layout
#   sudo ./install.sh --no-activate    install only; switch with /etc/sddm.conf.d/20-hyprlogin.conf later
#   sudo ./install.sh --uninstall      remove it; SDDM goes back to the theme set in the other conf files
#   ./install.sh --dry-run             print what it would do
#
# What it touches:
#   /usr/share/sddm/themes/hyprlogin/      the theme, plus JetBrainsMono Nerd Font copied from your fonts
#   /var/lib/hyprlogin/                    owned by you: colours + wallpaper that `hyprlogin sync` writes
#                                          (theme.conf.user in the theme folder points here)
#   /var/lib/AccountsService/icons/<you>   your ~/.face as the login avatar, only if there is none yet
#   /etc/sddm.conf.d/20-hyprlogin.conf     [Theme] Current=hyprlogin (and the weston keymap, with --keymap)
#   ~/.config/omarchy/hooks/theme-set.d/hyprlogin, ~/.local/bin/hyprlogin   links, as you
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
DESTDIR=${DESTDIR:-}                  # for testing: install under another root
THEME_DIR=$DESTDIR/usr/share/sddm/themes/hyprlogin
STATE_DIR=$DESTDIR/var/lib/hyprlogin
CONF=$DESTDIR/etc/sddm.conf.d/20-hyprlogin.conf
WESTON_INI=$DESTDIR/etc/sddm/hyprlogin-weston.ini
ICONS=$DESTDIR/var/lib/AccountsService/icons
FONTS=(JetBrainsMonoNerdFont-Regular.ttf JetBrainsMonoNerdFont-Bold.ttf JetBrainsMonoNerdFont-Light.ttf)

DRY=0 ACTIVATE=1 UNINSTALL=0 KEYMAP=""
while (($#)); do
  case $1 in
    --dry-run) DRY=1 ;;
    --no-activate) ACTIVATE=0 ;;
    --uninstall) UNINSTALL=1 ;;
    --keymap) KEYMAP=${2:?--keymap needs a layout, e.g. br}; shift ;;
    --keymap=*) KEYMAP=${1#*=} ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

USER_NAME=${SUDO_USER:-$(id -un)}
USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
LINK_HOME=$DESTDIR$USER_HOME          # where the user links go (under DESTDIR when testing)

run() { echo "+ $*"; ((DRY)) || "$@"; }
as_user() { if [[ $(id -un) == "$USER_NAME" ]]; then run "$@"; else run sudo -u "$USER_NAME" -H "$@"; fi; }

if ((!DRY)) && [[ -z $DESTDIR && $EUID -ne 0 ]]; then
  echo "hyprlogin: run it with sudo (or --dry-run to see what it does)" >&2
  exit 1
fi
if [[ $USER_NAME == root ]]; then
  echo "hyprlogin: run it with sudo from your own account, so it knows whose theme and fonts to use" >&2
  exit 1
fi

if ((UNINSTALL)); then
  run rm -rf "$THEME_DIR" "$STATE_DIR"
  run rm -f "$CONF" "$WESTON_INI"
  as_user rm -f "$LINK_HOME/.config/omarchy/hooks/theme-set.d/hyprlogin" "$LINK_HOME/.local/bin/hyprlogin"
  echo "hyprlogin removed; SDDM uses the theme from the remaining /etc/sddm.conf.d files at the next login"
  exit 0
fi

if ! command -v sddm-greeter-qt6 >/dev/null && ! command -v sddm-greeter >/dev/null; then
  echo "hyprlogin: SDDM isn't installed (sudo apt install sddm)" >&2
  exit 1
fi

# 1. the theme
run install -d -m 755 "$THEME_DIR" "$THEME_DIR/fonts"
run install -m 644 "$HERE"/theme/*.qml "$HERE/theme/metadata.desktop" "$HERE/theme/theme.conf" "$THEME_DIR/"
[[ -f $HERE/theme/preview.png ]] && run install -m 644 "$HERE/theme/preview.png" "$THEME_DIR/"

# 2. the font: the greeter runs as the sddm user, which can't read ~/.local/share/fonts
for f in "${FONTS[@]}"; do
  src=$(find "$USER_HOME/.local/share/fonts" /usr/local/share/fonts /usr/share/fonts -name "$f" -print -quit 2>/dev/null || true)
  if [[ -n $src ]]; then run install -m 644 "$src" "$THEME_DIR/fonts/$f"
  else echo "  warning: $f not found; the greeter falls back to the system mono font and icons may be missing"; fi
done

# 3. colours + wallpaper, writable by you so a theme switch needs no sudo
run install -d -m 755 -o "$USER_NAME" -g "$(id -gn "$USER_NAME")" "$STATE_DIR"
run ln -sfn /var/lib/hyprlogin/theme.conf "$THEME_DIR/theme.conf.user"
as_user env HYPRLOGIN_DIR="$STATE_DIR" "$HERE/hyprlogin" sync

# 4. avatar
if [[ -f $USER_HOME/.face && ! -e $ICONS/$USER_NAME ]]; then
  run install -d -m 775 "$ICONS"
  run install -m 644 "$USER_HOME/.face" "$ICONS/$USER_NAME"
fi

# 5. follow theme changes, and put the command on PATH
as_user mkdir -p "$LINK_HOME/.config/omarchy/hooks/theme-set.d" "$LINK_HOME/.local/bin"
as_user ln -sfn "$HERE/hyprlogin" "$LINK_HOME/.config/omarchy/hooks/theme-set.d/hyprlogin"
as_user ln -sfn "$HERE/hyprlogin" "$LINK_HOME/.local/bin/hyprlogin"

# 6. make it the active theme (a conf.d file that sorts after 10-hyprland.conf, so it wins)
if ((ACTIVATE)) || [[ -n $KEYMAP ]]; then
  run install -d -m 755 "$(dirname "$CONF")"
  {
    echo "# written by hyprland-widgets/hyprlogin/install.sh; remove with install.sh --uninstall"
    if ((ACTIVATE)); then printf '[Theme]\nCurrent=hyprlogin\n'; fi
    if [[ -n $KEYMAP ]]; then
      printf '\n[Wayland]\nCompositorCommand=weston --shell=kiosk --config=/etc/sddm/hyprlogin-weston.ini\n'
    fi
  } > "${TMPDIR:-/tmp}/hyprlogin.conf.$$"
  run install -m 644 "${TMPDIR:-/tmp}/hyprlogin.conf.$$" "$CONF"
  rm -f "${TMPDIR:-/tmp}/hyprlogin.conf.$$"
fi
if [[ -n $KEYMAP ]]; then
  run install -d -m 755 "$(dirname "$WESTON_INI")"
  printf '[keyboard]\nkeymap_layout=%s\n' "$KEYMAP" > "${TMPDIR:-/tmp}/hyprlogin-weston.$$"
  run install -m 644 "${TMPDIR:-/tmp}/hyprlogin-weston.$$" "$WESTON_INI"
  rm -f "${TMPDIR:-/tmp}/hyprlogin-weston.$$"
fi

echo
echo "hyprlogin installed. It shows at the next login screen; to see it now without logging out:"
echo "  hyprlogin preview"
((ACTIVATE)) || echo "Not activated: add [Theme] Current=hyprlogin to $CONF when you want it."
