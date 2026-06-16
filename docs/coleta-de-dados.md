# Processo de Coleta de Dados: VIPs e KPIs — SmartEvents

> **Nota (2026-06-12):** documento canônico da coleta (ex-`descricao-coleta.md`, escrito em 2026-05-28).
> Mudanças posteriores relevantes estão registradas em `.claude/MEMORY.md`, em especial a correção
> definitiva da coleta RJ (2026-06-11): o header `roarand` deve ecoar o **cookie** `roarand`
> (anti-CSRF double-submit) e os POSTs exigem `Origin`/`Referer`.

Este documento descreve detalhadamente o fluxo de coleta de dados no **SmartEvents**, abrangendo desde as fontes de dados, a orquestração em segundo plano pelo agendador, os mecanismos de autenticação e sessão com o iManager da Huawei via Playwright, as fórmulas físicas de conversão de sinal, até a persistência no banco SQLite e a lógica de geração de alertas.

---

## 1. Arquitetura Geral e Fluxo de Dados End-to-End

O pipeline de dados do SmartEvents foi desenhado para consolidar dados de performance de rede (KPIs) e traces de sinalização de usuários (VIPs) em tempo quase real, exibindo-os de maneira unificada e dinâmica.

O fluxo de dados segue a seguinte sequência lógica:

```mermaid
graph TD
    subgraph Fontes de Dados
        iManager[Huawei iManager U2020]
        FARS[Módulo FARS - Signaling Trace]
    end

    subgraph Coleta Backend (Python)
        Playwright[get_session.py Playwright Headless]
        Session[session.json cookies & CSRF token]
        Collector[HttpCollector / CsvCollector / MockCollector]
        Scheduler[Scheduler.py Orquestrador / Threads]
    end

    subgraph Armazenamento (SQLite)
        DBCentral[(smart_events.db Banco Central)]
        DBEvento[(smart_events_eventID.db Banco do Evento)]
    end

    subgraph Consumo e Interface
        API[api.py FastAPI/Ponte Webview]
        UI[Frontend HTML/JS/CSS]
    end

    %% Fluxo de Sessão
    Playwright -->|Renova cookies e roarand| Session
    Session -->|Autentica Requisições| Collector

    %% Fluxo de Coleta
    iManager -->|Dados de KPI / PM| Collector
    FARS -->|Mensagens RRC de Trace| Collector
    Collector -->|Batch de KPIs| Scheduler
    Collector -->|Batch de VIPs| Scheduler

    %% Fluxo de Persistência e Alertas
    Scheduler -->|Insere KPIs / Avalia Thresholds| DBEvento
    Scheduler -->|Insere VIPs / Avalia Sinal| DBCentral
    DBEvento -->|Alertas gerados| DBEvento

    %% Fluxo de API e UI
    DBEvento -->|Leitura de Séries Temporais| API
    DBCentral -->|Leitura de Status dos VIPs| API
    API -->|Notifica Callback _on_update| UI
```

---

## 2. O Agendador (Scheduler)

O arquivo [scheduler.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/core/scheduler.py) é o cérebro temporal da aplicação. Ele roda em background e gerencia ciclos de varredura assíncronos para não bloquear o funcionamento da interface de usuário (`pywebview`).

### A. Divisão em Threads Dedicadas
Quando um evento é iniciado (`status = 'ACTIVE'`), o Scheduler cria duas threads independentes em modo *daemon*:
1. **Thread `kpi-collector`**:
   - **Objetivo**: Coletar indicadores globais de capacidade e acessibilidade dos sites associados ao evento.
   - **Intervalo**: **120 segundos** (2 minutos), sincronizado com o período mínimo de atualização típico de contadores de PM (*Performance Monitor*) no iManager.
2. **Thread `vip-collector`**:
   - **Objetivo**: Coletar dados de cobertura e sinalização em tempo quase real dos usuários classificados como VIPs.
   - **Intervalo**: **60 segundos** (1 minuto), permitindo rastrear deslocamentos ou quedas de sinal de forma rápida.

