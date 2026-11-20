"""Fluxo da tela de configuração com efeitos colaterais falsos (precisa de display Wayland)."""
import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("WAYLAND_DISPLAY"), reason="sem display")

CID = "3f9a2c7e51d84b06a8e2f41c97d05b6e"


class Fake:
    def __init__(self):
        self.key = None
        self.cid = ""
        self.token_for = None
        self.status = {"running": True, "logged_in": False, "active": False}
        self.opened, self.copied, self.launched = [], [], 0
        self.login_cb = None

    def open_uri(self, uri, parent): self.opened.append(uri)
    def copy(self, text): self.copied.append(text)
    def has_key(self): return self.key is not None
    def save_key(self, key): self.key = key
    def client_id(self): return self.cid
    def save_client_id(self, cid): self.cid = cid
    def has_token(self, cid): return bool(cid) and self.token_for == cid
    def login(self, cid, done): self.login_cb = (cid, done)
    def soloist_status(self, cb): cb(dict(self.status))
    def launch_hyprspot(self): self.launched += 1


@pytest.fixture
def win():
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.init()
    from hyprspot.ui.setup_window import SetupWindow

    fake = Fake()
    w = SetupWindow(None, fake)
    yield w, fake
    w._stop_poll()
    w.destroy()


def page(w):
    return w.stack.get_visible_child_name()


def test_full_flow(win):
    w, f = win
    assert page(w) == "welcome" and not w.back_btn.get_visible()
    w.go_next()
    assert page(w) == "key"

    w.go_next()                                   # sem chave: fica
    assert page(w) == "key" and "Cole a chave" in w.error.get_text()
    w.key_entry.set_text("abc def")               # inválida
    w.go_next()
    assert page(w) == "key" and f.key is None
    w.key_entry.set_text("  SOLOIST_API_KEY=k3y-OK ")
    w.go_next()
    assert f.key == "k3y-OK" and page(w) == "app" and w.key_entry.get_text() == ""

    w.cid_entry.set_text("segredo-errado")
    w.go_next()
    assert page(w) == "app" and "32 caracteres" in w.error.get_text()
    w.cid_entry.set_text(CID.upper())
    w.go_next()
    assert f.cid == CID and page(w) == "login"

    w.go_next()                                   # sem token: fica
    assert page(w) == "login" and "Entre com o Spotify" in w.error.get_text()
    w._login()
    assert f.login_cb[0] == CID and not w.login_btn.get_sensitive()
    f.token_for = CID
    f.login_cb[1](None)
    assert "Conectado" in w.login_status.get_text() and w.login_btn.get_sensitive()
    w.go_next()
    assert page(w) == "pair"

    assert "Aguardando" in w.pair_status.get_text()
    assert w.next_btn.get_label() == "Pular por agora"
    f.status["logged_in"] = True
    w._poll()
    assert "Pareado" in w.pair_status.get_text() and w.next_btn.get_label() == "Próximo"
    w.go_next()
    assert page(w) == "done" and w._poll_id == 0

    w.go_next()
    assert f.launched == 1


def test_login_error_is_shown(win):
    w, f = win
    f.cid = CID
    w._show(3)
    w._login()
    f.login_cb[1]("A porta 8898 está ocupada por outro programa.")
    assert "8898" in w.error.get_text() and w.login_btn.get_sensitive()
    assert page(w) == "login"


def test_existing_setup_lets_user_skip_through(win):
    w, f = win
    f.key, f.cid, f.token_for = "k", CID, CID
    w._show(1)
    assert "Já existe uma chave" in w.key_status.get_text()
    w.go_next()
    assert page(w) == "app" and w.cid_entry.get_text() == CID
    w.go_next()
    assert page(w) == "login" and "Conectado" in w.login_status.get_text()


def test_links_and_copy(win):
    w, f = win
    from hyprspot.auth import REDIRECT_URI
    w._show(2)
    w._copy_uri()
    assert f.copied == [REDIRECT_URI] and w.copy_btn.get_label() == "Copiado ✓"


def test_soloist_not_running_message(win):
    w, f = win
    f.status = {"running": False, "logged_in": False, "active": False}
    w._show(4)
    assert "não está rodando" in w.pair_status.get_text()
