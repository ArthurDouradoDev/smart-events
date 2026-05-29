# Resumo de Alterações — SmartEvents (modo offline + correções)

Documento das mudanças feitas para tornar o app **totalmente utilizável sem depender de um
servidor central em rede** (evento urgente do Rio de Janeiro), entregue como um único `.exe`,
mais as correções e ajustes solicitados em seguida.

Build do executável:
```powershell
python -m PyInstaller --noconfirm --clean main.spec
# saída: dist\main.exe (arquivo único, ~187 MB, com logo, Playwright + Chromium embutidos)
```

---

## 1. Servidor embutido e modo offline

**Objetivo:** o `.exe` sobe o próprio servidor (FastAPI) localmente, semeia os dados na 1ª execução
e sincroniza tudo para o SQLite local — sem servidor em rede.

- **`server.py`**
  - Paths cientes do modo *frozen* (PyInstaller): `RESOURCE_DIR` (recursos read-only no bundle /
    `_MEIPASS`) e `DATA_DIR` (gravável, ao lado do `.exe` — mesmo critério de `core/database.py`).
  - `_seed_server_data()`: na 1ª execução copia o `server_data/` embutido para o lado do `.exe`
    (evento Rio + VIPs). Mantém `EVENTS_DIR`/`VIPS_DIR`/`FRONTEND_FILE` derivados desses dirs.
- **`main.py`**
  - `_start_embedded_server()`: sobe o FastAPI **numa thread daemon do próprio processo**
    (uvicorn `Server` com `install_signal_handlers` desativado), escolhendo porta livre
    (`_find_free_port`, tenta 8000) e retornando a URL local.
  - `_wait_server_ready()` + thread de *warmup* que aguarda o servidor e roda o sync inicial
    (`sync_events_from_server` + `sync_vips_from_server`) sem travar a abertura da janela.
  - Fixa `server_url` para `http://127.0.0.1:<porta>` nas settings (sync aponta para o local).
  - Modos auxiliares por argumento: `--serve` (servidor only) e `--get-session` (renovação de
    sessão; ver seção 4). Despacho no bloco `__main__`.
- **`api/api.py`**
  - `get_server_url()` e `open_server_ui()` (abre o painel do servidor no navegador padrão).
  - Atributo `_server_url` injetado pelo `main.py`.

---

## 2. Frontend — botão "Servidor", auto-sync e robustez de boot

- **`frontend/index.html`**: ícone de engrenagem (config/sync) substituído pelo botão **"Servidor"**
  (`#server-btn`); modal de sincronização removido.
- **`frontend/js/app.js`**:
  - `_setupServerButton()`: abre o painel do servidor; **auto-sync ao recuperar o foco** da janela.
  - `_bootSync()`: re-tentativas de sincronização no boot (o servidor embutido pode levar alguns
    segundos para responder na 1ª execução).
  - `_poll()` passou a chamar `refreshChart()` a cada ciclo (ver seção 7).
- **`frontend/js/bridge.js`**: wrappers `getServerUrl`, `openServerUi` (+ mocks para o modo browser).

---

## 3. Empacotamento (PyInstaller) — `main.spec`

- `datas`: inclui `frontend/`, `server_frontend/`, `server_data/` (semente Rio), `core/session_renew.py`,
  dados do `certifi`, pacote `playwright` (via `collect_all`) e os navegadores do Playwright.
- `hiddenimports`: `server`, `core.session_renew`, submódulos do `uvicorn`, `multipart`, `pandas`,
  `openpyxl`, e coleta forçada de `requests`/`urllib3`/`charset_normalizer`/`certifi`/`idna` +
  submódulos do `playwright`.
- Navegador: empacota **`chromium_headless_shell-1223`** + `ffmpeg-1011` + `winldd-1007` (o headless
  usa o *headless shell*, ~271 MB — não o Chromium completo).
- `icon='assets/logoSmartEvents.ico'` (logo no executável).
- `upx=False` (UPX poderia corromper o Chromium/node empacotados).

---

## 4. Coleta ao vivo do iManager (Playwright + Chromium no `.exe`)

- **`core/session_renew.py`** (novo): lógica de renovação de sessão (login no iManager via Playwright,
  captura de `bspsession`/`roarand`/`task_id`), com função `run(headless, module, base_url, session_file)`.
  Em modo *frozen*, aponta `PLAYWRIGHT_BROWSERS_PATH` para os navegadores empacotados (`ms-playwright/`).
- **`scratch/get_session.py`**: virou um *wrapper* CLI fino que chama `core.session_renew.run(...)`
  (fonte única de verdade; preserva o uso em dev).
- **`main.py`**: modo `--get-session` (`_run_get_session`) executa a renovação no próprio `.exe`.
- **`core/collector.py`**: a renovação de sessão roda em **subprocesso isolado** — no `.exe` via
  `main.exe --get-session`; em dev via Python do venv + `scratch/get_session.py`. (Ver seção 8 sobre
  por que subprocesso e não in-process.)

---

## 5. Correções de crash do executável (modo *frozen*)

- **`core/collector.py` e `core/database.py`** — *import requests*: o código removia a raiz do projeto
  do `sys.path` antes de `import requests` (para evitar shadowing em dev). No `.exe` isso removia o
  próprio diretório de extração (`_MEIPASS`) e quebrava o import. Agora: quando *frozen*, `import requests`
  direto, sem mexer no `sys.path`.
