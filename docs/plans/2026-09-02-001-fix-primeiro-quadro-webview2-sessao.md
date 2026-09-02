# 2026-09-02 — Primeiro quadro do WebView2: faixas por pintar na abertura

> Registro completo da sessão: o que foi investigado, o que foi corrigido, o que **não** foi
> resolvido e os erros de método cometidos no caminho.
> Entrada técnica resumida em `ERRORS.md` (2026-09-02); decisão travada em `MEMORY.md`.

---

## 1. Resumo executivo

| | |
|---|---|
| **Relato** | "O executável gerado pro Rock in Rio está totalmente quebrado ao abrir." |
| **Causa real** | Corrida na composição do primeiro quadro do WebView2. **Não era o build nem o instalador.** |
| **Correção** | `WindowChromeController.force_repaint()` — ciclo `SW_RESTORE` → `SW_MAXIMIZE` 8 s após o `loaded`. |
| **Verificação** | Baseline falhou **4/4** aberturas; com o fix, **3/3** limpas. Suíte: 855 passed, 10 skipped. |
| **Status** | Corrigido e verificado. Instalador regerado. Pendências de higiene no §8. |

---

## 2. O sintoma

Ao abrir o `.exe` instalado:

- faixas pretas no cabeçalho (área do seletor de evento), no mapa e no painel de VIPs;
- **passar o mouse por cima pintava de preto** o que estava embaixo;
- clicar em **restaurar/maximizar** consertava tudo, de uma vez;
- a severidade variava a cada abertura (às vezes quase perfeito, às vezes metade da tela).

O detalhe do mouse foi o que fechou o diagnóstico: região que fica preta **ao repintar** é camada
não apresentada pelo compositor, não é problema de CSS nem de layout.

---

## 3. O que foi descartado — com evidência, antes de mexer em código

O relato apontava para o instalador. Nada disso se sustentou:

| Hipótese | Como foi descartada |
|---|---|
| Build/empacotamento corrompido | `self-test` do build-base **e** do app instalado: 100% dos checks verdes (WebView2, pythonnet, moldura Win32, Playwright, SQLite, importação do `.sepack`, eventos sincronizados). |
| Frontend faltando ou desatualizado no bundle | Hash SHA-256 de **todos** os arquivos de `frontend/` empacotados: **byte-idênticos** ao fonte. |
| Geometria/DPI errados | Processo em `per-monitor-v2`, DPI 144. Janela maximizada em `(0,0)-(1920,1128)` = exatamente a área útil do monitor. Todos os HWND filhos do WebView2 cobrindo a área cliente. |
| App quebrado/crashando | Log do app instalado **sem um único erro**: coleta rodando, 581 alarmes, 3 VIPs, renovação de sessão OK. |
| Tiles do mapa não carregando | Servidores de tile respondendo: PNG real de 42 KB para São Paulo. |
| Artefato de captura de tela | Reproduzido com `PrintWindow` **e** com captura de tela real; o print do próprio usuário mostra o mesmo padrão. |

**Conclusão:** o app não estava quebrado — estava **mal pintado**. Layout, dados e geometria
sempre estiveram corretos.

---

## 4. Causa raiz

O WebView2 compõe o **primeiro quadro** enquanto a janela ainda está sendo maximizada e o
`WM_NCCALCSIZE` da moldura customizada ainda recalcula a área cliente. As camadas que perdem a
*shared image* nessa janela de tempo **nunca voltam a ser apresentadas**. Dali em diante, toda
repintura daquelas regiões — inclusive a disparada por hover — mostra o `background_color` da
janela (`#0D1117`), que é o preto observado.

**Assinatura no `debug.log`:** 6028 ocorrências de

```
ERROR:gpu\command_buffer\service\shared_image\shared_image_manager.cc:386]
SharedImageManager::ProduceMemory: Trying to Produce a Memory representation from a
non-existent mailbox.
```

Presentes **desde 21/08, em execuções de desenvolvimento** — ou seja, o problema é anterior ao
instalador do Rock in Rio e reproduz igual rodando do fonte quando o primeiro render é pesado o
bastante. O evento grande (muitos sites, marcadores SVG, 441 alarmes) num viewport de 1920×1128 é
o que torna a corrida quase determinística; o evento pequeno de dev (3 sites) não reproduz.

---

## 5. A correção

**`core/window_chrome.py`** — novo `force_repaint()`:

```python
def force_repaint(self) -> bool:
    if self.get_state().get("state") != "maximized":
        return False
    if not self._run_window_action("force_repaint/restore", ... SW_RESTORE):
        return False
    time.sleep(REPAINT_SETTLE_SECONDS)   # 0.5 s
    return self._run_window_action("force_repaint/maximize", ... SW_MAXIMIZE)
```

**`main.py`** — agendado no `loaded`, só no modo de moldura customizada:

```python
FIRST_PAINT_REPAINT_DELAY = 8.0
...
if custom_titlebar:
    threading.Timer(FIRST_PAINT_REPAINT_DELAY, chrome_controller.force_repaint).start()
```

