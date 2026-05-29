# Smart Events — Instruções para o Claude Code

## O que é este projeto

Plataforma desktop de monitoramento de RF em tempo real para grandes eventos (GPs, shows, finais de campeonato). Equipes de NOC visualizam saúde da rede, acompanham VIPs e respondem a alertas de capacidade.

Stack: **HTML + CSS + JS** no frontend, **Python + PyWebView** para o desktop, **SQLite** como banco local. Sem servidor dedicado. O executável (.exe) é portátil: basta conectar à VPN do cliente e rodar.

---

## Arquitetura em uma linha

```
OSS/Trace (VPN cliente) → Collector (Python) → SQLite → Api.py → PyWebView → JS Frontend
```

O JS nunca acessa o banco diretamente. Tudo passa pela classe `Api` em `api/api.py`, exposta como `window.pywebview.api` no frontend.

---

## Estrutura de arquivos

```
main.py                  # Entrada: cria janela PyWebView, injeta Api
server.py                # Servidor central FastAPI para coordenação de eventos na rede
requirements.txt

api/
  api.py                 # ÚNICO ponto de contato Python ↔ JS
                         # Métodos públicos viram window.pywebview.api.método()

core/
  models.py              # Dataclasses: Site, Cell, VIP, EventConfig, Alert...
  database.py            # Todo acesso ao SQLite fica aqui (nunca em outro lugar)
  collector.py           # Coleta dados do OSS. Tem 3 implementações:
                         #   CsvCollector  → lê exports do iManager (apenas KPI)
                         #   HttpCollector → REST direto no iManager (KPI + trace)
                         #   MockCollector → dados sintéticos para dev/testes
  scheduler.py           # Roda coleta em background threads (120s KPI, 60s VIP)
                         # Avalia alertas após cada coleta

frontend/
  index.html             # Shell HTML: estrutura, sem lógica
  css/main.css           # Design system completo (tokens CSS, dark theme)
  js/
    app.js               # Orquestrador: boot, ciclo de vida, polling de 30s
    bridge.js            # Wrapper JS → Python. Em dev sem PyWebView usa mock data
    state.js             # Estado global pub/sub (State.set, State.on, State.emit)
    map.js               # Leaflet + marcadores SVG de setor (fan/pétala)
    vip.js               # Renderiza painel VIP lateral
    kpi.js               # Lista de sites + Chart.js com threshold lines + gap zones
    alerts.js            # Painel de alertas e download de logs

server_frontend/
  index.html             # Interface web do Servidor Central para cadastrar/editar eventos e VIPs

server_data/
  events/                # Banco de arquivos JSON de eventos gerenciados pelo Servidor Central
  vips/                  # Banco de arquivos JSON de VIPs gerenciados pelo Servidor Central

events/
  sample_event.json      # Evento de exemplo para testes

data/
  smart_events.db        # Gerado automaticamente. Nunca commitar.
```

---

## Padrões críticos

### 1. Comunicação JS → Python

Sempre via `API` de `bridge.js`. Nunca chamar `window.pywebview.api` diretamente.

```javascript
// CORRETO
import API from "./bridge.js";
const sites = await API.getSites(eventId);

// ERRADO — não fazer
const sites = await window.pywebview.api.get_sites(eventId);
```

`bridge.js` garante que em modo dev (sem PyWebView rodando) os dados mock são retornados automaticamente, permitindo desenvolver o frontend no browser normal.

### 2. Estado global

Todo dado compartilhado entre módulos vai em `state.js`. Nenhum módulo importa outro módulo para pegar dados — apenas assina o State.

```javascript
// CORRETO — reage a mudanças
State.on("change:sites", (sites) => renderSiteList(sites));

// CORRETO — atualiza estado
State.set("selectedSite", siteId);
State.set("selectedCell", cellId); // "__all__", "__media__" ou ID da célula

// ERRADO — import circular entre módulos
import { renderSiteList } from "./kpi.js"; // em map.js
```

### 3. Banco de dados

Apenas `core/database.py` importa `sqlite3`. Todo o resto importa `core.database`.

```python
# CORRETO
from core import database as db
db.insert_kpi_batch(measurements)

# ERRADO
import sqlite3
conn = sqlite3.connect("data/smart_events.db")  # nunca fora de database.py
```

Conexões SQLite são thread-local (`_local = threading.local()`). O scheduler roda em threads separadas — isso já está tratado em `database.get_conn()`.

### 4. VIPs são identificados por `task_id`, não por IMSI

