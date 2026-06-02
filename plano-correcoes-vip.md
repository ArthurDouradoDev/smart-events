# Plano — Coleta de VIPs mais rápida, logs/feedback e correção do navegador no .exe

## Context
Após corrigir o loop de sessão/CAPTCHA, o sistema voltou a coletar, mas surgiram 4 problemas (observados em logs-11-39.log):

**VIPs atualizam ~10 min depois dos sites.** collect_vips faz, por VIP, até _MAX_MEAS_DECODES_PER_CYCLE = 50 chamadas sequenciais a msg-explain-info (collector.py:1228, collector.py:1283-1293). Com 15 VIPs são ~800 chamadas HTTP sequenciais por ciclo sobre a VPN — um ciclo leva vários minutos. KPIs usam um único POST em lote, por isso são rápidos.
**Logs de VIP imprecisos** — só o monitoring tem log-resumo; a coleta de VIP não informa progresso.
**Navegador de reauth não abre no .exe** (erro do Playwright). Causa: main.spec:25-29 empacota apenas chromium_headless_shell-1223 (shell headless, que não roda em modo visível). A reauth interativa chama launch(headless=False), que precisa do Chromium completo — ausente no bundle. O chromium-1223 completo já está instalado localmente (mesmo versionamento do shell).
**Feedback de atualização inexistente** — o frontend só faz polling de 30s (app.js:173); não há indicação visual de que dados estão sendo coletados nem detalhes da última coleta.
Resultado desejado: VIPs coletados rapidamente (modo expresso frequente + completo periódico, com paralelismo cauteloso), logs precisos de VIP, navegador de reauth funcionando no .exe, e feedback claro no header + popup de detalhes.

## Mudanças

### 1. Correção do navegador no .exe — main.spec
- Adicionar o Chromium completo ao bundle, ao lado do shell headless:
    - Em _browser_dirs (main.spec:25-29) incluir "chromium-1223" (já existe em %LOCALAPPDATA%/ms-playwright).
- core/session_renew._ensure_browsers_path já aponta PLAYWRIGHT_BROWSERS_PATH para ms-playwright/ no bundle, então launch(headless=False) encontrará chromium-1223 automaticamente. Sem mudança de código Python.
- Trade-off: +~150 MB no .exe (necessário para o login visível do operador). Manter upx=False (já está) para não corromper o Chromium.
- O número de versão acompanha o do shell (*-1223); ao atualizar o Playwright, ambos mudam juntos.

### 2. Coleta de VIPs: dois modos + paralelismo cauteloso — core/collector.py + core/scheduler.py
- Reduzir decodes (o grande ganho): a lista do filter-by-cols já vem ordenada do mais recente para o mais antigo, então decodificar poucas linhas basta. Novos limites em HttpCollector:
    - _VIP_DECODES_EXPRESS = 3 (coleta expressa, frequente)
    - _VIP_DECODES_FULL = 20 (coleta completa, periódica)
- collect_vips(mode: str = "express") passa o limite de decodes para _parse_filtered_trace_response(..., max_decodes) (hoje usa _MAX_MEAS_DECODES_PER_CYCLE). Parar cedo quando já obteve medições suficientes.
- Paralelismo cauteloso: extrair o fluxo por-VIP para _collect_one_vip(task_id, vip_name, mode, session) e rodar com concurrent.futures.ThreadPoolExecutor(max_workers=_VIP_MAX_WORKERS) (_VIP_MAX_WORKERS = 3, ajustável para 1 em caso de conflito FARS).
    - Cada worker usa sua própria requests.Session (helper que constrói do session.json sem mexer no cache compartilhado), reduzindo colisão de sessão FARS stateful.
    - Renovação fora dos workers: fazer um preflight sequencial de sessão antes do fan-out (reusar _renew_session("trace") + a lógica de circuit breaker já existente). Workers NÃO renovam — se um worker pegar SessionExpiredError, sinaliza e o próximo ciclo trata. Isso evita N renovações concorrentes.
