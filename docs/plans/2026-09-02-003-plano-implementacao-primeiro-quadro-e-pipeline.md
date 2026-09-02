# 2026-09-02 — Plano de implementação: primeiro quadro do WebView2 + garantia do instalador

> Deriva de `2026-09-02-002-analise-profunda-primeiro-quadro-e-pipeline.md`.
> Sete fases, em duas frentes independentes. A frente A conserta o app; a frente B conserta o
> pipeline que deveria ter pegado o erro. **As duas frentes não se tocam** — podem ser feitas em
> ordens diferentes, por sessões diferentes.

---

## Por que sete fases e não uma

Cada fase abaixo é independentemente entregável e independentemente verificável: dá para parar
depois de qualquer uma delas e o repositório fica num estado coerente. O que **não** dá é
misturar, por três razões concretas:

1. A Fase 1 é o instrumento de medida das Fases 2, 3 e 4. Sem ela, "funcionou" volta a ser opinião
   — foi exatamente assim que a sessão anterior perdeu a maior parte do tempo (§9.1 e §9.3).
2. A Fase 3 tem risco de regressão visual (`fitToEvent`) que nenhuma das outras tem. Precisa poder
   ser revertida sozinha.
3. A frente B mexe em `build.py`, no serviço e no HTML do estúdio — validação por testes e por
   tela, sem nenhuma relação com a validação da frente A, que é observação de janela real.

| Fase | Frente | Item da análise | Tamanho | Risco |
|---|---|---|---|---|
| 1 — Instrumentação do `force_repaint` | A | 8 | trivial | nenhum |
| 2 — Gatilho real do primeiro quadro | A | 5 | pequeno | baixo |
| 3 — Janela nasce no tamanho certo | A | 6 | pequeno | **médio** (`fitToEvent`) |
| 4 — Experimento: visibilidade do controle | A | 7 | pequeno | é experimento — **condicional** |
| 5 — Gate de árvore suja no `build.py base` | B | 1 | trivial | nenhum |
| 6 — Build-base obsoleto e proveniência no estúdio | B | 3 + 2 | médio | baixo |
| 7 — Self-test que enxerga o primeiro quadro | B | 4 | **alto** | médio (calibração) |

**Ordem recomendada:** 1 → 2 → 5 → 6 → 3 → 7 → (4, se necessário).
As Fases 1, 2 e 5 podem ser feitas hoje; a 6 é a que impede a repetição do erro que motivou tudo.

---

## Regras válidas para todas as fases

Vêm do §9 da sessão anterior e não são formalidade — foram elas que custaram o tempo daquela vez.

- **Ambiente isolado obrigatório** em qualquer validação com janela real:
  `SMARTEVENTS_DATA_DIR` apontando para uma cópia dos dados, e **nenhuma outra instância aberta**
  (instâncias concorrentes compartilham o `storage_path` do WebView2 e servem o frontend umas das
  outras).
- **Nunca medir estado com uma chamada que muda esse estado.** `IsZoomed` e `GetWindowRect` são
  leitura; `ShowWindow` não é.
- **Suíte completa antes e depois** de cada fase: `python -m pytest -q`. A linha de base atual é
  855 passed / 10 skipped, com uma falha pré-existente conhecida e não relacionada em
  `tests/test_frontend_alerts_alarms_filter_ui.py::test_filtro_de_alarmes_e_alertas_respeita_o_site_selecionado`
  (falha também em `HEAD` limpo — §8.1).
- **Ao fim de cada fase**, apender em `MEMORY.md` (decisão + data + porquê) e, se algo tiver
  quebrado no caminho, em `ERRORS.md` (causa raiz + regra de prevenção). É o §5 do `CLAUDE.md`.
- **Commitar antes de gerar instalador.** O manifesto grava `source_commit`; instalador gerado de
  árvore suja tem rastro enganoso (§8.3) — e a partir da Fase 5 isso passa a ser bloqueado.

---

# FRENTE A — O app

## Fase 1 — Instrumentação do `force_repaint`

### O que é

Hoje o ciclo que conserta a pintura roda **em silêncio**: o `threading.Timer` descarta o retorno
de `force_repaint()`, e quando a janela não está maximizada a função devolve `False` sem escrever
uma linha no log. Não há como saber, olhando o log de uma máquina de operador, se o conserto
rodou, quando rodou, ou se fez alguma diferença.

Esta fase não muda comportamento nenhum. Ela só faz o app **contar o que fez** — e é o que torna
as Fases 2, 3 e 4 verificáveis em vez de opináveis.

### Escopo detalhado