Cada VIP no cadastro global de VIPs possui uma regional (`oss`) e uma `task_id` apontando para a sua Signaling Trace dedicada no iManager. O collector carrega dinamicamente todos os VIPs pertencentes ao OSS do evento ativo via `db.get_event_vips(event_id)` e itera `vips_by_task` (`{task_id: nome}`). Cada chamada ao FARS já sabe a quem pertence — não há matching por IMSI nem dependência do campo `GUUserId` (que costuma vir `N/A` no payload do trace).

A classe `Api` tem `_sanitize_event()` que remove `sites` inteiros do payload antes de serializar.

---

## Modos de execução

### 5. Alertas em 3 níveis

```
GLOBAL   → sempre ativos (site fora do ar, célula indisponível)
EVENT    → configurados no JSON do evento (RSRP abaixo de X, utilização acima de Y%)
INSTANCE → operador silencia na UI (não persiste entre sessões)
```

Silenciamento via `db.silence_alert(alert_key)`. A key segue o padrão `{tipo}_{severidade}_{site_id}`, ex: `util_crit_ERB-07`.

### 6. Sincronização de Eventos e VIPs (Central Server ↔ SQLite Local)

- O app desktop busca e sincroniza eventos e cadastros globais de VIPs a partir do servidor central configurado na URL `server_url` (em Settings).
- A sincronização local (`database.py` e `api.py`) é realizada ao abrir o app e pode ser forçada manualmente no menu de configurações.
- Regra de precedência de status: ao sincronizar um evento, se o status no banco local for `ACTIVE` ou `ENDED`, ele é preservado sobre o status `SCHEDULED` vindo do servidor central.

### 7. Limpeza Definitiva do Histórico do Evento (Clear Event History)

- O indicador de gravação (`rec-indicator` "REC X MB") na barra superior do frontend é interativo. Ao clicar nele, inicia-se o fluxo de limpeza de histórico do evento corrente.
- Possui um modal de dupla confirmação para evitar exclusões acidentais de dados de campo.
- Ao confirmar, as tabelas `kpi_measurements`, `vip_measurements` e `alerts` do evento atual são deletadas no banco local SQLite, o conjunto de arquivos processados (`_processed`) no coletor de CSV é zerado, e é executada uma instrução `VACUUM` no SQLite para liberar espaço físico no disco.

### 8. Navegação e Seleção Dinâmica de Eventos no Cliente

- O header da aplicação contém um menu dropdown que lista todos os eventos cadastrados (divididos em ativos, agendados e históricos/encerrados).
- Selecionar um evento no dropdown chaveia dinamicamente a aplicação entre o modo **Monitoramento Ativo** (para eventos ativos/agendados, iniciando as threads do `scheduler`) e modo **Histórico/Leitura** (para eventos encerrados, permitindo navegar pelas coletas gravadas usando um controle de timeline/slider).
- Se a duração de um evento ativo/agendado for maior que 7 dias, o timer progressivo do evento no header é ocultado automaticamente para melhor ergonomia visual.

### 9. Destaque Visual de VIPs em Sites do Evento (VIPs at Site Highlight)
- **Resolução de Site**: O trace do iManager reporta o nome do site receptor no campo `source` de `tableData` (ex. `SR-RPITJ2`) e o ID local da célula em `GLCellId` (ex. `2`). O coletor concatena estes campos como `SR-RPITJ2_2` e grava no campo `serving_cell`. A API em seguida faz o parse do nome do site ou divide IDs de células numéricos globais por `256` (4G ECI) ou `4096` (5G NCI) para correlacionar e resolver o `serving_site` e `serving_site_name` mapeados no JSON do evento.
- **Destaque na Lista (Sidebar)**: Se o VIP estiver no evento (`vip.in_event`) e conectado a um site do evento (`vip.serving_site`), o card dele recebe a classe `.vip-card.vip-at-site`. Isso estiliza o card com fundo dourado translúcido sutil (`--vip-gold-bg`), borda lateral direita dourada (`--vip-gold`), e cor dourada nos textos do nome e site. O nome é prefixado com o ícone de coroa (`👑`). Ao clicar em um card de VIP com site conectado (`vip.serving_site`), o app atualiza o estado `selectedSite`, fazendo com que o mapa centralize e aproxime (zoom) no site e abra seu respectivo popup, além de destacar o site na listagem inferior.
- **Destaque no Mapa (Leaflet)**: O site correspondente ganha uma insígnia dourada "V" no canto superior direito do ícone SVG e o popup do Leaflet exibe os nomes dos VIPs conectados ao site.
- **Detalhes no Popup**: Ao clicar em qualquer VIP no painel (esteja ele no evento ou fora), o popup de detalhes do VIP (`#vip-detail-modal`) exibe o nome do último site conectado / site atual (`serving_site_name`) e a data e hora de seu último registro (`last_timestamp`).
- **Destaque na Lista de Sites (KPI Panel)**: Um emoji de coroa (`👑`) é adicionado à direita do nome do site na lista de sites caso haja algum VIP ativo conectado àquele site. A lista reage e atualiza as coroas dinamicamente quando o estado dos VIPs muda.

