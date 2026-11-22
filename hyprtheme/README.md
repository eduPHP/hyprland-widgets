# hyprtheme

A theme picker for [`omarchy-theme`](../omarchy-theme) in the hypr-ui look. It replaces the fuzzel
menu on Super+Ctrl+Shift+Space.

- **Live preview.** Once the selection rests for 300 ms, the whole desktop switches to it (borders,
  waybar, mako, terminal, wallpaper, widgets). GTK's light/dark setting waits for Enter, because it
  makes every GTK window repaint. Enter keeps the theme; Esc (or toggling the picker away) puts back
  the theme and wallpaper you had when it opened. A spinner and the badge next to the name show
  when a theme is being applied.
- **Doesn't take over the screen.** A light veil sits over the desktop, clicks outside the card go
  through to your windows, and the picker only holds the keyboard while it has focus.
- **Tiles drawn from the palette:** a crop of the wallpaper, a mini bar and terminal in the theme's
  colours, and its six accents. A theme doesn't need a `preview.png`, so your own themes get real
  tiles too.
- **A grid with fuzzy search** and All, Dark, Light and Favorites filters.
- **Wallpapers on the same screen.** The column on the right lists the highlighted theme's
  wallpapers. The one you apply is remembered for that theme, so switching back to the theme brings
  it back.
- **Undo.** After a switch, a toast at the bottom offers to go back to the previous theme and
  wallpaper for 10 seconds. `hyprtheme undo` or `omarchy-theme undo` does the same at any time.

## Keys

| key | does |
|---|---|
| type | fuzzy filter (`tn` finds Tokyo Night) |
| ← ↑ ↓ → , PgUp/PgDn | move, previewing as you go |
| Tab / Shift+Tab | next / previous wallpaper of the highlighted theme |
| Enter, or double-click | apply the theme with that wallpaper |
| Ctrl+F, or click the star | favourite |
| Ctrl+R | a random theme from the current list |
| Ctrl+1 … Ctrl+4 | All, Dark, Light, Favorites |
| Esc | clear the filter, then close and revert to the theme you had |

## Install

`omarchy-theme/install.sh` links `hyprtheme` into `~/.local/bin`, and `omarchy-theme-menu` opens it
from then on. Start the daemon with Hyprland so the first open is instant, in `~/.config/hypr/autostart.conf`:

```ini
exec-once = hyprtheme --daemon
```

The existing bind keeps working (`omarchy-theme-menu` toggles hyprtheme), or bind it directly:

```ini
bind = SUPER CTRL SHIFT, SPACE, exec, hyprtheme
```

It needs the same packages as the other GTK widgets, plus `webp-pixbuf-loader` because many Omarchy
wallpapers are WebP. Without it those tiles show a gradient of the theme's colours instead.
`OMARCHY_THEME_FUZZEL=1 omarchy-theme-menu` still opens the old fuzzel menu.

## How it works

- `hyprtheme` is a GTK-free launcher that talks to the resident daemon over
  `$XDG_RUNTIME_DIR/hyprtheme.sock`: `toggle` (default), `open`, `close`, `undo`, `quit`.
- `themes.py` reads the installed themes through `omarchy_theme.py` and maps each palette with
  `hypr-ui/theme.py`, so the preview shows the colours the widgets will really get.
- `widget.py` is a full-screen layer-shell surface on the OVERLAY layer, keeping waybar's exclusive
  zone, with keyboard mode on-demand and an input region covering only the card. The card's
  stylesheet is the hypr-ui base sheet rendered with the highlighted theme's colours.
- A preview runs `omarchy-theme set <theme> --background <image> --preview` (no gsettings, nothing
  remembered, no undo step). Runs never overlap: the newest selection wins once the one in flight
  finishes. Enter runs the full `set`, with `--from` the look the picker opened on, so `undo` goes
  back past all the previews. Esc runs a `--preview` set back to that look.
- Every run and its output is logged to the daemon's stderr.
- Wallpaper thumbnails are cached in `~/.cache/hyprtheme/thumbs`.
- Favourites live in `~/.local/state/hyprtheme/state.json`. The remembered wallpapers and the undo
  step live in `~/.local/state/omarchy-theme/state.json`.
- It joins the one-popover rule (`hypr-ui-solo hyprtheme`, flag file `hyprtheme.open`).

`HYPRTHEME_WINDOWED=1 hyprtheme --daemon` runs it as a normal window, without layer shell, for
testing on X11 or under Xvfb.
