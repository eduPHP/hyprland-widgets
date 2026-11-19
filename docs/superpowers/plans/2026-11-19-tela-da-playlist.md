# Tela da playlist e busca geral — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clicar numa playlist toca e abre a tela dela, com a fila "A seguir" vinda do Soloist e escolha de música. A busca passa a mostrar playlists também.

**Architecture:** Três peças novas e testáveis.
- **Fila:** `QueueSync`, uma classe pura que pede e guarda a fila do Soloist sem cair em laço.
- **Tocar a partir de uma música:** `WebApi.play_at`, um `PUT /me/player/play` com `offset` no aparelho "hyprspot".
- **Tela da playlist:** `PlaylistPage`, um widget com cabeçalho e lista paginada.

O `ExpandedView` passa a ter, em cada aba, uma pilha com duas telas: a lista e a página da playlist. O `app.py` liga tudo.

**Tech Stack:** Python 3.11+, GTK 4 (PyGObject), libsoup3, pytest. Os testes de widget pulam quando não há `WAYLAND_DISPLAY`.

**Spec:** `docs/superpowers/specs/2026-11-19-tela-da-playlist-design.md`

## Global Constraints

- **Fonte da lista:** sempre a fila do Soloist, via `get_queue` com `limit: 0`, inclusive nas Curtidas. Nenhuma chamada a `/playlists/{id}/items` nem `/me/tracks` para listar músicas.
- **Fora do escopo:** API interna do player web, álbuns, artistas e podcasts.
- **Expandido:** `ExpandedView.WIDTH, HEIGHT = 360, 600`. O compacto não muda.
- **Paginação na tela:** blocos de **30** linhas, e o próximo entra quando faltam menos de 2 linhas (92 px) para o fim da rolagem.
- **Busca:** `type=track,playlist`, com no máximo **5 playlists**, só na primeira página. Enter e ↑/↓ atuam só nas músicas.
- **Textos da interface em pt-BR:**
  - "A seguir"
  - "Tocar"
  - "Aleatório"
  - "Esta playlist não está tocando"
  - "Playlists"
  - "Músicas"
- **Tocar a partir de uma música:** `PUT /me/player/play?device_id=<id>` com o corpo abaixo. O `id` é o do aparelho cujo `name` é igual a `Playback.device_name` em `GET /me/player/devices`.
  ```json
  {"context_uri": "...", "offset": {"uri": "..."}}
  ```
- **Playlist que já está tocando:** clicar nela só abre a tela. A comparação é com `Playback.context_uri`.
- **Mensagens de erro** passam pelo `_flash_error`, como hoje.

## Review Focus

1. **Laço da fila:** a resposta do `get_queue` também chega como `queue_changed`. Ela não pode disparar outro `get_queue`. Teste: `test_queue_sync_does_not_loop`.
2. **Fila de outro contexto:** a fila só vale para o contexto que está tocando. Se outra coisa começar a tocar com a tela aberta, a lista some e aparece "Esta playlist não está tocando". Teste: `test_page_shows_not_playing_for_other_context`.
3. **Entradas sem música:** episódios, anúncios e faixas locais sem URI `spotify:track:` não podem quebrar a lista. São ignorados. Teste: `test_parse_queue_skips_non_tracks`.
4. **Aparelho não encontrado:** `play_at` sem o aparelho "hyprspot" em `/me/player/devices` chama o callback com erro e não faz o `PUT`. Teste: `test_play_at_without_device_reports_error`.
5. **Esc em camadas:** Esc na tela da playlist volta para a lista; Esc na raiz recolhe. Teste: `test_escape_goes_back_before_collapsing`.

---

### Task 1: Tocar a partir de uma música (Web API) e validar nas Curtidas

**Files:**
- Modify: `hyprspot/webapi.py`
- Test: `tests/test_webapi_client.py`

**Interfaces:**
- Produces:
  ```python
  WebApi.play_at(device_name: str, context_uri: str, track_uri: str,
                 cb: Callable[[str | None], None]) -> None
  ```
  - `cb(None)` quando der certo, ou `cb(mensagem)` quando falhar.
  - Guarda o `id` do aparelho em `self._device_id`.
  - Se o `PUT` responder 404, limpa esse `id` e tenta uma vez de novo, pedindo os aparelhos outra vez.

