# omarchy-theme

[Omarchy](https://github.com/basecamp/omarchy)'s themes on a Hyprland desktop that isn't Omarchy
(here Ubuntu 26.04 + Hyprland 0.53 + waybar). One command switches the wallpaper, window borders,
waybar, notifications, launcher, lock screen, terminal, btop, Neovim, GTK light/dark and every widget
in this repo, hyprspot included. The running apps restyle live.

```bash
omarchy-theme list              # installed themes, * marks the current one
omarchy-theme set gruvbox       # switch
omarchy-theme next              # or step through them (prev too)
omarchy-theme bg-next           # next wallpaper of the current theme
omarchy-theme bg ~/pics/x.jpg   # any wallpaper for the current theme
omarchy-theme undo              # back to the theme and wallpaper before the last change
omarchy-theme-menu              # the hyprtheme picker (Super+Ctrl+Shift+Space), fuzzel without it
```

## Install

```bash
./install.sh            # fetches Omarchy's themes into ~/.local/share/omarchy/themes, links the commands
./install.sh --update   # re-fetch the themes later
omarchy-theme set tokyo-night
```

`install.sh` also links `hypr-ui/theme.py` as a theme-set hook, which is how the widgets follow along.

## How it works

A theme is an Omarchy theme folder: `colors.toml`, `backgrounds/`, and sometimes app files of its own
(`btop.theme`, `neovim.lua`, ...). Themes are read from `~/.config/omarchy/themes/` (yours, which win)
and `~/.local/share/omarchy/themes/` (Omarchy's).

`omarchy-theme set` does this:

1. Resolves the palette the way Omarchy's `omarchy-theme-color` does: legacy names, ANSI `color0`–`color15`
   aliases and derived shades. Any key an Omarchy template uses works here.
2. Renders every `*.tpl` in [`themed/`](themed), and in `~/.config/omarchy/themed/` (yours win), into
   `~/.config/omarchy/current/theme/`. A file the theme ships itself is used instead of the template.
   Templates take `{{ key }}`, `{{ key_strip }}` (no `#`), `{{ key_rgb }}` (`r,g,b`) and `{{ mix a b 30% }}`.
3. Writes the resolved palette to `colors.json` there and swaps the folder in atomically.
4. Sets the wallpaper: the one given with `--background`, else the one last used with this theme, else
   its first. Yours go in `~/.config/omarchy/backgrounds/<theme>/` and come first. The wallpaper per
   theme and the look before the change (for `undo`) are kept in `~/.local/state/omarchy-theme/state.json`.
5. Pokes the running apps: Hyprland borders through `hyprctl keyword`, then waybar, mako, Alacritty,
   btop and gsettings.
6. Runs the hooks in `~/.config/omarchy/hooks/theme-set.d/` with the theme name.

Omarchy 4 themes set their window border in `hyprland.lua`, which Hyprland 0.53 can't read. The border
colours are taken from that file instead, gradients included.

## Wiring an app

Each app is pointed at `~/.config/omarchy/current/theme/` once:

| app | file | change |
|---|---|---|
| Hyprland | `~/.config/hypr/hyprland.conf` | `source = ~/.config/omarchy/current/theme/hyprland.conf` after the look'n'feel defaults |
| waybar | `~/.config/waybar/style.css` | `@import url("file://$HOME/.config/omarchy/current/theme/waybar.css");`, then use `@foreground`, `@accent`, `@red`, ... |
| mako | `~/.config/mako/config` | first line `include=$HOME/.config/omarchy/current/theme/mako.ini`, no colour lines |
| fuzzel | `~/.config/fuzzel/fuzzel.ini` | `include=$HOME/.config/omarchy/current/theme/fuzzel.ini` under `[main]`, no `[colors]` |
| hyprlock | `~/.config/hypr/hyprlock.conf` | `source = ~/.config/omarchy/current/theme/hyprlock.conf`, then `$lock_background`, `$lock_outer`, ... |
| Alacritty | `~/.config/alacritty/alacritty.toml` | `import = ["~/.config/omarchy/current/theme/alacritty.toml"]` |
| btop | `~/.config/btop/btop.conf` | `ln -s ~/.config/omarchy/current/theme/btop.theme ~/.config/btop/themes/current.theme`, `color_theme = "current"` |
| Neovim (LazyVim) | `~/.config/nvim/lua/plugins/theme.lua` | symlink to `~/.config/omarchy/current/theme/neovim.lua` |

Replace `$HOME` with your real home path: these config files don't expand variables.

The `alacritty.toml`, `btop.theme` and `neovim.lua` templates are Omarchy's own (MIT).
