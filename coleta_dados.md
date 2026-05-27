# Coleta de Dados — SmartEvents

Este documento descreve detalhadamente como funciona o fluxo de coleta de dados no SmartEvents, detalhando as fontes de dados, o agendamento em segundo plano, os mecanismos de autenticação e os arquivos envolvidos no processo.

---

## 1. Arquitetura Geral da Coleta

O fluxo de dados da aplicação segue o seguinte pipeline:
```
OSS/Trace (Huawei iManager / VPN) ──> Collector (Python) ──> SQLite (Banco Local por Evento) ──> Api.py ──> Frontend (JS/HTML/CSS)
```

Toda a coleta de dados é executada localmente pelo backend em Python, operando em threads dedicadas em segundo plano para não bloquear a interface do usuário (`pywebview`). Os dados coletados são imediatamente gravados no banco de dados SQLite correspondente ao evento.

---

## 2. O Agendador (Scheduler)

O arquivo [scheduler.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/core/scheduler.py) é o responsável por orquestrar a execução das coletas em ciclos periódicos:

- **Threads Dedicadas**: Quando um evento ativo é iniciado, o `scheduler` cria duas threads daemon independentes:
  - `kpi-collector`: Executa a coleta de indicadores de performance de rede (KPIs).
  - `vip-collector`: Executa a coleta de traces de sinalização de usuários VIP.
- **Intervalos de Coleta**:
  - **KPIs**: Executado a cada **120 segundos** (2 minutos), alinhado com o ciclo de atualização típico do OSS.
  - **VIPs**: Executado a cada **60 segundos** (1 minuto), permitindo um rastreamento quase em tempo real da experiência dos VIPs.
- **Validação de Alertas**: Logo após cada ciclo de coleta, o `scheduler` avalia os dados recebidos contra as regras de thresholds do evento e gera alertas de sistema se limites críticos forem violados (ex: RSRP baixo de VIPs, utilização de bloco físico DL elevada, acessibilidade baixa).
- **Callback de Atualização**: Ao término de cada loop bem-sucedido, o `scheduler` invoca um callback (`_on_update`) que avisa a ponte de comunicação do frontend para atualizar a interface com os novos dados.

---

## 3. Os Coletores (Collectors)

O arquivo [collector.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/core/collector.py) define a interface base `BaseCollector` e implementa 4 estratégias de coleta construídas dinamicamente via fábrica `build_collector()`:

### A. MockCollector (Modo de Desenvolvimento)
- **Ativação**: Iniciado quando o app roda com o parâmetro `--mock` (`python main.py --mock`).
- **Funcionamento**: Gera valores sintéticos baseados em distribuições gaussianas para simular o comportamento de KPIs reais (throughput, utilização, acessibilidade, usuários ativos) e traces de VIPs (RSRP/RSRQ variando em torno de médias operacionais). É usado para testar e desenhar o frontend sem conexão com a VPN do cliente.

### B. CsvCollector (Fase 1: Coleta via Arquivos locais)
- **Ativação**: Usado se a configuração do evento (`oss.import_folder`) apontar para uma pasta local válida.
- **KPIs**: Lê arquivos no formato `kpi_*.csv` exportados da interface iManager. O coletor mapeia as colunas usando sinônimos declarados em `KPI_COLUMN_MAP` (para cobrir variações de nomeação do iManager) e mantém em memória um conjunto `_processed` de nomes de arquivos para evitar reprocessamento desnecessário (reiniciar a aplicação limpa o conjunto e reprocessa tudo).
- **Traces de VIP**: O trace de VIP via CSV foi descontinuado neste fluxo, sendo substituído pela consulta direta em API HTTP por task ID.