### B. Ciclo de Vida do Loop
Cada thread executa um loop contínuo baseado em `threading.Event`. O fluxo de cada iteração consiste em:
1. **Executar a Coleta**: Invoca o método correspondente do coletor ativo (`collect_kpis()` ou `collect_vips()`).
2. **Persistir os Resultados**: Grava as medições no banco de dados SQLite.
3. **Avaliar Regras de Negócio**: Processa os dados contra as regras de limites (*thresholds*) para gerar possíveis alertas.
4. **Notificar a Interface**: Dispara o callback `_on_update`, que avisa o frontend (JavaScript) que novos dados estão disponíveis para recarga assíncrona nas telas.

---

## 3. As Estratégias de Coleta (Collectors)

O SmartEvents utiliza uma fábrica de coletores (`build_collector()`) que define qual classe utilizar com base na configuração do evento e nos argumentos de execução:

```
                  ┌─────────────── BaseCollector ───────────────┐
                  │                 (Interface)                 │
                  └──────────────────────┬──────────────────────┘
             ┌───────────────────────────┼───────────────────────────┐
  ┌──────────┴──────────┐     ┌──────────┴──────────┐     ┌──────────┴──────────┐
  │    MockCollector    │     │    CsvCollector     │     │    HttpCollector    │
  │ (Desenvolvimento)   │     │ (Fase 1 - Arquivos) │     │ (Fase 2 - API Real) │
  └─────────────────────┘     └─────────────────────┘     └─────────────────────┘
```

### A. MockCollector (Modo de Desenvolvimento)
* **Ativação**: Parâmetro `--mock` na inicialização (`python main.py --mock`).
* **Funcionamento**: Simula de forma autônoma o comportamento de rede sem precisar de acesso à VPN do cliente ou ao iManager. Utiliza distribuições de probabilidade gaussiana (`random.gauss`) em torno de médias operacionais típicas:
  - **Métricas de KPI**: PRB DL (~50%), Throughput DL (~40 Mbps), Acessibilidade (~99.5%), etc.
  - **Métricas de VIP**: RSRP variando em torno de -88 dBm, escolhendo de forma randômica uma das células ativas do evento para simular mobilidade.

### B. CsvCollector (Coleta Manual em Arquivos)
* **Ativação**: Se a propriedade `oss.import_folder` do evento apontar para um diretório local válido.
* **Funcionamento**: Varre a pasta à procura de arquivos com o padrão `kpi_*.csv` gerados através de exportação manual do iManager.
  - **Mapeamento Flexível**: Para evitar falhas por pequenas variações nos nomes das colunas ao exportar no iManager, a classe mapeia os cabeçalhos usando listas de sinônimos definidos em `KPI_COLUMN_MAP`.
  - **Prevenção de Reprocessamento**: Mantém em memória um conjunto `_processed` com os nomes dos arquivos lidos. Apenas arquivos novos são analisados por ciclo.

### C. HttpCollector (Coleta em Produção via API REST)
Esta é a estratégia principal do sistema. Ela realiza chamadas diretas aos endpoints REST do iManager da Huawei através de sessões HTTP autenticadas.

---

## 4. O Fluxo de Autenticação e Sessão (HttpCollector)

A integração via HTTP com o iManager envolve um pipeline robusto de autenticação, captura de credenciais e contramedidas para expiração de sessão.

### A. Arquivo de Sessão (`session.json`)
Os cookies de autenticação e os tokens de segurança são centralizados no arquivo `data/session.json`. O formato esperado para cada módulo do coletor é estruturado da seguinte forma:

```json
{
    "trace": {
        "bspsession": "TOKEN_BSP_SESSION",
        "roarand": "TOKEN_CSRF_ROARAND",
        "task_id": 12345,
        "cookies": [
            { "name": "JSESSIONID", "value": "...", "domain": "..." }
        ]
    },
    "monitoring": { ... }
}
```

### B. Renovação de Sessão via Playwright (`get_session.py`)
Caso o coletor receba uma resposta de erro (HTTP `401`, `403` ou redirecionamento HTML contendo telas de Login/SSO), a sessão é considerada expirada. O HttpCollector então dispara o script auxiliar [get_session.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/scratch/get_session.py) via subprocesso Python em modo oculto (*headless*):

