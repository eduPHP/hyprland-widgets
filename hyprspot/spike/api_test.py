"""Spike descartável: login PKCE + endpoints que o hyprspot precisa (só biblioteca padrão).

Uso:
  python3 api_test.py login <client_id>   # abre o navegador, salva o token
  python3 api_test.py read                # testa leitura: perfil, devices, player, playlists, busca
  python3 api_test.py play [device_name]  # toca a 1ª playlist e as Curtidas, mede a latência de pausar
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

REDIRECT = "http://127.0.0.1:8898/callback"
SCOPES = " ".join([
    "user-read-playback-state", "user-modify-playback-state", "user-read-currently-playing",
    "playlist-read-private", "playlist-read-collaborative", "user-library-read", "user-library-modify",
])
CONF_DIR = os.path.expanduser("~/.config/hyprspot")
TOKEN = os.path.join(CONF_DIR, "token.json")
API = "https://api.spotify.com/v1"


def save_token(tok):
    os.makedirs(CONF_DIR, exist_ok=True)
    tok["expires_at"] = time.time() + tok["expires_in"] - 30
    fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(tok, f)


def token_request(data):
    req = urllib.request.Request("https://accounts.spotify.com/api/token", data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def login(client_id):
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPES,
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state,
    })
    result = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<p>hyprspot: pode fechar esta aba.</p>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 8898), H)
    print("Abrindo o navegador para autorizar…\n" + url, flush=True)
    webbrowser.open(url)
    while "code" not in result and "error" not in result:
        srv.handle_request()
    if result.get("state") != state or "error" in result:
        sys.exit(f"Falhou: {result}")
    tok = token_request({"grant_type": "authorization_code", "code": result["code"], "redirect_uri": REDIRECT,
                         "client_id": client_id, "code_verifier": verifier})
    tok["client_id"] = client_id
    save_token(tok)
    print(f"Token salvo em {TOKEN}")


def access_token():
    with open(TOKEN) as f:
        tok = json.load(f)
    if time.time() > tok["expires_at"]:
        new = token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": tok["client_id"]})
        tok.update(new)
        save_token(tok)
    return tok["access_token"]


def call(method, path, body=None, quiet=False):
    t = time.perf_counter()
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {access_token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    ms = (time.perf_counter() - t) * 1000
    if not quiet:
        print(f"  {method:6} {path[:60]:60} -> {status} ({ms:.0f} ms)")
    try:
        return status, json.loads(raw) if raw else None, ms
    except json.JSONDecodeError:
        return status, raw.decode(errors="replace"), ms


def read():
    _, me, _ = call("GET", "/me")
    print(f"    usuário: {me.get('display_name') if isinstance(me, dict) else me}")
    _, dev, _ = call("GET", "/me/player/devices")
    for d in (dev or {}).get("devices", []):
        print(f"    device: {d['name']} ({d['type']}) ativo={d['is_active']}")
    st, pl, _ = call("GET", "/me/player")
    print(f"    player: {'nada tocando' if st == 204 else (pl.get('item') or {}).get('name')}")
    _, pls, _ = call("GET", "/me/playlists?limit=50")
    items = (pls or {}).get("items", [])
    print(f"    playlists: {len(items)} → {', '.join(p['name'] for p in items[:5])}…")
    _, sr, _ = call("GET", "/search?" + urllib.parse.urlencode({"q": "bossa nova", "type": "track", "limit": 10}))
    tracks = (sr or {}).get("tracks", {}).get("items", [])
    print(f"    busca: {len(tracks)} faixas → {', '.join(t['name'] for t in tracks[:3])}…")


def play(device_name=None):
    _, dev, _ = call("GET", "/me/player/devices")
    devices = (dev or {}).get("devices", [])
    if not devices:
        sys.exit("Nenhum device disponível. Abra o Spotify em algum lugar ou rode o spotifyd.")
    d = next((x for x in devices if device_name and x["name"] == device_name), devices[0])
    print(f"    usando device: {d['name']}")
    _, pls, _ = call("GET", "/me/playlists?limit=1")
    first = pls["items"][0]
    call("PUT", f"/me/player/play?device_id={d['id']}", {"context_uri": first["uri"]})
    time.sleep(4)
    _, now, _ = call("GET", "/me/player")
    print(f"    tocando: {(now or {}).get('item', {}).get('name')} de '{first['name']}'")
    lat = [call("PUT", "/me/player/pause", quiet=True)[2], 0]
    time.sleep(1)
    lat[1] = call("PUT", "/me/player/play", quiet=True)[2]
    print(f"    latência pausar/retomar via API: {lat[0]:.0f} ms / {lat[1]:.0f} ms")
    for path, body in [("/me/player/next", None), ("/me/player/volume?volume_percent=50", None),
                       ("/me/player/shuffle?state=false", None), ("/me/player/seek?position_ms=30000", None)]:
        call("POST" if "next" in path else "PUT", path, body)
        time.sleep(0.6)
    _, me, _ = call("GET", "/me", quiet=True)
    call("PUT", f"/me/player/play?device_id={d['id']}", {"context_uri": f"spotify:user:{me['id']}:collection"})
    time.sleep(3)
    _, now, _ = call("GET", "/me/player")
    print(f"    Curtidas via context_uri: {(now or {}).get('context', {}) and now['context'].get('uri')}")
    call("PUT", "/me/player/pause")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "read"
    if cmd == "login":
        login(sys.argv[2])
    elif cmd == "read":
        read()
    elif cmd == "play":
        play(sys.argv[2] if len(sys.argv) > 2 else None)
