# hyprspot — widget de Spotify para Hyprland

Status: rascunho para aprovação · 2026-11-18

## 1. Intenção

Um mini player de Spotify que fica flutuando sobre as outras janelas no Hyprland, sempre visível, ocupando o mínimo de tela. Os controles básicos (tocar/pausar, próxima, anterior) têm que responder na hora. Quando o usuário quer trocar de playlist ou buscar uma música, o widget se expande; assim que perde o foco, volta sozinho para o tamanho mínimo.

## 2. Objetivos

1. **Sempre por cima**: visível sobre qualquer janela, em todos os workspaces do monitor, sem aparecer no alt-tab nem ocupar espaço no tiling.
2. **Minimalista**: modo compacto com capa, título/artista e 3 botões. Nada mais.
3. **Rápido**: o clique tem resposta visual no mesmo frame; o comando chega ao player em menos de 50 ms quando o player é local.
4. **Trocar playlist** em até 2 cliques depois de expandir.
5. **Buscar músicas** (e tocar o resultado) sem sair do widget.
6. **Expande / recolhe sozinho**: expande sob demanda e recolhe ao perder o foco.
7. **Não roubar foco**: clicar em play/pause no modo compacto **não** tira o foco do teclado da janela onde você está digitando.

## 3. Fora do escopo (v1)

- Letras, podcasts/audiobooks, edição de playlists, edição da fila, arquivos locais.
- Várias contas, X11, outros compositores.
- Ser um cliente completo de Spotify (biblioteca, artistas, álbuns navegáveis).

## 4. Anti-objetivos (falha mesmo que "funcione")

- Atraso perceptível entre o clique e a reação da UI.
- Roubar o foco do teclado quando ninguém pediu.
- Depender de uma aba de navegador aberta para tocar.
- Visual de "app de verdade": barras de título, menus, bordas pesadas.
- Polling agressivo que estoure a cota da API (no modo dev a cota é contada por conta).

## 5. Ambiente atual (verificado nesta máquina)

| Item | Estado |
|---|---|
| SO / WM | Ubuntu 26.04 · Hyprland 0.53.3 · Omarchy (config em Lua: `~/.config/hypr/hyprland.lua`, `bindings.lua`) |
| Monitor | eDP-1 1920×1080 @ escala 1.2 → **1600×900 lógicos** |
| Python / GTK | Python 3 + PyGObject, **GTK 4.22**, **Gtk4LayerShell** e **libsoup 3** disponíveis via `gi` |
| Spotify | **Nenhum cliente instalado** (nem nativo, flatpak, snap, spotifyd ou librespot). `playerctl` instalado, sem players ativos |
| Outros | waybar, node, gjs. Sem quickshell / eww / ags / cargo |

## 6. Restrições externas (Spotify Web API, regras de 2026)

- O modo de desenvolvimento exige que o dono do app tenha **Spotify Premium**; o app fica limitado a poucos usuários autorizados (5 em fevereiro, ampliado para 25 em julho). Para uso pessoal isso não incomoda.
- **Busca**: `limit` padrão 5, máximo **10** por página; usar `offset` para paginar.
- **Playlists do usuário**: `GET /me/playlists` continua disponível.
- **Itens de playlist**: `GET /playlists/{id}/items` só devolve faixas de playlists **próprias ou colaborativas**. Para *tocar* uma playlist isso não importa: basta `PUT /me/player/play` com `context_uri`.
- **Curtidas**: salvar/remover via `PUT/DELETE /me/library` (URIs).
- Endpoints de player (play/pause/next/seek/volume/devices/transfer) não aparecem no guia de migração. **Confirmar no spike (§12).**
- Controlar a reprodução pela API exige Premium em qualquer caso. **O usuário tem Premium (confirmado em 2026-11-18).**