- **`main.py` — streams nulos**: em `.exe` sem console, `sys.stdout`/`sys.stderr` são `None`. Garante
  streams válidos em **UTF-8** (`errors="replace"`) **antes** do `logging.basicConfig` — vale para o
  processo GUI e para os modos auxiliares. Evita `AttributeError` e `UnicodeEncodeError` (cp1252).
- **`main.py` — uvicorn**: `uvicorn.run(..., log_config=None)` para não instanciar o formatter colorido
  (que chamava `sys.stdout.isatty()`).
- **`server.py`**: troca do caractere `→` por `->` em log (defensivo contra `UnicodeEncodeError`).

---

## 6. Dados do evento Rio + remoção de resíduos

- **`server_data/`**: podado para conter **apenas o Rio** — evento `vips-rio-tim-jun-2026.json`
  (status alterado para **`ACTIVE`**) + 15 VIPs RJ. Removidos os eventos `sao-paulo` e
  `teste-ribeirao-pires-dt` e os 3 VIPs SP.
- **`core/database.py`**:
  - `delete_event(event_id)`: remove o evento do banco global (apagando linhas-filhas — `sites` tem FK)
    e apaga o banco específico do evento (`smart_events_<id>.db`).
  - `sync_events_from_server()`: **limpeza de órfãos** — remove eventos locais que não existem mais no
    servidor (ex.: eventos de teste residuais como `gp-sp-2025`). **Salvaguarda**: não limpa nada se o
    servidor retornar lista vazia (evita apagar o evento ativo por resposta transitória). Retorna
    também `removidos`.

---

## 7. Atualização automática do gráfico de KPIs

- **`frontend/js/kpi.js`**: `_refreshChart(arg)` reconhece `fromPoll` e, nesse caso, **não reabre** o
  popup que o usuário fechou. Export `refreshChart()` agora chama `_refreshChart({fromPoll:true})`.
- **`frontend/js/app.js`**: `_poll()` (ciclo de 30s) passou a chamar `refreshChart()` — o gráfico
  atualiza sozinho sem precisar trocar de célula/métrica.

---

## 8. Estabilidade da coleta de VIPs (correção importante)

- **Sintoma:** depois de um tempo rodando, os dados de VIP paravam de atualizar (KPIs seguiam).
- **Causa:** uma versão intermediária rodava a renovação de sessão **in-process** (Playwright dentro do
  processo). Com duas threads de coleta (monitoring/KPI e trace/VIP) e a Sync API do Playwright não
  lidando bem entre threads — além de **vazar processos `node.exe`** ao longo do tempo — a renovação do
  **trace** quebrava com o tempo, parando os VIPs.
- **Correção:** renovação revertida para **subprocesso isolado** (um processo limpo por renovação).
  Revalidado: `[renew/trace] Sessão renovada com sucesso` e `collect_vips` retornando medições.

---

## 9. Painel de logs de coleta (`</>` — desenvolvedor)

- **`core/log_buffer.py`** (novo): *ring buffer* em memória que captura os logs dos módulos de coleta
  (`core.collector`, `core.scheduler`, `core.session_renew`). Instalado no `main.py` antes do boot.
- **`api/api.py`**: `get_collection_logs(limit)`, `clear_collection_logs()`, `download_collection_logs()`
  (salva `.log` em Downloads) + helper `_resolve_downloads_dir()`.
- **`frontend/js/logs.js`** (novo): drawer de logs estilo o de alertas, com **auto-refresh**, **Limpar**
  e **Baixar Logs**; aberto pelo botão **`</>`** no header. Wrappers no `bridge.js`, markup no
  `index.html` e estilos em `frontend/css/main.css` (`#logs-drawer`, `#logs-list`).

---

## 10. Encerramento / processos-filho

- **`main.py` — `_setup_windows_job()`**: coloca o processo num **Job Object** (Windows) com
  `KILL_ON_JOB_CLOSE`, para que os processos-filho (renovação de sessão + Chromium) sejam encerrados
  junto com o app, evitando órfãos ao fechar. (O servidor não é mais subprocesso — roda em thread —
  o que já elimina a principal origem do popup `_MEI` do PyInstaller onefile.)

---

## Arquivos novos
- `core/session_renew.py` — renovação de sessão (Playwright), compartilhada por dev e `.exe`.
- `core/log_buffer.py` — buffer em memória dos logs de coleta.
- `frontend/js/logs.js` — painel de logs do desenvolvedor.

## Arquivos principais alterados
- `main.py`, `server.py`, `api/api.py`, `core/collector.py`, `core/database.py`,
  `scratch/get_session.py`, `main.spec`
- `frontend/index.html`, `frontend/js/app.js`, `frontend/js/bridge.js`, `frontend/js/kpi.js`,
  `frontend/css/main.css`
- `server_data/` (podado para o Rio; evento `ACTIVE`)

## Procedimento na máquina final
1. Rode o `.exe` numa pasta limpa (ou apague `data/` e `server_data/` ao lado do `.exe`) para pegar o
   seed podado (só Rio, `ACTIVE`) e os 15 VIPs RJ.
2. Abra o painel **`</>`** com o evento ativo e confirme `[renew/trace] Sessão renovada com sucesso`
   e as medições de VIP entrando. O painel mostra o erro por `taskId` caso algum VIP específico falhe.
