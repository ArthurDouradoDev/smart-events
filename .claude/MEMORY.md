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
