# hyprmenu

A start menu in the hypr-ui look. **Super+Space** opens it with your most-used apps selected,
**Super+Alt+Space** opens the same card on the Omarchy menu. Pressing the bind again closes it.

- **Most used.** A grid of the 8 apps you use most, by time in focus. Pinned apps come first and
  never drop off (right-click a tile, or Ctrl+P). Below it, the last apps you used that aren't in
  the grid.
- **The Omarchy menu** on the right, with drill-in: Apps, Learn, Trigger, Share, Style, Setup,
  Install, Remove, Update, About, System. Entries that have a hypr-ui widget open the widget
  (Theme → hyprtheme, Audio → sound-widget, Wifi → wifi-widget, Bluetooth → hyprbt, Power and
  display → hyprpower). Install, Remove and Update use apt, snap and flatpak. An entry whose command
  isn't installed is hidden.
- **Search** over apps and menu entries together: typing "wifi" finds Setup › Wifi. When nothing
  matches, Enter runs what you typed as a shell command. "Apps" in the menu lists every app.
- **Power row:** Lock, Suspend, Restart, Shut down. Restart, Shut down and Log out ask for a second
  press.
- **Doesn't take over the screen.** A light veil, clicks outside the card reach your windows, and
  the menu closes as soon as another window takes the focus. Hovering a window doesn't count: with
  `follow_mouse = 1` the menu holds it at 2 while open (only a click moves the focus) and gives your
  value back when it closes, or when the daemon starts again after dying with the menu open.

## Keys

| key | does |
|---|---|
| type | search |
| ← ↑ ↓ → | move in the grid; → from the last column goes to the menu |
| Tab | switch between apps and menu |
| Enter | open |
| Alt+1 … Alt+8 | launch tile 1–8 |
| Ctrl+P | pin / unpin the selected tile |
| Esc | clear the search, then go back a level, then close |

## How "most used" is measured

The daemon listens to Hyprland's event socket. Each `activewindow` event ends the stretch of the
window that had focus and adds its seconds to that app. Scores decay with a 7-day half-life, so the
grid follows what you use now. A launch from the menu adds 5 minutes. A single stretch counts at
most 20 minutes, so a window left focused while you're away doesn't take over.

A window class is matched to a desktop entry by `StartupWMClass`, then by the desktop-file name and
the executable. Windows without an entry (Chrome web apps, for example) are counted under
`class:<name>` but don't show in the grid.

The first start seeds the scores from GNOME Shell's history
(`~/.local/share/gnome-shell/application_state`): the top app there starts at 3 hours, aged from
when GNOME last saw it, so it fades out over a few weeks.

`hyprmenu stats` prints the ranking. State lives in `~/.local/state/hyprmenu/usage.json`.

## Changing the menu

`~/.config/hyprmenu/menu.json` adds, replaces or hides entries:

```json
[
  {"path": "Setup/Printers", "run": "system-config-printer", "icon": "printer-symbolic"},
  {"path": "Install/Flatpak", "hide": true},
  {"path": "System/Reboot to firmware", "run": "systemctl reboot --firmware-setup", "confirm": true}
]
```

`HYPRMENU_TERMINAL` (default `alacritty`) and `HYPRMENU_EDITOR` (default `code`) pick the terminal
and editor its entries use.

## Install

```sh
hyprmenu/install.sh
```

links `hyprmenu` into `~/.local/bin`. Then in `~/.config/hypr/autostart.conf`:

```ini
exec-once = hyprmenu --daemon
```

and replace the walker and omarchy-menu binds:

```ini
bindd = SUPER, SPACE, Start menu, exec, hyprmenu
bindd = SUPER ALT, SPACE, Start menu (Omarchy menu), exec, hyprmenu menu
```

Keep the daemon running: it is what measures usage. Started from a bind, it starts itself.

## How it works

- `hyprmenu` is a GTK-free launcher that talks to the daemon over `$XDG_RUNTIME_DIR/hyprmenu.sock`:
  `toggle` (default), `menu`, `open`, `close`, `quit`.
- `widget.py` is a full-screen layer-shell surface on the OVERLAY layer, like hyprtheme: it keeps
  waybar's exclusive zone, is resizable so the compositor sizes it (a non-resizable one draws in the
  top-left corner), forces `GDK_BACKEND=wayland`, and takes input only on the card.
- Apps start without the daemon's layer-shell `LD_PRELOAD` and with the session's `GDK_BACKEND`.
- `usage.py` holds the scores and the Hyprland listener; `menu.py` the menu tree;
  `bin/hyprmenu-pkg` the apt/snap/flatpak entries.
- It joins the one-popover rule (`hypr-ui-solo hyprmenu`, flag file `hyprmenu.open`).

`HYPRMENU_WINDOWED=1 hyprmenu --daemon` runs it as a normal window, for testing on X11 or under Xvfb.
