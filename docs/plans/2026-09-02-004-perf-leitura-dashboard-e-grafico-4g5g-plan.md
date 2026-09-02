# Leitura do dashboard em escala e o gráfico 4G/5G

> Plano de implementação derivado da análise de 02/09/2026 sobre o evento
> `rock-in-rio-2026`. Diagnóstico completo com as medições:
> https://claude.ai/code/artifact/31305092-0eb5-4044-b023-586d8ad51c0e
>
> Nenhum arquivo foi alterado na análise. Este documento é o plano; a
> implementação não começou.

---

## 0. O que foi medido (base factual deste plano)

Medições feitas em 01–02/09/2026 sobre `data/smart_events_rock-in-rio-2026.db`
(185 MB, 691.358 linhas) e `data/smart_events.db`, com o interpretador do
`.venv` do projeto e perfilamento por `cProfile`.

### 0.1 O evento

| Dimensão | Valor |
| :--- | ---: |
| Sites na EP | 2.479 |
| Sites dentro do polígono | 37 (69 brutos) |
| Sites após `_merged_sites` | 1.454 |
| Células | 24.086 |
| Tasks PM | 5 (2307/2308/2309 4G, 2312 NR Cell, 2313 NR DU Cell) |
| Objetos por ciclo | 840 (676 no 4G, 164 no 5G) |
| Clusters resolvidos | 50 (19 cadastrados + 31 sintéticos por EARFCN) |

### 0.2 A coleta não é o gargalo

Ciclos de monitoring fecham entre 8 e 33 s, com `nao_mapeados=0` e
`invalidos=0`. Dois ciclos estouraram o intervalo de 120 s (173,4 s e 121,5 s)
e houve `ConnectTimeout` no host `10.220.30.9` entre 19:05 e 19:09 — instabilidade
do OSS de OUTRAS, fora do escopo de código.

### 0.3 A leitura é o gargalo

| Chamada | Tempo | Disparada | Onde vai o tempo |
| :--- | ---: | :--- | :--- |
| `get_sites` | 39–48 s | poll de 30 s | 35,8 s em `_find_merged_site` |
| `get_clusters` | 44 s | abertura/troca de evento | 39,4 s em `_find_merged_site` |
| `get_alarms` | 19 s | poll de 30 s | 16,7 s em `_resolve_site_for_source` |
| `get_latest_kpi` | 9,2 s | dentro de `get_sites` | varredura de 691.358 linhas |
| `get_kpi_overview` | 3,0–4,4 s | botão Ver KPIs | 9 métricas × expansão de site |
| `get_vips` | 1,4 s | poll de 30 s | `_merged_sites` recalculado |
| `get_kpi_series` | 0,3–0,9 s | poll e cliques | saudável, já indexado |

Um poll pede cerca de **60 s de Python** (`get_sites` + `get_alarms`) e dispara a
cada **30 s**. As chamadas empilham na ponte do pywebview e todo clique do
operador entra na fila atrás delas.

Causas raiz medidas:

- **`_find_merged_site` (api.py:689) é varredura linear** sobre os 1.454 sites
  fundidos. `_cluster_merged_site_ids` (api.py:700) a chama por membro de cada
  cluster: 6.991 chamadas × 1.454 sites = **13.311.475 avaliações** por chamada
  de API.
- **`_earfcn_clusters` (api.py:851) cobre a EP inteira**, não o polígono: gera 31
  clusters a partir dos 2.479 sites e 24.086 células. É o multiplicador do item
  acima. `_single_configured_family(config)` é chamado **uma vez por célula**
  (24.086 vezes) dentro do laço.
- **`_merged_sites` (api.py:644) não tem cache** e tem 19 pontos de chamada;
  1,8 s cada, sobre um `config_json` de 3,4 MB relido e reparseado do SQLite.
- **`_resolve_site_for_source` (api.py:2298)** compara 500 alarmes × 1.454 sites e
  chama `_site_identity_keys` (api.py:2206), que **remonta a lista de chaves do
  zero** a cada par: 678.467 reconstruções.
- **`get_latest_kpi` (database.py:802)** agrupa sem recorte temporal. Plano:
  `SEARCH ... USING COVERING INDEX (event_id=?)` + `USE TEMP B-TREE FOR GROUP BY`
  sobre 691.358 linhas para devolver 8.949. Recortado nos últimos 15 min de dado:
  4,1 s.
- **`get_sites` serializa 5,12 MB de JSON por poll** (1.454 sites com todas as
  células e portadoras), dos quais só 37 sites estão no polígono.
