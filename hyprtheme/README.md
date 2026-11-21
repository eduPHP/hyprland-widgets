# hyprtheme

A theme picker for [`omarchy-theme`](../omarchy-theme) in the hypr-ui look. It replaces the fuzzel
menu on Super+Ctrl+Shift+Space.

- **Preview without changing anything.** Moving through the grid repaints the picker in the
  highlighted theme and shows that theme's wallpaper behind it. The desktop stays as it is until
  Enter, and Esc leaves everything untouched.
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
| Esc | clear the filter, then close without changing anything (so does a click outside) |

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
- `widget.py` is a full-screen layer-shell surface on the OVERLAY layer. It keeps waybar's exclusive
  zone and takes the keyboard while open. The card's stylesheet is the hypr-ui base sheet rendered
  with the highlighted theme's colours.
- Applying runs `omarchy-theme set <theme> --background <image>`, or `omarchy-theme bg <image>` when
  only the wallpaper changed.
- Wallpaper thumbnails are cached in `~/.cache/hyprtheme/thumbs`. Backdrops are decoded at screen
  size off the main thread, and the last 8 are kept in memory.
- Favourites live in `~/.local/state/hyprtheme/state.json`. The remembered wallpapers and the undo
  step live in `~/.local/state/omarchy-theme/state.json`.
- It joins the one-popover rule (`hypr-ui-solo hyprtheme`, flag file `hyprtheme.open`).

`HYPRTHEME_WINDOWED=1 hyprtheme --daemon` runs it as a normal window, without layer shell, for
testing on X11 or under Xvfb.