- [ ] **Step 1: Escrever os testes que falham** (com o `FakeTransport` que já existe em `tests/test_webapi_client.py`)
  - `test_play_at_finds_device_and_plays_with_offset`:
    - a 1ª chamada é `GET .../me/player/devices`; responder com `{"devices":[{"id":"d1","name":"hyprspot"}]}`;
    - a 2ª chamada é `PUT .../me/player/play?device_id=d1`, com o corpo `{"context_uri":"spotify:playlist:p","offset":{"uri":"spotify:track:t"}}` (para isso, o `FakeTransport` passa a guardar o `body`);
    - responder 204; esperar `got == [None]`.
  - `test_play_at_reuses_device_id`: uma segunda chamada vai direto ao `PUT`.
  - `test_play_at_without_device_reports_error`: com `{"devices":[]}`, o callback recebe uma string e não há `PUT`.
  - `test_play_at_retries_once_on_404`: o `PUT` responde 404; vem um novo `GET` de aparelhos, outro `PUT`, e o callback é chamado uma vez só.

- [ ] **Step 2: Rodar e ver falhar**
  Run: `python3 -m pytest tests/test_webapi_client.py -q -k play_at`
  Expected: FAIL (`AttributeError: play_at`)

- [ ] **Step 3: Implementar `play_at`**
  Usar `_request`. Mensagem de erro de aparelho ausente: `"O hyprspot não aparece como aparelho no Spotify"`.

- [ ] **Step 4: Rodar e ver passar**
  Run: `python3 -m pytest tests/test_webapi_client.py -q`
  Expected: PASS

- [ ] **Step 5: Validar o `offset` nas Curtidas, de verdade** (uma vez, à mão)
  - Com o Soloist ativo, rodar um script que chama `play_at("hyprspot", "spotify:user:<id>:collection", <URI de uma música curtida que não seja a primeira>)`.
  - Conferir com `soloist ctl now`.
  - Anotar o resultado no ledger:
    - **Se funcionar:** nada muda.
    - **Se não funcionar:** a Task 5 usa a saída prevista no spec, mandando `skip_next` N vezes quando a música estiver entre as próximas 10.
  - No fim, voltar ao que estava tocando antes.

- [ ] **Step 6: Commit**
  ```bash
  git add hyprspot/webapi.py tests/test_webapi_client.py
  git commit -m "feat: WebApi.play_at toca uma playlist a partir de uma música"
  ```

### Task 2: Fila do Soloist (parser puro + sincronização sem laço)

**Files:**
- Modify: `hyprspot/soloist.py` (parser)
- Modify: `hyprspot/state.py` (`QueueSync` e o estado da fila)
- Test: `tests/test_soloist.py`, `tests/test_state.py`
- Fixture: `tests/fixtures/queue_changed.json`, montado a partir de uma resposta real do `get_queue`, com 3 entradas em `upcoming` (uma delas sem URI `spotify:track:`) e 1 em `previous`.

**Interfaces:**
- Produces:
  - `parse_queue(ev: dict) -> list[Track]`, em `soloist.py`. Devolve as entradas de `upcoming` na ordem recebida, convertidas com `entity_to_track(entry["item"])`, ignorando as que não são `spotify:track:`.
  - `QueueSync`, em `state.py`:
    ```python
    class QueueSync:
        def __init__(self, send: Callable[[str, dict], bool]): ...
        def request(self) -> None:
            ...  # manda get_queue(limit=0) se nenhum pedido estiver pendente
        def on_queue_event(self, ev: dict) -> list[Track] | None:
            ...  # lista nova (ou None se for só um aviso)
    ```
    Regras:
    - Um `queue_changed` que responde a um pedido pendente devolve a fila completa e encerra o pedido.
    - Um `queue_changed` espontâneo, sem pedido pendente, chama `request()` e devolve `None`.
  - `AppState.queue: list[Track]` e `AppState.queue_context: str | None`.
  - `AppState.set_queue(context_uri: str | None, tracks: list[Track])`, que emite `"queue"`.

- [ ] **Step 1: Testes que falham**
  - `test_parse_queue_reads_upcoming_in_order`: os títulos vêm na ordem da fixture.
  - `test_parse_queue_skips_non_tracks`: a entrada sem `spotify:track:` não aparece.
  - `test_queue_sync_does_not_loop`:
    - um `request()` registra 1 envio;
    - o `on_queue_event` da resposta devolve a lista e não gera novo envio;
    - um `queue_changed` espontâneo depois gera exatamente 1 envio e devolve `None`.
  - `test_queue_sync_request_is_idempotent_while_pending`: dois `request()` seguidos geram 1 envio só.
  - `test_set_queue_emits_once`: o mesmo valor duas vezes emite um sinal só.