- **O mapa reconstrói 1.454 ícones SVG por poll** (`map.js:170` →
  `_updateMarker`), e `_applyVisibility` (map.js:625) faz `State.sites.find`
  dentro do laço sobre os marcadores: 1.454 × 1.454. A lista lateral repete o
  padrão com `State.alarms.filter` por site (kpi.js:447): 1.454 × 500.
- **`init_event_db` (database.py:69) roda a migração a cada primeira conexão**:
  `DELETE` de deduplicação (1,1 s) + `DROP INDEX` + `CREATE UNIQUE INDEX` de sete
  colunas (6,2 s) + commit (4,5 s) ≈ **12 s por abertura**. WAL de 24 MB sem
  checkpoint.

### 0.4 O gráfico 4G/5G: quatro mecanismos distintos

Medido nos 20 sites fundidos que hoje têm dado nas duas famílias, filtro em
"4G e 5G", célula em "Média", janela de 60 min:

| Métrica | Duas linhas | Só 4G | Só 5G | Pontos 5G |
| :--- | ---: | ---: | ---: | ---: |
| `utilization_dl` / `_ul` | 20 | 0 | 0 | 6,0 |
| `availability` | 20 | 0 | 0 | 6,0 |
| `user_count` | 20 | 0 | 0 | 6,0 |
| `interference_ul` | 20 | 0 | 0 | 6,0 |
| `throughput_ul` | 20 | 0 | 0 | 5,8 |
| `throughput_dl` | 20 | 0 | 0 | 4,5 |
| **`drop_rate`** | **3** | **17** | 0 | 1,0 |
| **`accessibility`** | **1** | **19** | 0 | 1,0 |
| `traffic_volume_dl` / `_ul` | 0 | 20 | 0 | — |
| `traffic_volume_*_sa` / `_nsa` | 0 | 0 | 20 | 6,0 |

1. **A métrica não existe nas duas famílias, mesmo aparecendo como comum.**
   `accessibility` tem 56 linhas no evento inteiro contra 29.364 do 4G;
   `drop_rate`, 66 contra 29.236. A série 5G existe, com um ponto.
2. **Ponto isolado não desenha.** `pointRadius: 0` com `spanGaps: false`
   (kpi.js:1005 e 1009): um ponto cercado de `null` não vira segmento nem
   marcador. Na janela de 180 min há 24 pontos 5G isolados em `throughput_dl`,
   10 em `drop_rate` e 6 em `accessibility`. Como o número muda com a janela e
   com os buracos de coleta, a mesma métrica mostra duas linhas num momento e uma
   no outro.
3. **O filtro de tecnologia continua valendo depois de sumir da tela.**
   `_syncTechSelector` esconde o `#tech-selector` quando o site tem uma família só
   e `_effectiveTechFilter` (kpi.js:351) devolve `"all"` para o seletor de
   células, mas o gráfico usa `_techFamilyParam()` (kpi.js:334, 900), que lê
   `State.techFilter` cru. Filtro esquecido em 5G + site só-4G = gráfico vazio com
   a lista de células populada.
4. **A família sai do nome da célula, não da coluna `technology`.**
   `_average_series_by_family` (api.py:1483) e `_filter_cell_rows_for_family`
   (api.py:1475) procuram token no `cell_id`, ignorando a coluna já gravada. Nesta
   EP 620 células não têm token (`18NLRJPE41A`); nenhuma está no polígono hoje.
   Quando estiver, vira uma terceira série com `technology: null`, rotulada só
   "Média" e pintada com o azul do 4G (`FAMILY_COLORS[...] || "#388BFD"`,
   kpi.js:998): duas linhas azuis indistinguíveis.

Confirmado como **não** sendo causa: gravação e leitura das duas famílias. Nos 48
minutos em que o 5G existe, os três valores de `technology` aparecem no mesmo
minuto. Dos 37 sites fundidos do polígono, 20 têm dado nas duas famílias; nos
outros 17 uma linha só está correta.

### 0.5 Baseline da suíte

O último baseline registrado na `MEMORY.md` é de 20/08 (**357 passed, 10 skipped,
0 failed**). **Rodar e anotar o baseline atual antes de tocar em código**:

```bash
pytest tests/ -q
```

---

## 1. Decisões travadas por este plano

- **A fusão de sites vira uma estrutura derivada com cache, não uma função pura
  recalculada.** O resultado de `_merged_sites` só muda quando o `config_json` do
  evento muda; passa a ser calculado uma vez por versão de config.
