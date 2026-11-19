"""Aplicação: instância única, ações, ligação Soloist ↔ estado ↔ UI ↔ Web API."""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Soup", "3.0")
from gi.repository import Gio, GLib, Gtk, Soup  # noqa: E402

from hyprspot import __version__, update  # noqa: E402
from hyprspot.auth import TokenStore  # noqa: E402
from hyprspot.cache import ArtCache, load_playlists, save_playlists  # noqa: E402
from hyprspot.cli import ACTIONS, parse_args  # noqa: E402
from hyprspot.config import CACHE_DIR, CONFIG_PATH, TOKEN_PATH, load_config, save_margin  # noqa: E402
from hyprspot.focus import FocusWatcher  # noqa: E402
from hyprspot.soloist import SoloistClient, repeat_commands  # noqa: E402
from hyprspot.soloistctl import days_left, installed_version  # noqa: E402
from hyprspot.state import AppState, Throttle, apply_event, clock_ms, optimistic, should_refresh_playlists  # noqa: E402
from hyprspot.ui.common import load_css  # noqa: E402
from hyprspot.ui.compact import CompactView, compact_key_action  # noqa: E402
from hyprspot.ui.cover import ArtLoader  # noqa: E402
from hyprspot.ui.expanded import ExpandedView  # noqa: E402
from hyprspot.ui.window import HyprspotWindow  # noqa: E402
from dataclasses import replace  # noqa: E402

from hyprspot.webapi import WebApi, fill_track, liked_playlist, soup_transport  # noqa: E402

APP_ID = "dev.hyprspot.App"
ERROR_CLEAR_MS = 2500
VOLUME_FLUSH_MS = 110
SOLOIST_CHECK_S = 6 * 3600


class HyprspotApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.win: HyprspotWindow | None = None
        self._error_id = 0
        self._collapse_id = 0
        self._vol_id = 0
        self._last_track_uri: str | None = None
        self._search_seq = 0
        self._playlists_ok: float | None = None
        self._playlists_loading = False
        self._meta: dict = {}
        self._meta_pending: set[str] = set()
        self._hide_hint_shown = False
        for name in (*ACTIONS.values(), "expand", "expand-search", "click-anywhere", "hide-toggle"):  # inclui "quit"
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda _a, _p, n=name: self.run_action(n))
            self.add_action(act)
        open_url = Gio.SimpleAction.new("open-url", GLib.VariantType.new("s"))
        open_url.connect("activate", lambda _a, url: Gio.AppInfo.launch_default_for_uri(url.get_string(), None))
        self.add_action(open_url)

    # ------------------------------------------------------------------ ciclo de vida
    def do_command_line(self, cmdline: Gio.ApplicationCommandLine) -> int:
        cmd, opts = parse_args(cmdline.get_arguments()[1:])
        if cmd == "quit" and self.win is None:
            return 0  # nada rodando: não abre só para fechar
        if self.win is None:
            self._build()
        if cmd == "expand":
            self.run_action("expand-search" if opts.get("search") else "expand")
        elif cmd in ACTIONS:
            self.run_action(ACTIONS[cmd])
        return 0

    def _build(self) -> None:
        self.hold()
        load_css()
        self.config = load_config()
        self.state = AppState()
        self.client = SoloistClient(self.config.soloist_ws)
        self.session = Soup.Session()
        self.api = WebApi(TokenStore(TOKEN_PATH), self.session)
        self.art = ArtLoader(ArtCache(CACHE_DIR / "art"), self.session)
        self.focus = FocusWatcher()
        self.volume = Throttle(100)

        self.compact = CompactView(self.state, self.on_command, lambda: self.run_action("expand"), self.art)
        self.expanded = ExpandedView(self.state, self.on_command, self.on_search,
                                     lambda: self.run_action("collapse"), self.on_like, self.art,
                                     on_quit=lambda: self.run_action("quit"))
        self.win = HyprspotWindow(self, self.config, self.compact, self.expanded,
                                  lambda m: save_margin(CONFIG_PATH, m), self._on_compact_key,
                                  on_push_offscreen=self._hide_hint)
        self.win.connect("notify::is-active", self._on_active_changed)
        self.focus.connect("focus-lost", lambda *_: self._schedule_collapse())

        self.client.connect("event", self._on_event)
        self.client.connect("connection-changed", self._on_connection)
        self.win.present()
        self.client.start()

        self.state.set_playlists(load_playlists(CACHE_DIR / "playlists.json"))
        self.refresh_playlists()
        self._check_soloist()
        GLib.timeout_add_seconds(SOLOIST_CHECK_S, lambda: (self._check_soloist(), True)[1])
        update.check(soup_transport(self.session), __version__, self._notify_update)

    # ------------------------------------------------------------------ ações
    def run_action(self, name: str) -> None:
        if self.win is None:
            return
        if name == "toggle":
            if not self.win.get_visible():
                self.win.set_visible(True)
            self.run_action("collapse" if self.state.ui_mode == "expanded" else "expand")
        elif name in ("expand", "expand-search"):
            self.win.set_visible(True)
            self._cancel_collapse()
            if name == "expand-search":
                self.state.set_ui(tab="search")
            self.state.set_ui(mode="expanded")
            self.win.set_mode("expanded")
            self.focus.arm()
            if should_refresh_playlists(self._playlists_ok, time.monotonic(), bool(self.state.playlists)):
                self.refresh_playlists()
            if self.state.tab == "search":
                GLib.idle_add(lambda: (self.expanded.focus_search(), False)[1])
        elif name == "collapse":
            self._cancel_collapse()
            self.focus.disarm()
            self.state.set_ui(mode="compact")
            self.win.set_mode("compact")
        elif name == "hide":
            self.run_action("collapse")
            self.win.set_visible(False)
        elif name == "show":
            self.win.set_visible(True)
        elif name == "hide-toggle":
            self.run_action("show" if not self.win.get_visible() else "hide")
        elif name == "play-pause":
            st = self.state.playback.status
            self.on_command("pause" if st in ("playing", "buffering") else "play", {})
        elif name == "next":
            self.on_command("skip_next", {})
        elif name == "prev":
            self.on_command("skip_prev", {})
        elif name == "quit":
            self._cancel_collapse()
            self.focus.disarm()
            self.client.stop()
            self.win.destroy()
            self.release()
            self.quit()
        elif name == "click-anywhere":
            self.focus.on_click_anywhere()

    def _on_compact_key(self, keyval: int) -> bool:
        action = compact_key_action(keyval)
        if action:
            self.run_action(action)
            return True
        return False

    def _on_active_changed(self, win, _p) -> None:
        if self.state.ui_mode == "expanded" and not win.is_active():
            self._schedule_collapse()
        elif win.is_active():
            self._cancel_collapse()

    def _schedule_collapse(self) -> None:
        if self._collapse_id or self.state.ui_mode != "expanded":
            return

        def go():
            self._collapse_id = 0
            self.run_action("collapse")
            return False

        self._collapse_id = GLib.timeout_add(self.config.collapse_delay_ms, go)

    def _cancel_collapse(self) -> None:
        if self._collapse_id:
            GLib.source_remove(self._collapse_id)
            self._collapse_id = 0

    # ------------------------------------------------------------------ Soloist
    def _on_connection(self, _c, ok: bool) -> None:
        self.state.set_playback(apply_event(self.state.playback, {"type": "_connected" if ok else "_disconnected"}))

    def _on_event(self, _c, ev: dict) -> None:
        if ev.get("type") == "error":
            self._flash_error(ev.get("message") or "Erro no Soloist")
            return
        pb = apply_event(self.state.playback, ev)
        self.state.set_playback(replace(pb, track=fill_track(pb.track, self._meta)))
        self._fetch_meta_if_missing()
        uri = self.state.playback.track.uri if self.state.playback.track else None
        if uri != self._last_track_uri:
            self._last_track_uri = uri
            self.state.set_liked(None)
            if uri and uri.startswith("spotify:track:"):
                self.api.is_liked(uri, lambda v, u=uri: self._last_track_uri == u and self.state.set_liked(v))

    def _fetch_meta_if_missing(self) -> None:
        t = self.state.playback.track
        if not t or t.title or not t.uri.startswith("spotify:track:") or t.uri in self._meta_pending:
            return
        self._meta_pending.add(t.uri)

        def got(full):
            self._meta_pending.discard(t.uri)
            if full is None:
                return
            self._meta[t.uri] = full
            pb = self.state.playback
            self.state.set_playback(replace(pb, track=fill_track(pb.track, self._meta)))

        self.api.track(t.uri, got)

    def on_command(self, name: str, fields: dict) -> None:
        pb = self.state.playback
        if not pb.connected:
            self._flash_error("Soloist desconectado")
            return
        if not pb.is_active:
            self.client.send("activate")
        if name == "set_repeat":
            for cmd, f in repeat_commands(fields["mode"]):
                self.client.send(cmd, **f)
            return
        if name == "set_volume":
            self.state.set_playback(optimistic(pb, name, **fields))
            v = self.volume.push(fields["volume"], clock_ms())
            if v is not None:
                self.client.send("set_volume", volume=v)
            if not self._vol_id:
                self._vol_id = GLib.timeout_add(VOLUME_FLUSH_MS, self._flush_volume)
            return
        self.state.set_playback(optimistic(pb, name, **fields))
        if not self.client.send(name, **fields):
            self.state.set_playback(pb)
            self._flash_error("Soloist desconectado")

    def _flush_volume(self) -> bool:
        self._vol_id = 0
        v = self.volume.flush(clock_ms())
        if v is not None:
            self.client.send("set_volume", volume=v)
        return False

    def _flash_error(self, msg: str) -> None:
        self.state.set_error(msg)
        if self._error_id:
            GLib.source_remove(self._error_id)

        def clear():
            self._error_id = 0
            self.state.set_error(None)
            return False

        self._error_id = GLib.timeout_add(ERROR_CLEAR_MS, clear)

    # ------------------------------------------------------------------ Web API
    def refresh_playlists(self) -> None:
        if self._playlists_loading:
            return
        self._playlists_loading = True

        def got(items, err):
            if err:
                self._playlists_loading = False
                self.state.set_playlists_error(err)
                return
            save_playlists(CACHE_DIR / "playlists.json", items)

            def publish(liked):
                self._playlists_loading = False
                self._playlists_ok = time.monotonic()
                self.state.set_playlists_error(None)
                self.state.set_playlists(([liked] if liked else []) + items)

            def with_count(n):
                if not n:
                    publish(None)
                    return
                self.api.user_id(lambda uid: publish(liked_playlist(uid) if uid else None))

            self.api.liked_count(with_count)

        self.api.list_playlists(got)

    def on_search(self, q: str, offset: int) -> None:
        self._search_seq += 1
        seq = self._search_seq
        if not q:
            self.state.set_search("", [])
            return

        def got(items, err):
            if seq != self._search_seq:
                return
            if err:
                self._flash_error(err)
                self.state.set_search(q, self.state.search_results if offset else [])
                return
            base = self.state.search_results if offset else []
            self.state.set_search(q, base + items)

        self.api.search(q, offset, got)

    def on_like(self) -> None:
        pb = self.state.playback
        if not pb.track or self.state.liked is None:
            return
        uri, new = pb.track.uri, not self.state.liked
        self.state.set_liked(new)

        def done(ok):
            if not ok and self._last_track_uri == uri:
                self.state.set_liked(not new)
                self._flash_error("Não foi possível atualizar a curtida")

        self.api.set_liked(uri, new, done)

    # ------------------------------------------------------------------ Soloist build
    def _check_soloist(self) -> None:
        v = installed_version()
        self.state.set_soloist_days(days_left(v[1], date.today()) if v else None)

    # ------------------------------------------------------------------ dicas
    def _hide_hint(self) -> None:
        """Quem empurra o widget para fora da tela provavelmente quer escondê-lo (1x por sessão)."""
        if self._hide_hint_shown:
            return
        self._hide_hint_shown = True
        n = Gio.Notification.new("Quer esconder o hyprspot?")
        n.set_body("Super+Alt+H esconde o widget sem parar a música. O mesmo atalho mostra de novo.")
        self.send_notification("hide-hint", n)

    # ------------------------------------------------------------------ versão nova
    def _notify_update(self, version: str, url: str) -> None:
        title, body = update.notification_text(version, __version__, str(Path(__file__).resolve().parent.parent))
        n = Gio.Notification.new(title)
        n.set_body(body)
        action = Gio.Action.print_detailed_name("app.open-url", GLib.Variant("s", url))
        n.set_default_action(action)
        n.add_button("Ver novidades", action)
        self.send_notification("update", n)


def run(argv: list[str]) -> int:
    return HyprspotApp().run(argv)
