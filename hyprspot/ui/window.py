"""Janela layer-shell (OVERLAY) que alterna entre compacto e expandido."""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

from hyprspot.config import Config  # noqa: E402
from hyprspot.focus import hypr_cursor, hypr_usable_area  # noqa: E402

EDGES = {"top": LS.Edge.TOP, "bottom": LS.Edge.BOTTOM, "left": LS.Edge.LEFT, "right": LS.Edge.RIGHT}
DRAG_THRESHOLD = 8  # px; abaixo disso o botão esquerdo continua sendo um clique
PUSH_OFFSCREEN_PX = 48  # empurrar além da borda mais que isto conta como "quero tirar da tela"


def drag_margin(start: tuple[int, int], dx: float, dy: float, right: bool, bottom: bool) -> tuple[int, int]:
    """Margem nova depois de arrastar (dx, dy); a margem conta a partir da borda ancorada."""
    sx = -1 if right else 1
    sy = -1 if bottom else 1
    return max(0, int(start[0] + sx * dx)), max(0, int(start[1] + sy * dy))


def clamp_margin(margin: tuple[int, int], size: tuple[int, int], area: tuple[int, int]) -> tuple[int, int]:
    """Mantém o widget inteiro dentro da área útil do monitor."""
    return (max(0, min(margin[0], area[0] - size[0])), max(0, min(margin[1], area[1] - size[1])))


class HyprspotWindow(Gtk.ApplicationWindow):
    def __init__(self, app, config: Config, compact: Gtk.Widget, expanded: Gtk.Widget | None,
                 on_margin_changed: Callable[[tuple[int, int]], None],
                 on_compact_key: Callable[[int], bool] | None = None,
                 cursor_pos: Callable[[], tuple[float, float] | None] = hypr_cursor,
                 screen_area: Callable[[float, float], tuple[int, int] | None] = hypr_usable_area,
                 on_push_offscreen: Callable[[], None] | None = None):
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

        # Mover: arrastar com o botão do meio (sempre) ou com o esquerdo (só no compacto, para
        # o touchpad). A posição vem do cursor na tela (socket do Hyprland), lida a cada quadro:
        # o deslocamento do GTK é relativo à janela, que anda junto, e faria o widget tremer.
        # Sem o socket, o widget só muda de lugar ao soltar.
        self._cursor_pos = cursor_pos
        self._screen_area = screen_area
        self._drag_area: tuple[int, int] | None = None
        self._drag_overshoot = 0.0
        self._on_push_offscreen = on_push_offscreen
        self._drag_start = self.margin
        self._drag_cursor0: tuple[float, float] | None = None
        self._dragging = False
        self._tick_id = 0
        for button, left in ((2, False), (1, True)):
            drag = Gtk.GestureDrag(button=button)
            if left:  # antes dos botões, para cancelar o clique quando vira arrasto
                drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            drag.connect("drag-begin", lambda g, x, y, lf=left: self._drag_begin(g, x, y, left=lf))
            drag.connect("drag-update", lambda g, x, y, lf=left: self._drag_update(g, x, y, left=lf))
            drag.connect("drag-end", lambda g, x, y, lf=left: self._drag_end(g, x, y, left=lf))
            self.add_controller(drag)

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

    # -- arrastar para mover --
    def _apply_margin(self) -> None:
        LS.set_margin(self, self._horiz, self.margin[0])
        LS.set_margin(self, self._vert, self.margin[1])

    def _drag_begin(self, _g, _x: float, _y: float, left: bool) -> None:
        self._drag_start = self.margin
        self._dragging = False
        self._drag_cursor0 = self._cursor_pos()
        self._drag_area = self._screen_area(*self._drag_cursor0) if self._drag_cursor0 else None
        self._drag_overshoot = 0.0

    def _drag_update(self, g, dx: float, dy: float, left: bool) -> None:
        if self._dragging or (left and self.mode != "compact"):
            return
        if left and (dx * dx + dy * dy) < DRAG_THRESHOLD ** 2:
            return
        self._dragging = True
        g.set_state(Gtk.EventSequenceState.CLAIMED)  # o botão sob o cursor não recebe o clique
        self.add_css_class("hs-dragging")
        self.set_cursor_from_name("grabbing")
        if self._drag_cursor0 is not None:
            self._tick_id = self.add_tick_callback(lambda *_: (self._drag_tick(), True)[1])
            self._drag_tick()

    def _margin_at_cursor(self) -> tuple[int, int] | None:
        c0 = self._drag_cursor0
        c = self._cursor_pos() if c0 is not None else None
        if c is None:
            return None
        sx = -1 if self._horiz == LS.Edge.RIGHT else 1
        sy = -1 if self._vert == LS.Edge.BOTTOM else 1
        raw = (self._drag_start[0] + sx * (c[0] - c0[0]), self._drag_start[1] + sy * (c[1] - c0[1]))
        m = (max(0, int(raw[0])), max(0, int(raw[1])))
        if self._drag_area:  # fora da tela: para no ponto visível mais próximo
            m = clamp_margin(m, (self.get_width(), self.get_height()), self._drag_area)
            self._drag_overshoot = max(abs(raw[0] - m[0]), abs(raw[1] - m[1]))
        return m

    def _drag_tick(self) -> None:
        m = self._margin_at_cursor() if self._dragging else None
        if m is not None and m != self.margin:
            self.margin = m
            self._apply_margin()

    def _drag_end(self, _g, dx: float, dy: float, left: bool) -> None:
        if not self._dragging:
            return
        self._dragging = False
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        self.remove_css_class("hs-dragging")
        self.set_cursor(None)
        self.margin = self._margin_at_cursor() or drag_margin(
            self._drag_start, dx, dy, self._horiz == LS.Edge.RIGHT, self._vert == LS.Edge.BOTTOM)
        self._apply_margin()
        self._on_margin_changed(self.margin)
        if self._drag_overshoot > PUSH_OFFSCREEN_PX and self._on_push_offscreen:
            self._on_push_offscreen()