- **A versão do config é o digest do `config_json` cru**, calculado em
  `db.get_event` antes do parse. É barato (~5 ms para 3,4 MB contra 30–170 ms do
  `json.loads`), não exige coluna nova no schema e invalida sozinho quando o
  cadastro muda.
- **Nenhuma varredura linear sobrevive dentro de um laço.** Toda resolução
  "id/nome/célula → site fundido" passa a sair de dicionário montado uma vez.
- **O recorte temporal das consultas de "última medição" é ancorado no dado, não
  no relógio.** A janela conta a partir de `MAX(timestamp)` da tabela (ou do
  `max_timestamp` no modo histórico). Durante uma parada de coleta a janela para
  junto e nada some da tela — o que sumiria com `now`.
- **A migração de schema do banco de evento passa a ser única**, controlada por
  `PRAGMA user_version`, e não uma tarefa de toda abertura.
- **O payload do poll separa o que é estático do que muda.** Geometria de site e
  célula vem do cadastro e não muda durante o evento; status e valor de métrica
  mudam a cada ciclo. Só o segundo trafega a cada 30 s.
- **A família de uma série sai da coluna `technology`**, que é a fonte
  autoritativa gravada pelo coletor. O nome da célula continua existindo apenas
  como fallback para linhas legadas sem a coluna preenchida.
- **Série sem dado é dita, não omitida.** O gráfico do dashboard passa a informar
  por que uma família não tem linha, reusando a semântica de `_overview_reasons`
  (`ok` / `no_traffic` / `no_data`) que a visão geral já usa.
- **Nenhuma biblioteca nova.** Chart.js, Leaflet e SQLite continuam sendo o que
  são; o trabalho é de estrutura de dados e de consulta.

---

## Ordem das fases e paralelismo

São quatro fases porque o trabalho atravessa quatro camadas com risco e critério
de verificação diferentes: indexação em memória (sem mudança de contrato), SQL e
schema (risco de migração), contrato do payload e render (mudança visível na
ponte), e semântica do gráfico (mudança que o operador vê). Juntas num diff só,
uma regressão fica impossível de atribuir.

```
Fase 1 ──► Fase 2 ──► Fase 3
(índices)  (SQL/schema) (payload + render)

Fase 4 (gráfico 4G/5G) ── independente, pode correr em paralelo
```

- **Fase 1 primeiro**: maior ganho medido, risco mínimo, nenhuma mudança de
  interface. É a única que faz sentido subir com um evento em andamento.
- **Fase 2 depois da 1**: o ganho da 2 só aparece depois que a 1 tira os 36 s de
  CPU da frente do SQL.
- **Fase 3 por último** entre as de performance: é a que muda contrato de API e
  mexe no render do mapa.
- **Fase 4 é independente** das três: não compartilha arquivo crítico com a 1 nem
  com a 2 e pode ser feita antes, depois ou em paralelo.

Ganho esperado por fase em `get_sites` (48,5 s hoje): **~11 s** após a Fase 1,
**~3 s** após a Fase 2, e payload de 5,12 MB → dezenas de kB na Fase 3. Os
números são projeção da decomposição do perfil e **precisam ser remedidos** ao
fim de cada fase.

---

## Fase 1 — Índices em memória no lugar das varreduras lineares

### Explicação simples

Toda vez que a tela pede qualquer coisa, o app remonta a fusão de sites 4G/5G do
zero e depois procura sites percorrendo a lista inteira, de novo e de novo. Com
2.479 sites e 50 clusters isso vira 13 milhões de comparações por chamada.

Esta fase calcula essas estruturas **uma vez por versão do cadastro do evento** e
guarda os índices prontos: procurar um site passa a ser uma consulta a
dicionário. Nada muda na tela — o mesmo dado, muito mais rápido.

### Escopo detalhado

**Código — `core/database.py`**

- `get_event(event_id)` passa a calcular o digest (`hashlib.sha1`) do
  `config_json` cru antes do parse e a memoizar o dict parseado por
  `(event_id, digest)`.
- O dict devolvido é uma **cópia rasa** do memoizado, e recebe a chave
  `_config_digest`. A cópia rasa é obrigatória: `activate_event` escreve
  `config["status"]` no retorno, e sem a cópia isso contaminaria o cache. As
  listas internas (`sites`, `clusters`) são compartilhadas e tratadas como
  somente leitura.