1. **Navegação e Preenchimento**: O Playwright abre uma instância do Chromium (ignorando erros de certificado HTTPS autossinado comuns em redes de operadoras).
2. **Login no Portal**: Acessa a página de SSO, insere o usuário e a senha criptografados/configurados no script e clica em entrar.
3. **Mapeamento de Módulos**: Navega até as páginas específicas de monitoramento de performance (`Performance Monitor`) e trace de sinalização (`Signaling Trace`).
4. **Interceptação de Tráfego**: Um observador de requisições (`monitor_requests`) intercepta as requisições AJAX do navegador para capturar:
   - O header HTTP `roarand` (token de proteção CSRF exigido pelas requisições POST do iManager).
   - O cookie `bspsession`.
   - O ID das tarefas ativas (`taskId`) e os identificadores de objetos de células (`objNo`).
5. **Gravação**: Grava os dados renovados em `data/session.json`, permitindo que as requisições HTTP subsequentes do coletor utilizem estes cabeçalhos.

### C. Concorrência e Backoff Exponencial
Para assegurar a estabilidade operacional do backend e não sobrecarregar o iManager:
* **Thread Locking**: A renovação de sessão é protegida por um `threading.Lock`. Se múltiplas threads detectarem expiração ao mesmo tempo, apenas a primeira executará o Playwright; as subsequentes aguardarão a liberação do lock e recarregarão a sessão diretamente do arquivo gerado.
* **Backoff Exponencial de Falhas**: Se a renovação falhar consecutivamente (por exemplo, VPN desconectada ou credenciais inválidas), o sistema aplica um atraso acumulado (60s, 120s, 180s até o teto de 300s) bloqueando tentativas repetitivas de rodar o Playwright para economizar CPU e evitar bloqueios na conta do iManager.

---

## 5. Processamento dos KPIs de Rede

Os KPIs monitoram a saúde das células que compõem os sites do evento. O processo divide-se em mapeamento de objetos e requisição de métricas:

### A. Mapeamento de Células (`objNo`)
No iManager, as células não são consultadas diretamente por seu nome ou ID de string (ex: `L_SITE_A_1`), mas por um identificador numérico interno do banco de dados chamado **`objNo`**.
1. **Mapeamento Estático**: Se já definidos no cadastro do evento, os pares `objNo` $\leftrightarrow$ `cell_id` são guardados.
2. **Descoberta Dinâmica (Auto-Mapeamento)**: Se houver células no evento que ainda não possuem um `objNo` cadastrado, o HttpCollector envia uma requisição de consulta com a lista de objetos vazia (`objNoExecTimes = []`). A resposta trará as estatísticas de todos os objetos monitorados pela tarefa de monitoramento. O coletor então varre o campo `objName` retornado, compara com o nome das células do evento (usando normalização de strings, remoção de traços e caixa alta) e mapeia os novos `objNo` de forma dinâmica em tempo de execução para os próximos ciclos de coleta.

### B. Requisição de Resultados
O coletor executa um `POST` para o endpoint:
`/rest/oss/access/pm/v1/monitor/task/result?nocache=TIMESTAMP`

Enviando a estrutura JSON indicando a tarefa de PM (`pm_task_id`) e a lista filtrada de `objNo` das células associadas ao evento.

```json
[
  {
    "taskId": 374,
    "preExecTime": 1716900000000,
    "objNoExecTimes": [
      { "preExecTime": 1716900000000, "objNo": 10543 },
      { "preExecTime": 1716900000000, "objNo": 10544 }
    ]
  }
]
```

### C. Mapeamento de Métricas no Parsing
O JSON retornado do iManager é analisado recursivamente para extrair os contadores de interesse configurados no dicionário `KPI_COLUMN_MAP`:

| Chave do Sistema | Contadores iManager Suportados (Candidates) |
| :--- | :--- |
| **`utilization_dl`** | `DL PRB USAGE`, `DL PRB Usage` |
| **`traffic_volume_dl`** | `Traffic Volume DL`, `{BRDC} Traffic Volume DL LTE`, `Traffic Volume DL LTE`, `{BRDC} NR DL Traffic Volume`, `NR DL Traffic Volume` |
| **`traffic_volume_ul`** | `Traffic Volume UL`, `{BRDC} Traffic Volume UL LTE`, `Traffic Volume UL LTE`, `{BRDC} NR UL Traffic Volume`, `NR UL Traffic Volume` |
| **`throughput_dl`** | `DL User Throughput`, `{BRDC} DL User Throughput`, `{BRDC} DL User Throughput LTE`, `DL User Throughput LTE`, `{BRDC} NR DL User Throughput`, `NR DL User Throughput` |
| **`throughput_ul`** | `UL User Throughput`, `{BRDC} UL User Throughput`, `{BRDC} UL User Throughput LTE`, `UL User Throughput LTE`, `{BRDC} NR UL User Throughput`, `NR UL User Throughput` |
| **`user_count`** | `{BRDC} Usuario`, `Usuario`, `Active Users`, `{BRDC} User PCell` |
| **`accessibility`** | `{BRDC} Acessibilidade`, `Acessibilidade RRC`, `ACC RRC`, `Accessibility` |

