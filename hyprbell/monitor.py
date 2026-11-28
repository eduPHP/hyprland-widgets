"""Watch the session bus for notifications without taking them over: mako keeps showing the popups,
hyprbell only listens (org.freedesktop.DBus.Monitoring.BecomeMonitor, no root needed).

Callbacks run on the GLib main loop:
  on_notify(n: dict)   a Notify call and, once mako answered, the id it got (0 if no answer)
  on_closed(id: int)   the popup with that id went away (expired, dismissed or invoked)
"""

from __future__ import annotations

import sys
import time

from gi.repository import Gio, GLib

IFACE = "org.freedesktop.Notifications"
RULES = [
    f"type='method_call',interface='{IFACE}',member='Notify'",
    f"type='method_return',sender='{IFACE}'",  # carries the id mako gave the notification
    f"type='signal',interface='{IFACE}',member='NotificationClosed'",
]


def _log(msg: str) -> None:
    print(f"hyprbell: {msg}", file=sys.stderr, flush=True)


class Monitor:
    def __init__(self, on_notify, on_closed):
        self.on_notify = on_notify
        self.on_closed = on_closed
        self.conn: Gio.DBusConnection | None = None
        self.pending: dict[tuple[str, int], dict] = {}

    # ---------- connection ----------

    def start(self) -> bool:
        try:
            addr = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
            flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
            conn = Gio.DBusConnection.new_for_address_sync(addr, flags, None, None)
            conn.set_exit_on_close(False)
            conn.add_filter(self._filter)
            conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.Monitoring",
                           "BecomeMonitor", GLib.Variant("(asu)", (RULES, 0)), None, Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error as e:
            _log(f"cannot watch the session bus ({e.message}); retrying in 5 s")
            GLib.timeout_add_seconds(5, self.start)
            return False
        conn.connect("closed", self._closed)
        self.conn = conn
        return False

    def _closed(self, *_):
        _log("session bus monitor closed; reconnecting")
        self.conn = None
        GLib.timeout_add_seconds(2, self.start)

    # ---------- messages (the filter runs on GDBus's worker thread) ----------

    def _filter(self, _conn, msg: Gio.DBusMessage, incoming: bool):
        if not incoming or msg.get_sender() == "org.freedesktop.DBus":
            return msg  # our own BecomeMonitor call and the bus's answer to it
        try:
            kind = msg.get_message_type()
            if kind == Gio.DBusMessageType.METHOD_CALL and msg.get_member() == "Notify":
                n = self._parse_notify(msg)
                if n:
                    GLib.idle_add(self._call, (msg.get_sender(), msg.get_serial()), n)
            elif kind == Gio.DBusMessageType.METHOD_RETURN:
                body = msg.get_body()
                if body is not None and body.get_type_string() == "(u)":
                    GLib.idle_add(self._reply, (msg.get_destination(), msg.get_reply_serial()), body.unpack()[0])
            elif kind == Gio.DBusMessageType.SIGNAL and msg.get_member() == "NotificationClosed":
                body = msg.get_body()
                if body is not None:
                    GLib.idle_add(self._closed_sig, body.unpack()[0])
        except Exception as e:  # never let a bad message kill the monitor
            _log(f"skipped a message: {e}")
        return None  # a monitor only listens; GDBus must not try to answer

    @staticmethod
    def _parse_notify(msg: Gio.DBusMessage) -> dict | None:
        body = msg.get_body()
        if body is None or body.get_type_string() != "(susssasa{sv}i)":
            return None
        hints = body.get_child_value(6)

        def hint(key):
            v = hints.lookup_value(key, None)
            return v.unpack() if v is not None else None

        if hint("transient"):
            return None  # the app asked for it not to be kept (volume and brightness OSDs)
        acts = body.get_child_value(5).unpack()
        return {
            "ts": round(time.time(), 3),
            "app": body.get_child_value(0).get_string(),
            "replaces": body.get_child_value(1).get_uint32(),
            "icon": body.get_child_value(2).get_string() or hint("image-path") or hint("image_path") or "",
            "summary": body.get_child_value(3).get_string(),
            "body": body.get_child_value(4).get_string(),
            "actions": [[acts[i], acts[i + 1]] for i in range(0, len(acts) - 1, 2)],
            "urgency": int(hint("urgency") or 1),
            "entry": hint("desktop-entry") or "",
        }

    # ---------- main loop side ----------

    def _call(self, key, n):
        self.pending[key] = n
        GLib.timeout_add(2000, self._unanswered, key)  # no daemon answered: keep it anyway
        return False

    def _reply(self, key, nid):
        n = self.pending.pop(key, None)
        if n is not None:
            n["id"] = nid
            self.on_notify(n)
        return False

    def _unanswered(self, key):
        n = self.pending.pop(key, None)
        if n is not None:
            n["id"] = 0
            self.on_notify(n)
        return False

    def _closed_sig(self, nid):
        self.on_closed(nid)
        return False