- Cache limitado a poucas entradas (evento ativo + o anterior), descartando o
  mais antigo, para não segurar vários `config_json` de 3,4 MB na memória.

**Código — `api/api.py`**

- Nova estrutura `_EventView` (dataclass ou classe simples), construída uma vez
  por `(event_id, _config_digest)` e guardada num cache pequeno na `Api`:
  - `sites`: resultado de `_merged_sites`;
  - `by_site_id`: `dict[str, site]` cobrindo o id fundido **e** o `site_id` de
    cada membro — substitui `_find_merged_site`;
  - `by_cell_id`: `dict[str, site]` — substitui `_find_site_for_cell`;
  - `identity_exact`: `dict[str, (site_id, site_name)]` com as chaves de
    `_site_identity_keys` em maiúsculas;
  - `identity_fuzzy`: lista pré-montada de `(chave, site_id, site_name)`
    ordenada por comprimento decrescente, para os casos de `startswith` /
    substring que o dicionário não cobre;
  - `clusters`: resultado de `_clusters_of`.
- `_find_merged_site`, `_find_site_for_cell` e `_cluster_merged_site_ids` passam
  a consultar a view. As assinaturas atuais são mantidas como fachada para não
  espalhar mudança pelos 19 pontos de chamada.
- `_resolve_site_for_source` tenta `identity_exact` primeiro (caso da esmagadora
  maioria dos alarmes) e só cai no `identity_fuzzy` pré-montado quando não há
  correspondência exata. `_site_identity_keys` deixa de ser chamado por par
  alarme×site.
- `_earfcn_clusters`: `_single_configured_family(config)` é resolvido **uma vez**
  antes do laço, não por célula.
- `_sanitize_event` remove `_config_digest` do payload enviado ao frontend, como
  já faz com `sites` e `vips`.

**Interface**

Nenhuma mudança. Esta fase é invisível na tela por construção: mapa, lista de
sites, seletores, alarmes e gráfico devem renderizar exatamente o mesmo conteúdo.

**Testes**

- `tests/test_api.py`, classe nova `TestEventViewIndex`:
  - `test_view_resolve_site_por_id_fundido_e_por_membro` — a view devolve o mesmo
    site que a varredura linear para o id fundido, para cada `site_id` de membro
    e `None` para id inexistente.
  - `test_view_resolve_celula_para_o_site_dono` — paridade com
    `_find_site_for_cell`.
  - `test_view_invalida_quando_o_cadastro_muda` — `save_event` com um site novo
    produz digest novo e view nova; sem alteração, a mesma instância é reusada.
  - `test_get_event_devolve_copia_rasa` — escrever `config["status"]` no retorno
    não afeta a próxima chamada.
  - `test_sanitize_event_remove_config_digest`.
- `tests/test_api.py`, alarmes:
  - `test_resolve_site_for_source_mantem_paridade` — para um conjunto de
    `source` (igualdade, prefixo, substring, nome do site, id de membro,
    desconhecido), o `serving_site` resolvido é idêntico ao da implementação
    linear.
- Guarda de regressão de custo (CPU pura, sem I/O, margem generosa para não
  ficar instável):
  - `test_get_sites_em_evento_grande_fica_abaixo_do_orcamento` — evento sintético
    com ~1.500 sites e ~50 clusters; `get_sites` deve fechar em menos de 5 s. Na
    árvore atual esse teste falha (o caminho linear leva dezenas de segundos), o
    que é o ponto: ele é o teste que a fase faz passar.

### Como validar e testar

**Visual (`python main.py --mock`)**

1. Abrir o app e o evento mock. Mapa, lista de sites e contadores
   (`N ok · N críticos`) devem estar idênticos ao comportamento atual.
2. Trocar a métrica no seletor, trocar o filtro de cluster, clicar em sites
   diferentes, abrir "Ver KPIs". Nenhuma diferença de conteúdo.
3. A resposta ao clique deve ficar visivelmente imediata.

**Com o evento real**

Rodar a medição antes e depois, com o interpretador do `.venv`, contra
`smart_events_rock-in-rio-2026.db`, e anotar os dois números:

```python
import time
from api.api import Api
api = Api()
ev = "rock-in-rio-2026"
api.get_sites(ev, None, "utilization_dl", None)   # aquece
for label, fn in (("get_sites",   lambda: api.get_sites(ev, None, "utilization_dl", None)),
                  ("get_clusters", lambda: api.get_clusters(ev)),
                  ("get_alarms",  lambda: api.get_alarms(ev))):
    t0 = time.perf_counter(); fn(); print(label, round(time.perf_counter() - t0, 2), "s")
```