Fontes: [guia de migração fev/2026](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide), [anúncio fev/2026](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security), [cotas jul/2026](https://developer.spotify.com/blog/2026-07-23-web-api-quota-updates).

## 7. Abordagens consideradas

| | Abordagem | Prós | Contras |
|---|---|---|---|
| **A (escolhida)** | **Python + GTK4 + gtk4-layer-shell**, daemon próprio; Web API para busca/playlists e MPRIS (D-Bus) como caminho rápido de controle; **spotifyd** como player local headless | Tudo já instalado; layer-shell garante "sempre por cima" de verdade; controle de foco fino; leve | Python + layer-shell exige `LD_PRELOAD` da lib; arrastar a janela precisa ser implementado (via margens) |
| B | Quickshell (QML) | Muito fluido, padrão no ecossistema Hyprland | Não instalado; OAuth/HTTP em QML/JS é mais trabalhoso; nova stack para manter |
| C | Janela comum (Electron/Tauri) + regras `float`/`pin` do Hyprland | UI web familiar | Pesado; "always on top" fraco (tela cheia cobre); foco e redimensionamento brigam com o WM |

**Player (quem toca o áudio):**

- **spotifyd (escolhido)**: dispositivo Spotify Connect headless (librespot), ~10–20 MB de RAM, expõe MPRIS (build "full"). Roda como serviço systemd de usuário. Não está no apt, então usar o binário de release do GitHub.
- Alternativa: o cliente oficial do Spotify. Funciona igual (também expõe MPRIS), mas abre uma janela pesada que você não quer ver.
- O widget é **agnóstico de dispositivo**: se o app oficial ou o celular estiver tocando, ele controla esse dispositivo via Web API.

## 8. Experiência

### 8.1 Modo compacto (padrão)

Tamanho ~**320×64 lógicos**, ancorado no canto inferior direito, margem 16 px.

```
┌──────────────────────────────────────────┐
│ ▓▓▓  Nome da Música            ⏮  ⏯  ⏭  │
│ ▓▓▓  Artista                             │
│▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔───────────────────────│ ← barra de progresso de 2 px
└──────────────────────────────────────────┘
```

- Fundo escuro translúcido, cantos 12 px, sem borda; texto com elipse.
- **Clicar nos botões**: executa o comando e **não pega foco de teclado**.
- **Clicar na capa ou no título**: expande.
- **Rolar o mouse sobre o widget**: volume ±5 %.
- **Arrastar** pela capa com o botão do meio (ou com Super pressionado): move o widget; a posição é salva.
- Sem nada tocando: mostra "Nada tocando" e ▶ retoma o último contexto.

### 8.2 Modo expandido

Tamanho ~**360×520 lógicos**. Cresce a partir do canto de ancoragem (para cima/esquerda), então os controles não "pulam" de lugar.

```
┌──────────────────────────────────────┐
│ [ Playlists ]  [ Buscar ]            │
│ ┌──────────────────────────────────┐ │
│ │ 🔍 buscar…                        │ │ ← só na aba Buscar; foco automático
│ └──────────────────────────────────┘ │
│ ▓ Curtidas                        ♥  │
│ ▓ Foco 2026                          │
│ ▓ Treino                          ▶  │ ← tocando agora
│ ▓ …                                  │
│                                      │
├──────────────────────────────────────┤
│ ▓▓▓▓  Nome da Música                 │
│ ▓▓▓▓  Artista · Álbum                │
│ 1:12 ━━━━━━━━━●──────────── 3:40     │
│      🔀   ⏮   ⏯   ⏭   🔁    🔊 ━━●─ │
└──────────────────────────────────────┘
```

- **Aba Playlists**: "Curtidas" no topo e depois `/me/playlists` na ordem do Spotify. Clique = toca a playlist do início (shuffle respeita o estado atual). A playlist tocando fica marcada.
- **Aba Buscar**: o campo recebe foco ao abrir a aba. Debounce de 250 ms, resultados de **faixas** (10 por página; rolar até o fim carrega mais). Enter toca o primeiro resultado. Clique toca a faixa. Opcional: `Ctrl+Enter` adiciona à fila.
- **Teclado**: `Tab` alterna abas, `/` vai para a busca, ↑/↓ + Enter navega e toca, `Esc` recolhe, `Espaço` (fora do campo) play/pause.
- Lembra a última aba usada.

### 8.3 Regras de expandir/recolher

| Evento | Resultado |
|---|---|
| Clique na capa/título, ou `hyprspot expand`, ou atalho | Expande e **pega foco de teclado** |
| Janela perde o foco (clique em outra janela, troca de workspace) | Recolhe após 150 ms de tolerância (evita piscar com popovers internos) |
| `Esc` | Recolhe |
| Tocar algo da lista | **Continua expandido** (o usuário pode querer escolher outra); recolhe só quando perder o foco |
| Atalho de toggle com o widget expandido | Recolhe |

Implementação do foco: no compacto, `keyboard_mode = NONE` (cliques não roubam foco). Ao expandir, mudar para `ON_DEMAND` e chamar `present()`. Detectar a perda de foco por `notify::is-active` na janela. Ao recolher, voltar para `NONE`. **Validar no spike** que o Hyprland 0.53 entrega o foco e o evento de perda corretamente para layer surfaces `ON_DEMAND`. Se não entregar, usar o plano B: `EXCLUSIVE` enquanto expandido, mais recolher ao detectar clique fora via `hyprctl`/socket de eventos (`activewindow`).

### 8.4 Atalhos e CLI

Aplicação de instância única (`Gtk.Application`). Comandos de CLI são repassados por D-Bus para o processo já aberto, em poucos milissegundos:

```
hyprspot                 # inicia o daemon (autostart)
hyprspot toggle          # expande/recolhe
hyprspot expand [--search]
hyprspot hide|show       # esconde o widget inteiro
hyprspot play-pause|next|prev
```

Binds propostos em `~/.config/hypr/bindings.lua` (sintaxe Lua do Omarchy; o builder confere o formato):

- `Super+Alt+M` → `hyprspot toggle`
- `Super+Alt+S` → `hyprspot expand --search`
- `Super+Alt+Shift+M` → `hyprspot hide/show`
- Teclas de mídia (`XF86AudioPlay/Next/Prev`): se ainda não estiverem ligadas ao `playerctl`, ligar. Funcionam direto via MPRIS do spotifyd.

Autostart em `~/.config/hypr/autostart.lua`.

## 9. Arquitetura

```
┌────────────── hyprspot (1 processo, GLib main loop) ───────────────┐
│ UI (GTK4 + layer-shell, camada OVERLAY)                            │
│   CompactView ◄──── AppState (fonte única da verdade) ────► ExpandedView
│                       ▲            │ comandos                      │
│        eventos/poll   │            ▼                               │
│   ┌───────────────┐   │   ┌──────────────────┐                     │
│   │ MprisClient   │───┘   │ CommandRouter     │──► MPRIS (local, ~ms)│
│   │ (D-Bus, push) │       │ otimista + reconc.│──► Web API (rede)    │
│   └───────────────┘       └──────────────────┘                     │
│   SpotifyApi (libsoup3 async, keep-alive) · Auth (PKCE) · Cache    │
└────────────────────────────────────────────────────────────────────┘
          │ D-Bus MPRIS                         │ HTTPS
     spotifyd (systemd --user)  ◄── Connect ──  api.spotify.com
```

### 9.1 Caminho rápido dos controles

1. Clique → `AppState` muda **otimisticamente** (ícone ⏯ alterna, título muda para "…" no next) e a UI redesenha no mesmo frame.
2. `CommandRouter`: se o dispositivo ativo é o spotifyd local **e** existe player MPRIS, manda `PlayPause/Next/Previous/Seek/Volume` por D-Bus. Senão, usa a Web API (`/me/player/...`).
3. Reconciliação: os sinais MPRIS (`PropertiesChanged`) ou um GET de estado 300 ms depois confirmam o resultado. Se o comando falhar, reverte o estado e mostra um aviso discreto (ponto vermelho por 2 s).
4. Seek e volume: enquanto arrasta, envia no máximo a cada 100 ms; ao soltar, envia o valor final.
5. Progresso: interpolado localmente a cada frame/segundo a partir de `progress_ms + timestamp`, sem precisar de rede.

### 9.2 Atualização de estado

- Com MPRIS local: tudo por push. A Web API só é chamada para dados que o MPRIS não tem (contexto/playlist atual, shuffle/repeat se faltar).
- Sem MPRIS (outro dispositivo tocando): `GET /me/player` a cada **1 s expandido / 5 s compacto**, pausando o polling quando não há reprodução há mais de 1 min (volta no próximo clique). Respeitar `429` + `Retry-After`.

### 9.3 Dispositivo

- Ao apertar ▶ sem dispositivo ativo: garantir que o `spotifyd.service` está rodando (`systemctl --user start`), depois `PUT /me/player` (transfer) para o device "hyprspot" e então tocar.
- Mostrar o nome do dispositivo atual em texto pequeno no expandido. Clique abre a lista de dispositivos para transferir (prioridade baixa, pode ficar para a v1.1).

### 9.4 Autenticação

- OAuth Authorization Code **com PKCE** (sem client secret no disco).
- Redirect: `http://127.0.0.1:8898/callback` (o Spotify exige IP de loopback, não `localhost`). Um servidor HTTP mínimo e temporário só durante o login.
- Primeiro uso: `hyprspot login` abre o navegador; o token fica em `~/.config/hyprspot/token.json` com permissão `0600` (ou libsecret, à escolha do builder). Refresh automático antes de expirar.
- Scopes: `user-read-playback-state`, `user-modify-playback-state`, `user-read-currently-playing`, `playlist-read-private`, `playlist-read-collaborative`, `user-library-read`, `user-library-modify`.
- O `client_id` vem de um app criado pelo usuário em developer.spotify.com e fica em `~/.config/hyprspot/config.toml`.

### 9.5 Cache

- Playlists: carregadas no startup e salvas em disco. A aba abre instantaneamente com o cache e atualiza em segundo plano.
- Capas: cache em disco (LRU ~50 MB, `~/.cache/hyprspot/art/`). Pedir o tamanho de 64 px para listas e 300 px para a capa grande. Pré-carregar a capa da próxima faixa quando possível.
- Busca: cache em memória das últimas 20 queries.

### 9.6 "Curtidas"

Tocar via `context_uri = spotify:user:{id}:collection`. **Validar no spike**; se não funcionar mais, tocar as N primeiras faixas de `GET /me/tracks` via `uris`.

## 10. Visual

Design de referência: canvas com protótipo clicável, estados e tokens (fora do repositório).

- Paleta Tokyo Night, a mesma da waybar do usuário: fundo `#1a1b26` a 94 %, campo/abas `#16161e`, superfície `#24283b`/`#292e42`, texto `#c0caf5`, texto secundário `#9aa5ce` (7:1), acento `#7aa2f7` (progresso, foco, toggles ligados), verde `#9ece6a` só para "tocando agora", curtida/erro `#f7768e`. Se o tema do Omarchy expuser cores legíveis, elas substituem esses valores.
- Fontes: Inter para a UI (título 13–14/600, listas 13/500, artista 11.5/400) e JetBrains Mono para tempos, nome do device e dicas de teclado.
- Medidas: compacto 320×64 com raio 14; expandido 360×520 com raio 16 e borda 1 px de acento a 45 %; capa de 48 (compacto), 52 (expandido) e 34 (listas); botões de 34; play circular de 36/44 em `#c0caf5` com ícone escuro; linhas de lista de 46; progresso de 2 px (compacto, colado na borda inferior) e 4 px com knob (expandido).
- Expandido, de cima para baixo: abas segmentadas (Playlists | Buscar) + botão recolher → lista rolável → rodapé fixo com capa, título, "artista · contexto", curtir, progresso, controles (aleatório, anterior, play, próxima, repetir), device atual e volume.
- Estados do compacto: tocando, pausado (barra cinza), hover, outro dispositivo ("No celular · artista"), nada tocando, falha (ponto vermelho por 2 s + texto do erro).
- Animação de expandir/recolher: ≤120 ms (ou nenhuma, se layer-shell + resize ficar com tremido; preferir instantâneo a feio).
- Ícones simbólicos GTK (`media-playback-start-symbolic` etc.).
- Blur: regra `layerrule = blur, hyprspot` (namespace da layer surface = `hyprspot`).

## 11. Configuração (`~/.config/hyprspot/config.toml`)

```toml
client_id = "..."
anchor = "bottom-right"      # top-left | top-right | bottom-left | bottom-right
margin = [16, 16]            # x, y (atualizado ao arrastar)
monitor = ""                 # vazio = monitor focado ao iniciar
device_name = "hyprspot"
collapse_delay_ms = 150
```

## 12. Fase 0 — spike obrigatório antes de construir

Script descartável que confirma as premissas arriscadas:

1. Criar o app no dashboard do Spotify (conta Premium), fazer login PKCE e chamar `GET /me/player`, `PUT /me/player/play` com `context_uri` de uma playlist, `GET /search?type=track&limit=10`, `GET /me/playlists`.
2. Instalar o spotifyd (binário "full" com MPRIS), autenticar, ver o device na API e o player no `playerctl -l`.
3. Janela GTK4 layer-shell em OVERLAY: alternar `keyboard_mode` NONE ↔ ON_DEMAND e verificar se o `is-active` muda ao clicar em outra janela no Hyprland 0.53.
4. Medir a latência clique → áudio pausado via MPRIS e via Web API.

Se o item 3 falhar → plano B da §8.3. Se o item 1 falhar nos endpoints de player → o widget vira só MPRIS para controles, e playlists/busca precisam de outra estratégia (rever com o usuário).

## 13. Estrutura sugerida

```
hyprspot/
  hyprspot/            __main__.py app.py state.py router.py
    ui/                compact.py expanded.py widgets.py style.css
    spotify/           api.py auth.py mpris.py devices.py
    cache.py config.py
  bin/hyprspot         wrapper com LD_PRELOAD=libgtk4-layer-shell.so
  systemd/             spotifyd.service (user)
  install.sh           copia arquivos, instala spotifyd, adiciona binds/autostart
  docs/specs/
```

Notas técnicas para o builder:

- `gtk4-layer-shell` em Python exige que `libgtk4-layer-shell.so` seja carregada antes da `libwayland-client` (via `LD_PRELOAD` no wrapper).
- Toda a rede é assíncrona (libsoup3) no main loop. Nada de bloquear a UI. Se usar threads, voltar para a UI com `GLib.idle_add`.
- Namespace da layer: `hyprspot`, camada `OVERLAY`, `exclusive_zone = 0`.

## 14. Critérios de aceite

- [ ] Widget visível sobre janelas tiled, flutuantes e em tela cheia, em todos os workspaces.
- [ ] Play/pause/next/prev no compacto: feedback visual imediato; o áudio responde em < 100 ms com o spotifyd local.
- [ ] Clicar nos controles do compacto não tira o foco do terminal/editor.
- [ ] Clicar na capa expande; clicar em outra janela recolhe em ≤ 200 ms.
- [ ] Trocar de playlist em ≤ 2 cliques após expandir; a lista abre instantaneamente (cache).
- [ ] Buscar "nome da música" + Enter toca o primeiro resultado.
- [ ] Atalhos de teclado e teclas de mídia funcionam.
- [ ] Sobrevive a reinício do spotifyd, perda de rede e token expirado sem travar (mostra estado e se recupera).
- [ ] Memória do processo < 80 MB; CPU ~0 % ocioso.

## 15. Resultados da Fase 0 (2026-11-18)

**Web API (app de dev, conta Premium):** tudo o que o widget precisa respondeu: `/me`, devices, estado do player, `/me/playlists`, busca (10 por página), transfer, play com `context_uri`, pause/play, next, volume, shuffle e seek.
- Latência por comando via API: **360–1000 ms** mesmo com conexão keep-alive. O caminho otimista na UI (§9.1) é obrigatório.
- Device inativo: chamar `PUT /me/player` (transfer) antes de tocar. Sem isso, o play devolveu 404 depois de 3,5 s.
- Curtidas: `spotify:user:{id}:collection` devolveu 403, mas a conta não tem nenhuma faixa curtida, então o teste ficou inconclusivo. Plano: tocar via `uris` a partir de `GET /me/tracks` (endpoint ok).

**spotifyd/librespot: BLOQUEADO.** Ele loga, aparece como device e aceita comandos (MPRIS responde em ~25 ms), mas o Spotify recusa a chave de áudio (`audio key error`) e não sai som. É um problema conhecido, sem correção, de contas mais novas com clientes open-source: [librespot #1649](https://github.com/librespot-org/librespot/issues/1649) está aberto, e [go-librespot #279](https://github.com/devgianlu/go-librespot/issues/279) foi fechado como "not planned". O serviço foi parado e desabilitado; o binário continua em `~/.local/bin`.

**Spotify Soloist: FUNCIONA (escolhido como player, substitui o spotifyd).** Soloist é o cliente Connect oficial e headless do Spotify, versão 1.3.10, em `~/.local/bin/soloist`, rodando como serviço de usuário `soloist.service` com `--ws 127.0.0.1:9090`. A chave fica em `~/.config/hyprspot/soloist.env` (0600). Alternativas avaliadas: `reports/Alternativas de áudio Spotify Linux.md`.
- **Toca na conta do usuário**: o áudio sai pelo PipeWire.
- **RAM**: ~91 MB (dois processos: 64 + 27 MB); CPU ~1,6 % tocando.
- **Pareamento**: uma única vez, pelo app do celular na mesma rede. A sessão fica salva em `~/.local/share/soloist`; depois de reiniciar, ele faz login sozinho em ~2 s, sem o celular.
- **Inativo, ele não aparece em `/me/player/devices`.** O comando WS `activate` o torna o device ativo; a partir daí a Web API também o enxerga.
- **API WebSocket (medido com libsoup3)**, comando → evento:

  | Comando | Tempo até o evento |
  |---|---|
  | `pause` / `play` | ~14 ms |
  | `skip_next` | `track_changed` em 22 ms, áudio em ~350 ms |
  | `set_volume` | 3 ms |

- `play` com `uri` toca uma playlist ou faixa localmente, sem a Web API.
- Os eventos chegam por push (`playback_state`, `track_changed`, `position_sync`, `volume_changed`, `queue_changed`), então o polling da §9.2 deixa de existir.
- **Expiração**: cada build vale 90 dias; a atual expira por volta de 2027-02-16 e sai com código 10. O app precisa avisar com antecedência e oferecer a atualização.

**Nova divisão de responsabilidades:**
- **WebSocket do Soloist**: controles, troca de playlist (`play uri`), tocar resultado de busca, estado em tempo real.
- **Web API**: apenas listar playlists e buscar (e curtir).
- **MPRIS**: dispensável (o soloist-mpris só seria útil para as teclas de mídia via playerctl). Alternativa: ligar as teclas de mídia direto em `hyprspot play-pause` etc.

O spotifyd foi desabilitado; o binário continua em `~/.local/bin/spotifyd` e pode ser removido.

**Foco (layer-shell):** o modo NONE não rouba o foco ✅. Redimensionar funciona ✅. O modo ON_DEMAND só recebe foco com clique real. O modo EXCLUSIVE pega o foco, mas não o perde quando outra janela é focada por comando. Trocar de EXCLUSIVE para ON_DEMAND perde o foco na hora. Falta o teste com clique real (`spike/click_test.py`).

## 16. Decisões tomadas por mim (corrija se discordar)

1. Stack Python + GTK4 + layer-shell (já instalado) em vez de Quickshell.
2. spotifyd como player local headless em vez do app oficial.
3. Canto inferior direito, 320×64 compacto / 360×520 expandido.
4. Expandir só por clique na capa/título ou atalho, não por hover.
5. Tocar algo não recolhe; só a perda de foco recolhe.
6. Busca só de faixas na v1 (sem álbuns/artistas).
7. Fila e seleção de dispositivo ficam para a v1.1.
