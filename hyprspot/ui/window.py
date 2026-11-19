"""Janela layer-shell (OVERLAY) que alterna entre compacto e expandido."""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

from hyprspot.config import Config  # noqa: E402

EDGES = {"top": LS.Edge.TOP, "bottom": LS.Edge.BOTTOM, "left": LS.Edge.LEFT, "right": LS.Edge.RIGHT}


class HyprspotWindow(Gtk.ApplicationWindow):
    def __init__(self, app, config: Config, compact: Gtk.Widget, expanded: Gtk.Widget | None,
                 on_margin_changed: Callable[[tuple[int, int]], None],
                 on_compact_key: Callable[[int], bool] | None = None):
        super().__init__(application=app, title="hyprspot")
        self.config = config
        self.compact = compact
        self.expanded = expanded
        self.mode = "compact"
        self._on_margin_changed = on_margin_changed
        self.add_css_class("hs-window")
        self.set_decorated(False)

        LS.init_for_window(self)
        LS.set_namespace(self, "hyprspot")
        LS.set_layer(self, LS.Layer.OVERLAY)
        LS.set_exclusive_zone(self, 0)
        vert, horiz = config.anchor.split("-")
        self._vert, self._horiz = EDGES[vert], EDGES[horiz]
        LS.set_anchor(self, self._vert, True)
        LS.set_anchor(self, self._horiz, True)
        self.margin = tuple(config.margin)
        self._apply_margin()
        LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.box.append(compact)
        if expanded is not None:
            self.box.append(expanded)
            expanded.set_visible(False)
        self.set_child(self.box)

        drag = Gtk.GestureDrag(button=2)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", lambda *_: self._on_margin_changed(self.margin))
        self.add_controller(drag)
        self._drag_start = self.margin

        # Com o mouse parado sobre o compacto, o widget pega o teclado (para "q" fechar);
        # ao sair, devolve. 250 ms de espera evitam roubar o foco quando o mouse só passa por cima.
        self._on_compact_key = on_compact_key
        self._hover_id = 0
        self._grabbed = False
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self._hover_enter())
        motion.connect("leave", lambda *_: self._hover_leave())
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # -- modos --
    def set_mode(self, mode: str) -> None:
        if mode == self.mode or (mode == "expanded" and self.expanded is None):
            return
        self.mode = mode
        exp = mode == "expanded"
        self.compact.set_visible(not exp)
        if self.expanded is not None:
            self.expanded.set_visible(exp)
        w = self.expanded if exp else self.compact
        self.set_default_size(*w.get_size_request())
        self._grabbed = False
        LS.set_keyboard_mode(self, LS.KeyboardMode.EXCLUSIVE if exp else LS.KeyboardMode.NONE)
        if exp:
            self.present()

    HOVER_DWELL_MS = 250

    def _hover_enter(self) -> None:
        if self.mode != "compact" or self._hover_id or self._grabbed:
            return

        def grab():
            self._hover_id = 0
            self._grab_keyboard()
            return False

        self._hover_id = GLib.timeout_add(self.HOVER_DWELL_MS, grab)

    def _grab_keyboard(self) -> None:
        if self.mode == "compact":
            LS.set_keyboard_mode(self, LS.KeyboardMode.EXCLUSIVE)
            self._grabbed = True

    def _on_motion(self, _ctrl, x: float, y: float) -> None:
        # Com o teclado exclusivo, o Hyprland continua mandando o movimento para o widget
        # mesmo com o cursor fora dele e não manda "leave". Sem isto, o widget prendia o
        # teclado e o mouse (e um "q" digitado em outra janela o fechava).
        if self.mode != "compact":
            return
        if 0 <= x < self.get_width() and 0 <= y < self.get_height():
            self._hover_enter()  # o "enter" pode chegar com o cursor ainda na borda, fora
        elif self._hover_id or self._grabbed:
            self._hover_leave()

    def _hover_leave(self) -> None:
        if self._hover_id:
            GLib.source_remove(self._hover_id)
            self._hover_id = 0
        if self.mode == "compact":
            LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)
            self._grabbed = False

    def _on_key(self, _c, keyval, _code, _state) -> bool:
        if self.mode == "compact" and self._on_compact_key is not None:
            return self._on_compact_key(keyval)
        return False

    def toggle_hidden(self) -> None:
        self.set_visible(not self.get_visible())

    # -- arrastar com o botão do meio --
    def _apply_margin(self) -> None:
        LS.set_margin(self, self._horiz, self.margin[0])
        LS.set_margin(self, self._vert, self.margin[1])

    def _drag_begin(self, *_a) -> None:
        self._drag_start = self.margin

    def _drag_update(self, _g, dx: float, dy: float) -> None:
        sx = -1 if self._horiz == LS.Edge.RIGHT else 1
        sy = -1 if self._vert == LS.Edge.BOTTOM else 1
        self.margin = (max(0, int(self._drag_start[0] + sx * dx)), max(0, int(self._drag_start[1] + sy * dy)))
        self._apply_margin()