Alvos: `get_sites` de 48 s para **≤ 12 s**, `get_clusters` de 44 s para
**≤ 5 s**, `get_alarms` de 19 s para **≤ 2 s**. O resíduo de `get_sites` é o SQL
que a Fase 2 ataca.

**Suíte**

```bash
pytest tests/test_api.py tests/test_database.py -q
pytest tests/ -q
```

Sem regressão contra o baseline anotado em 0.5.

### Sugestão de commit

```
feat: index merged sites and alarm identity to cut dashboard read time
```

---

## Fase 2 — Consultas de última medição recortadas e migração única

### Explicação simples

Para pintar o mapa, o app pergunta ao banco qual foi a última medição de cada
célula — e para responder isso o SQLite varre as 691 mil linhas do evento
inteiro, incluindo as de sete horas atrás que ninguém vai usar. Além disso, toda
vez que o app abre um evento ele refaz uma limpeza de schema que só precisava
acontecer uma vez, pagando 12 segundos na abertura.

Esta fase recorta as consultas na janela recente e transforma a limpeza numa
migração de mão única.

### Escopo detalhado

**Código — `core/database.py`**

- Constante `LATEST_WINDOW_MINUTES = 15` (nomeada e comentada, não literal solto).
- `get_latest_kpi`, `get_latest_kpi_by_metric` e `get_latest_site_kpi_by_metric`
  ganham um recorte `timestamp >= <âncora - janela>`, onde a âncora é
  `MAX(timestamp)` da própria tabela no modo ao vivo e o `max_timestamp` recebido
  no modo histórico. Ancorar no dado, e não em `now`, é o que impede a tela de
  esvaziar durante uma parada de coleta como as de 18:36→20:47 e 21:41→22:46.
- A subconsulta da âncora é resolvida uma vez e passada como parâmetro, não
  repetida dentro do `GROUP BY`.
- Migração de schema controlada por `PRAGMA user_version`:
  - `user_version == 0`: roda o `DELETE` de deduplicação e recria o índice único,
    como hoje, e grava `user_version = 1`;
  - `user_version >= 1`: só garante as tabelas e índices com
    `CREATE ... IF NOT EXISTS`, sem `DELETE` e sem `DROP INDEX`.
- `close_conn` executa `PRAGMA wal_checkpoint(TRUNCATE)` antes de fechar, para o
  WAL não crescer indefinidamente entre sessões.

**Medição antes de indexar**

Só depois de aplicar o recorte, reexecutar `EXPLAIN QUERY PLAN`. Se o plano
continuar em `USE TEMP B-TREE FOR GROUP BY` com custo relevante, avaliar um
índice `(event_id, timestamp)` ou `(event_id, metric, scope, timestamp)`. Índice
novo custa espaço e tempo de escrita em toda coleta: **não adicionar por
suposição**, só com o plano medido nas duas situações.

**Interface**

Nenhuma mudança pretendida. Uma consequência a observar e aceitar
explicitamente: uma célula que parou de reportar há mais de 15 minutos **antes do
último ciclo** deixa de aparecer com valor e passa a `unknown` no mapa. Isso é o
comportamento correto — hoje ela exibe um valor velho como se fosse atual — mas é
uma mudança visível e precisa ser conferida com o operador.

**Testes**

- `tests/test_database.py`:
  - `test_latest_kpi_recorta_pela_janela_ancorada_no_dado` — fixture com medições
    dentro e fora da janela; só as de dentro voltam, e a âncora é o `MAX` da
    tabela, não o relógio.
  - `test_latest_kpi_em_coleta_parada_mantem_o_ultimo_ciclo` — todas as medições
    com duas horas de idade; a consulta continua devolvendo o último ciclo.
  - `test_latest_kpi_historico_ancora_no_max_timestamp` — com `max_timestamp`, a
    janela é relativa a ele.
  - `test_latest_site_kpi_por_metrica_recortado` — mesma garantia para o escopo
    `SITE`, preservando o par `(site_id, technology)`.
  - `test_migracao_de_schema_roda_uma_vez` — abrir o mesmo arquivo duas vezes
    executa o `DELETE`/`CREATE INDEX` só na primeira; `user_version` vai a 1.
  - `test_banco_legado_sem_user_version_ainda_deduplica` — banco com duplicatas
    e `user_version = 0` é limpo normalmente.
