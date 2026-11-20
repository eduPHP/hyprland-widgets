# hyprland-widgets

Every desktop widget built for this Hyprland (Omarchy-style, Ubuntu 26.04) setup, in one versioned repo.

| folder | what | stack | launched from |
|---|---|---|---|
| `hypr-ui/` | shared design system (tokens → GTK CSS/Python + QML) | Python build | `~/.config/hypr-ui`, `~/.claude/skills/hypr-ui` |
| `hyprbt/` | Bluetooth panel | Quickshell/QML + dbus agent | `~/.config/quickshell/hyprbt` |
| `hyprcal/` | calendar popover (Google iCal, Calendly) | GTK4 layer-shell | `~/.local/bin/hyprcal` |
| `hyprpower/` | power + display popover | GTK4 layer-shell | `~/.local/bin/hyprpower` |
| `sound-widget/` | audio popover | GTK4 layer-shell | `~/.local/bin/sound-widget` |
| `wifi-widget/` | wifi / VPN / DNS popover | GTK4 layer-shell | `~/.local/bin/wifi-widget` |
| `hyprspot/` | Spotify mini-player (own look, not hypr-ui) | GTK4 + Soloist | `~/.local/bin/hyprspot{,-ctl}` |

The `~/.local/bin` and `~/.config` entries are symlinks into this repo, so edits here are live
(restart the daemon to pick them up).

## Secrets

Nothing secret lives in this repo. API keys, OAuth tokens, iCal secret URLs and Calendly PATs stay in
`~/.config/<widget>/` (e.g. `~/.config/hyprspot/`, `~/.config/hyprcal/config.toml`).
`.gitignore` blocks the usual key/token/env/config file names as a second line of defence —
still run `git diff --cached` before committing.

`hyprspot/` was imported with the history of its public `main` branch (github.com/eduPHP/hyprspot).