### C. HttpCollector (Fase 2: Conexão REST Direta ao iManager)
- **Ativação**: Usado em produção se nenhuma pasta de importação CSV for informada. Usa a URL do iManager (`base_url`) configurada nas propriedades do evento.
- **Gestão de Sessão (Playwright)**: Caso o coletor receba um erro de autenticação (HTTP 401/403 ou redirecionamento para login SSO), ele ativa um fluxo de renovação automática executando o script auxiliar [get_session.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/scratch/get_session.py) via subprocesso Playwright em modo oculto (*headless*). O script renova os cookies, obtém o token CSRF (`roarand`) e os salva no arquivo [session.json](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/data/session.json).
- **Coleta de KPIs**: Faz uma requisição `POST` para `/rest/oss/access/pm/v1/monitor/task/result` informando o ID da tarefa (`pm_task_id`) e a lista de códigos das células (`objNo`). Se novas células forem associadas a sites do evento em tempo de execução, o coletor faz uma varredura para associá-las dinamicamente aos seus respectivos IDs internos (`objNo`).
- **Coleta de VIPs (Signaling Trace via FARS)**: Cada VIP é mapeado diretamente a um ID de tarefa dedicado (`task_id`) criado no iManager FARS. A coleta de trace executa um fluxo de 3 passos descoberto da UI nativa do iManager:
  1. `GET /rest/oss/access/fars/v1/traceresult/pre-check?taskId={id}&queryType=0` para inicializar a sessão de consulta em backend.
  2. `GET /rest/oss/access/fars/v1/traceresult/query/result?...` para baixar a tabela paginada contendo as mensagens RRC do trace.
  3. Para cada mensagem do tipo `RRC_MEAS_RPRT` (limitado ao máximo de 50 decodificações por ciclo para otimizar performance), executa uma consulta a `/rest/oss/access/fars/v1/traceresult/query/msg-explain-info` enviando o índice da linha (`rowNo`) 1-indexado para obter o payload detalhado estruturado. O coletor então decodifica o RSRP e RSRQ do relatório de medição.

### Conversão Física de Sinal (Cálculos de RSRP e RSRQ)
Os dados recebidos do iManager FARS vêm em formato de índice bruto (ex: 0x35 (53) ou 0x30 (48)). O HttpCollector decodifica esses valores em métricas físicas reais utilizando as fórmulas de mapeamento padrão do 3GPP LTE:

- **RSRP (Reference Signal Received Power) em dBm**:
  RSRP = índice - 140.0 (Exemplo: Índice 53 vira -87 dBm)

- **RSRQ (Reference Signal Received Quality) em dB**:
  RSRQ = (índice / 2.0) - 19.5 (Exemplo: Índice 24 vira -7.5 dB)

---

## 4. Persistência dos Dados (SQLite por Evento)

A gravação dos dados coletados é realizada chamando os métodos do arquivo [database.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/core/database.py). 

Para evitar gargalos de performance e concorrência, o SmartEvents adota um modelo de isolamento:
1. **Banco Central (`smart_events.db`)**: Guarda configurações gerais, dados globais dos VIPs e lista de eventos.
2. **Banco do Evento (`smart_events_<event_id>.db`)**: Criado dinamicamente para cada evento. É aqui que o scheduler escreve as séries temporais coletadas nas tabelas:
   - `kpi_measurements` (armazena valores de métricas como utilização de downlink, tráfego, throughput e usuários ativos por célula/timestamp).
   - `vip_measurements` (armazena medições de RSRP/RSRQ e célula de serviço de cada VIP por timestamp).
   - `alerts` (contém o registro de alertas disparados pelo processamento de thresholds).

---

## 5. Arquivos Relacionados à Coleta

Aqui estão os arquivos diretamente responsáveis ou envolvidos no pipeline de coleta de dados:

1. **[core/scheduler.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/core/scheduler.py)**
   - Gerencia as threads de segundo plano e o temporizador de disparo das coletas.
   - Avalia thresholds e cria alertas associados a métricas insatisfatórias.
2. **[core/collector.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/core/collector.py)**
   - Contém a lógica de extração de dados (Mock, CSV e HTTP).
   - Decodifica os bytes das mensagens RRC do trace para extrair RSRP/RSRQ.
3. **[scratch/get_session.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/scratch/get_session.py)**
   - Script automatizado com Playwright para realizar login no portal do iManager, bypassar restrições e obter cookies/tokens CSRF atualizados.
4. **[data/session.json](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/data/session.json)**
   - Arquivo cache local que armazena os tokens (`roarand`), cookies de sessão e IDs de tarefas ativas coletadas para uso direto do `HttpCollector`.
5. **[core/database.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/core/database.py)**
   - Abstrai toda a camada SQL. Grava as medições recebidas pelo coletor usando conexões thread-safe e gerencia as chaves de silenciamento de alertas.
6. **[api/api.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/api/api.py)**
   - Expõe as funções de gerenciamento de gravação e consulta ao frontend JS e permite disparar ou pausar o agendador manualmente.
7. **[main.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/main.py)**
   - Ponto de entrada que inicializa a aplicação desktop, lê os argumentos de CLI (ex: `--mock`) e instancia os serviços centrais.