- [ ] **Step 2: Rodar e ver falhar**
  Run: `python3 -m pytest tests/test_soloist.py tests/test_state.py -q`
  Expected: FAIL (`ImportError: parse_queue / QueueSync`)

- [ ] **Step 3: Implementar** `parse_queue`, `QueueSync` e `set_queue`.

- [ ] **Step 4: Rodar a suíte**
  Run: `python3 -m pytest -q`
  Expected: PASS

- [ ] **Step 5: Commit**
  ```bash
  git commit -am "feat: fila do Soloist (parse_queue, QueueSync)"
  ```

### Task 3: Widget `PlaylistPage`

**Files:**
- Create: `hyprspot/ui/playlist_page.py`
- Modify: `hyprspot/ui/style.css` (cabeçalho, botões em pílula, título de seção)
- Test: `tests/test_playlist_page.py` (pula sem display, como `test_ui.py`)

**Interfaces:**
- Consumes: `track_row`, `make_list` e `ItemRow.set_active` de `ui/lists.py`; `Cover` de `ui/cover.py`.
- Produces:
  ```python
  PAGE_BLOCK = 30

  class PlaylistPage(Gtk.Box):
      def __init__(self, art: ArtLoader, on_back: Callable[[], None],
                   on_play: Callable[[bool], None],   # True = aleatório
                   on_pick: Callable[[str], None]): ...  # URI da música
      playlist: Playlist | None
      def show(self, playlist: Playlist, back_label: str) -> None: ...
      def set_queue(self, context_uri: str | None, tracks: list[Track],
                    current_uri: str | None) -> None: ...
      def rendered(self) -> int: ...  # quantas linhas estão desenhadas
      def load_more(self) -> None: ...  # desenha o próximo bloco de 30
  ```
  - O cabeçalho mostra o botão `←` com o texto `back_label` ("Playlists" ou "Buscar").
  - Mostra também capa (76 px), nome, "Playlist · {dono}", e os botões "Tocar" e "Aleatório".
  - Abaixo vem o título "A seguir" e a lista.
  - Se `context_uri` for diferente de `playlist.uri`, a lista some e aparece o rótulo "Esta playlist não está tocando".
  - A linha cujo URI é `current_uri` fica ativa.
  - `load_more` também roda quando a rolagem chega perto do fim.

- [ ] **Step 1: Testes que falham**
  - `test_page_renders_first_block_then_more`: com 75 faixas, `rendered() == 30`; depois de `load_more()`, 60; depois de outro, 75.
  - `test_page_highlights_current_track`.
  - `test_page_shows_not_playing_for_other_context`: a lista fica escondida e o rótulo visível.
  - `test_page_buttons_call_back`: "Tocar" chama `on_play(False)`, "Aleatório" chama `on_play(True)`, `←` chama `on_back()`, e ativar a linha 2 chama `on_pick(uri)`.

- [ ] **Step 2: Rodar e ver falhar**
  Run: `python3 -m pytest tests/test_playlist_page.py -q`
  Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implementar** o widget e o CSS, no estilo dos `hs-*` que já existem.

- [ ] **Step 4: Rodar e ver passar**
  Run: `python3 -m pytest tests/test_playlist_page.py -q`
  Expected: PASS

- [ ] **Step 5: Commit**
  ```bash
  git add hyprspot/ui/playlist_page.py hyprspot/ui/style.css tests/test_playlist_page.py
  git commit -m "feat: widget da tela da playlist"
  ```

### Task 4: Navegação no expandido (pilha por aba, Esc, altura 600)