### Decisões de projeto embutidas

- **A janela continua nascendo maximizada** (`maximized=True`). Isso não é detalhe: a alternativa
  "abrir restaurada e maximizar depois do boot" conserta a pintura, mas faz o `fitToEvent` rodar no
  viewport de 1558×922 e o mapa abre mostrando a **região metropolitana inteira** em vez do evento —
  regressão direta na feature de zoom na área monitorada.
- **Os dois passos são ações separadas** de `_run_window_action`: esse método segura o lock do
  controlador, e dormir 0,5 s com o lock na mão travaria o `window_get_state` que a barra de título
  consulta a cada 500 ms.
- **`REPAINT_SETTLE_SECONDS = 0.5` não é enfeite.** Sem a pausa, as duas mensagens se anulam e a
  superfície não é refeita.
- **Sem efeito quando a janela não está maximizada** — ali não houve corrida, e o ciclo só piscaria
  a tela à toa.
- **Custo aceito:** um piscar único da janela na abertura. É exatamente o que o operador já fazia à
  mão.
- **O atraso de 8 s é heurístico.** Medido: em t+10 s o dashboard já está montado e o ciclo devolve
  o quadro completo. Se o boot ficar mais lento, esse número precisa subir junto.

---

## 6. Verificação

| Cenário | Resultado |
|---|---|
| `.exe` original (baseline), perfil isolado | falhou **4/4** aberturas |
| `.exe` recompilado com o fix, perfil isolado | limpo **3/3** aberturas |
| Suíte completa | **855 passed, 10 skipped, 1 deselected** |
| `tests/test_window_chrome.py` | 71 passed (2 testes novos) |
| Binário empacotado × binário testado | SHA-256 confere (`f164c836…`) |

Testes rodados com `SMARTEVENTS_DATA_DIR` isolado (cópia dos dados reais), sem tocar na instância
do operador.

**Testes de regressão adicionados** em `tests/test_window_chrome.py`:

- `test_force_repaint_rebuilds_the_surface_with_a_restore_maximize_cycle` — garante a ordem
  `SW_RESTORE` → `SW_MAXIMIZE`;
- `test_force_repaint_does_nothing_when_the_window_is_not_maximized`.

---

## 7. Alternativas testadas e descartadas — por medição, não por opinião

| Alternativa | Resultado medido |
|---|---|
| `--disable-gpu-compositing` | Melhora muito, mas **sobra** um retângulo preto no topo. |
| `--disable-gpu` | **Pior** que o baseline: preto no topo + faixa grande no mapa. |
| `--disable-features=CalculateNativeWinOcclusion` (suspeito clássico) | **Não muda nada.** |
| Invalidação por JS — toggle de `opacity` | Melhora parcial, sobram vazios. |
| Invalidação por JS — toggle de `display` | **Pior** que o toggle de opacity. |
| Redimensionar o HWND filho do WebView2 via `SetWindowPos` | **Piora** — briga com o layout do WinForms. |
| Abrir restaurada + maximizar no fim do boot | Conserta a pintura, mas **regride o zoom do mapa** (§5). |

Conclusão: a falha é da **superfície do WebView2**, não da árvore de layout da página nem do
pipeline de GPU isoladamente. Só um redimensionamento real do host a reconstrói.

---

## 8. O que NÃO foi resolvido nesta sessão

### 8.1 Teste pré-existente quebrado (não é regressão)

`tests/test_frontend_alerts_alarms_filter_ui.py::test_filtro_de_alarmes_e_alertas_respeita_o_site_selecionado`

Falha **também em `HEAD` limpo** — confirmado com `git stash` das minhas alterações. É um teste
Playwright de UI de filtro de alarmes/alertas, sem relação com esta sessão. **Não investiguei.**

### 8.2 Pasta de distribuição incompleta, de origem desconhecida

`dist/distributions/rock-in-rio-2026-20260902T043638Z/` contém apenas o `.sepack` e o
`distribution.iss` (01:36), com `installer/` vazio e sem `build-manifest.json` — uma execução que
parou antes do ISCC.

Invoquei `build.py distribution` **uma única vez** (01:41, que produziu `044132Z`), e os testes que
rodei naquele horário só fazem *parse* de argumentos, não executam o pipeline. **Não descobri o que
a criou** e não vou inventar explicação.

### 8.3 O instalador foi gerado de árvore suja

`build-manifest.json` registra `source_dirty: True` e `source_commit: df99977` — commit que **não
contém o fix**. O instalador funciona (o binário empacotado é o corrigido, SHA conferido), mas o
rastro de proveniência está enganoso. Commitar e regerar antes de mandar para produção.

### 8.4 Indício não confirmado de cache de frontend

Em determinado momento o app servia o `index.html` com o `?v=` antigo mesmo após eu bumpar o token
e limpar o cache do WebView2 — o que significaria que **atualizações de UI podem não chegar ao
operador**. Porém isso apareceu num ambiente que eu havia contaminado (§9.2), e depois de limpar não
consegui isolar o caso. **Fica como suspeita não confirmada**, vale reproduzir com ambiente limpo.

