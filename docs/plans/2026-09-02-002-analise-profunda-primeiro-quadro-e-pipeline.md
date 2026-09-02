# 2026-09-02 — Aprofundamento: primeiro quadro do WebView2 + garantia do instalador

> Continuação de `2026-09-02-001-fix-primeiro-quadro-webview2-sessao.md`.
> Duas perguntas: **(A)** existe algo melhor que o `force_repaint` de 8 s? **(B)** dá para
> garantir que o instalador saído do Distribution Studio esteja sempre correto?
>
> Tudo marcado como *verificado* foi lido no código (arquivo:linha). O que é hipótese está
> marcado como **hipótese — medir**.

---

## Resumo executivo

| | |
|---|---|
| **A** | O fix funciona, mas é **frágil por construção**: 8 s fixos contra um boot cujo pior caso passa de 15 s. Há três alternativas mais baratas, e uma delas ataca a causa em vez do sintoma. |
| **B** | O Studio **não pode** garantir instalador correto hoje. Não é falha da barra nativa: o build-base é cacheado **só por `VERSION`**, e `VERSION` está em `1.0.0` desde sempre. O Studio nunca instala o que gera, e o self-test é estruturalmente cego a essa classe de bug. |
| **Sobre a sua intuição** | Você estava certo ao ligar o problema ao Studio — mas pelo motivo inverso do que parecia. A barra nativa **criou** o bug de pintura; o Studio é o que **impede de detectá-lo** antes de entregar. São dois problemas independentes que se somaram. |

---

# Parte A — O bug de pintura

## A.1 A cadeia real de eventos na abertura (verificada)

O documento anterior diz "o WebView2 compõe o primeiro quadro enquanto a janela ainda está sendo
maximizada". Está certo, mas é mais grave do que isso. A ordem exata:

