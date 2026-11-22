"""hyprmenu's right column: the Omarchy menu (what Super+Alt+Space opened), as a tree.

Upstream Omarchy's tree (Apps, Learn, Trigger, Share, Style, Setup, Install, Remove, Update, About,
System), pointed at what this Ubuntu setup has: the hypr-ui widgets where one exists, apt and snap
instead of pacman. A leaf whose command isn't installed is hidden, so the menu never offers
something that does nothing.

Override or extend it in ~/.config/hyprmenu/menu.json, a list of
    {"path": "Setup/Printers", "run": "system-config-printer", "icon": "printer-symbolic"}
(a "path" that already exists replaces that entry; "hide": true removes it).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
USER_MENU = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "hyprmenu/menu.json"
TERM = os.environ.get("HYPRMENU_TERMINAL", "alacritty")
PKG = str(HERE / "bin/hyprmenu-pkg")


def term(cmd: str, cls: str = "hyprmenu-term") -> str:
    """Run `cmd` in a terminal that stays open until a key is pressed."""
    inner = f"{cmd}; echo; read -n1 -rsp 'press any key to close'"
    return f"{TERM} --class {cls} -e bash -c {shlex.quote(inner)}"


def web(url: str) -> str:
    return f"xdg-open {shlex.quote(url)}"


@dataclass
class Node:
    title: str
    icon: str = ""  # a symbolic icon name
    run: str | None = None  # shell command; None for a submenu
    special: str | None = None  # handled by the widget: "apps", "confirm"
    keys: str = ""  # the existing bind, shown as a hint
    widget: bool = False  # opens one of the hypr-ui widgets
    needs: str | None = None  # command that must exist (default: the first word of `run`)
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = field(default=None, repr=False)

    @property
    def path(self) -> list[str]:
        out, n = [], self
        while n and n.parent:
            out.append(n.title)
            n = n.parent
        return out[::-1]

    def available(self) -> bool:
        if self.children:
            return any(c.available() for c in self.children)
        if self.special and not self.run:
            return True
        need = self.needs or (shlex.split(self.run)[0] if self.run else None)
        if not need:
            return True
        return bool(shutil.which(os.path.expanduser(need)))

    def visible_children(self) -> list["Node"]:
        return [c for c in self.children if c.available()]


def N(title, icon="", run=None, **kw):
    kids = kw.pop("children", [])
    n = Node(title, icon, run, **kw)
    for k in kids:
        k.parent = n
        n.children.append(k)
    return n


def tree() -> Node:
    home = os.path.expanduser("~")
    editor = os.environ.get("HYPRMENU_EDITOR", "code")
    root = N("Omarchy", children=[
        N("Apps", "view-app-grid-symbolic", special="apps"),
        N("Learn", "help-browser-symbolic", children=[
            N("Keybindings", "input-keyboard-symbolic", "omarchy-menu-keybindings", keys="super K"),
            N("Omarchy manual", "accessories-dictionary-symbolic", web("https://learn.omacom.io/2/the-omarchy-manual")),
            N("Hyprland wiki", "accessories-dictionary-symbolic", web("https://wiki.hypr.land/")),
            N("Ubuntu help", "accessories-dictionary-symbolic", web("https://help.ubuntu.com/")),
            N("Neovim keys", "accessories-dictionary-symbolic", web("https://www.lazyvim.org/keymaps")),
            N("Bash cheatsheet", "utilities-terminal-symbolic", web("https://devhints.io/bash")),
        ]),
        N("Trigger", "media-playback-start-symbolic", children=[
            N("Capture", "camera-photo-symbolic", children=[
                N("Screenshot", "camera-photo-symbolic", "omarchy-cmd-screenshot", keys="print"),
                N("Screenshot window", "focus-windows-symbolic", "omarchy-cmd-screenshot window", keys="⇧ print"),
                N("Screenshot display", "video-display-symbolic", "omarchy-cmd-screenshot output", keys="^ print"),
                N("Flameshot", "applets-screenshooter-symbolic", f"{home}/.local/bin/flameshot-gui"),
                N("Screen record", "media-record-symbolic", "omarchy-menu screenrecord", keys="alt print"),
                N("Color picker", "color-select-symbolic", "hyprpicker -a", keys="super print"),
            ]),
            N("Toggle", "emblem-synchronizing-symbolic", children=[
                N("Top bar", "view-reveal-symbolic", "pkill -SIGUSR1 waybar", keys="super ⇧ space"),
                N("Idle lock", "system-lock-screen-symbolic", "omarchy-toggle-idle", keys="super ^ I"),
                N("Notification silencing", "notifications-disabled-symbolic",
                  "omarchy-toggle-notification-silencing", keys="super ^ ,"),
                N("Nightlight", "night-light-symbolic", "omarchy-toggle-nightlight"),
                N("Workspace gaps", "view-grid-symbolic", "omarchy-hyprland-workspace-toggle-gaps",
                  keys="super ⇧ ⌫"),
                N("Window transparency", "view-dual-symbolic", "hyprctl dispatch setprop active opaque toggle",
                  keys="super ⌫"),
                N("Touchpad", "input-touchpad-symbolic", "omarchy-toggle-touchpad", keys="super F9"),
            ]),
            N("Clipboard", "edit-paste-symbolic", "omarchy-menu clipboard", keys="super ^ V"),
            N("Emoji", "face-smile-symbolic", "omarchy-menu emoji", keys="super ^ E"),
        ]),
        N("Share", "emblem-shared-symbolic", children=[
            N("Clipboard", "edit-paste-symbolic", "omarchy-cmd-share clipboard"),
            N("File", "text-x-generic-symbolic", "omarchy-cmd-share file"),
            N("Folder", "folder-symbolic", "omarchy-cmd-share folder"),
        ]),
        N("Style", "applications-graphics-symbolic", children=[
            N("Theme", "preferences-desktop-appearance-symbolic", "hyprtheme", widget=True, keys="super ^ ⇧ space"),
            N("Next background", "preferences-desktop-wallpaper-symbolic", "omarchy-theme-bg-next",
              keys="super ^ space"),
            N("Hyprland look", "document-edit-symbolic", f"{editor} {home}/.config/hypr/looknfeel.conf",
              needs=editor),
        ]),
        N("Setup", "preferences-system-symbolic", children=[
            N("Audio", "audio-volume-high-symbolic", "sound-widget", widget=True, keys="super ^ A"),
            N("Wifi", "network-wireless-symbolic", "wifi-widget", widget=True, keys="super ^ W"),
            N("Bluetooth", "bluetooth-active-symbolic", "qs -c hyprbt ipc call bt toggle", widget=True,
              keys="super ^ B"),
            N("Power and display", "battery-good-symbolic", "hyprpower", widget=True, keys="super ^ P"),
            N("Monitors", "video-display-symbolic", "nwg-displays"),
            N("Keybindings file", "input-keyboard-symbolic", f"{editor} {home}/.config/hypr/bindings.conf",
              needs=editor),
            N("Hyprland config", "document-edit-symbolic", f"{editor} {home}/.config/hypr", needs=editor),
            N("Waybar config", "document-edit-symbolic", f"{editor} {home}/.config/waybar", needs=editor),
            N("Reload Hyprland", "view-refresh-symbolic", "hyprctl reload", keys="super ^ ⇧ R"),
            N("Settings", "preferences-system-symbolic", "env XDG_CURRENT_DESKTOP=GNOME gnome-control-center",
              needs="gnome-control-center"),
        ]),
        N("Install", "list-add-symbolic", children=[
            N("Package (apt)", "package-x-generic-symbolic", term(f"{PKG} install"), needs=TERM),
            N("Snap", "package-x-generic-symbolic", term(f"{PKG} snap"), needs="snap"),
            N("Flatpak", "package-x-generic-symbolic", term(f"{PKG} flatpak"), needs="flatpak"),
            N("Web app", "web-browser-symbolic", "omarchy-webapp-install"),
        ]),
        N("Remove", "list-remove-symbolic", children=[
            N("Package (apt)", "package-x-generic-symbolic", term(f"{PKG} remove"), needs=TERM),
            N("Snap", "package-x-generic-symbolic", term(f"{PKG} snap-remove"), needs="snap"),
            N("Web app", "web-browser-symbolic", "omarchy-webapp-remove"),
        ]),
        N("Update", "software-update-available-symbolic", children=[
            N("System packages", "software-update-available-symbolic", term(f"{PKG} update"), needs=TERM),
            N("Firmware", "application-x-firmware-symbolic", term("fwupdmgr refresh; fwupdmgr update"),
              needs="fwupdmgr"),
            N("Omarchy themes", "preferences-desktop-appearance-symbolic",
              term(f"{HERE.parent}/omarchy-theme/install.sh --update"), needs=TERM),
            N("hyprland-widgets", "folder-download-symbolic", term(f"git -C {HERE.parent} pull --ff-only"),
              needs=TERM),
        ]),
        N("About", "help-about-symbolic", term("fastfetch"), needs="fastfetch"),
        N("System", "system-shutdown-symbolic", children=[
            N("Lock", "system-lock-screen-symbolic", "omarchy-system-lock", keys="super ^ L"),
            N("Suspend", "weather-clear-night-symbolic", "systemctl suspend"),
            N("Log out", "system-log-out-symbolic", "hyprctl dispatch exit", special="confirm"),
            N("Restart", "system-reboot-symbolic", "systemctl reboot", special="confirm"),
            N("Shut down", "system-shutdown-symbolic", "systemctl poweroff", special="confirm"),
        ]),
    ])
    _apply_user(root)
    return root


def find(root: Node, path: list[str]) -> Node | None:
    n = root
    for p in path:
        n = next((c for c in n.children if c.title.lower() == p.lower()), None)
        if n is None:
            return None
    return n


def _apply_user(root: Node, path: Path = USER_MENU):
    try:
        items = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    for it in items if isinstance(items, list) else []:
        parts = [p for p in str(it.get("path", "")).split("/") if p]
        if not parts:
            continue
        parent = root
        for p in parts[:-1]:  # create submenus as needed
            nxt = find(parent, [p])
            if nxt is None:
                nxt = N(p, "folder-symbolic")
                nxt.parent = parent
                parent.children.append(nxt)
            parent = nxt
        old = find(parent, parts[-1:])
        if it.get("hide"):
            if old:
                parent.children.remove(old)
            continue
        node = old or N(parts[-1])
        node.run = it.get("run", node.run)
        node.icon = it.get("icon", node.icon or "application-x-executable-symbolic")
        node.keys = it.get("keys", node.keys)
        node.needs = it.get("needs", node.needs)
        if it.get("confirm"):
            node.special = "confirm"
        if not old:
            node.parent = parent
            parent.children.append(node)


def leaves(n: Node) -> list[Node]:
    out = []
    for c in n.visible_children():
        if c.children:
            out.extend(leaves(c))
        else:
            out.append(c)
    return out