- Scheduler (scheduler.py:96-108): o ciclo de VIP roda expresso a cada INTERVAL_VIP_SECONDS; dispara completo quando passou INTERVAL_VIP_FULL_SECONDS (ex.: 600s) desde a última coleta completa (contador/timestamp em Scheduler). collect_vips_now() (refresh manual) usa modo expresso.
### 3. Log-resumo de VIPs — core/collector.py
    - Ao final de collect_vips, emitir um INFO espelhando o do monitoring: Trace/VIP (modo=expresso): N/Y VIPs com dados, M medições, K no evento, em T.Ts.
    - Incluir contadores: VIPs consultados, VIPs com medição, total de medições, quantos in_event, modo e tempo decorrido.
### 4. Status de coleta no backend — core/scheduler.py + api/api.py
- Scheduler mantém self._status atualizado em volta de cada coleta (início → running, fim → ok/error com last_success, last_count, duration_s, e para VIP: mode, vips_total, vips_with_data).
- Novo método Api.get_collection_status() retornando dict {ok, recording, kpi:{...}, vip:{...}, session:{needs_interactive, region}, now}. O estado de sessão vem de HttpCollector._needs_interactive + self._region.
Seguir convenção da Api (sempre dict, nunca levantar exceção).
### 5. Feedback no header + popup — frontend/
- Header (index.html:44-48): novo #sync-indicator ao lado do #rec-indicator, com ícone que pulsa/gira enquanto há coleta em andamento e mostra a última atualização relativa (ex.: "VIP há 12s"). Tooltip "Atualizando dados…". Clique abre o popup.
- Popup #sync-modal (reusar estilos de .modal em main.css): detalhes de KPI e VIP (último horário, nº de medições, modo, status), status da sessão (ok / "reautenticação necessária") e próximo ciclo.
- app.js: poll leve de status a cada ~5s (API.getCollectionStatus()) enquanto em modo ativo, atualizando o indicador e o popup (se aberto); reaproveitar no _poll de 30s. Novo _setupSyncIndicator().
- bridge.js: shortcut getCollectionStatus() + entrada em _mock.
- Sanitizar via _esc(); sem console.log de debug.

## Arquivos a modificar
- main.spec — bundlar chromium-1223 (Chromium completo) para o login visível no .exe.
- core/collector.py — modos expresso/completo, _collect_one_vip, paralelismo (ThreadPoolExecutor, sessões por-worker), preflight de sessão, log-resumo de VIP.
- core/scheduler.py — agendar expresso vs completo, manter self._status.
- api/api.py — get_collection_status().
- frontend/index.html, frontend/css/main.css, frontend/js/app.js, frontend/js/bridge.js — indicador no header + popup + poll de status.

## Constantes (ajustáveis)
- _VIP_DECODES_EXPRESS=3, _VIP_DECODES_FULL=20, _VIP_MAX_WORKERS=3, INTERVAL_VIP_FULL_SECONDS=600, status poll ~5s.

## Verificação
- Velocidade VIP (dev): python main.py na VPN (evento RJ). No log, o resumo Trace/VIP (modo=expresso): … deve aparecer e um ciclo expresso completar em poucos segundos (vs minutos antes). A cada ~10 min, um ciclo modo=completo (20 decodes/VIP).
- Paralelismo seguro: confirmar nos logs que não há erros FARS "linkage"/500 com _VIP_MAX_WORKERS=3; se houver, baixar para 1 valida a hipótese de statefulness.
Feedback UI: abrir o app, ver o #sync-indicator pulsando durante coletas e a última atualização; clicar abre o popup com KPI/VIP/sessão. Em browser puro (mock), o indicador funciona com dados mock.
Navegador no .exe: python build.py, rodar dist/main.exe na VPN, forçar reauth (sessão expirada/CAPTCHA) e confirmar que o navegador visível abre (Chromium completo do bundle) sem erro de Playwright; concluir login e ver a coleta retomar.
Regressão: python main.py --mock continua coletando com MockCollector; KPIs inalterados.