### 10. Visualização de Células Individuais no Gráfico do Site Completo (Multi-Cell Chart Comparison & Popup View)
- **Modo Site Completo**: Quando o seletor de célula está em `Site completo` (`cell_id = "__all__"`), o gráfico de multi-células é exibido automaticamente em um popup premium que cobre 80% da tela (esta é a única forma de visualização de todo o site completo para melhor leitura). O contêiner de gráfico principal exibe um placeholder informando que o gráfico está no popup com um botão para reabri-lo.
- **Botão de Expandir**: Adicionado no canto superior direito do painel de gráficos (`#expand-chart-btn`), permite abrir esse mesmo popup de 80% da tela para visualizar de forma mais detalhada qualquer gráfico de célula individual (sem afetar a visualização do painel principal).
- **Backend API (`get_kpi_series`)**: Retorna o dicionário `cells_data` mapeando `cell_id -> list[float]` alinhado aos `labels` temporais (timestamps).
- **Legenda e Interação**: A legenda do Chart.js é exibida apenas no modo expandido/popup para identificar as células por cor. O tooltip de hover está configurado com `mode: "index"` e `intersect: false`, permitindo ver os valores comparativos de todas as células para qualquer instante.

---

## Modos de execução

```bash
python main.py              # Produção (requer VPN cliente)
python main.py --mock       # Dev com dados sintéticos (MockCollector)
python main.py --mock --dev # Dev + DevTools aberto na janela
```

O frontend também pode ser aberto diretamente no browser (sem Python) para desenvolver CSS/JS: `bridge.js` detecta a ausência de `window.pywebview` e usa os mock data embutidos.

---

## Coleta de dados

Duas fontes coexistem; o `build_collector()` escolhe na ordem: mock → CSV (se `oss.import_folder` existir) → HTTP.

No caso do coletor HTTP, a `base_url` é resolvida a partir de `oss.base_url`. Se vazia, é mapeada de acordo com `oss.region` (ex: `SP` -> `https://10.220.50.9:31943`, `RJ` -> `https://10.220.30.9:31943`), tendo como fallback final a regional `SP`.

Cada regional utiliza um arquivo de sessão isolado em `data/` para evitar conflito de cookies (SP usa `session.json`, outras regionais usam `session_<ip_slug>.json` como `session_10_220_30_9.json`). O script de renovação `scratch/get_session.py` aceita `--base-url` e `--session-file` dinâmicos.

**HTTP (Fase 2 — em produção):** `HttpCollector` consome diretamente o iManager via REST. A sessão é renovada por `scratch/get_session.py` (Playwright, login + cookies + roarand).

- **KPI:** `POST /rest/oss/access/pm/v1/monitor/task/result` com `objNoExecTimes` (lista de cells). Cada cell precisa do campo `obj_no` no JSON do evento.
- **Trace de VIP:** fluxo de 4 passos descoberto na UI nativa do iManager FARS:
  1. `GET /rest/oss/access/fars/v1/traceresult/pre-check?taskId={id}&queryType=0` — inicializa a sessão de query no backend FARS.
  2. `GET /rest/oss/access/fars/v1/traceresult/query/result?startRow=0&pageSize=1&taskId={id}&msgId=1` — devolve a resposta contendo o `msgId` interno da sessão de query.
  3. `POST /rest/oss/access/fars/v1/traceresult/query/filter-by-cols` com payload JSON para filtrar por `Message Type` = `RRC_MEAS_RPRT` e `isAscend` = `false` — devolve as mensagens ordenadas decrescente por data/hora (mais recente primeiro).
  4. Para cada `RRC_MEAS_RPRT` (limitado), `GET /rest/oss/access/fars/v1/traceresult/query/msg-explain-info` com `rowNo = índice + 1` (1-indexado relativo à view filtrada) — devolve árvore decodificada com `rsrpResult` / `rsrqResult`.

  ⚠️ Não use `/traceresult/query/sort` para puxar resultados. Ele é só para reordenar uma sessão já criada — sem `pre-check` antes, retorna 500 "server communication linkage of task".