---

## 6. Coleta dos Traces de VIPs (Signaling Trace via FARS)

A coleta de VIPs não funciona por consultas diretas de contadores, mas por análise das mensagens de sinalização enviadas pelo celular do usuário VIP conectadas ao sistema FARS (*Fast Analysis of Signaling Trace*). Cada VIP possui uma tarefa de Signaling Trace ativa no iManager mapeada por um `task_id` único.

O fluxo de coleta de trace consome a API REST interna em 4 passos:

### Passo 1: Inicialização da Consulta (Pre-check)
Faz uma requisição `GET` para o endpoint abaixo para avisar o servidor FARS de que uma sessão de leitura está iniciando para a tarefa específica:
`/rest/oss/access/fars/v1/traceresult/pre-check?taskId={task_id}&queryType=0`

Se a propriedade `checkState` retornada for `false`, o ciclo daquele VIP é abortado por indisponibilidade temporária do serviço iManager.

### Passo 2: Abertura de Sessão de Query
Faz uma requisição `GET` ao endpoint de resultados para registrar e recuperar um identificador de sessão de mensagem (`sess_msg_id` ou `msgId`):
`/rest/oss/access/fars/v1/traceresult/query/result?taskId={task_id}&msgId=1&pageSize=10`

### Passo 3: Filtragem Avançada (Filter-by-cols)
O iManager gera milhares de mensagens de sinalização (como trocas de chaves de criptografia, medições internas, requisições de portadora). Para otimizar a performance e focar no que importa, o SmartEvents faz uma chamada `POST` filtrando e ordenando a tabela:
`/rest/oss/access/fars/v1/traceresult/query/filter-by-cols`

No corpo da requisição, é enviado um filtro específico para reter apenas mensagens do tipo **`RRC_MEAS_RPRT`** (Measurement Report do RRC) e com ordenação por tempo invertida (campo `Time` de forma descendente, para trazer os dados mais recentes primeiro).

### Passo 4: Decodificação da Mensagem (Msg Explain Info)
O endpoint de filtro retorna o metadado básico das mensagens. No entanto, os níveis de sinal RSRP/RSRQ não estão no corpo básico; eles vêm encapsulados na mensagem RRC bruta (hexadecimal ou bytes compactados).

Para extrair os valores físicos, o HttpCollector executa requisições individuais limitadas a no máximo **50 decodificações por ciclo** (para economizar banda e tempo de processamento) no endpoint:
`/rest/oss/access/fars/v1/traceresult/query/msg-explain-info?taskId={task_id}&msgId={sess_msg_id}&rowNo={row_no}&tabularFlag=y`

*Nota: O parâmetro `rowNo` é 1-indexado e corresponde à posição da linha dentro da página consultada.*

A resposta traz uma estrutura JSON ramificada em árvore (`children`), onde o coletor busca recursivamente por nós chamados `rsrpResult` e `rsrqResult`.

---

## 7. Decodificação Física de Sinal e Conversão de Fuso Horário

### A. Fórmulas de Conversão (Padrão 3GPP LTE)
Os valores decodificados das mensagens `RRC_MEAS_RPRT` do iManager FARS vêm em formato de índice bruto de RF (representados em formato hexadecimal ou decimal, ex: `0x35` / `53`). O HttpCollector calcula as métricas físicas reais do sinal usando as especificações técnicas da norma 3GPP:

1. **RSRP (Reference Signal Received Power)**:
   Mapeado de forma linear em dBm:
   $$\text{RSRP (dBm)} = \text{Índice} - 140.0$$
   *Exemplo*: Um índice decodificado de `53` resulta em:
   $$53 - 140.0 = -87.0\text{ dBm}$$

