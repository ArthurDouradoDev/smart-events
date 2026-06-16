# MEMORY.md

Registro de decisões de design, fatos permanentes, regras de domínio e arquitetura do SmartEvents.

## Informações Gerais do Projeto

### O que é este projeto
Plataforma desktop de monitoramento de RF em tempo real para grandes eventos (GPs, shows, finais de campeonato). Equipes de NOC visualizam saúde da rede, acompanham VIPs e respondem a alertas de capacidade.

### Stack Tecnológico
- **Frontend**: HTML, CSS (Vanilla), JS (ES Modules, sem bundler).
- **Backend/Desktop**: Python + PyWebView, FastAPI para o servidor central de coordenação.
- **Banco de Dados**: SQLite local (sem servidor remoto de banco de dados).
- **Portabilidade**: O executável (`.exe`) é portátil. Funciona conectando à VPN do cliente e rodando localmente.

---

## Arquitetura e Estrutura do Projeto

### Visão Geral da Arquitetura
```
OSS/Trace (VPN cliente) → Collector (Python) → SQLite → Api.py → PyWebView → JS Frontend
```
O JS no frontend nunca acessa o banco de dados diretamente. Toda interação é intermediada pela classe `Api` em `api/api.py`, exposta globalmente no frontend como `window.pywebview.api` (mas encapsulada via `bridge.js`).

### Estrutura de Arquivos Crítica
- `main.py`: Ponto de entrada que inicializa a janela do PyWebView e injeta a API.
- `server.py`: Servidor central FastAPI para coordenação e cadastro centralizado de eventos/VIPs.
- `api/api.py`: Único ponto de contato Python ↔ JS. Métodos públicos expostos via PyWebView. Contém `_sanitize_event()` que limpa dados desnecessários antes de enviar ao frontend.
- `core/models.py`: Modelos de dados (Site, Cell, VIP, EventConfig, Alert).
- `core/database.py`: Ponto exclusivo de acesso ao SQLite.
- `core/collector.py`: Coleta de dados do OSS com 3 modos (CsvCollector, HttpCollector, MockCollector).
- `core/scheduler.py`: Threads de segundo plano para execução periódica de coletas (KPI: 120s, VIP: 60s) e avaliação de alertas.
- `frontend/`:
  - `index.html`: Estrutura base da interface.
  - `css/main.css`: Tokens de design e estilos globais (incluindo tema escuro).
  - `js/app.js`: Orquestrador e ciclo de vida do frontend (polling de 30s).
  - `js/bridge.js`: Camada de abstração que permite usar dados mockados em desenvolvimento direto no navegador.
  - `js/state.js`: Barramento de estado pub/sub global.
  - `js/map.js`: Renderização de mapas Leaflet e marcadores SVG.
  - `js/vip.js`: Lógica do painel de monitoramento de VIPs.
  - `js/kpi.js`: Painel de visualização de KPI e gráficos.
  - `js/alerts.js`: Histórico de alertas e download de logs.
- `server_frontend/`: Painel web do Servidor Central.
- `server_data/`: Cadastro de eventos e VIPs em formato JSON gerenciados pelo Servidor Central.
- `events/`:
  - `sample_event.json`: Evento de exemplo para testes.
- `data/`:
  - `smart_events.db`: Gerado automaticamente (banco central/global). Nunca commitar.

---

## Decisões de Design e Padrões Críticos

### 1. Comunicação JS ↔ Python
Toda chamada ao backend deve passar exclusivamente por `bridge.js`. Nunca chamar `window.pywebview.api` diretamente. Isso permite rodar o frontend no navegador (sem o container desktop PyWebView) para desenvolvimento visual.

### 2. Estado Global Pub/Sub
Os componentes do frontend comunicam-se via eventos através do `state.js`. Nenhum componente deve importar diretamente outro componente para manipular dados ou disparar renderizações de forma circular.
- Exemplo correto: `State.on("change:sites", (sites) => renderSiteList(sites))` ou `State.set("selectedSite", siteId)`.