**CSV (Fase 1 — apenas KPI):** `CsvCollector` lê `kpi_*.csv` em `oss.import_folder`. Arquivos rastreados em `_processed` (set em memória) — reiniciar o app reprocessa tudo. O CSV de trace foi descontinuado: VIPs agora são identificados por `task_id` (vide seção 4), não por matching de IMSI no Source.

---

## Schema do banco (Divisão por Evento)

O SmartEvents divide os dados em dois tipos de bancos SQLite localizados na pasta `data/`:
1. **Banco Central/Global (`smart_events.db`)**: Contém a lista de eventos, o cadastro global de VIPs e o silenciamento de alertas:
   - `events`            -- Configurações e metadados dos eventos
   - `vips`              -- Cadastro global de VIPs (com coluna regional `oss`)
   - `silenced_alerts`   -- Chaves de alertas silenciados

2. **Banco Específico do Evento (`smart_events_<event_id>.db`)**: Criado dinamicamente para cada evento para isolar medições e evitar conflitos:
   - `sites`             -- Sites associados a este evento
   - `event_vips`        -- Associação local VIP↔Evento com a `task_id` específica do evento
   - `kpi_measurements`  -- Série temporal de KPIs: site_id, cell_id, metric, value, timestamp
   - `vip_measurements`  -- Série temporal de VIPs: vip_name, rsrp, rsrq, serving_cell, in_event
   - `alerts`            -- Alertas gerados no evento

Conexões SQLite são thread-local. O banco global usa `database.get_conn()`, enquanto o banco do evento usa `database.get_event_conn(event_id)`.

Índices críticos em `kpi_measurements(site_id, metric, timestamp)` e `vip_measurements(vip_name, timestamp)`. Não remover esses índices — queries de série temporal dependem deles.

---

## Design system (CSS)

Tokens principais em `frontend/css/main.css`:

```css
--bg-base:      #0D1117   /* fundo principal */
--bg-surface:   #161B22   /* cards, painéis */
--accent:       #CF0A2C   /* Huawei red — usar só para brand e alertas críticos */
--success:      #3FB950   /* verde operacional */
--warning:      #D29922   /* âmbar operacional */
--danger:       #F85149   /* vermelho operacional */
```

Nunca usar `--accent` para status de rede. Status sempre via `--success / --warning / --danger`.

---

## Convenções de código

**Python:**
- Type hints em todas as funções públicas
- `logger = logging.getLogger(__name__)` em cada módulo
- Métodos da classe `Api` retornam sempre `dict` com `{"ok": bool, ...}` ou `list`
- Nunca levantar exceção nos métodos da `Api` — capturar e retornar `{"ok": False, "error": str(e)}`

**JavaScript:**
- ES Modules (`import/export`), sem bundler
- `async/await` para todas as chamadas à `API`
- Sanitizar strings antes de inserir em `innerHTML` (usar `_esc()` presente em cada módulo)
- Nenhum `console.log` de debug no commit — usar `console.error` apenas em catch blocks

---

## O que está fora do escopo atual

Não implementar sem discutir primeiro:
- Login ou gestão de usuários
- Servidor dedicado ou banco remoto
- Compartilhamento de dados entre instâncias
- GPS de VIPs
- Integração com ferramenta Lenin (fase final)
- Agente de IA para diagnóstico automático
- App mobile nativo (apenas export PDF/HTML)

---

## Tarefas comuns

**Adicionar nova métrica de KPI:**
1. Adicionar `option` em `frontend/index.html` no `#metric-selector`
2. Adicionar label em `METRIC_LABELS` em `kpi.js`
3. Mapear coluna do CSV em `CsvCollector.KPI_COLUMN_MAP` em `collector.py` (e em `HttpCollector.KPI_COLUMN_MAP` se aplicável na Fase 2)

**Adicionar novo tipo de alerta:**
1. Avaliar no `_evaluate_kpi_alerts` ou `_evaluate_vip_alerts` em `scheduler.py`
2. Seguir padrão de `alert_key` para suporte a silenciamento

**Adicionar campo ao JSON de evento:**
1. Atualizar `core/models.py` (dataclass `EventConfig`)
2. Atualizar `api/api.py` → `_sanitize_event()` se necessário
3. Atualizar `events/sample_event.json`

**Mapear nova coluna na importação de sites (Servidor Central):**
1. Atualizar a estrutura de sinônimos/aliases no dicionário `col_mappings` em `server.py` (linha ~90).

