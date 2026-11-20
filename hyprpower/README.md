# hyprpower

Power + display popover for Hyprland, in the hypr-ui look (`~/code/hyprland-widgets/hypr-ui`).
GTK4 + gtk4-layer-shell, one resident Python daemon driven over a unix socket.

## What it does

**power tab**
- battery: charge %, charging / on battery, time to full or empty, charge rate in W, power source (battery / ac / usb-c pd)
- charge level over the last 3 h (upower's history)
- health (full vs design capacity), cycles, temperature, voltage
- using the battery (only while unplugged): the 3 apps with the most CPU time, as a share of all
  CPU work, plus the total draw in W, how many cores are busy and the screen brightness.
  An app is everything the session started under it, so all chrome processes (windows, web
  apps, helpers) are one "chrome"; scripts are named after the script. Per-app watts would
  need root (powertop), so CPU time stands in for it; the GPU and screen are not split per app.
  A pulsing skeleton shows for the first second while it measures; click the list to open htop
  (sorted by CPU) in alacritty.
- power profile: power-saver / balanced / performance (`power-profiles-daemon`)
- Dell charge mode: adaptive / standard / fast / custom, with start/stop limits for custom
  (needs `sudo ./install-charge-rule` once, see below)
- keep awake (stops / starts `hypridle` through `actions-bar`, same as the bar indicator)
- follow the charger: unplugged → power-saver, plugged in → balanced, with a toast
- low battery alert: starts / stops the `battery-alert` user service

**display tab**
- screen brightness (logind, no video group needed) and keyboard light
- night light via `hyprsunset` or `wlsunset` (4000 K), switched through `actions-bar` so the top-bar indicator follows
- layout: extend / mirror / laptop only / external only, placement left / right / above / below
- per screen: resolution + refresh, scale, rotate, identify (shows each screen's name on it)
- every layout change shows "keep this layout?" and reverts after 10 s unless you press keep.
  Keep writes extend layouts to `~/.config/hypr/monitors.conf` (first backup:
  `monitors.conf.bak.hyprpower`). Mirror and single-screen layouts stay for the session only,
  so an unplugged monitor can never leave you with no screen.

## Use

```
hyprpower                  toggle on the power tab
hyprpower display          toggle on the display tab
hyprpower close | quit
hyprpower profile [next|power-saver|balanced|performance]
```

Opened by a click on the waybar battery (right-click keeps the old `omarchy-menu system`)
and `SUPER CTRL P`. Keys: `tab` switch tab · `1 2 3` profile · `k` keep awake ·
`← →` brightness · `m` cycle layout · `enter` keep layout · `esc` close.

## Charge mode permission

The charge files in `/sys/class/power_supply/BAT0` are root-only. Once:

```
sudo ~/code/hyprland-widgets/hyprpower/install-charge-rule
```

adds a udev rule that makes them group-writable for `plugdev`. `--remove` undoes it.

## Files

| | |
|---|---|
| `hyprpower` | CLI + launcher (no GTK, fast) |
| `backend.py` | sysfs / upower / powerprofilesctl / logind / hyprctl |
| `widget.py` | the popover, toast, identify windows, charger watcher |
| `style.css` | only hyprpower's own rules; the rest is hypr-ui |
| `~/.config/hyprpower/state.json` | the "follow the charger" switch |