### 3. Acesso ao SQLite Thread-Local
Apenas `core/database.py` pode interagir com o módulo `sqlite3`. Devido ao uso de threads no scheduler, conexões SQLite são gerenciadas por thread local (`threading.local()`) através de `database.get_conn()` para o banco global e `database.get_event_conn(event_id)` para o banco específico do evento.

### 4. Mapeamento de VIPs via `task_id` (Sem Dependência de IMSI)
Os VIPs são identificados pelo `task_id` da Signaling Trace dedicada no iManager, e não pelo IMSI ou pelo campo `GUUserId` (que costuma retornar `N/A`). O collector carrega dinamicamente todos os VIPs pertencentes ao OSS do evento ativo via `db.get_event_vips(event_id)` e itera `vips_by_task` (`{task_id: nome}`). Cada chamada ao FARS já sabe a quem pertence — não há matching por IMSI nem dependência do campo `GUUserId`.
- A classe `Api` possui o método `_sanitize_event()` que remove `sites` inteiros do payload de eventos antes de serializar.

### 5. Alertas em 3 Níveis
- **GLOBAL**: Sempre ativos (ex: site/célula fora do ar).
- **EVENT**: Parametrizados no arquivo JSON de configuração do evento (ex: limite de RSRP ou utilização).
- **INSTANCE**: Silenciados temporariamente pelo operador na interface gráfica. São salvos no banco sob a tabela `silenced_alerts` usando a chave `{tipo}_{severidade}_{site_id}` (ex: `util_crit_ERB-07`), persistindo no banco global. O silenciamento é gerenciado por `db.silence_alert(alert_key)`.

### 6. Sincronização Servidor Central ↔ SQLite Local
- O app desktop busca e sincroniza eventos e cadastros globais de VIPs a partir do servidor central configurado na URL `server_url` (em Settings).
- A sincronização local (`database.py` e `api.py`) é realizada ao abrir o app e pode ser forçada manualmente no menu de configurações.
- Regra de precedência: Ao sincronizar um evento, se o status no banco local for `ACTIVE` ou `ENDED`, ele é preservado sobre o status `SCHEDULED` vindo do servidor central.

### 7. Limpeza do Histórico (Clear Event History)
- O indicador de gravação (`rec-indicator` "REC X MB") na barra superior do frontend é interativo. Ao clicar nele, inicia-se o fluxo de limpeza de histórico do evento corrente.
- Possui um modal de dupla confirmação para evitar exclusões acidentais de dados de campo.
- Ao confirmar, as tabelas `kpi_measurements`, `vip_measurements` e `alerts` do evento atual são deletadas no banco local SQLite, o conjunto de arquivos processados (`_processed`) no coletor de CSV é zerado, e é executada uma instrução `VACUUM` no SQLite para liberar espaço físico no disco.

### 8. Navegação e Timeline Histórica
- O header da aplicação contém um menu dropdown que lista todos os eventos cadastrados (divididos em ativos, agendados e históricos/encerrados).
- Selecionar um evento no dropdown chaveia dinamicamente a aplicação entre o modo **Monitoramento Ativo** (para eventos ativos/agendados, iniciando as threads do `scheduler`) e modo **Histórico/Leitura** (para eventos encerrados, permitindo navegar pelas coletas gravadas usando um controle de timeline/slider).
- Se a duração de um evento ativo/agendado for maior que 7 dias, o timer progressivo do evento no header é ocultado automaticamente para melhor ergonomia visual.