### 8.5 Cópias obsoletas de MEMORY/ERRORS

`ORGANIZACAO.md` §1 diz que a memória viva está em `.claude/MEMORY.md` e `.claude/ERRORS.md`, mas os
arquivos vivos são os da **raiz** (146 KB e 71 KB, editados hoje). Os de `.claude/` estão parados
desde 16/07 e 11/08. Não mexi em nenhum dos dois lados além de apender nos da raiz — apenas registro
a divergência.

### 8.6 Artefatos antigos com o bug ainda no disco

`dist/distributions/rock-in-rio-2026-20260902T012132Z/` e `…T012922Z/` contêm o executável **com o
bug**. Não removi.

---

## 9. Erros de método cometidos nesta sessão

Registrados porque custaram a maior parte do tempo e são armadilhas reincidentes.

### 9.1 O harness de teste alterava o que media

O script de captura chamava `ShowWindow(SW_MAXIMIZE)` antes do screenshot. Como maximizar **é** a
correção, o próprio script "consertava" o app antes de fotografá-lo: **três verificações
"bem-sucedidas" seguidas foram do script, não do código**. Só apareceu quando comparei o
`IsZoomed` antes e depois.

> **Regra:** capturar estado sempre com leitura (`IsZoomed`/`GetWindowRect`) e **nunca** com uma
> chamada que muda esse estado.

### 9.2 Instâncias concorrentes contaminaram as medições

Deixei ~18 instâncias de teste abertas. Elas compartilham o perfil do WebView2 (`storage_path`) e
chegaram a servir o frontend umas das outras pelo servidor HTTP do pywebview — o app de teste
carregava o `index.html` de **outro processo**, com o `?v=` antigo. Resultado: nenhuma alteração de
frontend fazia efeito, e várias conclusões intermediárias sobre "o fix não funciona" eram falsas.

> **Regra:** testar com `SMARTEVENTS_DATA_DIR` isolado e sem nenhuma outra instância aberta.

### 9.3 Um fix foi implementado, verificado errado e revertido

Cheguei a implementar a versão "abrir restaurada + maximizar via gatilho do frontend", com timer de
segurança de 20 s. Por causa de 9.1 e 9.2, ela pareceu funcionar. Ao medir corretamente: o gatilho
do frontend **nunca disparava** (o `boot()` não chegava ao fim) e quem maximizava era o timer — um
fix que depende de um timer de 20 s, com a janela pequena até lá, é pior que o bug. Foi revertido
por inteiro antes de partir para a solução final.

---

## 10. Arquivos alterados

**Correção:**

- `core/window_chrome.py` — `force_repaint()`, `REPAINT_SETTLE_SECONDS`, `import time`
- `main.py` — `FIRST_PAINT_REPAINT_DELAY`, agendamento no `_on_loaded`
- `tests/test_window_chrome.py` — 2 testes de regressão

**Documentação:**

- `ERRORS.md` — entrada 2026-09-02 (causa, correção, 6 alternativas descartadas, 4 regras)
- `MEMORY.md` — decisão travada e o que não trocar
- `docs/plans/2026-09-02-001-fix-primeiro-quadro-webview2-sessao.md` — este arquivo

**Não tocados (trabalho pré-existente do usuário, sobre `VERSION` no bundle):**
`core/self_test.py`, `main.spec`, `tests/test_distribution_build.py`, `tests/test_installer.py`

---

## 11. Artefato gerado

`dist/distributions/rock-in-rio-2026-20260902T044132Z/`

| | |
|---|---|
| `installer/Setup_SmartEvents_Rock_In_Rio_2026.exe` | 579,9 MB (608.055.875 bytes) |
| SHA-256 do setup | `32e7e425b6b43b795b019a14648ea8891da1aab8c0f44eb70e2e381899c9a736` |
| SHA-256 do `.sepack` | `8c5b394173d635b3a4d13d7790cfba73e7b243fe7de8760fc1d80b4b92e33748` |
| SHA-256 do `SmartEvents.exe` | `f164c8365d602653f09ef1e4d6134b0fdbf46032def858c755e585f2b128ccfa` |
| Evento / cliente | `rock-in-rio-2026` / TIM |
| Warnings | nenhum |

---

## 12. Próximos passos sugeridos

1. Commitar o fix e **regerar o instalador**, para o `source_commit` do manifesto apontar para o
   código corrigido (§8.3).
2. Remover as distribuições obsoletas `012132Z`, `012922Z` e a incompleta `043638Z` (§8.2, §8.6).
3. Validar a abertura numa máquina de operador (GPU diferente da desta) — a corrida é sensível a
   hardware, e 8 s pode não ser o número certo lá.
4. Decidir o que fazer com as cópias obsoletas em `.claude/` e corrigir o `ORGANIZACAO.md` (§8.5).
5. Reproduzir, em ambiente limpo, a suspeita de cache de frontend (§8.4) — se confirmada, é um bug
   de entrega de UI independente deste.
