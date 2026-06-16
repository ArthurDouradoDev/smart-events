# Organização do Projeto SmartEvents

> Mapa da estrutura do repositório após a reorganização de 2026-06-12.
> Fonte da verdade do que é "em uso": o que `main.spec` empacota, o que `main.py`/`server.py`
> importam, e o que o `core/collector.py` invoca em runtime.

---

## 1. Estrutura atual

```
SmartEvents/
├─ main.py  server.py  build.py  main.spec  clear_demo_event.py
├─ requirements.txt  README.md  sample_event.json  .gitignore
├─ api/            # api.py — único contato Python ↔ JS (PyWebView)
├─ core/           # collector, scheduler, database, models, session_renew, log_buffer
├─ frontend/       # index.html, css/, js/, lib/ (Chart.js + Leaflet vendored)
├─ server_frontend/# painel web do Servidor Central
├─ scratch/        # SÓ get_session.py (CRÍTICO em dev: invocado pelo collector) + get_session_regional.py
├─ tools/          # oss_validate.py — validador standalone da coleta (KPI/VIP)
├─ assets/         # logoSmartEvents.ico (ícone do .exe)
├─ documentacao/   # documentação técnica viva (ver §2)
├─ references/     # material de referência usado como base (ver §3)
├─ to-delete/      # arquivos aguardando exclusão manual (ver §4)
├─ .claude/        # CLAUDE.md, MEMORY.md, ERRORS.md — memória viva do projeto
├─ data/           # runtime, gitignored: *.db, session*.json, credentials.json, browser_profile/
├─ server_data/    # semente de eventos/VIPs empacotada no build (gitignored)
└─ dist/           # .exe final (gitignored)
```

⚠️ **Nunca excluir** `data/*.db`, `data/session*.json`, `data/credentials.json` — dados de campo / credenciais.

---

## 2. `documentacao/` — documentação viva

| Arquivo | Conteúdo |
|---|---|
| `ORGANIZACAO.md` | Este mapa. |
| `coleta-de-dados.md` | Documento canônico do fluxo de coleta (ex-`descricao-coleta.md`). Correções pós-escrita (ex.: CSRF double-submit de 2026-06-11) estão em `.claude/MEMORY.md`. |
| `evento-cadastro-e-petalas.md` | Fórmulas de plotagem das pétalas (SemiCircle vs SVG) e campos obrigatórios do JSON de evento (ex-`evento.md`). |
| `guia-vm-servidor.md` | Passo a passo de VM Linux/VirtualBox para o servidor central (ex-`guiavm.md`). |
| `smart-events.html` | Documentação técnica completa em HTML. |

---

## 3. `references/` — material usado como base

| Caminho | Conteúdo |
|---|---|
| `references/requests/` | Traces HTTP reais capturados do iManager (PM e FARS), inclusive `new-explain-info.txt`. **Valor alto** — base do `HttpCollector`; referenciados em comentários de `core/collector.py`. |
| `references/npsmart/` | Cópias offline das páginas do NPSmart/OSS (`NPSmart Tim.html` + `_files/`, `earth_petal.html`, `offline_site/`) e `download_page_assets.py` (script que as gerou). |
| `references/prototipo/` | `SmartEvents Dashboard _Standalone_.html` (protótipo visual que originou o frontend) + `prototype.md` (design brief). |
| `references/get-info.md` | Elementos da tela de login do MAE usados na automação Playwright. |
| `references/sample.md` | Amostra de dados de células (formato de importação de sites do Servidor Central). |
| `references/vpn.md` | Referência do mecanismo de checagem de VPN do projeto MDT (base da checagem daqui). |

---

## 4. `to-delete/` — aguardando exclusão manual

Conteúdo movido em 2026-06-12; nada ali é necessário em runtime/build (ver `to-delete/LEIA-ME.md`).
Categorias: saída de build (`build/`, `pycache/`), cópia morta do app (`files/`),
duplicatas do antigo `arquivos_auxiliares/`, planos já executados (`planos/`, `docs-historicos/`),
scripts one-off de debug (`scratch/`) e logs antigos.

Após revisar, basta excluir a pasta inteira. Todos os arquivos rastreados pelo git são
recuperáveis via `git restore` / histórico mesmo depois da exclusão física.