### 9. Resolução de Site e Destaque Visual de VIPs
- **Resolução de Site**: O trace do iManager reporta o nome do site receptor no campo `source` de `tableData` (ex. `SR-RPITJ2`) e o ID local da célula em `GLCellId` (ex. `2`). O coletor concatena estes campos como `SR-RPITJ2_2` e grava no campo `serving_cell`. A API em seguida faz o parse do nome do site ou divide IDs de células numéricos globais por `256` (4G ECI) ou `4096` (5G NCI) para correlacionar e resolver o `serving_site` e `serving_site_name` mapeados no JSON do evento.
- **Destaque na Lista (Sidebar)**: Se o VIP estiver no evento (`vip.in_event`) e conectado a um site do evento (`vip.serving_site`), o card dele recebe a classe `.vip-card.vip-at-site`. Isso estiliza o card com fundo dourado translúcido sutil (`--vip-gold-bg`), borda lateral direita dourada (`--vip-gold`), e cor dourada nos textos do nome e site. O nome é prefixado com o ícone de coroa (`👑`). Ao clicar em um card de VIP com site conectado (`vip.serving_site`), o app atualiza o estado `selectedSite`, fazendo com que o mapa centralize e aproxime (zoom) no site e abra seu respectivo popup, além de destacar o site na listagem inferior.
- **Destaque no Mapa (Leaflet)**: O site correspondente ganha uma insígnia dourada "V" no canto superior direito do ícone SVG e o popup do Leaflet exibe os nomes dos VIPs conectados ao site.
- **Detalhes no Popup**: Ao clicar em qualquer VIP no painel (esteja ele no evento ou fora), o popup de detalhes do VIP (`#vip-detail-modal`) exibe o nome do último site conectado / site atual (`serving_site_name`) e a data e hora de seu último registro (`last_timestamp`).
- **Destaque na Lista de Sites (KPI Panel)**: Um emoji de coroa (`👑`) é adicionado à direita do nome do site na lista de sites caso haja algum VIP ativo conectado àquele site. A lista reage e atualiza as coroas dinamicamente quando o estado dos VIPs muda.

### 10. Visualização Expandida de KPIs e Células
- **Modo Site Completo**: Quando o seletor de célula está em `Site completo` (`cell_id = "__all__"`), o gráfico de multi-células é exibido automaticamente em um popup premium que cobre 80% da tela (esta é a única forma de visualização de todo o site completo para melhor leitura). O contêiner de gráfico principal exibe um placeholder informando que o gráfico está no popup com um botão para reabri-lo.
- **Botão de Expandir**: Adicionado no canto superior direito do painel de gráficos (`#expand-chart-btn`), permite abrir esse mesmo popup de 80% da tela para visualizar de forma mais detalhada qualquer gráfico de célula individual (sem afetar a visualização do painel principal).
- **Backend API (`get_kpi_series`)**: Retorna o dicionário `cells_data` mapeando `cell_id -> list[float]` alinhado aos `labels` temporais (timestamps).
- **Legenda e Interação**: A legenda do Chart.js é exibida apenas no modo expandido/popup para identificar as células por cor. O tooltip de hover está configurado com `mode: "index"` e `intersect: false`, permitindo ver os valores comparativos de todas as células para qualquer instante.

