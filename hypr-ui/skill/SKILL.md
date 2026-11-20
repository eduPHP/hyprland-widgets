---
name: hypr-ui
description: >
  The hypr-ui design system for this Hyprland/Omarchy desktop (Tokyo Night,
  JetBrainsMono Nerd Font, ~/code/hyprland-widgets/hypr-ui). Use when building, restyling or
  fixing any desktop widget, popover, OSD/toast or waybar pop-up (GTK4 layer-shell
  or Quickshell), when the user says "hypr-ui", "our design system", "make it look
  like the other widgets", or names wifi-widget, sound-widget or hyprbt.
  Not for hyprspot, which is a separate project with its own look.
---

# hypr-ui

Every desktop widget gets its look from **one** place: `~/code/hyprland-widgets/hypr-ui`
(also at `~/.config/hypr-ui`). Read `~/code/hyprland-widgets/hypr-ui/README.md` for the details;
this page is the checklist.

## Where things are

| File | What |
|---|---|
| `tokens.json` | source of truth: colours, font, sizes, radii, spacing, shadow, popover position |
| `build.py` | renders the outputs below; run it after editing tokens or the CSS template |
| `gtk/hypr-ui.css.in` → `gtk/hypr-ui.css` | GTK base stylesheet (generated) |
| `gtk/hypr_ui.py` | GTK helper: `load_css()`, `popover()`, `toast()`, `solo()` |
| `qml/Tokens.qml` | Quickshell singleton (generated) |
| `bin/hypr-ui-solo` | closes the other popovers when one opens |

Widgets using it: `~/code/hyprland-widgets/wifi-widget`, `~/code/hyprland-widgets/sound-widget`, `~/code/hyprland-widgets/hyprcal`, `~/code/hyprland-widgets/hyprpower` (GTK4),
`~/code/hyprland-widgets/hyprbt` (Quickshell). The README has the full widget map; add new
widgets to it.

## Rules

1. **Never hard-code a colour, font or size in a widget.** Use the tokens
   (`@blue`, `@fg`, `@muted`… in GTK CSS; `Tokens.blue`, `Tokens.sizeBody`… in QML).
   To change the look, change `tokens.json` or the base sheet and run `./build.py`,
   so every widget changes together.
2. **Frame:** a 1px opaque `frame` border (#3f537d). Never a translucent border:
   the screen is at 1.2× scale and translucent 1px lines look uneven.
3. **Shadow:** only the token shadow (0 4px 12px, 38% black). The layer surface is
   `surface.margin` (16px) bigger than the card on every side, with negative layer
   margins of the same amount, and that margin is click-through.
4. **One popover at a time:** while open, keep `$XDG_RUNTIME_DIR/<name>.open`; as it
   opens, run `hypr-ui-solo <name>`; give it a close command and add a block for it
   in `bin/hypr-ui-solo`.
5. **Under screenshots:** set the layer with `hypr_ui.layer(window, Layer, Layer.Layer.OVERLAY)`
   (never `Layer.set_layer` for OVERLAY), so the surface steps down to TOP while
   Flameshot's overlay is up (`$XDG_RUNTIME_DIR/hypr-ui-capture`). Quickshell: copy the
   `capturing` FileView and layer binding from `hyprbt/shell.qml`.
6. **Look:** lowercase titles, JetBrainsMono Nerd Font everywhere, Nerd Font glyphs
   for icons, a footer with `esc close` style key hints.

## New GTK4 widget

```python
sys.path.insert(0, os.path.expanduser("~/.config/hypr-ui/gtk"))
import hypr_ui

hypr_ui.load_css(HERE / "style.css")     # in Application.do_startup; style.css = this widget's rules only
hypr_ui.layer(window, Layer, Layer.Layer.OVERLAY)  # steps down under Flameshot
hypr_ui.popover(window, Layer)           # or hypr_ui.toast(window, Layer, bottom=88)
root.add_css_class("popover")            # or "toast"
root.set_size_request(360 + 2 * hypr_ui.MARGIN, -1)
hypr_ui.solo("<name>")                   # in the "opening" branch of toggle()
```

Base classes: `.app-icon` `.title` `.subtitle`, `button.primary` `.ghost` `.icon` `.link`,
`switch`, `.card`, `.tabs` + `button.tab`, `.section` `.section-meta` `.row` `.row.selected`
`.badge`, `entry`, `.footer` `.key` `.hint`.

## New Quickshell widget

`ln -s ~/code/hyprland-widgets/hypr-ui/qml/Tokens.qml Tokens.qml` in the config folder, then copy
from `~/code/hyprland-widgets/hyprbt`: the PanelWindow in `shell.qml` (margins, `mask`, hidden
`shadowShape` + MultiEffect, `frame` Rectangle, `setOpen()` with the flag file and
`hypr-ui-solo`), plus `Btn.qml`, `Txt.qml` and `SectionHeader.qml` as needed.

## After a change

```bash
cd ~/code/hyprland-widgets/hypr-ui && ./build.py
sound-widget quit; setsid -f sound-widget --daemon
for p in $(pgrep -f "^/usr/bin/python3 $HOME/code/hyprland-widgets/wifi-widget/wifi-widget"); do kill $p; done
```

hyprbt reloads by itself. Don't use `pkill -f` with a pattern that also matches
your own shell command. Check the result with a `grim` screenshot of the popover.
