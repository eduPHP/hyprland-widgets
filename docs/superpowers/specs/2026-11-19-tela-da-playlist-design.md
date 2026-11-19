# Tela da playlist e busca geral

## Intenção

O hyprspot deve funcionar como um **Spotify de celular simplificado**, preso no canto da tela. O app de desktop mostra informação demais e não encolhe. Hoje, clicar numa playlist só começa a tocá-la. A proposta é aproximar o widget do app de celular:

- **Playlist:** clicar abre a tela dela, com as músicas, tocar, aleatório e voltar.
- **Busca:** também encontra playlists, não só músicas.

## Objetivos

1. **Tocar e abrir:** clicar numa playlist, na biblioteca ou na busca, **começa a tocá-la e abre a tela dela**. A decisão foi do usuário, por causa da restrição da API descrita abaixo.
2. **Conteúdo da tela da playlist:**
   - botão **voltar**;
   - capa, nome e dono;
   - botões **Tocar** e **Aleatório**;
   - a **lista de músicas**, com a que está tocando em destaque.
3. **Escolher uma música:** clicar numa música da lista toca a playlist **a partir dela**.
4. **Paginação:** as listas são desenhadas aos poucos, conforme a rolagem.
5. **Busca geral:** a busca mostra **músicas e playlists**. Uma playlist encontrada abre a mesma tela de playlist.

## Fora do escopo (por enquanto)

- Álbuns, artistas, podcasts e a tela inicial com recomendações.
- **A API interna do player web do Spotify,** que daria a lista completa de qualquer playlist. Foi descartada: vai contra os termos do Spotify, pode quebrar a qualquer atualização e exigiria guardar um cookie de sessão. Isso é um **anti-objetivo**.
- O widget compacto não muda.

## Restrições (verificadas em 2026-11-19 na conta do usuário)

- **Playlists de outras pessoas:** o app está no modo de desenvolvimento do Spotify, e `GET /playlists/{id}/items` responde **403** para elas. As 3 playlists do usuário são de outras pessoas. Com playlists próprias ou colaborativas, e com `GET /me/tracks` (Curtidas), funciona.
- **Total de músicas:** `GET /playlists/{id}` devolve só metadados (nome, capa, dono). Não traz mais o total de músicas.
- **Fila do Soloist:** o comando `get_queue` com `limit: 0` devolve a fila do **contexto que está tocando**: o array `previous` e o array `upcoming`, cujas entradas têm `uid`, `source` e `item` com nome, criadores e capa.
  - Medido: 41 itens numa playlist pequena e 80 próximas na DUBDOGZ, maior. Parece haver um teto de cerca de 80.
  - Só funciona para o que já está tocando.
  - Com o aleatório ligado, vem na ordem embaralhada.
  - Os eventos `queue_changed` espontâneos trazem no máximo 10 entradas.