### 11. Renovação de Sessão do iManager, CAPTCHA e Reautenticação Interativa
*(Decisão 2026-06-01 — corrige o loop infinito registrado em [ERRORS.md](file:///.claude/ERRORS.md).)*
- **Renovação honesta:** `core/session_renew.py` só declara sucesso após **provar autenticação com uma sonda REST real** (`_probe_authenticated`, resposta JSON e não HTML/SSO). Exit codes: `EXIT_SUCCESS=0`, `EXIT_GENERIC_FAIL=1`, `EXIT_NEEDS_INTERACTIVE=2` (CAPTCHA/SSO).
- **CAPTCHA = reauth manual:** o OSS do RJ exige um CAPTCHA ("não sou robô") no login, irresolvível em headless. Ao detectar (`EXIT_NEEDS_INTERACTIVE`), o `HttpCollector` **abre automaticamente um navegador VISÍVEL** (`run_interactive_reauth`, classmethod) para o operador concluir o login. **Single-flight** (`_interactive_lock`) garante uma só janela mesmo com as threads monitoring+trace; **cooldown** de 5 min evita reabertura em rajada. Também há botão **"Reautenticar"** no alerta (frontend `alerts.js` → `API.reauthSession()` → `Api.reauth_session`).
- **Circuit breaker:** `collector._renew_session` retorna `bool`; módulo bloqueado entra em `_needs_interactive` e a coleta pausa (sem loop) até o operador reautenticar. Caso "renovou mas continua inválido" engata backoff (`_engage_backoff`). Detecção de sessão em cache obsoleta via `_session_built_roarand` (relê o `session.json` novo sem rodar Playwright).
- **Credenciais por regional:** cada OSS tem conta própria. Resolução em `session_renew._resolve_credentials(region)`: `data/credentials.json[REGIÃO]` → `_REGIONAL_CREDENTIALS[REGIÃO]` (em código) → defaults. `data/credentials.json` é local (a pasta `data/` é gitignored) e editável **sem recompilar o .exe**. A `region` é propagada do `oss.region` do evento via `--region` para o renovador.

---

## Detalhes da Coleta de Dados

### Seleção de Coletor
O coletor é instanciado em ordem de prioridade via `build_collector()`: `MockCollector` (se `--mock`) → `CsvCollector` (se houver diretório configurado em `import_folder`) → `HttpCollector` (fallbacks por regional/IP configurado).

### HTTP Collector (iManager REST API)
- A `base_url` padrão varia por regional: SP (`https://10.220.50.9:31943`), RJ (`https://10.220.30.9:31943`), com fallback padrão para SP.
- Cada regional usa seu arquivo de sessão em `data/` para evitar colisão de cookies (ex: `session.json`, `session_10_220_30_9.json`).
- O script `scratch/get_session.py` (via Playwright) automatiza a renovação de tokens e cookies, aceitando `--base-url` e `--session-file` dinâmicos.
- **Coleta de KPI**: Envio de `POST /rest/oss/access/pm/v1/monitor/task/result` mapeado pelos IDs de célula. Cada cell precisa do campo `obj_no` no JSON do evento.
- **Coleta de VIP Trace**: Segue fluxo específico de 4 etapas documentado em [ERRORS.md](file:///.claude/ERRORS.md) para evitar erros de comunicação de sessão do FARS.

### CSV Collector
- Lê arquivos `kpi_*.csv` do diretório `oss.import_folder`.
- Rastreia arquivos processados em um conjunto (`set`) em memória chamado `_processed`. Reiniciar o app reprocessa tudo.
- O trace via CSV foi descontinuado em favor do mapeamento direto via `task_id` da API FARS.

---

## Schema do Banco de Dados

O SmartEvents divide os dados em dois tipos de bancos SQLite localizados na pasta `data/`:

### 1. Banco Central/Global (`data/smart_events.db`)
- `events`: Configurações globais e metadados dos eventos.
- `vips`: Cadastro geral de VIPs com identificação regional de `oss`.
- `silenced_alerts`: Chaves de alertas marcados como silenciados.
- Gerenciado via `database.get_conn()`.

### 2. Banco de Evento (`data/smart_events_<event_id>.db`)
- `sites`: Lista de sites do evento.
- `event_vips`: Mapeamento VIP-Evento e respectivo `task_id` de trace do iManager.
- `kpi_measurements`: Timestamps, cell_id, metric, value, site_id.
- `vip_measurements`: Nome do VIP, rsrp, rsrq, cell ativa e indicador se está no evento.
- `alerts`: Histórico de alertas emitidos para o evento.
- Gerenciado via `database.get_event_conn(event_id)`.

*Nota:* Os índices em `kpi_measurements(site_id, metric, timestamp)` e `vip_measurements(vip_name, timestamp)` são críticos para a performance das queries temporais do frontend e não devem ser removidos.

---

## Convenções de Estilo Visual (Design System)
Os tokens principais em `frontend/css/main.css` são:
- `--bg-base`: `#0D1117` (Fundo geral)
- `--bg-surface`: `#161B22` (Painéis e cards)
- `--accent`: `#CF0A2C` (Marca/Crítico. **Nunca** usar para status de rede normal)
- `--success`: `#3FB950` (Verde operacional)
- `--warning`: `#D29922` (Âmbar de alerta)
- `--danger`: `#F85149` (Vermelho de falha)

---

## Escopo e Limitações (Fora de Escopo Atual)
Não implementar ou propor sem alinhamento prévio:
- Mecanismos de login ou autenticação de usuários.
- Banco de dados remoto compartilhado (toda persistência é local via SQLite).
- Rastreamento por coordenadas GPS de VIPs.
- Integração de IA/Diagnósticos automatizados.
- Aplicação mobile nativa (apenas exportação PDF/HTML).
- Integração com ferramenta Lenin (fase final).

---

## Guia de Tarefas Comuns
Consulte as regras de modificação rápida:
- **Adicionar Métricas**:
  1. Registrar `option` em `frontend/index.html` no `#metric-selector`.
  2. Definir label em `METRIC_LABELS` (`kpi.js`).
  3. Mapear colunas correspondentes em `CsvCollector.KPI_COLUMN_MAP` em `collector.py` (e em `HttpCollector.KPI_COLUMN_MAP` se aplicável na Fase 2).
- **Adicionar Alertas**: Inserir lógica de avaliação em `_evaluate_kpi_alerts` ou `_evaluate_vip_alerts` em `scheduler.py` seguindo o formato padrão de chave `{tipo}_{severidade}_{site_id}` para suporte a silenciamento.
- **Campos de Evento**:
  1. Alterar `core/models.py` (dataclass `EventConfig`).
  2. Ajustar a sanitização em `api/api.py` -> `_sanitize_event()`.
  3. Atualizar `events/sample_event.json`.
- **Mapeamento de Importação de Sites (Servidor Central)**: Modificar a estrutura de sinônimos/aliases no dicionário `col_mappings` em `server.py` (linha ~90).

## Diretrizes de Desenvolvimento e Regras do Agente

### Conformidade com o CLAUDE.md
- **[Decisão 2026-06-01]** O agente deve seguir estritamente todas as diretrizes comportamentais e convenções definidas em [.claude/CLAUDE.md](file:///.claude/CLAUDE.md) em todas as sessões e interações com este projeto. Isso inclui os modos de execução, convenções de código (Python/JS), processos de design simples e cirúrgico, documentação em MEMORY.md/ERRORS.md, e a verificação contínua antes de finalizar tarefas.

## Registro de Atividades / Sessões

### [Sessão 2026-06-12] Reorganização física do diretório (executada)
- **Nova estrutura:** `references/` (material base: `requests/` traces HTTP, `npsmart/` páginas offline + `download_page_assets.py`, `prototipo/` standalone HTML + design brief, `get-info.md`, `sample.md`, `vpn.md`) e `to-delete/` (aguardando exclusão manual pelo operador: `build/`, `__pycache__`, `files/`, `arquivos_auxiliares/` inteiro — eram duplicatas byte-idênticas dos originais, só fins de linha diferentes —, `planos/`, `docs-historicos/`, scratch one-off, logs). Detalhes em [to-delete/LEIA-ME.md](file:///to-delete/LEIA-ME.md).
- **Documentação consolidada em `documentacao/`:** `coleta-de-dados.md` (ex-`descricao-coleta.md`, doc canônico de coleta, com nota apontando p/ a correção CSRF de 2026-06-11), `evento-cadastro-e-petalas.md` (ex-`evento.md`), `guia-vm-servidor.md` (ex-`guiavm.md`), `ORGANIZACAO.md` reescrito. `coleta_dados.md` e `explicacao-coleta.md` foram aposentados (redundantes/superados) em `to-delete/docs-historicos/`.
- **`scratch/` agora contém SÓ** `get_session.py` (load-bearing: invocado pelo collector em dev) e `get_session_regional.py`. `tools/oss_validate.py` mantido.
- **Referências atualizadas:** comentário em `core/collector.py` (`requests/` → `references/requests/`), mapa de diretórios e links do `README.md`.
- **Validação:** `py_compile` + imports (`core.*`, `api.api`) OK após as movimentações; `main.spec` não referencia nenhuma pasta movida.

### [Sessão 2026-06-11] Conserto DEFINITIVO da coleta RJ (VALIDADO AO VIVO na VPN)
- **Causa raiz única = anti-CSRF double-submit cookie.** Os POST do iManager (KPI `monitor/task/result`, Trace `filter-by-cols`) eram rejeitados porque o header `roarand` não ECOAVA o **cookie `roarand`** (o servidor compara `header == cookie` só no POST; o GET não checa — por isso GET 200 / POST 401, e antes 302→404 sem `Origin/Referer`). Toda a coleta RJ estava quebrada por isso (não era VPN, rota, task id nem nocache).
- **Correções aplicadas (todas em `core/collector.py`):**
  1. **`_build_session`**: header `roarand` = valor do **cookie `roarand`** da sessão (fallback p/ o campo `roarand` só se o cookie faltar). ← **a correção essencial**.
  2. **`_build_session`**: envia `Origin`, `Referer` (PM `/oss/access/pm/index.html`, FARS `/omc/farswebsite/index.html`), `Accept`, `X-Requested-With` — sem eles o POST cai em 302→SSO (não passa nem do gate same-origin).
  3. POSTs com `allow_redirects=False` + `_check_session_valid` trata `3xx→unisso/unisess/auth` como sessão inválida (não mascara como 404).
  4. `?nocache=` removido dos 2 POSTs (limpeza inócua; não era a causa).
- **`session_renew.py` NÃO foi alterado** (a heurística de CAPTCHA pré-submit que o operador tinha adicionado e quebrava o login headless foi revertida ao HEAD; o cookie `roarand` já era capturado via `context.cookies()`).
- **Validação ao vivo (VPN RJ, `tools/oss_validate.py --kind both --region RJ`):** KPI **7291 medições / 206 células**; VIP **15 medições / 8 VIPs / 14 no evento**; todos os POST **200**. Detalhe da causa em [ERRORS.md](file:///.claude/ERRORS.md).
- **Diagnóstico HTTP:** `core/collector.HttpCollector.http_debug=True` ou env `SMARTEVENTS_HTTP_DEBUG=1` loga método/status/URL + `req.roarand`/`req.bsp`/`resp.set_bsp` por requisição — foi como se achou o double-submit.
- **Organização:** criado [documentacao/ORGANIZACAO.md](file:///documentacao/ORGANIZACAO.md) (Em uso / Arquivar / Excluir). Limpeza física **não executada** (operador faz manual). Nota: `scratch/get_session.py` é load-bearing em dev — não arquivar.

### Registro anterior

### [Sessão 2026-06-01] Correção do Navegador Playwright no Executável (.exe)
- **O que foi feito:** 
  - Atualização do arquivo [main.spec](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/main.spec) para incluir a pasta do Chromium completo (`chromium-1223`) nos diretórios do Playwright empacotados (`_browser_dirs`).
  - Execução bem-sucedida do script [build.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/build.py) no ambiente virtual `.venv` para gerar o executável final em `dist/main.exe` (369.4 MB).
  - Adicionado compromisso explícito na seção `Diretrizes de Desenvolvimento e Regras do Agente` para sempre seguir as convenções de [.claude/CLAUDE.md](file:///.claude/CLAUDE.md).
- **Status:** Sucesso completo. O Chromium completo agora está embutido no executável, habilitando o modo visível (`headless=False`) para o fluxo de reautenticação com CAPTCHA do iManager.

### [Sessão 2026-06-01] Coleta VIP Rápida com Modos Expresso/Completo e Paralelismo Cauteloso
- **O que foi feito:**
  - Adicionadas constantes de controle de decodes (`_VIP_DECODES_EXPRESS = 3`, `_VIP_DECODES_FULL = 20`) e limite de concorrência (`_VIP_MAX_WORKERS = 3`) em `HttpCollector`.
  - Assinaturas de `collect_vips` e subclasses atualizadas para receber `mode: str = "express"`.
  - Implementado preflight sequencial de sessão e fan-out paralelo de coleta de VIPs por meio de `ThreadPoolExecutor` com requests.Session individuais no `HttpCollector`.
  - Atualizado `Scheduler` para alternar dinamicamente entre modos `express` e `full` (a cada 10 min) e forçar `express` no refresh sob demanda.
  - Implementado o **log-resumo de VIPs** ao final de `collect_vips` no `HttpCollector` contendo contadores e tempo de processamento.
- **Status:** Sucesso completo. O tempo de ciclo VIP foi otimizado radicalmente no modo expresso (de minutos para poucos segundos no iManager), o paralelismo foi controlado para evitar erros concorrentes no iManager FARS, e as métricas do ciclo de VIP agora são auditáveis com logs precisos.


### [Sessão 2026-06-01] Perfil persistente do Playwright na reautenticação (corrige lentidão ~3x + captura de tokens)
- **O que foi feito:** Em `core/session_renew.run`, troquei o navegador de perfil descartável (`p.chromium.launch()` + `new_context()`) por **perfil persistente** `p.chromium.launch_persistent_context("data/browser_profile", …)`. Isso mantém o cache de disco do Chromium quente entre execuções (a SPA pesada do iManager carregava ~3x mais devagar com cache vazio, fazendo os waits de PM/Trace expirarem antes do `roarand` ser capturado → rc=1 "Nenhuma sessão ou token"). Helper `_alive()` substitui `browser.is_connected()` (robusto a `context.browser` None); `browser.close()` → `context.close()`. O `run` agora sonda autenticação após `goto(login_url)` e pula o login (`already_auth`) se o perfil já estiver autenticado.
- **Arquivos:** `core/session_renew.py`. Novo diretório `data/browser_profile/` (já coberto por `data/` no .gitignore).
- **Status:** Implementado; sintaxe validada. Falta validar em runtime com VPN do cliente (observar tempo de carregamento e captura de bspsession/roarand).


### [Sessão 2026-06-01] Status de coleta no backend + indicador de sincronização no header (plano VIP, pontos 4 e 5)
- **O que foi feito:**
  - `Scheduler` mantém `self._status` (sub-dicts `kpi`/`vip`) atualizado em volta de cada coleta: `state` (idle/running/ok/error), `last_success`, `last_count`, `duration_s`, `error`, `interval_s`; para VIP também `mode`, `vips_total`, `vips_with_data`. Resetado em `start()`. Novo método `Scheduler.get_status()` retorna snapshot.
  - Novo `Api.get_collection_status()` → `{ok, recording, kpi, vip, session:{needs_interactive, region}, now}`. `needs_interactive` vem de `HttpCollector._needs_interactive`; `region` de `collector._region`. Segue convenção da Api (sempre dict).
  - Frontend: `#sync-indicator` no header (ícone gira enquanto coleta roda, mostra "VIP há 12s"/"Reautenticar"; clique abre `#sync-modal` com KPI/VIP/sessão/próximo ciclo). `app.js` faz poll leve de 5s (`_pollSyncStatus`) só em modo ativo; `bridge.js` ganhou `getCollectionStatus()` + mock. Datas UTC naïve do backend tratadas via `_parseUtc` (anexa "Z").
- **Status:** Implementado; sintaxe Python e JS validada. Falta validar em runtime (UI e VPN).

### [Sessão 2026-06-01] Estabilidade de login: credenciais regionais, fechamento rápido pós-login e persistência de sessão
- **Issue 1 — credenciais de SP no login do RJ:** `core/session_renew._resolve_credentials` agora só usa os defaults (conta SP) quando `region in ("", "SP")`. Para regional diferente sem conta, devolve credenciais vazias (login em branco p/ o operador) e loga aviso para configurar `data/credentials.json["RJ"]`. Nunca usar a conta de uma regional como fallback de outra.
- **Issue 2 — navegador ficava ~80s aberto após login:** caminho `fast_capture = already_auth or not headless` pula a navegação PM/Trace e o bloco de metadados de dev (agora atrás de `SMARTEVENTS_CAPTURE_METADATA=1`); na reauth do operador captura cookies+roarand e fecha logo após a sonda final.
- **Issue 3 (ROOT CAUSE) — pedia login a cada início:** `main.py` chamava `webview.start(storage_path=data/)` sem `private_mode=False`. O pywebview em private_mode (default True) APAGA a storage_path ao fechar → destruía o `session.json` a cada saída. Corrigido com `private_mode=False`; agora `session.json` e `data/browser_profile` persistem e o login só é refeito quando a sessão expira no servidor.
- **Arquivos:** `core/session_renew.py`, `main.py`. **Pendente:** validar em runtime com VPN do cliente (RJ) — confirmar que (a) não preenche credenciais de SP, (b) fecha rápido após login, (c) ao reabrir o app não pede login se a sessão ainda estiver válida.

### [Sessão 2026-06-02] Correção dos 3 bugs de tratamento de cookies (path / poluição regional / mismatch de domínio)
- **Bug 1 (path descartado):** `core/session_renew.py` (bloco "4. Cookies e tokens globais") passou a gravar `path` em cada cookie; `core/collector._build_session()` passou a aplicar `path=cookie.get("path", "/")` no `sess.cookies.set(...)`. Sem isso, o `requests` assumia `path="/"` e cookies homônimos (ex.: `JSESSIONID` do SSO `/unisso` vs. da app `/`) se sobrescreviam, enviando o cookie errado e disparando loops de reauth.
- **Bug 2 (poluição entre regionais):** o perfil do Chromium é compartilhado (`data/browser_profile`), então `context.cookies()` sem filtro vazava cookies de outros OSS no `session.json`. Solução: filtrar por **HOST** da `base_url` (`urllib.parse.urlparse(base_url).hostname`, comparando o `domain` de cada cookie sem ponto inicial). ⚠️ NÃO usar `context.cookies(urls=[base_url])`: o Playwright casa host **E path**, e a `base_url` (path raiz `/`) descarta o `bspsession` quando ele está num path não-raiz → captura vazia e `rc=1` "Nenhuma sessão ou token pôde ser capturado" no reauth interativo do RJ (regressão observada em runtime 2026-06-02 e revertida para filtro por host). Registrado em [ERRORS.md](file:///.claude/ERRORS.md). Com a lista filtrada por host, `bspsessions[0]` é sempre o correto.
- **Bug 3 (mismatch de domínio do CookieJar):** `_build_session()` agora alinha o `domain` de cada cookie ao host real de `self.base_url` (`urllib.parse.urlparse(self.base_url).hostname`, com `import urllib.parse` local como no resto do módulo), em vez do domínio gravado. Como o coletor SEMPRE bate em `self.base_url`, isso garante que o `requests` anexe os cookies mesmo se o evento usar hostname/IP diferente do capturado pelo Playwright. Fallback para o domínio gravado se o parse falhar.
- **Compatibilidade:** retrocompatível — `session.json` antigos sem `path` caem no default `"/"`. Estrutura do JSON inalterada (só ganha o campo `path` por cookie).
- **Arquivos:** `core/session_renew.py`, `core/collector.py`. Origem do diagnóstico: `explicacao-coleta.md`. **Status:** implementado, `py_compile` OK nos dois módulos. **Pendente:** validar em runtime com VPN — inspecionar `data/session_*.json` (cada cookie com `path`, só host da regional) e confirmar ausência de loop de reauth.

### [Sessão 2026-06-02] Correção de falso positivo na sonda REST da sessão (loop de reautenticação)
- **O que foi feito:**
  - Corrigido o bug em que a reautenticação manual fechava prematuramente a janela do operador sem de fato obter cookies/tokens logados devido a um falso positivo na detecção de autenticação.
  - O problema ocorria porque a sonda REST (`_probe_authenticated` no renovador e `_check_session_valid` no coletor) recebia a página de login SSO redirecionada com status HTTP `200 OK`, mas a página HTML possuía uma tag `<head>` extremamente longa (mais de 2000 caracteres), o que empurrava as palavras-chave de login/sso para além do limite de varredura do corpo (2000 caracteres), enganando o sistema a achar que a sessão estava autenticada.
  - Implementada verificação rígida da URL da resposta (`unisso` ou `login.action`) nas funções `_is_auth_response` em [session_renew.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/core/session_renew.py) e `_check_session_valid` em [collector.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/core/collector.py) para classificar o redirecionamento como não-autenticado imediatamente.
- **Arquivos:** `core/session_renew.py`, `core/collector.py`.
- **Status:** Sucesso completo. O operador consegue realizar o login e o CAPTCHA normalmente na janela visível e a janela só se fecha após a sonda REST detectar a real autenticação da sessão.

