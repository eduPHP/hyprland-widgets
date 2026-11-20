#!/usr/bin/env bash
# Build Quickshell from source into ~/.local (Ubuntu has no package).
# Needs the apt packages listed in README.md. Re-run to upgrade: set QS_TAG.
set -euo pipefail

QS_TAG="${QS_TAG:-v0.3.2}"
SRC="${XDG_CACHE_HOME:-$HOME/.cache}/hyprbt-build/quickshell"

if [ ! -d "$SRC/.git" ]; then
  git clone --depth 1 --branch "$QS_TAG" https://git.outfoxxed.me/quickshell/quickshell "$SRC"
fi
cd "$SRC"

# Only what hyprbt needs (Wayland layer-shell, Hyprland, Bluetooth, IPC
# sockets); everything else is off to keep the dependency list short.
cmake -S . -B build -G Ninja \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DCMAKE_INSTALL_PREFIX="$HOME/.local" \
  -DDISTRIBUTOR="hyprbt (source build)" \
  -DCRASH_HANDLER=OFF \
  -DUSE_JEMALLOC=OFF \
  -DX11=OFF -DI3=OFF \
  -DSCREENCOPY=OFF \
  -DSERVICE_STATUS_NOTIFIER=OFF -DSERVICE_PIPEWIRE=OFF -DSERVICE_MPRIS=OFF \
  -DSERVICE_PAM=OFF -DSERVICE_POLKIT=OFF -DSERVICE_GREETD=OFF \
  -DSERVICE_UPOWER=OFF -DSERVICE_NOTIFICATIONS=OFF -DNETWORK=OFF \
  -DBLUETOOTH=ON -DHYPRLAND=ON -DWAYLAND=ON -DSOCKETS=ON

cmake --build build
cmake --install build
echo "installed: $(command -v qs || echo "$HOME/.local/bin/qs")"