**Files:**
- Modify: `hyprspot/ui/expanded.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `PlaylistPage` (Task 3); `AppState.queue` e `queue_context` (Task 2).
- Produces:
  - Novo parâmetro de construtor no `ExpandedView`:
    ```python
    on_open_playlist: Callable[[Playlist, str], None]  # str = aba
    ```
    É chamado ao ativar uma linha de playlist, no lugar do antigo `on_command("play")`.
  - Métodos públicos:
    ```python
    ExpandedView.open_playlist(playlist: Playlist, tab: str) -> None  # mostra a página na pilha daquela aba
    ExpandedView.close_playlist(tab: str | None = None) -> None       # volta à lista
    ExpandedView.page_open(tab: str) -> bool
    ```
  - Callbacks que o expandido repassa ao app:
    ```python
    on_play_playlist: Callable[[str, bool], None]
    on_pick_track: Callable[[str, str], None]  # contexto, música
    ```
  - Cada aba (`"playlists"` e `"search"`) tem um `Gtk.Stack` com `"list"` e `"page"`. As duas abas usam instâncias próprias de `PlaylistPage`.
  - Em `what == "queue"` ou `"playback"`, a página visível chama `set_queue(state.queue_context, state.queue, current_uri)`.

- [ ] **Step 1: Testes que falham** (em `tests/test_ui.py`, criando `ExpandedView` com callbacks falsos)
  - `test_expanded_is_600_tall`: `ExpandedView.HEIGHT == 600`.
  - `test_clicking_playlist_calls_open`: `row-activated` em `pl_list` chama `on_open_playlist(playlist, "playlists")`.
  - `test_escape_goes_back_before_collapsing`: com a página aberta, Esc fecha a página e não chama `on_collapse`; um segundo Esc chama `on_collapse`.
  - `test_switching_tabs_keeps_open_page`: abrir a página na aba Playlists, ir para Buscar e voltar mantém `page_open("playlists")`.
  - `test_slash_returns_to_search_results`: com a página aberta na aba Buscar, `/` fecha a página e foca o campo.

- [ ] **Step 2: Rodar e ver falhar**
  Run: `python3 -m pytest tests/test_ui.py -q`
  Expected: FAIL

- [ ] **Step 3: Implementar** a pilha por aba, o Esc em camadas, o `/` e a altura 600.

- [ ] **Step 4: Rodar a suíte**
  Run: `python3 -m pytest -q`
  Expected: PASS. Um teste existente que construa `ExpandedView` com a assinatura antiga deve ganhar os novos callbacks; isso não é regressão.

- [ ] **Step 5: Commit**
  ```bash
  git commit -am "feat: navegação até a tela da playlist no expandido"
  ```

### Task 5: Ligar no app (tocar e abrir, fila, escolher música)

**Files:**
- Modify: `hyprspot/app.py`
- Test: `tests/test_app_playlist.py`. Testar as decisões puras extraídas para funções em `hyprspot/state.py`, sem subir o app.

**Interfaces:**
- Consumes: `play_at` (Task 1), `QueueSync` e `set_queue` (Task 2), as callbacks do `ExpandedView` (Task 4).
- Produces:
  ```python
  playlist_click_commands(pb: Playback, uri: str) -> list[tuple[str, dict]]
  # [] se uri == pb.context_uri; senão [("play", {"uri": uri})]

  play_button_commands(uri: str, shuffle: bool) -> list[tuple[str, dict]]
  # [("set_shuffle", {"enabled": shuffle}), ("play", {"uri": uri})]

  skip_count(queue: list[Track], uri: str) -> int | None
  # posição 1..10 da música nas próximas; None se não estiver lá ou passar de 10
  ```
  - `skip_count` só é usado se o Step 5 da Task 1 mostrar que o `offset` não funciona nas Curtidas.
  - No `HyprspotApp`:
    - `_open_playlist(p, tab)` executa `playlist_click_commands` via `on_command` e chama `self.expanded.open_playlist(p, tab)`;
    - `_play_playlist(uri, shuffle)` usa `play_button_commands`;
    - `_pick_track(ctx, uri)` chama `self.api.play_at(self.state.playback.device_name, ctx, uri, ...)` e, se der erro, `_flash_error`;
    - `QueueSync(self.client.send)` é criado no `_build`;
    - em `track_changed`, `context_changed` e no `_connected`, chama `queue_sync.request()`;
    - em `queue_changed`, se `on_queue_event` devolver uma lista, chama `state.set_queue(self.state.playback.context_uri, lista)`.
  - `on_command` continua marcando o `SkipDetector`. O `play_at` também conta como comando do usuário: chamar `self.skips.user_command(clock_ms())` antes dele.

- [ ] **Step 1: Testes que falham**
  - `test_click_on_current_playlist_only_opens`: `playlist_click_commands` com o mesmo URI devolve `[]`.
  - `test_click_on_other_playlist_plays`.
  - `test_play_buttons_set_shuffle_first`.
  - `test_skip_count`: posição 3 devolve 3; ausente devolve `None`; posição 12 devolve `None`.

- [ ] **Step 2: Rodar e ver falhar**
  Run: `python3 -m pytest tests/test_app_playlist.py -q`
  Expected: FAIL (`ImportError`)

- [ ] **Step 3: Implementar** as funções puras e a ligação no `app.py`.

- [ ] **Step 4: Rodar a suíte**
  Run: `python3 -m pytest -q`
  Expected: PASS

- [ ] **Step 5: Verificar de verdade**
  - Reiniciar o widget (`hyprspot-ctl quit` e depois `~/.local/bin/hyprspot`).
  - Expandir, clicar numa playlist e conferir que:
    - ela toca;
    - a tela abre com "A seguir";
    - clicar na 3ª música toca essa música;
    - Esc volta.
  - Usar o widget instrumentado do scratchpad, se precisar ver eventos.
  - No fim, restaurar o que estava tocando.

- [ ] **Step 6: Commit**
  ```bash
  git commit -am "feat: clicar na playlist toca e abre a tela com a fila"
  ```

### Task 6: Busca geral com playlists

**Files:**
- Modify: `hyprspot/webapi.py` (`search_url`, `parse_search_playlists`, `search`)
- Modify: `hyprspot/state.py` (`search_playlists`)
- Modify: `hyprspot/app.py` (`on_search`)
- Modify: `hyprspot/ui/expanded.py` (seções)
- Test: `tests/test_webapi.py`, `tests/test_ui.py`

**Interfaces:**
- Produces:
  - `search_url(q, offset)` passa a usar `type=track,playlist`.
  - `parse_search_playlists(body) -> list[Playlist]`, reutilizando `parse_playlists` sobre `body["playlists"]`.
  - `WebApi.search` passa a chamar `cb(tracks, playlists, err)`.
  - `AppState.set_search(query, results, playlists=None)`: `None` mantém as playlists atuais, para as páginas seguintes da busca. Emite `"search"`.
  - No expandido, a aba Buscar ganha:
    - um título "Playlists";
    - `sr_pl_list`, com no máximo 5 linhas, escondida se estiver vazia;
    - um título "Músicas";
    - `sr_list`, como hoje.
  - Ativar uma linha de `sr_pl_list` chama `on_open_playlist(p, "search")`.

- [ ] **Step 1: Testes que falham**
  - `test_search_url_asks_tracks_and_playlists`: a URL contém `type=track%2Cplaylist`.
  - `test_parse_search_playlists`, com uma fixture mínima.
  - `test_search_shows_at_most_five_playlists`: com 8 playlists no estado, `sr_pl_list` tem 5 linhas.
  - `test_enter_still_plays_first_track`: com playlists e músicas, Enter manda `play` com o URI da 1ª música.
  - Atualizar `test_no_token_reports_auth_without_network` para a nova assinatura do callback de 3 argumentos.

- [ ] **Step 2: Rodar e ver falhar**
  Run: `python3 -m pytest tests/test_webapi.py tests/test_ui.py tests/test_webapi_client.py -q`
  Expected: FAIL

- [ ] **Step 3: Implementar.** Só a página com `offset == 0` atualiza as playlists. Placeholder do campo: "Buscar músicas ou playlists".

- [ ] **Step 4: Rodar a suíte**
  Run: `python3 -m pytest -q`
  Expected: PASS

- [ ] **Step 5: Commit**
  ```bash
  git commit -am "feat: busca mostra playlists e abre a tela delas"
  ```

### Task 7: Documentação e imagens

**Files:**
- Modify: `README.md` (tabela de Uso: clicar na playlist, tela da playlist, Esc, busca de playlists)
- Modify: `scripts/make_screenshots.py` (gerar também `expanded-playlist-page.png`, com fila fictícia de rock; a busca passa a ter playlists)
- Regenerate: `docs/screenshots/*.png`

- [ ] **Step 1:** Atualizar o README e o script.
- [ ] **Step 2:** Rodar `python3 scripts/make_screenshots.py` e conferir as imagens. Nenhuma pode sair vazia: conferir os pixels com alfa diferente de zero, como já foi feito antes.
- [ ] **Step 3:** Rodar a suíte. Esperado: PASS.
- [ ] **Step 4: Commit**
  ```bash
  git commit -am "docs: README e capturas da tela da playlist"
  ```
