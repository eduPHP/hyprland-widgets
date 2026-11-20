# Aviso de versão nova

**Por quê:** o hyprspot é instalado com `git clone`, então quem usa não fica sabendo quando sai uma versão nova.

**O que faz:** quando o widget abre, consulta a última release do GitHub (`eduPHP/hyprspot`). Se a versão dela for maior que `hyprspot.__version__`, mostra uma notificação do sistema:

- título: "hyprspot X.Y.Z disponível";
- corpo: a versão instalada e o comando para atualizar (`git -C <pasta da instalação> pull`);
- botão "Ver novidades", que abre a página da release (clicar na notificação faz o mesmo).

**Regras:**
- Verifica só ao iniciar o widget. Não verifica de novo enquanto ele está aberto, nem nos comandos de linha (`next`, `toggle` etc.).
- Falha de rede, limite da API do GitHub, repositório sem release ou resposta estranha: não faz nada e não mostra erro.
- Tags `v1.2.3` e `1.2.3` são aceitas. A comparação é numérica, então `0.10.0` é maior que `0.9.0`. Tags que não são números são ignoradas.
- O endpoint `releases/latest` já ignora rascunhos e pré-releases.

**Abordagens consideradas:**
- *Release do GitHub contra `__version__`* (escolhida pelo usuário). Avisa só quando uma versão é publicada de propósito.
- *Qualquer commit novo na `main`.* Avisaria a cada correção pequena e precisaria saber o commit instalado.

**Fora do escopo:** atualizar sozinho, um comando `hyprspot update`, lembrar de avisos já dispensados.

**Para funcionar:** é preciso publicar releases com tags no formato acima. Hoje o repositório ainda não tem nenhuma.