2. **RSRQ (Reference Signal Received Quality)**:
   Mapeado com passo de 0.5 dB:
   $$\text{RSRQ (dB)} = \frac{\text{Índice}}{2.0} - 19.5$$
   *Exemplo*: Um índice decodificado de `24` resulta em:
   $$\frac{24}{2.0} - 19.5 = -7.5\text{ dB}$$

### B. Normalização Temporal do Fuso Horário
Os servidores iManager geralmente operam com o horário local da rede regional do cliente (ex: UTC-3). O banco de dados do SmartEvents padroniza todas as entradas temporais no formato UTC (ISO 8601 com sufixo `Z`).

O HttpCollector realiza a conversão dinâmica calculando o fuso com base na propriedade `oss.timezone_offset_min` do evento (padrão é `-180` minutos para o fuso brasileiro UTC-3):
$$\text{Timestamp UTC} = \text{Timestamp Local do OSS} - \text{Offset (minutos)}$$

*Exemplo*: Se o iManager indica um evento às `15:00:00` (UTC-3), o cálculo subtrai `-180` minutos (o que soma 3 horas), gerando o registro normalizado `18:00:00Z` no banco SQLite.

---

## 8. Persistência dos Dados (Estrutura SQLite)

Para garantir excelente desempenho e evitar concorrência ou corrupção de arquivos por acessos paralelos de threads de leitura e escrita, o SmartEvents isola as informações em dois níveis de banco de dados SQLite operando em modo **WAL (Write-Ahead Logging)**.

```
                                  ┌───────────────────────────┐
                                  │      Diretório /data      │
                                  └─────────────┬─────────────┘
                                                │
                         ┌──────────────────────┴──────────────────────┐
                         ▼                                             ▼
           ┌───────────────────────────┐                 ┌───────────────────────────┐
           │      smart_events.db      │                 │ smart_events_<event_id>.db│
           │      (Banco Central)      │                 │    (Bancos dos Eventos)   │
           └───────────────────────────┘                 └───────────────────────────┘
           │ - Configuração de Eventos │                 │ - Sites e coordenadas     │
           │ - Cadastro Global de VIPs │                 │ - kpi_measurements        │
           │ - vip_measurements        │                 │ - alerts locais           │
           │ - silenced_alerts         │                 │ - silenced_alerts         │
           └───────────────────────────┘                 └───────────────────────────┘
```

### A. Esquema de Tabelas Relevantes para a Coleta

#### 1. Banco Central (`smart_events.db`)
Armazena a série temporal histórica dos VIPs, pois um usuário VIP pode estar associado a múltiplos eventos ao longo do tempo.

```sql
-- Cadastro Global de VIPs
CREATE TABLE IF NOT EXISTS vips (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    role       TEXT,
    notes      TEXT,
    task_id    INTEGER,
    updated_at TEXT
);

-- Medições de Sinal do VIP
CREATE TABLE IF NOT EXISTS vip_measurements (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vip_name     TEXT NOT NULL,
    event_id     TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    serving_cell TEXT,
    rsrp         REAL,
    rsrq         REAL,
    in_event     INTEGER DEFAULT 0
);

-- Índice único de Deduplicação
CREATE UNIQUE INDEX IF NOT EXISTS idx_vip_dedup 
ON vip_measurements(vip_name, timestamp);
```

#### 2. Banco do Evento (`smart_events_<event_id>.db`)
Tabelas específicas do ciclo de vida e área geográfica daquele evento.

```sql
-- Medições de KPI por Célula do Evento
CREATE TABLE IF NOT EXISTS kpi_measurements (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id   TEXT NOT NULL,
    cell_id   TEXT NOT NULL,
    event_id  TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    metric    TEXT NOT NULL,
    value     REAL
);

-- Índice de Busca Temporal rápida
CREATE INDEX IF NOT EXISTS idx_kpi_site_time
ON kpi_measurements(site_id, metric, timestamp);

-- Registro de Alertas Disparados
CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT,
    level        TEXT,
    severity     TEXT,
    site_id      TEXT,
    cell_id      TEXT,
    message      TEXT,
    timestamp    TEXT,
    acknowledged INTEGER DEFAULT 0
);

-- Alertas Silenciados
CREATE TABLE IF NOT EXISTS silenced_alerts (
    alert_key TEXT PRIMARY KEY
);
```

