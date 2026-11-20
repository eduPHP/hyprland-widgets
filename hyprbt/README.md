# hyprbt

Bluetooth panel for Hyprland, built on [Quickshell](https://quickshell.org)'s
`Quickshell.Bluetooth` (BlueZ over D-Bus). Design canvas:
https://claude.ai/artifact/KabTznDYm7wY64BnGSYwsS

- power on/off, scan (stops after 30 s), visible/hidden
- Connected / Paired / Nearby lists; Connect, Disconnect, Pair, Cancel, Forget
- battery when the device reports it
- pairing prompts (passkey confirm, "type this code") shown in the panel by
  `hyprbt-agent.py`, a BlueZ agent the shell runs as a child process

## Install (Ubuntu)

```bash
sudo apt install cmake ninja-build pkg-config qt6-base-dev qt6-base-private-dev \
  qt6-declarative-dev qt6-declarative-private-dev qt6-wayland-dev qt6-wayland-private-dev \
  qt6-shadertools-dev libcli11-dev spirv-tools libwayland-dev libdrm-dev libgbm-dev \
  libegl-dev qml6-module-qtquick-effects
./build-quickshell.sh                      # builds Quickshell v0.3.2 into ~/.local
ln -sfn "$PWD" ~/.config/quickshell/hyprbt
```

## Wiring

- `~/.config/hypr/autostart.conf`: `exec-once = qs -c hyprbt -d`
- `~/.config/hypr/omarchy/bindings/utilities.conf`: SUPER CTRL B → `qs -c hyprbt ipc call bt toggle`
- waybar `bluetooth` module: left click toggles hyprbt, right click opens blueman-manager

IPC: `qs -c hyprbt ipc call bt toggle|open|close|power|status`

Edits are hot-reloaded by Quickshell; restart with `qs -c hyprbt kill; qs -c hyprbt -d`.
Look and colours come from the shared design system in `~/code/hyprland-widgets/hypr-ui` (`Tokens.qml` is a symlink to its generated singleton; `Theme.qml` maps hyprbt's names onto it).
