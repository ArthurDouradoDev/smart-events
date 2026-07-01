# Organização do Projeto SmartEvents

> Mapa da estrutura do repositório.
> Fonte da verdade do que é "em uso": o que `main.spec` empacota, o que `main.py`/`server.py`
> importam, e o que o `core/collector.py` invoca em runtime.

---

## 1. Estrutura atual

```
SmartEvents/
├─ main.py  server.py  build.py  main.spec  clear_demo_event.py
├─ requirements.txt  pytest.ini  README.md  sample_event.json  .gitignore
├─ api/            # api.py — único contato Python ↔ JS (PyWebView)
├─ core/           # collector, scheduler, database, models, session_renew, log_buffer
├─ frontend/       # index.html, css/, js/ (inclui logs.js), lib/ (Chart.js + Leaflet vendored)
├─ server_frontend/# painel web do Servidor Central
├─ tests/          # suíte pytest (unitários + integração marcada com `vpn`)
├─ scratch/        # get_session.py (CRÍTICO em dev: invocado pelo collector) + get_session_regional.py
├─ tools/          # oss_validate.py — validador standalone da coleta (KPI/VIP)
├─ assets/         # logoSmartEvents.ico (ícone do .exe)
├─ docs/           # documentação técnica viva (ver §2) + references/ (ver §3)
├─ .claude/        # CLAUDE.md, MEMORY.md, ERRORS.md — memória viva do projeto
├─ data/           # runtime, gitignored: *.db, session*.json, settings.json, credentials.json, browser_profile/
├─ server_data/    # semente de eventos/VIPs (events/ + vips/), gitignored
└─ dist/           # .exe final (gitignored)
```

⚠️ **Nunca excluir** `data/*.db`, `data/session*.json`, `data/credentials.json` — dados de campo / credenciais.

---

## 2. `docs/` — documentação viva

| Arquivo | Conteúdo |
|---|---|
| `ORGANIZACAO.md` | Este mapa. |
| `coleta-de-dados.md` | Documento canônico do fluxo de coleta de KPI/VIP. Correções pós-escrita (ex.: CSRF double-submit de 2026-06-11) estão em `.claude/MEMORY.md` e `.claude/ERRORS.md`. |
| `coleta-de-alarmes.md` | Descobertas que viabilizaram a captura de alarmes (Current Alarms) filtrados: mecanismo `cmd 1102`/`1103`, pares `{alarmId, alarmGroupId}`, `bspSessionId=""`, reuso de sessão. Base de `imaster_alarms.py`. |
| `evento-cadastro-e-petalas.md` | Fórmulas de plotagem das pétalas (SemiCircle vs SVG) e campos obrigatórios do JSON de evento. |
| `guia-vm-servidor.md` | Passo a passo de VM Linux/VirtualBox para o servidor central. |
| `smart-events.html` | Documentação técnica completa em HTML (a visão mais detalhada e atualizada da arquitetura). |

---

## 3. `docs/references/` — material usado como base

| Caminho | Conteúdo |
|---|---|
| `references/requests/` | Traces HTTP reais capturados do iManager (PM e FARS), inclusive `new-explain-info.txt`. **Valor alto** — base do `HttpCollector`; referenciados em comentários de `core/collector.py`. |
| `references/npsmart/` | Cópias offline das páginas do NPSmart/OSS (`NPSmart Tim.html` + `_files/`, `earth_petal.html`, `offline_site/`) e `download_page_assets.py` (script que as gerou). **Os espelhos pesados (`offline_site/`, `NPSmart Tim_files/`) são gitignored.** |
| `references/prototipo/` | `SmartEvents Dashboard _Standalone_.html` (protótipo visual que originou o frontend) + `prototype.md` (design brief). |
| `references/get-info.md` | Elementos da tela de login do iManager usados na automação Playwright. |
| `references/sample.md` | Amostra de dados de células (formato de importação de sites do Servidor Central). |
| `references/vpn.md` | Referência do mecanismo de checagem de VPN do projeto MDT (base da checagem daqui). |

---

## 4. `core/` — módulos load-bearing

| Módulo | Responsabilidade |
|---|---|
| `models.py` | Dataclasses de domínio (Cell, Site, VIP, Thresholds, OssConfig, EventConfig, KpiMeasurement, VipMeasurement, Alert). |
| `database.py` | Única camada de acesso ao SQLite (banco global + bancos por-evento), thread-local. |
| `collector.py` | `BaseCollector` e implementações (`MockCollector`, `CsvCollector`, `HttpCollector`) + `build_collector()`; coleta KPI/VIP e gerencia a sessão HTTP do iManager. |
| `scheduler.py` | Threads daemon de coleta (KPI 120s / VIP 60s), avaliação de limiares e geração de alertas. |
| `session_renew.py` | Renovação de sessão do iManager via Playwright (login headless e interativo); exit codes `EXIT_SUCCESS` / `EXIT_GENERIC_FAIL` / `EXIT_NEEDS_INTERACTIVE`. |
| `log_buffer.py` | Ring buffer de logs em memória que alimenta o painel de logs do desenvolvedor (`frontend/js/logs.js`). |

---

## 5. `tests/` — suíte pytest

Configurada em `pytest.ini` (`testpaths = tests`). Rodar com `pytest` na raiz.

| Arquivo | Escopo |
|---|---|
| `test_models.py`, `test_database.py`, `test_collector.py`, `test_scheduler.py`, `test_api.py` | Testes unitários — rodam offline. |
| `test_http_vpn.py` | Integração com o iManager real — marcado com `@pytest.mark.vpn`; **só passa conectado à VPN do cliente**. Use `pytest -m "not vpn"` para pular offline. |
| `conftest.py` | Fixtures compartilhadas. |
