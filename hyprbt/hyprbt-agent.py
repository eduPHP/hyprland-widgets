#!/usr/bin/env python3
"""hyprbt-agent: BlueZ pairing agent that talks to the hyprbt widget over stdio.

The widget starts this as a child process, so it lives and dies with the shell.

stdout, one JSON object per line:
  {"type": "confirm", "device": "/org/bluez/hci0/dev_..", "passkey": "482913"}
      a passkey to compare with the device; answer on stdin
  {"type": "authorize", "device": "..."}        just-works pairing; answer on stdin
  {"type": "display", "device": "...", "passkey": "482913"}
      type this on the device (keyboards); no answer needed
  {"type": "done"}                              the request finished or was cancelled
stdin, one word per line: "accept" or "reject".
"""
import json
import sys

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

AGENT_PATH = "/org/hyprbt/agent"
CAPABILITY = "KeyboardDisplay"


class Rejected(dbus.DBusException):
    _dbus_error_name = "org.bluez.Error.Rejected"


class Canceled(dbus.DBusException):
    _dbus_error_name = "org.bluez.Error.Canceled"


def emit(**msg):
    print(json.dumps(msg), flush=True)


class Agent(dbus.service.Object):
    def __init__(self, bus, path):
        super().__init__(bus, path)
        self.pending = None  # (reply_ok, reply_err)

    def _ask(self, ok, err, **msg):
        self._drop()
        self.pending = (ok, err)
        emit(**msg)

    def _drop(self):
        if self.pending:
            _, err = self.pending
            self.pending = None
            err(Canceled("superseded"))

    def answer(self, word):
        if not self.pending:
            return
        ok, err = self.pending
        self.pending = None
        if word == "accept":
            ok()
        else:
            err(Rejected("rejected by user"))
        emit(type="done")

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Release(self):
        emit(type="done")

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        return

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        # Legacy (pre-2.1) devices: most accept 0000; keyboards want it typed.
        emit(type="display", device=str(device), passkey="0000")
        return "0000"

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        emit(type="display", device=str(device), passkey=str(pincode))

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        # The device shows a number we would have to type here: not supported.
        raise Rejected("passkey entry not supported")

    @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        emit(type="display", device=str(device), passkey="%06d" % passkey)

    @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="",
                         async_callbacks=("ok", "err"))
    def RequestConfirmation(self, device, passkey, ok, err):
        self._ask(ok, err, type="confirm", device=str(device), passkey="%06d" % passkey)

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="",
                         async_callbacks=("ok", "err"))
    def RequestAuthorization(self, device, ok, err):
        self._ask(ok, err, type="authorize", device=str(device))

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Cancel(self):
        self.pending = None
        emit(type="done")


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    agent = Agent(bus, AGENT_PATH)
    manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
    manager.RegisterAgent(AGENT_PATH, CAPABILITY)
    manager.RequestDefaultAgent(AGENT_PATH)

    loop = GLib.MainLoop()

    def on_stdin(fd, cond):
        if cond & (GLib.IO_HUP | GLib.IO_ERR):
            loop.quit()
            return False
        line = sys.stdin.readline()
        if not line:
            loop.quit()
            return False
        agent.answer(line.strip())
        return True

    GLib.io_add_watch(sys.stdin.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, on_stdin)
    try:
        loop.run()
    finally:
        try:
            manager.UnregisterAgent(AGENT_PATH)
        except dbus.DBusException:
            pass


if __name__ == "__main__":
    main()