**Código — [`core/window_chrome.py`](../../core/window_chrome.py#L932)**

- `force_repaint()` passa a registrar, em `INFO`, uma linha antes e uma depois do ciclo, contendo
  **apenas leituras**: `is_zoomed`, `get_window_rect` e `get_client_size`, mais o tempo decorrido.
- O caminho de saída antecipada (janela não maximizada) deixa de ser mudo: passa a registrar em
  `INFO` que o ciclo foi dispensado e por quê (o estado lido).
- `diagnostic()` ganha os campos `last_repaint_at` (ISO-8601 UTC, ou `None`) e `repaint_count`.
  Não entram em `get_state()`: a barra de título consulta esse método a cada 500 ms e não tem uso
  para os campos.

**Código — [`main.py`](../../main.py#L578)**

- O callback do timer deixa de ser `chrome_controller.force_repaint` direto e passa a ser uma
  função nomeada que consome o retorno e registra `WARNING` quando o ciclo não acontece. Um
  conserto que não rodou precisa aparecer no log do operador.

**Interface**

Nenhuma alteração.

**Testes — [`tests/test_window_chrome.py`](../../tests/test_window_chrome.py)**

Usando o `FakeAdapter` e o helper `attached_controller()` já existentes:

- `test_force_repaint_records_the_window_state_before_and_after` — com `caplog`, garante que as
  duas linhas saem e que **nenhuma** chamada de escrita (`show_window`) foi usada para medir.
- `test_force_repaint_reports_when_it_is_skipped` — janela não maximizada: retorna `False`, não
  chama `show_window`, e **registra o motivo**.
- `test_diagnostic_reports_the_last_repaint` — `repaint_count` sobe de 0 para 1 e
  `last_repaint_at` deixa de ser `None`.

Os dois testes existentes (`test_force_repaint_rebuilds_the_surface_with_a_restore_maximize_cycle`
e `test_force_repaint_does_nothing_when_the_window_is_not_maximized`) continuam passando sem
alteração — é o sinal de que a fase não mudou comportamento.

### Como validar e testar

**Testes**

```bash
python -m pytest tests/test_window_chrome.py -q      # esperado: 74 passed (71 + 3)
python -m pytest -q                                   # suíte completa, sem novas falhas
```

**Visual / manual** — com `SMARTEVENTS_DATA_DIR` isolado e nenhuma outra instância aberta:

```bash
python main.py
```

Depois de fechar, abrir `<SMARTEVENTS_DATA_DIR>/logs/smart_events.log` e confirmar que existem as
duas linhas do ciclo, com retângulos e `is_zoomed` iguais antes e depois (o ciclo restaura e
re-maximiza: a geometria final tem de ser idêntica à inicial).

**Critério de aceite:** o log responde, sem ambiguidade, a três perguntas — o ciclo rodou? em que
instante? a janela voltou ao mesmo retângulo?

### Commit sugerido

```
feat: log the window state around the first-paint repaint cycle
```

---

## Fase 2 — Gatilho real do primeiro quadro

### O que é

O conserto dispara 8 segundos depois do evento `loaded`, que é o **DOM pronto** — não o dashboard
montado. Entre um e outro roda `await _bootSync()`, que tenta 10 vezes com 1,5 s de intervalo
(~15 s no pior caso) e depende do servidor embutido, que numa instalação nova pode levar dezenas
de segundos para responder.

Ou seja: na máquina do operador, na primeira abertura depois de instalar — o único cenário que
realmente importa — o conserto pode disparar **antes** de existirem as camadas que se perdem, e
não fazer nada.

Esta fase inverte a lógica: quem manda consertar é o frontend, quando o dashboard termina de se
montar. Os 8 s viram um **teto de segurança**, não uma estimativa.

### Escopo detalhado

**Código — `core/window_chrome.py`**

- Novo `force_repaint_once()`: delega para `force_repaint()` protegido por uma flag interna, sob o
  mesmo lock. Chamadas seguintes retornam `False` sem tocar na janela. É o que impede o sinal do
  frontend e o timer de teto de dispararem dois ciclos.
- `force_repaint()` fica como está — os dois testes de regressão da sessão anterior continuam
  valendo sobre ela sem alteração.

**Código — `main.py`**

- `FIRST_PAINT_REPAINT_DELAY = 8.0` vira `FIRST_PAINT_REPAINT_CEILING = 25.0`, com o porquê no
  comentário: 15 s de pior caso do `_bootSync` mais margem para o servidor embutido. O nome muda
  porque o papel muda — deixou de ser estimativa e virou limite.
- Nova função exposta `window_first_paint_ready()`, registrada no `window.expose(...)` junto das
  outras, chamando `chrome_controller.force_repaint_once()`.
- O timer passa a chamar `force_repaint_once` também. Quem chegar primeiro vence; o segundo é
  descartado pela flag.

**Código — `frontend/js/bridge.js`**

- `window_first_paint_ready` entra em `_windowMocks` (registra a chamada em
  `_windowChromeMock.calls`, retorna `true`) e é exportado como `windowFirstPaintReady`.

**Código — `frontend/js/app.js`**

- `boot()` ganha um `try { ... } finally { ... }`, e o sinal é emitido **no `finally`**, dentro de
  um `requestAnimationFrame`. O `finally` não é preciosismo: `boot()` hoje não tem tratamento de
  erro, e uma exceção em `API.getActiveEvent()` mataria o gatilho — que é exatamente o que
  aconteceu no §9.3 da sessão anterior. A chamada em si vai envolvida em `try/catch` próprio: um
  erro de shell nunca pode derrubar o boot.

**Código — `frontend/index.html`**

- Bumpar o `?v=` de `js/app.js`.
- **Além disso:** `app.js` importa `./bridge.js` e `./state.js` **sem token de versão**, ao
  contrário de todos os outros módulos. Como esta fase altera `bridge.js`, o token entra também
  nesse import — senão a alteração pode simplesmente não chegar ao operador. É a pista do §8.4
  registrada como suspeita não confirmada.

**Interface**

Nenhuma mudança visível ao usuário. O que muda é *quando* o piscar único da abertura acontece:
passa a coincidir com o fim da montagem do dashboard, em vez de um instante arbitrário.

**Testes**

- `tests/test_window_chrome.py`:
  - `test_force_repaint_once_runs_a_single_cycle_even_when_called_twice` — duas chamadas, um só
    par `SW_RESTORE`/`SW_MAXIMIZE`.
  - `test_force_repaint_once_lets_the_ceiling_timer_lose_the_race` — simula a ordem inversa
    (timer antes do sinal) e garante o mesmo resultado.
- `tests/test_frontend_window_chrome_ui.py`:
  - teste estrutural: `bridge.js` exporta `windowFirstPaintReady` e o mock responde ao método.
  - teste estrutural: `app.js` emite o sinal dentro de um bloco `finally`.
  - teste de Playwright: no fim do boot em modo mock, `window.__windowChromeMock.calls` contém
    exatamente **uma** entrada `window_first_paint_ready`.

### Como validar e testar

**Testes**

```bash
python -m pytest tests/test_window_chrome.py tests/test_frontend_window_chrome_ui.py -q
python -m pytest -q
```

**Visual / manual** — o ponto desta fase é o *momento*, e é por isso que a Fase 1 vem antes:

1. Ambiente isolado, nenhuma outra instância. Abrir o app com o evento grande (Rock in Rio).
2. No log, comparar o instante da linha `Interface carregada` com o instante do ciclo de repintura.
   **O ciclo tem de acontecer depois de o dashboard estar montado na tela**, não 8 s após o
   `loaded`.
3. Confirmar que o ciclo aconteceu **uma vez só**: `repaint_count == 1`.
4. Simular boot lento (desligar a VPN, ou apontar `server_url` para um endereço morto) e repetir:
   o `_bootSync` esgota as 10 tentativas, e o sinal ainda assim chega — depois, não antes.
5. Abrir 3 vezes seguidas e confirmar que **nenhuma** apresenta faixas pretas no cabeçalho, no
   mapa ou no painel de VIPs. Passar o mouse sobre as três regiões: nada pode ficar preto.

**Critério de aceite:** 3 aberturas limpas em 3, com o ciclo registrado *depois* da montagem do
dashboard, inclusive no cenário de boot lento.

### Commit sugerido

```
feat: trigger the first-paint repaint from the frontend instead of a fixed delay
```

---

## Fase 3 — A janela nasce no tamanho certo

### O que é

Esta é a única fase que ataca a **causa** em vez do sintoma.

O controle WebView2 é criado dentro do construtor do formulário WinForms — antes do nosso
`attach` e antes da maximização. Ele aloca a primeira superfície a partir de
`width=1440, height=900` **em pixels lógicos**, que a 150% viram 2160×1350 físicos: um viewport
maior que a tela inteira. Depois disso a área cliente encolhe duas vezes até 1920×1128.

Pedir a janela já no tamanho da área útil faz a superfície inicial nascer praticamente do tamanho
final. O grande redimensionamento desaparece; sobra o ruído da borda (~11 px por lado).

> **Isto é hipótese com fundamento, não certeza.** Reduzir o Δ pode não eliminar a corrida
> sozinho. Por isso vem depois da Fase 2: o `force_repaint` continua ali como rede, e o que se
> mede aqui é se ele ainda é necessário.

### Escopo detalhado

**Código — `core/window_chrome.py`**

- Nova função pura `work_area_logical_size(dpi, work_width_px, work_height_px, min_size)` que
  converte pixels físicos em lógicos (`px * 96 / dpi`) e aplica o piso do `min_size`. Pura de
  propósito: é o que permite testá-la fora do Windows, como o resto do módulo.
- Novo `default_window_size(min_size=(1024, 600))` que lê `SPI_GETWORKAREA` e `system_dpi()`,
  chama a função pura e devolve `tuple[int, int]` ou `None` quando qualquer coisa falhar
  (não-Windows, API ausente, retorno degenerado). **Nunca levanta.**

**Código — `main.py`**

- No modo de moldura customizada, `width`/`height` do `create_window` passam a vir de
  `default_window_size()`, com `1440×900` como fallback quando ela devolve `None`.
- **Só no modo customizado** — `--native-titlebar` continua com o comportamento anterior, pela
  mesma razão que `_setup_per_monitor_dpi()` já é condicional: preservar o rollback.

**Imprecisão aceita, e documentada no código:** `SPI_GETWORKAREA` responde sobre o monitor
primário. Se a janela abrir num monitor secundário com DPI diferente, o tamanho pedido não bate
exatamente. Não é problema: o objetivo é reduzir o Δ, não acertar o pixel, e `maximized=True`
corrige o resto.

**Interface**

Nenhuma alteração no modo maximizado, que é como o app abre. O que muda é o tamanho **restaurado**
que o Windows guarda antes de `apply_default_geometry` sobrescrevê-lo — nada visível.

**Testes — `tests/test_window_chrome.py`**

- `test_work_area_logical_size_converts_physical_pixels_at_150_percent` — 1920×1128 a 144 DPI
  vira 1280×752.
- `test_work_area_logical_size_never_goes_below_the_minimum` — área útil minúscula não produz
  janela menor que 1024×600.
- `test_work_area_logical_size_rejects_a_degenerate_work_area` — largura ou altura ≤ 0.
- `test_default_window_size_is_none_outside_windows` — com `os.name` monkeypatchado.

### Como validar e testar

**Testes**

```bash
python -m pytest tests/test_window_chrome.py -q
python -m pytest -q
```

**Visual / manual — esta é a fase com risco de regressão, e a validação visual é obrigatória:**

1. **Zoom do mapa (o risco).** Abrir o evento Rock in Rio e confirmar que o mapa abre enquadrando
   **a área do evento**, e não a região metropolitana. Foi exatamente essa a regressão que fez a
   alternativa "abrir restaurada e maximizar depois" ser descartada no §5 — e é por isso que aqui a
   janela continua nascendo maximizada. Se o mapa abrir aberto demais, **reverter a fase**.
2. Repetir com um evento pequeno (3 sites) — o enquadramento também tem de continuar correto.
3. **Pintura.** 3 aberturas limpas em 3, mesma inspeção da Fase 2 (cabeçalho, mapa, painel de VIPs,
   hover).
4. **Medição do que interessa:** com o log da Fase 1, verificar o retângulo registrado. Se as três
   aberturas ficarem limpas, repetir com o `force_repaint_once` desativado temporariamente
   (comentando o agendamento) e observar 5 aberturas. Se as 5 ficarem limpas, o problema foi
   resolvido na raiz e o ciclo pode virar rede de segurança — **mas isso é conclusão para o
   relatório, não para remover código nesta fase**.
5. Se houver segundo monitor com escala diferente, abrir uma vez com a janela nele.

**Critério de aceite:** enquadramento do mapa idêntico ao de antes da fase, em evento grande e
pequeno, e 3 aberturas limpas em 3.

### Commit sugerido

```
feat: size the window to the monitor work area before the first frame
```

---

## Fase 4 — Experimento: visibilidade do controle WebView2 (condicional)

### O que é

O conserto atual custa um piscar da janela inteira (`SW_RESTORE` → `SW_MAXIMIZE`), custo que o §5
aceitou conscientemente. Existe uma camada intermediária que **nunca foi testada**: o §7 testou
`opacity` e `display` dentro do documento (camada de cima) e o resize do HWND filho via
`SetWindowPos` (camada de baixo), mas não o controle WinForms no meio — que é onde o compositor
efetivamente recria a árvore visual.

Se funcionar, substitui o ciclo e elimina o piscar.

### Gate de decisão — só executar esta fase se

Depois das Fases 2 e 3, numa máquina de perfil de operador (GPU diferente da de desenvolvimento),
**pelo menos 1 em 5 aberturas** ainda apresentar faixas por pintar; **ou** se o piscar da janela
for considerado inaceitável pelo cliente.

Caso contrário, **não implementar**. É experimento, não requisito.

### Escopo detalhado

**Código — `core/window_chrome.py`**

- Novo método privado `_repaint_via_control()`: obtém o controle por
  [`_webview_control`](../../core/window_chrome.py#L1320) — helper que já existe — e alterna
  `control.Visible` para `False` e de volta para `True`, com o mesmo `REPAINT_SETTLE_SECONDS` entre
  os dois passos e fora do lock, pela mesma razão do ciclo atual (`window_get_state` é consultado a
  cada 500 ms).
- `force_repaint()` tenta essa estratégia primeiro e **cai para o ciclo `SW_RESTORE`/`SW_MAXIMIZE`**
  quando o controle não existe, quando a chamada falha, ou quando a estratégia estiver desligada.
- A escolha da estratégia fica numa constante de módulo, não numa flag de linha de comando: é
  decisão de projeto, não opção do operador.

**Interface**

Some o piscar da janela na abertura, se a estratégia for adotada.

**Testes**

Honestamente: **testes não conseguem validar esta fase.** Eles cobrem só a mecânica de
fallback — `FakeAdapter` não tem compositor.

- `test_repaint_falls_back_to_the_window_cycle_when_the_control_is_missing`
- `test_repaint_falls_back_to_the_window_cycle_when_the_control_raises`

A decisão de adotar ou não vem inteira da observação da janela real.

### Como validar e testar

**Testes**

```bash
python -m pytest tests/test_window_chrome.py -q
```

**Visual / manual — é o único juiz aqui:**

1. 5 aberturas com a estratégia ligada, ambiente isolado, evento grande.
2. Registrar, para cada uma: houve faixa por pintar? houve piscar?
3. 5 aberturas com a estratégia desligada (ciclo atual), como controle.
4. **Adotar apenas se** as 5 com a estratégia nova forem limpas **e** sem piscar. Empate técnico
   não justifica a troca — o ciclo atual já está medido e provado.

**Critério de aceite:** 5/5 limpas e sem piscar, ou a fase é descartada e o resultado negativo é
registrado em `MEMORY.md` para ninguém tentar de novo.

### Commit sugerido

*Só se adotada:*

```
feat: rebuild the webview surface by toggling the control instead of the window
```

---

# FRENTE B — O pipeline

## Fase 5 — Gate de árvore suja no `build.py base`

### O que é

`build.py base` grava `source_dirty: true` no manifesto e segue em frente sem dizer nada. Foi
assim que nasceu o build-base que está em disco hoje: `source_commit df99977`, `source_dirty true`
— um commit que não contém o conserto. O binário estava certo, o rastro não.

Dez linhas resolvem: quem quiser buildar de árvore suja diz isso explicitamente.

### Escopo detalhado

**Código — [`build.py`](../../build.py#L116)**

- Nova função `source_state()` que substitui `_source_state()` e **não levanta** quando o `git` não
  existe ou o diretório não é um repositório: devolve `("", False)` e emite aviso. Hoje ela usa
  `subprocess.check_output` cru e derrubaria um build a partir de um tarball.
- Nova função `require_clean_source(allow_dirty: bool) -> tuple[str, bool]`, pura o suficiente para
  ser testada sem rodar o PyInstaller: devolve `(commit, dirty)`, e levanta `SystemExit(EXIT_ARGUMENTS)`
  com mensagem explícita quando a árvore está suja e `allow_dirty` é falso.
- `command_base` chama `require_clean_source(args.allow_dirty)` **antes** de qualquer trabalho
  pesado — não faz sentido descobrir isso depois de 20 minutos de PyInstaller.
- `command_base` e `command_distribution` passam a imprimir o commit (curto) e o estado da árvore
  no resumo final.
- Parser: `--allow-dirty` no subcomando `base`.

**Interface**

Só terminal. Mensagem de recusa no formato já usado pelo arquivo:

```
ERRO: arvore de trabalho suja (3 arquivo(s) modificado(s)).
      O build-base gravaria um source_commit enganoso no manifesto.
      Commite as alteracoes, ou repita com --allow-dirty.
```

**Testes — [`tests/test_distribution_build.py`](../../tests/test_distribution_build.py)**

- `test_base_refuses_a_dirty_worktree` — `require_clean_source(False)` com árvore suja levanta
  `SystemExit` com código 2.
- `test_base_accepts_a_dirty_worktree_when_explicitly_allowed` — com `True`, devolve
  `(commit, True)`.
- `test_source_state_survives_a_directory_without_git` — devolve `("", False)` em vez de explodir.
- Estender o `test_build_exposes_the_three_commands_and_keeps_the_legacy_flag` existente para
  travar a presença de `--allow-dirty`.

### Como validar e testar

**Testes**

```bash
python -m pytest tests/test_distribution_build.py -q
python -m pytest -q
```

**Manual**

1. Com a árvore suja, rodar `python build.py base --force` e confirmar a recusa **imediata**
   (segundos, não minutos) e o código de saída 2:
   ```powershell
   python build.py base --force; $LASTEXITCODE
   ```
2. Repetir com `--allow-dirty` e confirmar que o build começa.
3. Commitar, rodar sem a flag, e confirmar que o resumo final imprime o commit correto.
4. Conferir `dist/base/1.0.0/base-manifest.json`: `source_dirty` tem de ser `false`.

**Critério de aceite:** é impossível produzir um build-base de árvore suja sem ter digitado
`--allow-dirty`.

### Commit sugerido

```
feat: refuse to build the base from a dirty worktree unless explicitly allowed
```

---

## Fase 6 — Build-base obsoleto e proveniência no estúdio

### O que é

**Esta é a fase que impede a repetição do erro que motivou toda a investigação.**

O build-base é cacheado só por `VERSION`. `VERSION` é `1.0.0` e nunca mudou. `build.py base`
retorna sucesso sem recompilar quando `dist/base/1.0.0` existe e confere, e o estúdio nunca chama
o PyInstaller — ele só recompila o Setup por cima do cache.

Consequência: **qualquer alteração em Python ou no frontend sem um `build.py base --force` é
invisível para o estúdio.** Ele segue gerando instaladores com o binário antigo, indefinidamente,
sem um aviso — e a verificação de integridade passa, porque o cache está íntegro; ele só está
velho.

A tela mostra hoje `Build-base 1.0.0 de 2026-09-02T04:41:21Z`. Data não é resposta: ninguém sabe
de cor qual código estava no repositório naquele horário. Commit é.

A peça necessária já existe: `_source_state(repo)` no serviço do estúdio já calcula o `HEAD` atual
e o estado da árvore sem levantar exceção. Falta comparar.

### Escopo detalhado

**Código — [`core/base_build.py`](../../core/base_build.py#L436)**

- `source_state(repo)` sobe para este módulo (movimento literal do que já existe em
  `core/distribution_service.py:193`, mesmo comportamento, mesma tolerância a falha). O serviço
  passa a importá-la, sem mudança nos dois pontos de uso.
- `describe(...)` — que **já recebe `repo`** — passa a devolver, além do que já devolve:
  - `base_source_commit` e `base_source_dirty`, promovidos de `build.summary()` para o topo;
  - `source_commit`: o `HEAD` atual;
  - `base_stale: bool` — verdadeiro quando os dois commits são conhecidos e **diferem**, ou quando
    `base_source_dirty` é verdadeiro;
  - `base_stale_reason: str` — texto pronto para a tela, dizendo qual das duas condições ocorreu.
- Quando o commit atual é desconhecido (sem `git`), `base_stale` é **falso** e o motivo diz que não
  foi possível comparar. Não se acusa o que não se sabe.

**Código — [`core/distribution_service.py`](../../core/distribution_service.py#L360)**

- `capabilities()` repassa os campos novos.
- O formato `full_setup` **continua habilitado** quando o build-base está obsoleto. Bloquear seria
  errado: existe o caso legítimo de gerar deliberadamente a partir de um build-base anterior. O que
  não pode continuar existindo é o silêncio.
- `setup_format` ganha `stale: bool` e `stale_reason: str`.

**Interface — [`tools/distribution_studio.html`](../../tools/distribution_studio.html#L828)**

1. `renderDistributionBaseNote()`: com `base_stale`, o cartão passa de `info` para `warning` e
   passa a mostrar o commit curto, o selo **"árvore suja"** quando for o caso, e a ação
   (`python build.py base --force`).
2. **Confirmação explícita**: com `base_stale`, aparece uma caixa de seleção
   `#distribution-base-stale-ack` — *"Entendo que o build-base não corresponde ao código atual"* —
   e o botão de gerar fica **desabilitado** enquanto ela não for marcada, **somente** para o
   formato `full_setup`. O `.sepack` não é afetado: ele não carrega binário.
3. Tela de sucesso (`renderDistributionJob`, linha 957): a linha `Build-base` passa a mostrar
   também o commit curto e o selo de árvore suja.

> **Versão mais barata, se preferir escalar para baixo:** só o item 1 (cartão de aviso). Resolve o
> silêncio, custa ~15 linhas e nenhuma mudança de fluxo. Perde a garantia de que alguém *leu* — e
> foi um aviso não lido que produziu o instalador desta história. **Recomendo os três.**

**Testes**

- `tests/test_distribution_build.py`:
  - `test_describe_flags_a_base_built_from_another_commit`
  - `test_describe_flags_a_base_built_from_a_dirty_worktree`
  - `test_describe_does_not_flag_a_base_that_matches_head`
  - `test_describe_never_flags_when_the_current_commit_is_unknown`
- `tests/test_distribution_studio_api.py`:
  - `test_capabilities_expose_the_base_commit_and_the_stale_verdict`
  - `test_full_setup_stays_available_when_the_base_is_stale` — obsoleto avisa, não bloqueia.
- `tests/test_distribution_studio_ui.py` (o arquivo já tem os dois estilos, estrutural e Playwright):
  - estrutural: o HTML contém `distribution-base-stale-ack` e o texto do aviso.
  - Playwright, servindo `capabilities` com `base_stale: true`: o botão de gerar nasce
    desabilitado, marcar a caixa o habilita, e trocar o formato para `.sepack` também o habilita.

### Como validar e testar

**Testes**

```bash
python -m pytest tests/test_distribution_build.py tests/test_distribution_studio_api.py tests/test_distribution_studio_ui.py -q
python -m pytest -q
```

**Visual — os três estados precisam ser vistos na tela:**

1. **Obsoleto por commit.** Com o build-base atual em disco (`df99977`) e o `HEAD` num commit
   posterior, abrir o estúdio:
   ```bash
   python tools/distribution_studio.py
   ```
   Esperado: cartão **amarelo**, commit curto visível, caixa de confirmação presente, botão de
   gerar desabilitado no formato "Instalador completo".
2. **Obsoleto por árvore suja.** Rodar `build.py base --force --allow-dirty` com um arquivo
   modificado e reabrir o estúdio. Esperado: mesmo aviso, com o selo **"árvore suja"**.
3. **Em dia.** Commitar tudo, rodar `python build.py base --force`, reabrir. Esperado: cartão
   **azul** de volta, sem caixa de confirmação, botão habilitado — e o commit curto agora batendo
   com `git rev-parse --short HEAD`.
4. Gerar um `.sepack` no estado (1) e confirmar que ele **não** é afetado pelo bloqueio.
5. Gerar um instalador completo no estado (3) e conferir, na tela de sucesso, que a linha
   `Build-base` traz o commit certo.

**Critério de aceite:** é impossível gerar um instalador completo a partir de um build-base que não
corresponde ao código atual sem ter marcado uma caixa dizendo que sabe disso.

### Commit sugerido

```
feat: warn when the cached base build no longer matches the current source
```

---

## Fase 7 — Self-test que enxerga o primeiro quadro

### O que é

O `--self-test` deu **100% verde** enquanto o app abria com faixas pretas na tela. Não foi azar:
[`run_window_chrome_probe`](../../core/self_test.py#L187) monta uma janela `hidden=True`, de
600×400, **nunca maximizada**, com `<html><body>SmartEvents window chrome</body></html>`, e não lê
um único pixel. Ela prova que `attach`/`set_regions`/`detach` funcionam, e não tinha como falhar.

Esta fase acrescenta um diagnóstico que **reproduz o mecanismo** — janela maximizada, sem moldura,
com o controlador anexado como em produção — e depois **olha os pixels**.

### Decisão de projeto: página sintética, não o frontend real

A tentação é carregar `frontend/index.html`. Não é o caminho certo:

- exigiria servidor embutido, banco e um evento — o diagnóstico do build-base é **genérico** por
  construção (`command_base` roda o self-test sem evento nenhum);
- sem evento, o dashboard abre em modo de espera, que é legitimamente quase todo escuro — não há
  limiar de pixel que separe "tela de espera" de "camada perdida" sem calibração frágil.

A prova usa então uma página **sintética** de fundo saturado e alta contagem de camadas
compositadas, ocupando o viewport inteiro. Assim, **qualquer** pixel igual a `#0D1117` é, por
construção, superfície ausente — e não conteúdo legítimo. O limiar deixa de ser calibração e vira
lógica.

**Limitação a declarar no docstring:** a página sintética reproduz o mecanismo, não o dashboard. Se
ela não for pesada o bastante para provocar a corrida, o check passa sem valer nada — e é
exatamente isso que o critério de aceite abaixo verifica.

### Escopo detalhado

**Código — `main.py`**

- Novo modo sem janela principal `--self-test-first-paint`, no mesmo bloco dos outros
  (`--self-test-webview`, `--self-test-window-chrome`), delegando para o probe e devolvendo o
  código de saída.

**Código — `core/self_test.py`**

- `run_first_paint_probe(report_path)`, espelhando a estrutura de `run_window_chrome_probe`:
  - `webview.create_window(..., frameless=True, maximized=True, background_color="#0D1117")`,
    carregando a página sintética por `html=`;
  - `attach` em `before_show` e `arm_nonclient_regions`, **na mesma ordem da produção** — é a ordem
    que cria a corrida;
  - depois do `loaded` e de um settle curto, captura a área cliente com `PrintWindow`;
  - grava o relatório como uma linha JSON no stdout e no arquivo, igual ao probe existente;
  - `detach` antes de destruir a janela; watchdog de 30 s, como o outro probe.
- `background_pixel_fraction(pixels, background) -> float`: **função pura**, recebe o buffer e a
  cor, devolve a fração. Pura para poder ser testada sem janela, sem GPU e fora do Windows — é o
  padrão do módulo.
- `_first_paint()`: roda o probe em subprocesso via `_child_command`, com a mesma tolerância a
  stdout ausente que `_window_chrome_window` já tem (no executável `windowed`, o stdout do filho
  pode não existir e o arquivo é a fonte confiável).
- Limiar: **1%** dos pixels da área cliente. Não é calibração — na página sintética o esperado é
  zero; 1% é folga para antialiasing e bordas.

**Código — `core/self_test.py`, `run_self_test`**

- O novo check `"primeiro quadro do WebView2"` entra **condicionalmente**, sob um parâmetro
  `check_first_paint: bool = False`, ativado pela flag `--first-paint`.
- **Por que condicional:** o check abre uma janela maximizada de verdade. O `--self-test` também
  roda no computador do operador logo depois de instalar (é o que
  `test_wrapper_imports_package_before_self_test` trava), e um lampejo de janela cheia durante a
  instalação é um efeito colateral que ninguém pediu. O check pertence à máquina de quem
  distribui.

**Código — `build.py`**

- `command_base` passa `--first-paint` no self-test que já roda depois do PyInstaller
  ([build.py:365](../../build.py#L365)).
- `smoke_test_setup` passa a exigir o check no relatório: se `checks` não contiver o item do
  primeiro quadro aprovado, o smoke falha.

**Interface**

Terminal e relatório JSON. Uma linha a mais no diálogo de diagnóstico, quando reprovado.

**Testes**

- `tests/test_installer.py`:
  - `test_background_fraction_is_zero_for_a_fully_painted_frame`
  - `test_background_fraction_counts_only_the_exact_background_color`
  - `test_background_fraction_reports_a_fully_lost_surface` — buffer inteiro na cor de fundo → 1.0.
  - `test_first_paint_check_is_absent_unless_requested` — `run_self_test()` sem a flag não inclui o
    check; com ela, inclui.
- `tests/test_distribution_build.py`:
  - `test_base_build_runs_the_self_test_with_the_first_paint_check`
  - `test_smoke_test_requires_the_first_paint_check_to_pass`

### Como validar e testar

**Testes**

```bash
python -m pytest tests/test_installer.py tests/test_distribution_build.py -q
python -m pytest -q
```

**Manual — verde:**

```bash
python main.py --self-test --first-paint --report ./scratch/self-test.json
```

Esperado: código de saída 0, e no relatório o check `"primeiro quadro do WebView2"` com `ok: true`
e a fração de pixels de fundo registrada na mensagem.

**Manual — vermelho. Este passo não é opcional:**

Um diagnóstico que nunca foi visto reprovando não é evidência de nada — é a lição inteira desta
investigação. Antes de fechar a fase:

1. `git stash` das Fases 1–3, ou `git checkout df99977 -- main.py core/window_chrome.py` num
   worktree separado;
2. rodar o probe de novo, na mesma máquina, em ambiente isolado;
3. **o check tem de reprovar.**

Se ele passar com o código anterior ao conserto, a página sintética não é pesada o bastante e
precisa ganhar camadas até reprovar. Enquanto isso não acontecer, a fase **não está pronta** — não
importa quantos testes unitários estejam verdes.

**Critério de aceite:** o check reprova no código pré-conserto e aprova no código atual, na mesma
máquina, com o mesmo comando.

### Commit sugerido

```
feat: fail the self-test when the first webview frame is not fully painted
```

---

## Limpeza — fora das fases

Pendências de higiene que a sessão anterior deixou registradas (§8.2, §8.5, §8.6) e que não
pertencem a nenhuma fase. Fazer quando der, de preferência num commit só:

- Remover as distribuições obsoletas `dist/distributions/rock-in-rio-2026-20260902T012132Z/` e
  `…T012922Z/` (contêm o executável **com** o bug) e a incompleta `…T043638Z/`.
- Decidir o destino das cópias paradas de `MEMORY.md` e `ERRORS.md` em `.claude/` — os arquivos
  vivos são os da raiz — e corrigir o §1 do `ORGANIZACAO.md`, que aponta para o lugar errado.
- Regerar o instalador de produção depois da Fase 2, para o `source_commit` do manifesto apontar
  para o código corrigido.

Sugestão de commit:

```
chore: drop stale distribution artifacts and fix the memory file pointers
```