---

## 9. Mecanismo de Alertas e Limiares (Thresholds)

Imediatamente após a conclusão de cada inserção de batch no SQLite, o Scheduler executa rotinas de avaliação de métricas (`_evaluate_kpi_alerts` e `_evaluate_vip_alerts`) com base nos limites configurados nas propriedades de `thresholds` do evento.

### A. Regras para KPIs

1. **Utilização DL PRB (Downlink Physical Resource Block)**:
   - Mede o nível de saturação/tráfego físico na portadora.
   - **Warning (Alerta Elevado)**: Se o valor for maior ou igual ao limite configurado (Padrão: $\ge 80\%$).
   - **Critical (Alerta Crítico)**: Se o valor for maior ou igual ao limite crítico (Padrão: $\ge 95\%$).
   - **Registro**: Gera um registro na tabela `alerts` indicando o site afetado.

2. **Acessibilidade RRC**:
   - Mede o percentual de conexões estabelecidas com sucesso pelas células.
   - **Critical**: Se a acessibilidade cair abaixo do limiar crítico (Padrão: $< 95\%$).
   - **Registro**: Cria alerta indicando a falha de acessibilidade da célula de rede específica.

### B. Regras para VIPs

A avaliação de alertas de VIPs possui uma lógica refinada baseada na cobertura do sinal de RF:
1. **Verificação de Limiares**:
   - **Warning**: RSRP entre o limiar de atenção (Padrão: $\le -100\text{ dBm}$) e o crítico.
   - **Critical**: RSRP pior ou igual ao limiar de corte (Padrão: $\le -110\text{ dBm}$).
2. **Filtro de Área (in_event)**:
   - Para que o alarme seja gerado no painel do evento, a célula servidora do VIP (`serving_cell`) deve ter correspondência positiva com as células do evento. A correspondência é verificada por matching de nomes, mapeamento de `objNo` ou decodificação de ECI/NCI. Se a célula não fizer parte do local do evento (`in_event = 0`), o sinal é gravado no banco histórico global, mas o alerta é suprimido no dashboard do evento ativo para evitar falsos alertas se o VIP se afastar da área de cobertura do evento.

### C. Silenciamento de Alertas
Para evitar spams ou duplicação de mensagens idênticas no dashboard:
* Uma chave de alerta é montada (Ex: `rsrp_crit_VIPName`, `util_warn_SiteName`).
* Antes de gravar o novo alerta na tabela `alerts`, o sistema faz uma consulta na tabela `silenced_alerts`. Se o alerta estiver silenciado pelo painel de controle da UI, a inserção é ignorada.

---

## 10. Mapeamento de Arquivos e Responsabilidades

Abaixo está o inventário de arquivos diretamente envolvidos no ecossistema de coleta de dados do SmartEvents:

1. **[core/scheduler.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/core/scheduler.py)**: Orquestra o tempo de execução (loops periódicos) das threads daemon de coleta, avalia os limiares de alertas e gerencia callbacks de atualização da tela.
2. **[core/collector.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/core/collector.py)**: Contém as implementações dos coletores (Mock, CSV, HTTP). É responsável por formatar payloads, requisitar APIs do iManager e decodificar dados brutos de RF (RSRP/RSRQ) a partir das estruturas FARS.
3. **[scratch/get_session.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/scratch/get_session.py)**: Script autônomo com Playwright executado em modo headless que realiza login na console web do iManager, intercepta cookies de sessão válidos, obtém o token CSRF (`roarand`) e os IDs de tarefas ativas.
4. **[data/session.json](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/data/session.json)**: Cache de sessão contendo cookies ativos e tokens que o `HttpCollector` reutiliza em suas chamadas.
5. **[core/database.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/core/database.py)**: Gerencia as conexões thread-safe SQLite (banco central e bancos dinâmicos dos eventos), provendo métodos para inserção em lote (`insert_kpi_batch`, `insert_vip_batch`) e checagem de silenciamento.
6. **[api/api.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/api/api.py)**: Expõe as funções Python de leitura de dados e início/parada do scheduler para que sejam invocadas pelo frontend JS.
7. **[main.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/main.py)**: Ponto de entrada que lê argumentos do CLI (ex: `--mock`) e inicializa a aplicação desktop e as pontes de comunicação.