- `tests/test_api.py`:
  - `test_get_sites_status_igual_com_recorte` — para um evento cujas medições
    cabem todas na janela, o resultado é idêntico ao de antes.

### Como validar e testar

**Visual (`python main.py --mock`)**

1. Abrir o evento mock: a abertura deve ficar sensivelmente mais rápida.
2. Conferir que os sites do mapa mantêm cor e valor de métrica.
3. Arrastar o slider do modo histórico ponta a ponta: os valores por instante
   devem bater com os de antes da mudança.

**Com o evento real**

1. **Fazer cópia do banco antes de qualquer teste de migração.** A migração
   escreve.
2. Medir a abertura: cronometrar de `activate_event` até a primeira renderização,
   antes e depois. Alvo: sair dos ~12 s de manutenção de schema.
3. Repetir o script de medição da Fase 1. Alvo: `get_sites` de ~12 s para
   **≤ 4 s**; `get_latest_kpi` de 9,2 s para **≤ 4 s**.
4. Conferir o tamanho do `-wal` após fechar o app: deve encolher.

**Suíte**

```bash
pytest tests/test_database.py tests/test_api.py -q
pytest tests/ -q
```

### Sugestão de commit

```
feat: bound latest-KPI queries and run event schema migration once
```

---

## Fase 3 — Payload do poll e render incremental do mapa

### Explicação simples

A cada 30 segundos o app manda do Python para a tela os 1.454 sites inteiros, com
todas as células e portadoras: 5 MB de JSON. Desses, só o status e o valor da
métrica realmente mudaram — a geometria das antenas é a mesma desde o cadastro.
Em seguida o mapa redesenha os 1.454 ícones SVG, mudem eles ou não.

Esta fase separa o que é fixo do que muda: a geometria vai uma vez, o status vai
a cada ciclo, e o mapa só repinta o que mudou de verdade.

### Escopo detalhado

**Código — `api/api.py`**

- `get_site_layout(event_id, technology_family=None)`: parte estática por site —
  `id`, `name`, `original_name`, `lat`, `lng`, `cells`, `members`,
  `tech_families`, `is_event_site`, `cluster_ids`, `carriers`. Chamada uma vez no
  carregamento e ao trocar de evento ou de filtro de tecnologia.
- `get_site_status(event_id, metric, timestamp=None, technology_family=None)`:
  parte dinâmica — `id`, `status`, `utilization`, `metric_value`,
  `metric_is_share`. É o que o poll passa a chamar.
- `get_sites` permanece, compondo as duas, para não quebrar consumidor existente
  (testes, modo histórico, `server_frontend`). A composição das duas partes deve
  produzir exatamente o objeto que `get_sites` devolve hoje.

**Código — `frontend/js/bridge.js`**

- `getSiteLayout` e `getSiteStatus` adicionados ao objeto `API` e aos mocks, com
  os mocks derivando do mesmo `_mockSites` já existente para não duplicar
  fixture.

**Código — `frontend/js/app.js`**

- `_poll` chama `getSiteStatus` e funde o status no `State.sites` já carregado,
  em vez de rebuscar tudo.
- `POLL_INTERVAL_MS` passa de 30 s para o intervalo da coleta (120 s), ou para um
  valor derivado de `INTERVAL_KPI_SECONDS` exposto por `get_collection_status`. O
  poll de status de sincronismo (5 s) continua como está — ele é leve e é o que
  dá vida ao indicador do cabeçalho.

**Código — `frontend/js/map.js`**

- `renderSites` só chama `_updateMarker` para sites cujo `status`,
  `metric_value`, seleção ou zoom mudaram desde o último render; os demais ficam
  como estão.
- `_applyVisibility` recebe (ou monta uma vez) um `Map` de `id → site` no lugar
  do `State.sites.find` dentro do laço.
- `_buildSectorIcon` continua sendo refeito quando o zoom muda — isso é
  necessário e já é disparado pelo evento de zoom, não pelo poll.

**Código — `frontend/js/kpi.js`**

- `_renderSiteList` monta **uma vez por render** os mapas `site_id → alarmes` e
  `site_id → tem VIP`, em vez de filtrar `State.alarms` e `State.vips` dentro do
  laço de sites.

**Interface**

Sem mudança visível pretendida: mesma lista, mesmos marcadores, mesmos badges,
mesmas cores. O que muda é a fluidez. Verificar explicitamente que o badge de VIP
e o triângulo de alarme continuam aparecendo e sumindo na hora certa, já que o
render deixou de ser incondicional.

**Testes**