| # | Onde | O que acontece |
|---|---|---|
| 1 | `webview/platforms/winforms.py:207` | `self.Size = 1440×900 × escala do monitor` → **2160×1350 px físicos** a 150% |
| 2 | `winforms.py:235` | `WindowState = Maximized` — apenas **armazenado**, a janela ainda não foi exibida |
| 3 | `winforms.py:270` | `FormBorderStyle = None` (frameless) |
| 4 | `winforms.py:275` → `edgechromium.py:95-120` | **O controle WebView2 é criado aqui**: `Controls.Add`, `Dock = Fill`, `EnsureCoreWebView2Async` |
| 5 | `winforms.py:775` | `before_show` → nosso [`_on_before_show`](../../main.py#L536): `attach()` (estilos + WNDPROC + `SWP_FRAMECHANGED`) e `apply_default_geometry()` |
| 6 | `winforms.py:779` | `browser.Show()` → **agora sim** a maximização acontece |

**O ponto que muda o diagnóstico:** o WebView2 nasce no passo 4, ou seja, **antes** do nosso
`attach` e **antes** da maximização. Ele aloca a primeira superfície para um viewport de
~2160×1350 px — **maior que a tela inteira** (área útil medida: 1920×1128). Depois disso a área
cliente muda mais duas vezes:

```
2160×1350  (nasce)                     ← superfície inicial, viewport que nunca existirá
   ↓  attach → SWP_FRAMECHANGED → WM_NCCALCSIZE com inset de borda
2160×1350 − 2×~11 px                   ← encolhe pela espessura do frame
   ↓  Show() → maximize → WM_NCCALCSIZE sem inset (_is_full_surface)
1920×1128                              ← −11% em largura, −16% em altura
```

**Três redimensionamentos do host antes do primeiro quadro**, o último deles grande. É nessa
janela de tempo que as *shared images* se perdem.

> Detalhe que fecha o sintoma: `background_color="#0D1117"` vira o `DefaultBackgroundColor` do
> controle (`edgechromium.py:106`). O preto observado **é** essa cor. Não é "tela quebrada" — é a
> superfície ausente deixando o fundo do controle à mostra.

## A.2 Por que o fix atual é frágil — três motivos, em ordem de gravidade

### A.2.1 Oito segundos fixos contra um boot sem teto (o problema real)

`FIRST_PAINT_REPAINT_DELAY = 8.0` ([main.py:78](../../main.py#L78)) conta a partir do `loaded`,
que é o **DOM pronto** — não o dashboard montado. O que roda depois do `loaded`:

```js
// frontend/js/app.js:89
await _bootSync();          // app.js:493 → maxAttempts = 10, delayMs = 1500
// → pior caso ~15 s só de espera, mais 10 idas ao servidor embutido
await API.getActiveEvent();
await _enterActiveMode(event);   // é aqui que mapa, sites e alarmes são pintados
```

E o servidor embutido pode demorar: `_wait_server_ready(timeout=45.0)` existe justamente porque
*"a 1ª execução do .exe onefile pode levar dezenas de segundos"* ([main.py](../../main.py#L342)).

**Portanto:** numa instalação nova — exatamente a máquina do operador, exatamente o cenário que
importa — o `force_repaint` dispara em t+8 s, quando as camadas que se perdem **ainda não foram
criadas**. Ele refaz uma superfície vazia e não faz nada de útil. O bug volta, e o log não acusa
nada.

É a mesma armadilha do §9.3 anterior (fix que "funciona" no ambiente de quem desenvolve e não no
de quem usa), com a diferença de que agora dá para prever por quê.

### A.2.2 `force_repaint` não verifica nada

[`force_repaint`](../../core/window_chrome.py#L932) retorna `bool`, mas o `threading.Timer` de
[main.py:578](../../main.py#L578) descarta o retorno. Se a janela não estiver maximizada, retorna
`False` em silêncio. **Não existe uma linha de log dizendo que o ciclo rodou** — e a regra 9.1 da
sessão anterior ("capturar estado sempre com leitura") foi escrita para o harness de teste, mas
não foi aplicada ao código de produção.

### A.2.3 Uma tentativa só

Não há segunda chance nem verificação posterior. Se os 8 s caírem no lugar errado, acabou.

## A.3 Alternativas não testadas, em ordem de retorno

O §7 anterior descartou 7 alternativas por medição — o trabalho está bem-feito e não vale
repetir. Mas as 7 são todas **reparo depois do fato** ou flags de GPU. Nenhuma mexe na causa
identificada em A.1. Estas quatro são novas:

### 1. Nascer no tamanho certo — ataca a causa, custo quase zero

Trocar `width=1440, height=900` ([main.py:491](../../main.py#L491)) pela área útil do monitor
**em pixels lógicos** (1280×752 no monitor medido). A janela continua nascendo maximizada; o que
muda é que a superfície inicial do WebView2 passa a ter praticamente o tamanho final, e o grande
redimensionamento do passo 6 vira ruído de ~11 px de borda.

- Não mexe em `maximized=True` → **não regride o `fitToEvent`** (era o bloqueio do §5).
- Não pisca a tela.
- O material para calcular já existe: `SPI_GETWORKAREA` em
  [`_workspace_origin`](../../core/window_chrome.py#L615) e `system_dpi()`.
- **Hipótese — medir:** reduzir o Δ pode não bastar sozinho. Mas mesmo que não elimine, reduz a
  probabilidade da corrida e é compatível com manter o `force_repaint` como rede.

### 2. Disparo por sinal do frontend, com o timer virando **teto**

Expor `window_first_paint_ready()` e chamá-la do `app.js` ao fim de `_enterActiveMode()` /
`_enterStandbyMode()`, depois do `requestAnimationFrame` que fecha o layout. O timer de 8 s
continua existindo, mas como **limite superior** — não como estimativa.

Resolve A.2.1 por completo: o ciclo passa a acontecer *depois* do render pesado, seja ele aos 6 s
ou aos 40 s.

> O §9.3 registra que um gatilho de frontend "nunca disparava". A causa provável está em
> `_bootSync` consumindo as 10 tentativas. Vale conferir — se for isso, o gatilho é confiável
> desde que colocado **fora** do caminho que depende do servidor (ex.: também no
> `_enterStandbyMode`, que roda antes do `await _bootSync()`).

### 3. Alternar a visibilidade do **controle** WebView2, não da janela

O §7 testou `opacity` e `display` **em JS** (dentro do documento) e resize do HWND filho via
`SetWindowPos`. Não testou a camada do meio: o controle WinForms.

```python
control = _webview_control(window)   # já existe em core/window_chrome.py:1320
control.Visible = False
control.Visible = True
```

É a camada em que o compositor efetivamente recria a árvore visual, sem tocar no HWND de topo. Se
funcionar, substitui o `SW_RESTORE`/`SW_MAXIMIZE` **e elimina o piscar da janela** — o custo que o
§5 aceitou.

- **Hipótese — medir.** Pode não recuperar mailboxes já perdidas. Barato de testar: o helper
  `_webview_control` já existe.

### 4. Adiar a navegação, não a janela

Criar a janela com `about:blank` e chamar `window.load_url(frontend_url)` depois do evento `shown`
mais um settle curto. O primeiro documento passa a renderizar já com a geometria final.

- Mais invasivo que (1) e (2); mexe no `?chrome=custom#desktop`, que
  [window_chrome.js:41](../../frontend/js/window_chrome.js#L41) lê para decidir o layout.
- Guardar como plano C.

### O que não vale tentar de novo

`--disable-gpu*` e `--disable-features=*` já foram medidos: pioras ou empates. E o canal de
entrega dessas flags comprovadamente funciona (`--disable-gpu` mudou o comportamento de forma
visível), então não há suspeita a levantar ali.

---

# Parte B — Por que o instalador do Studio não é garantido

Aqui está a resposta à sua observação de que "antes da barra nativa não havia problema com
instaladores, mas o processo não passava pelo Distribution Studio". As duas coisas são verdadeiras
e **independentes**: a barra criou um bug de runtime; o Studio criou um pipeline que não consegue
vê-lo. Cinco lacunas, em ordem de gravidade.

## B.1 O build-base é cacheado só por `VERSION` — e `VERSION` nunca muda ⚠️ crítico

Verificado:

- `VERSION` = `1.0.0`.
- [build.py:305](../../build.py#L305): se `dist/base/<VERSION>` existe e confere, `build.py base`
  **retorna sucesso sem recompilar nada**.
- O Studio nunca chama o PyInstaller:
  [`_compile_setup`](../../core/distribution_service.py#L880) faz `load_base(ep.app_version(), …)`
  e só recompila o Setup.
- Manifesto atual em disco (`dist/base/1.0.0/base-manifest.json`):

```json
{ "source_commit": "df99977…", "source_dirty": true,
  "executable_sha256": "f164c836…", "built_at_utc": "2026-09-02T04:41:21Z" }
```

**Consequência:** qualquer alteração em Python ou no frontend que não venha acompanhada de um
`build.py base --force` (ou de um bump em `VERSION`) é **invisível para o Studio**. Ele continuará
gerando instaladores com o binário de 02/09 04:41 indefinidamente, sem um único aviso — e a
verificação de integridade vai passar, porque o cache está íntegro; ele só está velho.

É a falha mais perigosa das cinco: falha em silêncio e produz um artefato que parece correto.

## B.2 O Studio nunca instala o que gera

- [`smoke_test_setup`](../../build.py#L500) — instalação silenciosa isolada, conferência do
  `--self-test`, checagem dos eventos e desinstalação — existe **só** no CLI e **só** atrás de
  `--smoke` ([build.py:750](../../build.py#L750)), que não é o padrão.
- A etapa chamada `testing` no Studio é [`inspect_setup`](../../core/base_build.py#L621), cujo
  próprio docstring diz: *"confere o que foi produzido, **sem instalar nada**"*. Ela verifica hash
  do pacote, tamanho do Setup e log do ISCC. Nada mais.

A decisão de não instalar na máquina do operador está certa. O que falta é **um lugar onde
instalar**: nada impede um `--smoke` opcional na máquina de quem distribui.

## B.3 O self-test é estruturalmente cego a esta classe de bug

[`run_window_chrome_probe`](../../core/self_test.py#L187) monta a janela assim:

```python
webview.create_window(..., html="<html><body>SmartEvents window chrome</body></html>",
                      hidden=True, frameless=True, width=600, height=400)
```

Ou seja: **oculta**, **600×400**, **nunca maximizada**, com HTML trivial e **sem olhar um único
pixel**. Ela prova que `attach`/`set_regions`/`detach` funcionam — e não tem como falhar quando o
app abre com faixas pretas. Isso explica exatamente o §3 anterior: "self-test 100% verde" enquanto
o app estava visivelmente errado.

**O que faltava para pegar o bug:** uma janela maximizada, com o frontend real, e uma leitura de
pixels da área cliente reprovando quando uma fração alta deles for exatamente `#0D1117`. É o mesmo
`PrintWindow` que já foi usado à mão na investigação — só que dentro do diagnóstico.

## B.4 A proveniência existe, mas não chega à tela

`build.summary()` ([core/base_build.py:225](../../core/base_build.py#L225)) devolve
`source_commit` e `source_dirty`, e eles chegam intactos em `capabilities()["base"]`. Mas
[distribution_studio.html:835](../../tools/distribution_studio.html#L835) só imprime versão e
data:

```
Build-base 1.0.0 de 2026-09-02T04:41:21Z
```

Quem gera o instalador não tem como saber que aquele build-base veio de árvore suja, nem de qual
commit. É o §8.3 anterior — mas como **propriedade permanente da interface**, não como acidente
daquela build.

## B.5 `build.py base` aceita árvore suja sem dizer nada

`_source_state()` ([build.py:116](../../build.py#L116)) registra `dirty` no manifesto e segue em
frente. Não há aviso no terminal nem flag para exigir árvore limpa.

---

# Parte C — O que eu faria, em ordem

| # | Ação | Resolve | Custo | Risco |
|---|---|---|---|---|
| 1 | `build.py base`: avisar em árvore suja e exigir `--allow-dirty`; imprimir o commit ao fim | B.5 | ~10 linhas | nenhum |
| 2 | Studio: mostrar `source_commit` curto e um selo **"árvore suja"** no bloco do build-base | B.4 | ~15 linhas HTML | nenhum |
| 3 | **Detecção de build-base obsoleto**: comparar `source_commit` do manifesto com o `HEAD` atual e degradar o formato "Instalador completo" para aviso explícito quando divergirem | **B.1** | médio — `describe()` já recebe `repo` | baixo |
| 4 | Novo check no `--self-test`: janela **maximizada**, frontend real, leitura de pixels reprovando se >X% for `#0D1117` | **B.3** | médio-alto | precisa calibração para não dar falso positivo |
| 5 | `force_repaint` disparado por sinal do frontend, com os 8 s virando teto | **A.2.1** | pequeno | baixo |
| 6 | Janela nascer no tamanho da área útil (A.3.1) | causa raiz de A | pequeno | baixo — medir o `fitToEvent` |
| 7 | Medir o toggle de `control.Visible` (A.3.3) e, se funcionar, trocar o ciclo restaurar/maximizar | o piscar da tela | pequeno | é experimento |
| 8 | Logar `IsZoomed` + retângulo antes/depois em `force_repaint` | A.2.2 | trivial | nenhum |

**Se der para fazer só três:** 3, 5 e 1. O item 3 impede a repetição do erro que você descreveu; o
5 faz o fix atual valer na máquina do operador; o 1 custa dez linhas.

**Ordem sugerida:** 1 e 8 (triviais) → 5 e 6 (fecham a frente A) → 3 e 2 (fecham a frente B) → 4
(o mais caro, mas é o único que transforma "self-test verde" numa afirmação com valor) → 7
(experimento opcional).

---

## Observação de método

O erro da sessão anterior não foi técnico: o diagnóstico do §3 é exemplar. O erro foi confiar num
diagnóstico verde que nunca teve como ficar vermelho. Enquanto o `--self-test` não abrir uma
janela maximizada com o frontend real, "self-test aprovado" não é evidência de que o instalador
está correto — é evidência de que o empacotamento está completo, que é outra coisa.