- **Tocar a partir de uma música:** o `play` do Soloist não aceita uma música inicial (pedidos abertos: spotify/soloist#9 e #37). Pela Web API funciona. Testado: `PUT /me/player/play?device_id=<id do aparelho "hyprspot">` com o corpo abaixo respondeu 204 e tocou certo.
  ```json
  {"context_uri": "...", "offset": {"uri": "..."}, "position_ms": 0}
  ```
  O id do aparelho vem de `GET /me/player/devices`, procurando o nome `hyprspot`.
- **Busca:** `GET /search?type=track,playlist` funciona, com no máximo 10 itens por tipo e por página.

## Abordagens consideradas

| | Abordagem | Situação |
|---|---|---|
| **A** | **Só a fila do Soloist para todas as playlists, inclusive as Curtidas** | **Escolhida pelo usuário:** um comportamento só, em todas as telas |
| B | Web API para as Curtidas e as playlists próprias; fila para as outras | Recusada: dois caminhos diferentes, e hoje só as Curtidas ganhariam com isso |
| C | API interna do player web | Descartada: anti-objetivo |

**Escolher uma música não depende da origem da lista.** A fila traz o URI de cada música, e a Web API toca a playlist a partir dele. Isso foi testado numa playlist de outra pessoa.

## Comportamento

### Tamanho do expandido
- A altura passa de **520 para 600 px**. O usuário pediu "um pouco mais alto, não muito".
- A largura continua 360 px.
- O rodapé do player continua completo em todas as telas.
- O ajuste que mantém o expandido dentro da tela, quando o compacto está perto da borda, continua valendo com a altura nova.

### Navegação no expandido
- As abas continuam **Playlists | Buscar**. Cada aba guarda a própria navegação: o usuário vai da lista à tela da playlist e volta.
- A tela da playlist aberta pela busca fica dentro da aba Buscar. O **voltar** retorna aos resultados, com o texto e a rolagem preservados.
- **Esc** na tela da playlist volta. Esc na raiz da aba recolhe o widget, como hoje.
- **Trocar de aba** não perde a tela aberta na outra aba.

### Tela da playlist
- **Cabeçalho:** `←` (voltar), capa, nome e dono. Vêm do item clicado; não é preciso pedir os metadados de novo.
- **Tocar:** toca a playlist do começo, com o aleatório desligado.
- **Aleatório:** liga o aleatório e toca.
- **Origem da lista:** sempre a fila do Soloist (`get_queue` com `limit: 0`), inclusive nas Curtidas.
  - A tela mostra a música atual e as próximas, sob o título **"A seguir"**.
  - A fila é pedida de novo quando a faixa muda (`track_changed`) e quando chega `queue_changed`, porque os eventos espontâneos trazem só 10 entradas.
  - Com o aleatório ligado, a ordem é a embaralhada, que é a ordem em que as músicas vão tocar.
  - São no máximo cerca de 80 músicas à frente: é o que o Soloist entrega.
- **Paginação na tela:** a fila inteira fica na memória, mas as linhas são desenhadas em blocos de 30. O próximo bloco entra ao chegar perto do fim da rolagem.
- **Clicar numa música:** chama `PUT /me/player/play` com `context_uri` da playlist e `offset.uri` da música.
  - Nas Curtidas, o contexto é `spotify:user:{id}:collection`. **Validar o `offset` nesse contexto na primeira tarefa da implementação.**
  - Se o teste mostrar que o `offset` nunca funciona nas Curtidas, a saída é mandar `skip_next` tantas vezes quanto a posição da música na fila, desde que ela esteja entre as próximas 10. Para uma música mais distante, a linha mostra a mensagem de erro (`_flash_error`).
  - Uma falha ocasional da API (rede, cota) mostra a mensagem de erro de sempre.
- **Destaque:** a música que está tocando fica destacada, usando o mesmo estilo de "tocando" que a busca já tem.
- **Outra coisa começou a tocar:** se o contexto atual deixar de ser esta playlist enquanto a tela está aberta, a lista some. No lugar dela aparece "Esta playlist não está tocando", com o botão Tocar.
- **Playlist que já está tocando:** clicar nela, na biblioteca ou na busca, só abre a tela, sem recomeçar a música. A comparação é com o `context_uri` atual.

### Busca
- **Pedido:** `type=track,playlist`.
- **Seções:** **Músicas** (como hoje, com paginação por rolagem) e **Playlists** (até 5, na primeira página, sem paginação nesta versão).
- **Ordem:** playlists acima de músicas, como no celular.
- **Clicar numa playlist:** toca a playlist e abre a tela dela dentro da aba Buscar.
- **Teclado:**
  - Enter e as setas ↑/↓ continuam tocando e percorrendo **só as músicas**. As playlists ficam numa lista própria, clicável com o mouse.
  - `/` e o atalho `expand-search`, com uma playlist aberta na aba Buscar, voltam para os resultados e põem o foco no campo de busca.

## Decidido / deixado para quem implementar

- **Decidido pelo usuário:**
  - clicar numa playlist toca e abre a tela;
  - abordagem A (fila do Soloist para tudo);
  - paginação por rolagem;
  - expandido com 600 px de altura.
- **Decidido por mim, sujeito a revisão:**
  - abas mantêm os nomes;
  - Esc volta;
  - clicar na playlist que já está tocando só abre a tela;
  - Enter e as setas da busca continuam só nas músicas;
  - `/` volta aos resultados;
  - 5 playlists na busca;
  - blocos de 30 linhas;
  - título "A seguir".
- **Fica para quem implementar:**
  - estrutura dos widgets (`Gtk.Stack` por aba ou equivalente);
  - onde guardar o id do aparelho, que pode ser pedido de novo quando faltar;
  - o que fazer com faixas locais ou episódios que aparecerem na fila.