- `tests/test_api.py`:
  - `test_layout_mais_status_compoem_get_sites` — para o evento de fixture,
    `get_site_layout` + `get_site_status` reproduzem campo a campo o retorno de
    `get_sites`.
  - `test_status_nao_carrega_celulas` — a resposta de `get_site_status` não
    contém `cells` nem `carriers` (é o ponto da fase).
- `tests/test_frontend_collection_ui.py` (ou arquivo novo
  `test_frontend_site_render.py`), com Playwright no mesmo padrão dos testes de
  frontend existentes:
  - `test_poll_de_status_nao_recria_marcadores_sem_mudanca` — instrumentar um
    contador de chamadas de `setIcon` e afirmar que um ciclo de status sem
    alteração não incrementa.
  - `test_mudanca_de_status_repinta_somente_o_site_afetado`.
  - `test_badges_de_vip_e_alarme_continuam_sincronizados`.

### Como validar e testar

**Visual (`python main.py --mock`)**

1. Abrir o evento mock e deixar rodando por três ciclos de poll. O mapa não pode
   "piscar": marcadores não devem sumir e reaparecer.
2. Arrastar e dar zoom no mapa durante um poll — o gesto deve continuar fluido.
3. Ligar e desligar o filtro "somente sites do evento" e o filtro de cluster;
   conferir que os marcadores certos aparecem.
4. Forçar um alarme no mock e conferir que o triângulo aparece no site correto no
   ciclo seguinte, e some quando o alarme sai.

**Com o evento real**

1. Medir o tamanho do payload do poll antes e depois:
   `len(json.dumps(api.get_sites(...)))` contra
   `len(json.dumps(api.get_site_status(...)))`. Alvo: de 5,12 MB para **dezenas
   de kB**.
2. Com o DevTools aberto (`python main.py --dev`), verificar no Performance que o
   ciclo de poll deixou de produzir bloqueio longo da thread principal.

**Suíte**

```bash
pytest tests/test_api.py -q
pytest tests/test_frontend_collection_ui.py tests/test_frontend_cluster_filter_ui.py -q
pytest tests/ -q
```

### Sugestão de commit

```
feat: split site layout from status to shrink the dashboard poll
```

---

## Fase 4 — O gráfico 4G/5G: linha visível e ausência explicada

### Explicação simples

No gráfico principal, ao escolher "4G e 5G", às vezes aparecem as duas linhas e
às vezes só uma. São quatro causas diferentes: a métrica escolhida às vezes não
existe no 5G, um ponto solto no meio de buracos de coleta não é desenhado, o
filtro de tecnologia continua valendo depois de sumir da tela, e a família da
linha é adivinhada pelo nome da célula em vez de ser lida da coluna do banco.

Esta fase resolve as quatro e, quando uma família realmente não tem dado, faz a
tela **dizer isso** em vez de simplesmente não desenhar a linha.

### Escopo detalhado

**Código — `api/api.py`**

- `_average_series_by_family` e `_filter_cell_rows_for_family` passam a
  classificar pela coluna `technology` da linha, via `_technology_family`. O
  nome da célula (`_cell_technology_family`) fica como fallback apenas quando a
  coluna vem vazia — linhas legadas anteriores à coluna.
- `_collect_cell_rows`: o ramo de fallback de `utilization` (que combina
  `utilization_dl` e `utilization_ul` em dicionários montados à mão) passa a
  **preservar `technology`** nas linhas sintetizadas. Hoje elas saem sem a coluna
  e cairiam no fallback por nome logo depois da mudança acima.
- `get_kpi_series` passa a devolver `reasons`, um dicionário por família com a
  mesma semântica de `_overview_reasons`: `ok`, `no_traffic` (houve coleta na
  janela, a métrica ficou indefinida) e `no_data` (não houve coleta). As
  famílias consideradas são as do site fundido (`tech_families`), para que uma
  família esperada e ausente apareça como motivo em vez de sumir em silêncio.

**Código — `frontend/js/kpi.js`**

- `_techFamilyParam()` passa a receber o escopo selecionado e a devolver o filtro
  **efetivo** (o mesmo que `_effectiveTechFilter` já aplica ao seletor de
  células): um filtro que não está visível não pode recortar o gráfico. Com isso
  o seletor de células e o gráfico param de discordar.
- Os datasets de série ganham `pointRadius` calculado: quando a série tem poucos
  pontos não nulos, ou pontos isolados entre nulos, os marcadores passam a ser
  desenhados. `spanGaps: false` permanece — o objetivo é tornar o ponto isolado
  visível, não fingir continuidade sobre um buraco de coleta.
- Nota de motivo perto de `#chart-controls`, alimentada por `reasons`: "5G sem
  dado nesta métrica" (`no_data`) ou "5G sem tráfego no período"
  (`no_traffic`). O texto reusa o vocabulário que `kpi_overview.js` já mostra nos
  nove painéis, para a tela não ter dois jargões para a mesma coisa.
- A série de família desconhecida (`technology: null`) deixa de herdar o azul do
  4G por queda no padrão de `FAMILY_COLORS`: ganha cor e rótulo próprios.

**Interface**

- Mudança visível e desejada: onde antes não havia linha nem explicação, passa a
  haver ou uma linha com pontos marcados, ou uma frase dizendo por que não há.
- O seletor de tecnologia continua se escondendo em site de família única, mas
  deixa de ter efeito escondido sobre o gráfico.

**Testes**

- `tests/test_api.py`:
  - `test_serie_usa_a_coluna_technology_e_nao_o_nome` — célula sem token no nome
    (`18NLRJPE41A`) gravada com `technology='5G_NRDUCELL'` cai na série 5G.
  - `test_nome_da_celula_ainda_resolve_linha_legada` — linha com `technology`
    vazia continua classificada pelo nome.
  - `test_utilization_combinada_preserva_technology` — o ramo de fallback de
    `utilization` mantém a coluna.
  - `test_reasons_distingue_sem_coleta_de_sem_trafego` — família sem nenhuma
    linha na janela com coleta presente devolve `no_traffic`; janela sem coleta
    nenhuma devolve `no_data`; com dado devolve `ok`.
- `tests/test_frontend_kpi_overview_ui.py` (padrão existente) ou arquivo novo
  `tests/test_frontend_kpi_chart_ui.py`, com Playwright:
  - `test_ponto_isolado_recebe_marcador_visivel` — mock com uma série 5G de um
    ponto entre nulos; o dataset resultante tem `pointRadius > 0`.
  - `test_familia_sem_dado_mostra_motivo` — com o 5G ausente, a nota aparece com
    o texto correto.
  - `test_filtro_oculto_nao_esvazia_o_grafico` — deixar o filtro em 5G, escolher
    um site só-4G: o gráfico desenha a linha 4G e o seletor some.
  - `test_serie_sem_familia_nao_usa_a_cor_do_4g`.

### Como validar e testar

**Visual (`python main.py --mock`)**

1. Selecionar um site com as duas famílias, filtro em "4G e 5G", célula em
   "Média". Percorrer as métricas do seletor: onde há dado nas duas, duas linhas;
   onde só há 4G, uma linha **mais a frase** explicando o 5G.
2. Alternar a janela temporal (15 / 60 / 180 / Evento) na mesma métrica: a linha
   5G esparsa deve permanecer visível como pontos em todas as janelas, em vez de
   aparecer numa e sumir na outra.
3. Deixar o filtro em 5G, clicar num site só-4G: o seletor some e o gráfico
   **continua desenhando o 4G**. Antes, ficava vazio.
4. Conferir que a legenda distingue "Média 4G" e "Média 5G" com as cores fixas de
   família (4G `#388BFD`, 5G `#ab7df6`), e que nenhuma terceira série sai azul.

**Com o evento real**

Repetir o levantamento que produziu a tabela de 0.4 e conferir que as colunas
"Duas linhas / Só 4G / Só 5G" continuam as mesmas — esta fase **não inventa
dado**. O que muda é que `accessibility` e `drop_rate` passam a exibir os pontos
5G que existem e a dizer o motivo nos sites onde não existem.

**Suíte**

```bash
pytest tests/test_api.py -q
pytest tests/test_frontend_kpi_chart_ui.py tests/test_frontend_kpi_overview_ui.py -q
pytest tests/ -q
```

### Sugestão de commit

```
feat: keep the 5G line visible and explain when a family has no data
```

---

## Encerramento de cada fase

Conforme o `CLAUDE.md` do projeto:

- Anotar na `MEMORY.md` a data, as decisões travadas e o gate de testes da fase
  (`pytest tests/ -q` com o número de passed/skipped/failed).
- Registrar na `ERRORS.md` qualquer abordagem que falhou no caminho, com causa
  raiz e regra de prevenção.
- As medições de antes e depois de cada fase entram na entrada da `MEMORY.md`:
  são elas que impedem a próxima sessão de refazer o diagnóstico do zero.
