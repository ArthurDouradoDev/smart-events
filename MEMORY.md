# MEMORY.md — Log de decisões e fatos permanentes

Registro vivo do projeto. Nunca apagar histórico; sempre acrescentar com data e motivo.

---

## 2026-08-12 — Contrato real de coleta de VIP (Trace/FARS)

**Fonte de verdade:** `har-atualizado-filtrado.har`, captura manual da task 2072 (VIP Baldin),
validada ao vivo pela VPN em 12/08/2026.

O navegador **não** usa `query/subscribe-result` para a consulta de histórico. A sequência
correta, por ciclo, é:

1. `GET /traceresult/pre-check?taskId=<id>&queryType=0` — valida a task (`checkState`).
2. `GET /traceresult/query/result?taskId=<id>&msgId=1&startRow=0&pageSize=100` — **aloca um
   `msgId` novo** e devolve `recordCount` do conjunto completo.
3. `GET /traceresult/query/fetch-field-values?taskId=<id>&msgId=<alocado>` — devolve
   `startTime`/`endTime` da task.
4. `POST /traceresult/query/filter-by-cols` — filtra `Message Type = RRC_MEAS_RPRT` no
   servidor e pagina por `pageDto.startRow`/`pageSize`.
   > **Superado em 12/08/2026** pela entrada "Paginação do VIP" abaixo: paginar re-POSTando
   > o `filter-by-cols` re-executa o filtro contra os dados vivos. O passo 4 agora é
   > `filter-by-cols` (uma vez) → `sort` → `result-paging`. Mantido aqui como histórico.

**Decisões travadas:**

- **`msgId` é um handle descartável de consulta.** Cada `query/result` com `msgId=1` aloca um
  novo (observado: 7708005 → 7709005 → 7711005 → 7712005 → 7713005). Ele precisa ser criado e
  consumido no mesmo ciclo. Reaproveitar um `msgId` entre ciclos faz o FARS responder sem linhas.
- **`startTime`/`endTime` são obrigatórios em `filter-by-cols`**, mesmo com
  `hasStartTime`/`hasEndTime` falsos. Vazios → HTTP 500 `framwork.remote.SystemError`.
  Por isso o passo `fetch-field-values` não é opcional.
- **RSRP/RSRQ são decodificados localmente** a partir de `messageBody` (`core/rrc_decode.py`),
  não via `msg-explain-info`. O contrato de `rowNo` do `msg-explain-info` nunca foi capturado e
  chutá-lo produz dado errado. O decode local é ASN.1 UPER de `UL-DCCH-Message` (TS 36.331), com
  1 byte de cabeçalho proprietário antes do PDU. Cobertura: 386/386 na captura, 616/616 ao vivo.
- **Cursor duplo por task:** `serial` (marca d'água de `serialNo`) e `row` (offset na lista
  filtrada). `row` é a otimização que evita rebaixar o conjunto inteiro; `serial` é a garantia
  de correção. O índice único `(task_id, serial_no)` é o backstop final contra replay.
- **`serialNo` é monotônico com o tempo** e estável entre ciclos enquanto a task roda — é um
  cursor válido. A resposta de `filter-by-cols` **não** vem ordenada; ordenar no cliente.
- **Uma task = um VIP em deslocamento.** A task 2072 cobriu 16 células de 9 NEs distintos numa
  única janela. A premissa de atribuir todas as `RRC_MEAS_RPRT` da task ao VIP configurado está correta.
- **Decode indecifrável avança o cursor.** `decode_meas_report` é função pura dos bytes: uma
  mensagem que falha hoje falha sempre. Parar nela travaria a coleta para sempre. A ocorrência
  vira diagnóstico e `coverage["undecoded_messages"]`, nunca zero artificial.

**Fixtures de regressão** (sanitizadas, sem cookies/tokens):
`tests/fixtures/vip_filter_by_cols.json`, `vip_query_result.json`,
`rrc_meas_report_vectors.json` (386 vetores reais).

---

## 2026-08-12 — Contrato do `rowNo` / `msg-explain-info` (Captura A) — RESOLVIDO

**Fonte:** `har-atualizado-filtrado-ordenado-completo.har`, task 2072. Análise completa em
`analise-har-captura-a.md`.

- **`msgId` = "este conjunto de resultados, NESTA ordenação".** Não é só a sessão de consulta.
  Todo passo que muda o conjunto aloca um handle novo e o devolve em `data.msgId`:
  `query/result`(msgId=1) → 7861005 → `filter-by-cols` → 7866005 → `sort`(asc) → 7867005 →
  `sort`(desc) → 7868005. `fetch-field-values` e `msg-explain-info` **não** alocam.
- **`rowNo` é o índice 1-based da linha no conjunto do `msgId` enviado, na ordenação daquele
  `msgId`.** Não é relativo à página; não é o `serialNo`. Provado por casamento exato de bytes
  em 4 cliques (rowNo 1/36/618/500), com dois deles feitos após rolar para `startRow` 588 e 492 —
  o que elimina a hipótese de índice de página. **Um `rowNo` só é válido junto do `msgId` que
  produziu aquela ordenação**; cruzar os dois lê a mensagem errada em silêncio (bug da Fase 3).
- **Parâmetros do `msg-explain-info` confirmados** como já são enviados: `tabularFlag=y`,
  `isSubscribe=false`, `isSecondDecode=false`, `isPlayback=false`. Resposta: chave **`val`**
  (não `value`), valor no decimal entre parênteses (`0x16(22)` → 22), folha vs. container por
  **`attrType`** (1 vs 3), `children` para aninhar. **`serialNo` da raiz vem sempre `null`.**
  `tableData` traz os bytes brutos em **lista de chunks** (concatenar antes de comparar).
- **Decodificador local validado contra o dissector do servidor: 4/4 exatos** em
  `measId`/`rsrpResult`/`rsrqResult`, e **619/619** decodificadas no conjunto filtrado, header
  proprietário sempre de 1 byte. Fecha o item 3 da validação com VPN da seção 7 do plano.
- **O 5G do VIP já está dentro do `RRC_MEAS_RPRT` LTE** que já coletamos: mensagens EN-DC/NSA
  trazem `measResultServFreqListNR-r15` e `measResultNeighCellListNR-r15` com RSRP/RSRQ/RS-SINR e
  PCI NR, em ASN.1 **padrão 3GPP** (TS 36.331 rel-15) — não proprietário. `carrierFreq-r15=634080`
  = 3511,2 MHz = n78, o 3,5 GHz brasileiro. **Logo o 5G do VIP não depende do `msg-explain-info`
  nem do dissector Huawei**; depende de estender `core/rrc_decode.py` além de `measResultPCell`.
  Gate ainda aberto: conferir as conversões TS 38.133 contra a tela antes de gravar.
- **`filter-by-cols` sem sort vem embaralhado**: 127 violações de ordem crescente em 619 linhas
  (ex.: serial 64898 @12:29:05 seguido de 64476 @12:28:36). `POST query/sort` com
  `sqlColumnName:"Time"` devolve ordenação perfeita (0 violações).
- **`serialNo` é monotônico com o tempo, com empates.** As 4 inversões observadas no conjunto
  ordenado ocorrem todas em timestamps idênticos. O cursor por `serialNo` é válido; só não se pode
  assumir ordem estrita dentro do mesmo milissegundo.
- **Comparar `Time` como string é bug**: os ms não são zero-preenchidos, então `"(98)" > "(179)"`
  lexicograficamente. Toda ordenação por tempo passa pelo parse com `zfill(3)`.
- **`GET query/result-paging`** (endpoint novo, antes desconhecido) pagina um `msgId` já
  materializado: `?msgId=&taskId=&startRow=&pageSize=`. `startRow` além do fim devolve
  `tableData: []` com HTTP 200 — condição de parada segura. É como o navegador pagina; re-POSTar
  `filter-by-cols` por página re-executa o filtro contra dados vivos e pode pular/duplicar linhas.

---

## 2026-08-12 — Paginação do VIP: filtrar uma vez, ordenar, paginar

Implementação do que a captura A revelou. Fluxo por ciclo em `collect_vips`:

```
query/result (msgId=1)  →  handle bootstrap
filter-by-cols          →  handle filtrado   (uma vez por ciclo, startRow sempre 0)
query/sort  Time asc    →  handle ordenado
result-paging × N       →  as linhas, sobre o handle ordenado
```

- **`filter-by-cols` roda uma vez por ciclo.** Repetí-lo por página re-executa o filtro contra
  os dados vivos: duas páginas do mesmo ciclo podiam sair de snapshots diferentes, e cada
  chamada ainda vazava um handle no servidor.
- **Ordenação ascendente por `Time`, no servidor.** Ascendente é obrigatório para o cursor por
  offset: dados novos entram no fim e o prefixo já consumido continua estável. Descendente
  desloca todos os offsets a cada ciclo. (Descendente é o que a tela usa — não confundir.)
- **A marca d'água de `serial` só avança quando o conjunto foi lido inteiro.** Com `backlog`,
  só o cursor `row` avança. Ver a entrada correspondente no `ERRORS.md`.
- **`_VIP_PAGE_SIZE` (1000) é usado também no `filter-by-cols` e no `sort`**, cujas linhas são
  descartadas — só interessam `data.msgId` e `recordCount`. É tráfego redundante conhecido;
  reduzir o `pageSize` desses dois passos não foi feito porque nenhum valor menor foi capturado.
- **Falha no `sort` derruba o ciclo como `partial` com diagnóstico visível**, de propósito.
  Nenhum fallback silencioso para o fluxo antigo — degradação silenciosa foi o que escondeu a
  falha da Fase 3.

Regressões: `test_o_filtro_roda_uma_vez_e_a_paginacao_usa_o_conjunto_ordenado`,
`test_a_ordenacao_e_ascendente_para_o_cursor_por_offset`,
`test_ciclo_parcial_nao_avanca_a_marca_dagua_de_serial`,
`test_conjunto_fora_de_ordem_nao_perde_linhas_em_ciclo_parcial`.

---

## 2026-08-12 — Falhas pré-existentes conhecidas (não introduzidas pela Fase 3)

- `tests/test_alarms.py::TestMockCollectAlarms` (2 testes) espera `list`, mas a Fase 1 mudou
  `collect_alarms` para devolver `CollectionResult`. O teste nunca foi atualizado.
- `tests/test_credentials.py::test_resolve_base_url_pelo_catalogo` resolve RJ para o IP de SP.
  Problema de catálogo em `credentials.json`/`clientes.json`.

Ambas falham no HEAD e são independentes da coleta de VIP.

---

## 2026-08-13 — Popup analítico de VIP: implementação completa do plano (U1+U2+U3)

Uma tentativa anterior (Gemini) implementou só a U1 (enriquecimento de `get_vip_series` com
`serving_site`/`serving_site_name` em `api/api.py`) mais um ajuste cosmético de CSS/HTML
(`popup-chart-box`). `frontend/js/vip.js` não tinha sido tocado — por isso a interface parecia
inalterada. Esta sessão completou U2 e U3 sobre o plano
`docs/plans/2026-08-12-001-feat-vip-detail-popup-plan.md`.

- **Reestruturação do modal** (`frontend/index.html`, `frontend/css/main.css`): cabeçalho
  (nome, função, badge de status, último registro), faixa compacta (site/célula/RSRP/RSRQ) e
  região de gráfico flexível com estados loading/vazio/erro mutuamente exclusivos.
- **Tooltip via `external` callback do Chart.js, não callbacks de canvas.** O tooltip padrão do
  Chart.js desenha no canvas (pixels), o que o torna impossível de inspecionar via DOM em
  testes Playwright. Trocado por um `<div id="vip-modal-tooltip">` posicionado via
  `tooltipModel.caretX/caretY`, alimentado pelo mesmo array canônico indexado por `dataIndex`.
  Isso também é o que permite testar conteúdo do tooltip (site/célula/RSRP/RSRQ) sem comparação
  de pixel — mitigação de risco que o próprio plano pedia.
- **Linha-guia vertical**: plugin Chart.js local (`_vipHoverLinePlugin`, `afterDraw`), passado
  via `plugins: [...]` no config do chart (não `Chart.register` global) para não vazar para os
  gráficos de `kpi.js`.
- **Proteção contra resposta tardia**: token `_requestGen` incrementado em `_openModal`/
  `_closeModal`; comparado após o `await` antes de renderizar. `State.on("change:activeEvent"/
  "change:historicalEvent", _closeModal)` fecha o popup ao trocar de evento.
- **Cards de VIP viraram focáveis** (`tabIndex=0`, `role="button"`, Enter/Espaço abrem) — sem
  isso, `_lastFocusedEl.focus()` no fechamento do modal é um no-op (elemento não focável) e a
  restauração de foco (R13) não tem como funcionar.
- **Mocks determinísticos** em `frontend/js/bridge.js` (`?vipSeriesScenario=`): `default`
  (sequência fixa cruzando 2 sites, célula não mapeada, timestamp duplicado, métrica nula),
  `empty`, `error`, `error_once` (falha uma vez por nome de VIP, depois recupera — testa retry),
  `delay` (1.5s, para exercitar a proteção contra resposta tardia).
- Suíte nova: `tests/test_frontend_vip_modal_ui.py` (11 testes Playwright, mesmo padrão de
  `test_frontend_collection_ui.py`). Aponta o hover para pixels calculados via
  `Chart.instances` + `getDatasetMeta` em vez de comparar imagem.
- Gate completo (`pytest tests/ -v`): 258 passed, 3 failed (as pré-existentes documentadas
  acima), 10 skipped. Nenhuma regressão nova.

---

## 2026-08-13 — Fix: `cells_data` vazio no fast path do agregado de site persistido

`Api.get_kpi_series` (`api/api.py:451-465`) tem dois caminhos para `cell_id == "__all__"`: um
fast path que devolve o agregado SITE já persistido (`db.get_kpi_site_series`, gravado por
`core/collector.py::_site_rows` e pelo recalculo de métricas `site_aggregation="recalculate"`,
ex. `accessibility`), e um fallback que agrega on-the-fly a partir das linhas CELL. Só o
fallback populava `cells_data` (linhas por célula do gráfico "Site completo"); o fast path
devolvia `cells_data: {}`, e `frontend/js/kpi.js:569` cai para uma linha única quando isso
acontece. Corrigido: o fast path agora também monta `cells_data` a partir de
`db.get_kpi_series` (linhas CELL), alinhado aos `labels` do agregado. Ver detalhe da causa em
`ERRORS.md` ("Gráfico 'Site completo' perdeu as linhas por célula").

---

## 2026-08-13 — Painel "Status de coleta" enxuto; erro técnico só nos logs

O popup listava ~10 linhas por coletor (tentativa, ciclo válido, medições, cobertura, próximo
ciclo) e despejava a exceção crua em "Diagnóstico" (ex.: `HTTPSConnectionPool(...) Max retries
exceeded ... ConnectTimeout`). Decisão: o popup é do **usuário**, não do desenvolvedor.

- **Uma linha por coletor** (`frontend/js/app.js::_syncLine`): Sites (KPI) / VIPs / Alarmes /
  Sessão → `● estado · idade do último dado [· n/N VIPs]`. O timestamp absoluto virou `title`
  (hover). Removidas as linhas de tentativa/ciclo/medições/cobertura/serial/próximo ciclo e o
  `_syncSection`/`_syncSessionSection` antigos (com `_nextCycleText` e `_formatStatusTime`).
- **Erro traduzido** (`_syncHint`): classifica pelo `code` do último diagnóstico
  (`network`/`http`/`contract`/`catalog`/`auth_required`) → frase acionável
  ("Sem conexão com o servidor — verifique a VPN.", "Sessão expirada — reconecte…"). Sem código
  reconhecido: "Falha na coleta — detalhes nos logs técnicos.". Botão **Ver logs técnicos**
  abre a gaveta `</>` (clica em `logs-btn`).
- **Texto cru garantido nos logs:** os ramos `except` de `_collect_kpis_v2`, do Trace e parte
  dos alarmes (`core/collector.py`) retornavam `CollectionResult.error/auth_required` **sem**
  logar; como a UI não mostra mais a exceção, cada ramo ganhou `logger.error` (root handler →
  buffer do painel `</>`).
- `core/scheduler.py::_apply_unexpected_error` agora zera `diagnostics`: a UI classifica pelo
  `code` do último diagnóstico e o do ciclo anterior descreveria outra falha.
- CSS (`frontend/css/main.css`): `.sync-hint`/`.sync-hint.err` e `.sync-logs`; removidos
  `.sync-section-title` e `.sync-err`, órfãos após a mudança.
- Gate: `pytest -q` = 258 passed, 3 failed (as pré-existentes: 2 de `TestMockCollectAlarms`
  esperando `list` em vez de `CollectionResult`, 1 de catálogo em `test_credentials`), 10
  skipped. Rodar com `--basetemp` dentro do projeto/scratch: o tmp padrão do pytest dá
  `PermissionError` neste Windows.


---

## 2026-08-13 — Mapeamento objeto→célula deixa de depender do token de tecnologia no nome

**Contexto:** análise de multi-regional (troca SantoAmaro → Curitiba). Ver a entrada
correspondente no `ERRORS.md` para a causa raiz e a prova nos bancos.

- **A tecnologia do dado é da TASK PM consultada, não do palpite pelo nome da célula.**
  `_normalize_cell_technology` continua existindo (é o que separa 4G de 5G quando o inventário
  declara), mas `_resolve_monitoring_cell` passa a aceitar célula de tecnologia DESCONHECIDA
  (`None`) como candidata de qualquer task, gravando no `_obj_to_cell` a tecnologia da task.
  Célula de tecnologia CONHECIDA e diferente continua recusada — o gate existe para impedir a
  troca silenciosa de 4G por 5G, e essa parte se mantém.
- **A regra de candidato único (`len(choices) != 1`) é o que segura a ambiguidade.** Com o
  filtro relaxado, dois nomes iguais em tecnologias diferentes continuam falhando visivelmente
  em vez de escolher um.
- **Convenção de nome de célula é por OSS, não por cliente.** SP responde `4G-SPSMG7-18-C`
  (token no nome); o OSS de Curitiba/OUTRAS responde `18NLCTAL01GI`. O inventário do evento de
  Curitiba mistura as duas convenções (5.637 células `4G-CT…` vindas do servidor central e 605
  `18NL…`), e só as `18NL…` têm chance de casar com o que aquele OSS devolve. Isso limita a
  cobertura mesmo depois desta correção — o log novo é o que mede o quanto.
- **Cobertura parcial agora tem causa visível:** `_log_unmapped` emite um WARNING por ciclo com
  os nomes vindos do OSS e exemplos das células cadastradas, lado a lado. `coverage.unmapped_cells`
  já viajava em `get_collection_status`, mas nenhum arquivo do frontend o consome — o log é a
  superfície operacional (painel de Logs + download).
- Regressões: `test_celula_sem_tecnologia_no_nome_e_mapeada_pela_task`,
  `test_celula_de_outra_tecnologia_nao_e_sequestrada_pela_task`,
  `test_objeto_nao_mapeado_e_diagnosticado_pelo_nome`. As duas primeiras falham com a correção
  revertida (verificado), a terceira falha com o `objName` lido só de `obj`.
- Gate: `pytest tests/ -q --basetemp=.pytest-work/tmp` = 261 passed, 3 failed (as mesmas
  pré-existentes), 10 skipped.

**Pendências desta análise, em ordem (não implementadas):** cursor não avançar quando o ciclo
descarta tudo; `region` do evento com fonte única de verdade e sem fallback silencioso para SP;
troca de projeto encerrando o anterior de verdade (`stop()` com `join(timeout=5)` + `_stop_event.clear()`
pode vazar thread de coleta); estado de renovação de sessão (`_needs_interactive`, backoff,
`browser_profile`) chaveado por regional; `task_id` de VIP validado contra o OSS conectado.

---

## 2026-08-14 — Contrato de Monitoring difere por regional na identificação do objeto

**Fato permanente (medido no corpo real, não presumido):** o resultado de
`POST /rest/oss/access/pm/v1/monitor/task/result` identifica a célula com grafias diferentes
conforme o OSS:

| Campo | SP (`10.220.50.9`) | Curitiba/OUTRAS (`10.220.30.9`) |
|---|---|---|
| número do objeto | `objRes[].obj.objNo` | `objRes[].obj.objectNo` (string) |
| nome do objeto | `objRes[].obj.objName` | `objRes[].obj.objectName` — e `objName` vem `null` |

O eco de cursores no nível da task (`data[].objNoExecTimes[].objNo`) usa `objNo` **nos dois**.
`counterRes` é lista de dicts nos dois. `_obj_field` em `core/collector.py` lê as duas grafias;
não substituir por uma só sem capturar os dois OSS de novo.

**Descoberta:** o navegador abre a task com `[{"taskId": N, "preExecTime": 0}]`, sem a chave
`objNoExecTimes`. O app faz o mesmo desde 14/08.

**Forma do corpo (Curitiba, task 2225):** `data[0].results[]` traz uma janela por `execTime`
(period=5), cada uma com os 116 `objRes`. O OSS acumula até ~6 janelas, então uma consulta de
descoberta devolve 464–696 objetos — repetição esperada, absorvida pela chave única de
`kpi_measurements`. As métricas `ran_rtt` e `terrestrial_rtt` não têm contador nessa task e
sempre geram `invalid_formula`; o ciclo fica `partial` por isso mesmo com dados bons.

**Estado das fases:** Fase 4 fechada em 14/08 — replay do dump real dá 464 recebidos, 0 não
mapeados, 5.124 linhas. Fase 5 (contrato FARS assíncrono para VIP) segue pendente: em Curitiba
`query/fetch-field-values` responde HTTP 500 em todo ciclo.

---

## 2026-08-14 — O 5G do Monitoring é NR Cell + NR DU Cell; "SA/NSA" não existe como task

**Fonte:** `har-5g-oss/` (4 HARs, os dois OSS, NR Cell e NR DU Cell). Fecha a "Captura B" que
`pendencia-har.md` listava como a última lacuna funcional. Plano derivado:
`docs/plans/2026-08-14-001-feat-5g-nrcell-nrducell-monitoring-plan.md`.

- **Não existe task "SA" nem "NSA"** — 98 tasks em OUTRAS, 106 em SP, nenhuma. A divisão é por
  **tipo de objeto**: `TesteSantoAmaro NRCELL (749)` / `NRDUCELL (748)` em SP;
  `TESTE FERRAMENTA - 5G CELL (2241)` / `5G DUCELL (2242)` em OUTRAS. `GET monitor/task/view-tree`
  enumera todas com id, nome e status.
- **Os dois conjuntos de contadores são disjuntos** (interseção vazia) e **o catálogo 5G atual já
  está correto**: NR Cell (20 contadores) fecha `accessibility`, `drop_rate`, `availability`,
  `user_count` — 4/4 sem ausência; NR DU Cell (12) fecha `utilization_dl/ul`, `throughput_ul`,
  `interference_ul` e os quatro `traffic_volume_*` — 8/9. Nenhum contador da task fica sem uso.
- **Único bloqueio: `N.ThpVol.DL.LastSlot` não está configurado na task DU Cell**, então
  `throughput_dl` 5G não calcula. Encaminhamento é **pedir o contador no iManager**, não alterar a
  fórmula para caber na task.
- **`N.ThpVol.DL == N.NSA.ThpVol.DL` em 5/5 objetos, DL e UL.** Todo o tráfego é NSA e o volume SA é
  zero — `traffic_volume_dl_sa = N.ThpVol.DL − N.NSA.ThpVol.DL` está **certo** e mede isso. A
  distinção SA↔NSA é de **contador dentro da task DU Cell**, nunca de task. Não trocar a subtração
  por leitura direta de `N.NSA.ThpVol.*`: daria o mesmo número hoje e apagaria a capacidade de ver
  tráfego SA quando existir.
- **`objNo` é namespace da task, não do OSS.** Mesma célula tem objNo diferente em cada task
  (`5G-SPSMG7-35-MB` = 17714 na 749, 17717 na 748) e **63 dos 105 objNo da 2241 reaparecem na 2242**
  em OUTRAS (em SP são disjuntos). Com `_obj_to_cell` chaveado só por `obj_no`, a segunda task herda
  o mapeamento da primeira pelo early-return e grava medição na célula errada, em silêncio. A chave
  correta é **`(task_id, obj_no)`** — não `(technology, obj_no)`.
- **Unidades medidas:** `N.ThpVol.DL`/`.UL` e `N.NSA.ThpVol.*` em `kbit`; `N.Cell.Avail.Dur` em `s`;
  `N.UL.NI.Avg` em `dBm`. Os contadores de tempo (`N.ThpTime.*`) vêm **sem unidade** — é o que ainda
  segura `production_ready` de `throughput_ul`.
- **O regex `Cell Name\s*=\s*([^,]+)` já serve aos dois tipos**: em `NR DU Cell Name=…` ele casa o
  sufixo e devolve o nome certo. E **o nome da célula é o mesmo nas duas tasks** — uma entrada de
  inventário serve às duas, a resolução dinâmica por nome funciona, `obj_no` manual continua
  desnecessário.
- **Inventário:** `testesantoamaro` já tem as 5 células 5G e elas casam 5/5 com o OSS — SP é
  validável hoje só adicionando as tasks 748/749. `teste-curitiba` tem **0** células 5G em 6.242
  contra 105 objetos na task 2241 — OUTRAS exige recadastro do inventário antes de qualquer teste.
- **Sem custo de migração:** `kpi_measurements` tem 100% `4G` nos dois bancos de produção
  (99.060 Curitiba / 137.387 Santo Amaro) e nenhuma linha `5G`.
- **Pendente de medição:** o corpo da task 2242 (NR DU Cell de OUTRAS) não foi capturado — as 3
  respostas vieram com 200 e ~284 KB, mas sem `content.text`. A NR Cell é idêntica entre os OSS, o
  que torna razoável esperar o mesmo da DU Cell; razoável não é medido. Recapturar com corpo antes
  de fechar OUTRAS.

---

## 2026-08-14 — Fases 5 e 6: contrato FARS regional e saneamento seguro

O FARS deve ser selecionado por capacidade do host e versão autenticada da sessão:

- **síncrono (SP):** `fetch-field-values` + `filter-by-cols`;
- **assíncrono (Curitiba/OUTRAS):** `fetch-field` + um `filter-by-cols-start`, seguido de polling
  em `filter-by-cols-result`; `sort` e `result-paging` continuam síncronos nos dois OSS.

A interface operacional mostra regional, host, contrato FARS detectado e a causa de cada task
VIP que falhou. Não inferir o contrato pelo IP nem esconder falha de task numa causa agregada.

O saneamento de checkpoints é deliberadamente em duas etapas: `tools/checkpoint_hygiene.py audit`
gera relatório somente leitura; `apply` exige o arquivo, seu `confirmation_hash`, nenhum evento
`ACTIVE` e cria backup SQLite validado por `integrity_check` antes de qualquer `DELETE`. Cada linha
é revalidada por chave, cursor e `updated_at`; se mudou desde a auditoria, toda a transação sofre
rollback. Nunca editar/apagar checkpoints diretamente em produção.

Persistência de KPI, VIP, alarmes, alertas e checkpoints usa WAL, `busy_timeout=30000`, transações
curtas e rollback explícito. `resolve_base_url` e `get_event_vips` falham fechado quando
cliente/regional não estão resolvidos; não existe mais fallback silencioso para SP ou para toda a
lista global de VIPs.

---

## 2026-08-19 — Fase 1: fusão de sites 4G/5G e filtro por tecnologia

Plano: `docs/plans/2026-08-19-001-feat-site-merge-clusters-kpi-overview-plan.md`.

A EP entrega o mesmo site físico duas vezes (eNodeB 4G e gNodeB 5G, ids distintos, nome e
coordenada iguais). A fusão é recorte de leitura: não migra dado, não altera coleta, não muda
schema. `kpi_measurements.site_id` continua guardando os ids originais.

**Decisões travadas:**

- **Chave de fusão:** nome normalizado (`strip().upper()`) **e** distância ≤ 50 m (haversine).
  Nome igual em coordenada distante **não funde** e emite `logger.warning` com os dois ids.
- **Id do site fundido é sintético** (nome normalizado, ex. `SPSMG7`), com
  `members: [{site_id, family}]`. Nunca reaproveita o id de um dos gêmeos.
- **Site sem gêmeo mantém o id original** (ex. `725469` / `SR-SPPNB2`). Usar o nome como id
  também nos não-fundidos quebraria consumidores que já resolvem pelo eNodeB (`get_vip_series`,
  alarmes, testes).
- **Uma única função:** `Api._merged_sites(config)` em `api/api.py`. Consumida por
  `get_sites`, `get_site_cells`, `get_kpi_series`, `get_alarms`, `get_vips` e `get_vip_series`.
  Quem ler `config["sites"]` cru devolve um `site_id` que nenhum marcador tem.
- **Família do membro** sai das células via `_cell_technology_family`. Membro sem família
  identificável entra como `None` e permanece visível em qualquer filtro.
- **`metric_by_site` é chaveado por `(merged_id, family)`.** Corrige o §0.3: duas linhas SITE
  (4G e 5G) para a mesma métrica deixam de se sobrescrever. Em "Ambas", o valor da lista combina
  as famílias com a regra da métrica: soma (throughput/volume), média (availability/accessibility),
  máximo (utilização/usuários).
- **`get_kpi_series` aceita o id fundido**, expande para os membros e devolve
  `series: [{technology, labels, values}]`. Com uma família só, `labels`/`values` no formato
  antigo. Com duas, `values` fica vazio de propósito — não misturar 4G e 5G num array só.
- **`serving_site` de alarme e VIP é o id fundido.** Pré-requisito da Fase 2 (badge no marcador).
- **Filtro de tecnologia** (`State.techFilter`: `"all"|"4G"|"5G"`). `#tech-selector` no
  `#chart-controls`, oculto quando o site tem uma família só. Recorta células no seletor e
  pétalas no mapa. Cores de série fixas por família (4G `#388BFD`, 5G `#ab7df6`).

**Baseline de testes (corrigido):** o plano mediu 332 passed / 10 skipped em 19/08.
A menção antiga "261 passed, 3 failed" (13/08) está superada — as 3 falhas pré-existentes de
`TestMockCollectAlarms` / catálogo já tinham sido corrigidas em sessões posteriores.

---

## 2026-08-19 — Correção: média e site completo no site fundido

Campo: no SPSMG7 fundido, "Média" e "Site completo" saíam em branco. Causa: `get_kpi_series`
curto-circuitava nas linhas SITE e, com duas famílias, devolvia `values: []` e `cells_data: {}`.

- **Média:** média das células **por família** — duas linhas (Média 4G / Média 5G) em "Ambas".
  O agregado SITE não substitui a média.
- **Site completo:** devolve `cells_data` das células dos membros. Ambas / 4G / 5G recortam
  as linhas; a legenda do Chart.js continua ocultando célula por clique.
- Trocar o filtro de tecnologia **mantém** Site completo ou Média se já estava selecionado.
  Ao selecionar um site, o default é sempre **Média** — Site completo abre popup e não
  deve disparar sozinho a cada troca de site.
- O rótulo do filtro de tecnologia é **4G e 5G** (não "Ambas").
- Badges de VIP e alarme vão num pane Leaflet `badges` (z-index 625, acima do
  `markerPane`). Sites indoor na mesma coordenada não tapam o V / o triângulo.

Gate: `pytest tests/ -q --basetemp=.pytest-work/tmp` → **343 passed, 10 skipped, 0 failed**.

---

## 2026-08-19 — Fase 2 fechada: badges de VIP e de alarme no marcador

Plano: `docs/plans/2026-08-19-001-feat-site-merge-clusters-kpi-overview-plan.md`.

- **Badge é marcador próprio, não elemento do ícone de setores.** Cada site com VIP e/ou alarme
  ganha um `L.marker` extra no pane `badges` (z-index 625), com `interactive: false`. O SVG das
  pétalas continua sendo só pétalas — o recorte do `viewBox` do ícone de setores deixa de ser
  problema por construção.
- **O `viewBox` do badge dimensiona pela MAIOR das duas extensões** (`offset + max(raio do VIP,
  metade da base/altura do triângulo) + stroke + 1`). Só com o raio do VIP, o triângulo era
  recortado em zoom máximo (`scale = 2`).
- **VIP à direita (círculo dourado com "V"), alarme à esquerda (triângulo com "!").** A cor do
  triângulo vem da maior severidade do site: Critical `#F85149`, Major `#FF7B00`,
  Minor `#D29922`, resto `#58A6FF`.
- **Ganchos de teste no SVG:** `className: "site-badge"` no divIcon, `data-site` no `<svg>` e
  grupos `.badge-vip` / `.badge-alarm`. Os testes de Playwright medem `getBBox()` contra o
  `viewBox` — sem esses ganchos não há como afirmar que o ícone não foi cortado.
- **O alarme do mock cai no site fundido** (`serving_site: "SPSMG7"` vindo de célula 5G), que é o
  que exercita a dependência da Fase 1 sem VPN.

**Corrigido junto (fragilidade pré-existente, não desta fase):** o mock de série do VIP
(`_mockVipSeriesRows`) gerava 7 pontos com passo fixo de 20 min. A janela "Hoje" corta em 00:00
local, então rodar a suíte antes das 2h20 deixava a fixture inteira no dia anterior e
`test_vip_modal_time_windows_switch_without_leaking_state` falhava. O passo agora é
`min(20 min, elapsed_desde_meia_noite / 7)` — a fixture cabe no dia corrente em qualquer hora e
o comportamento durante o dia fica idêntico ao de antes.

Gate: `pytest tests/ -q --basetemp=.pytest-work/tmp` → **346 passed, 10 skipped, 0 failed**
(rodado às 00:24, exatamente a faixa horária que reprovava).

---

## 2026-08-20 — Coleta parada na OSS OUTRAS: a task é que não executa

Investigação a partir do painel "Parcial · recebidos 0" no evento `teste-curitiba`
(OSS OUTRAS, host 10.220.30.9).

- **Monitoring (task PM 2225):** o OSS responde `HTTP 200`, `success: true`,
  `state: -1`, `results: []` e `execTime = 2026-08-16 12:54`. Os 116 objetos voltam com
  `preExecTime` congelado no mesmo instante. Última coleta com dado: **16/08 12:56**
  (`recebidos=232`). Desde 18/08, todo ciclo é `recebidos=0`.
- **`objNoExecTimes` da resposta é estado do OSS, não eco da requisição.** Prova: em
  20/08 09:19 o app enviou 116 objetos (`tasks=2225/116obj` no log) e a resposta veio com
  `objNoExecTimes: []`; em SP, com 28 objetos enviados, também veio `[]`.
- **Trace/VIP (task FARS 14837):** `recordCount` congelado em **22635** desde 14/08 19:57,
  com `backlog=0` — o coletor já consumiu tudo o que a task tem. O OSS não gera registro novo.
- **O código não é a causa:** SP (10.220.50.9), com exatamente o mesmo coletor, coletou
  normalmente em 20/08 09:14 (`recebidos=76`). O padrão "dois ciclos vazios e depois dado"
  aparece nos dois OSS quando a task está rodando — é a descoberta, não uma falha.
- **Ação fora do app:** reativar a task PM 2225 e a task de trace 14837 no iManager de
  OUTRAS. Nenhuma mudança de código produz dado que o OSS não está gerando.
- **Defeito real corrigido junto:** o ciclo virava "Parcial" genérico e escondia a frase do
  próprio OSS. `_describe_idle_tasks` passa a ler `execTime`/`state` das tasks que voltaram
  sem `results` e produz a causa `"O OSS não registra execução nova do Monitoring: task 2225
  desde 16/08 12:54 (há 3 d)"`, em WARNING no log e no painel (`_syncHint` usa `s.cause`).
  Só dispara com `recebidos == 0` **e** `execTime` presente — o primeiro ciclo de descoberta
  não tem `execTime` e não pode ser acusado de task parada.

Gate: `pytest tests/ -q --basetemp=.pytest-work/tmp` → **349 passed, 10 skipped, 0 failed**.

---

## 2026-08-20 — Fase 3: N tasks por tecnologia, um POST por task

Plano: `docs/plans/2026-08-19-001-feat-site-merge-clusters-kpi-overview-plan.md`.

O OSS responde uma task por POST (medido nos 8 HARs). Cada monitoring cabe 300 células, então
um evento grande precisa de várias tasks da mesma tecnologia. A trava "uma task por tecnologia"
foi removida; a unicidade passa a ser por `task_id` — o mesmo id com duas tecnologias era a
ambiguidade real (`task_id → technology` perderia uma em silêncio).

**Decisões travadas:**

- **Um POST por task, sequencial.** O payload continua `[{taskId: N, …}]`. As respostas
  bem-sucedidas são concatenadas e parseadas de uma vez, para o agregado SITE (sum/mean/
  recalculate) continuar vendo todas as células da mesma tecnologia juntas. Concatenar as
  linhas SITE de cada parse parcial duplicaria o site quando duas tasks 4G cobrissem células
  do mesmo eNodeB.
- **Falha de uma task não derruba as outras.** HTTP 500 / timeout / JSON inválido vira
  diagnóstico daquela task (`details.task_id`) e o ciclo fecha `partial` com as linhas das
  que responderam. Se nenhuma task responde, o ciclo fecha `error`.
- **Renovação de sessão uma vez por ciclo**, não por task. Sessão inválida depois da
  renovação: se já houver dado, persiste `partial` e para as restantes; se não houver, fecha
  `auth_required`.
- **Log:** uma linha `[monitoring] task=<id> HTTP=…` por task, mais uma linha de total do
  ciclo com `duracao`. WARNING se a duração passar de `INTERVAL_KPI_SECONDS` (120 s).
- **Diagnósticos deduplicados por `(metric, code)`** antes de sair do parser. Um contador
  ausente em 100 objetos gera uma entrada, não 100.
- **Cadastro:** os três inputs fixos viraram lista repetível `{tecnologia, task_id}`.
  Reeditar um evento com duas tasks 4G deixa de apagar a segunda. Evento legado com
  `pm_task_id` continua populando uma linha 4G via `configuredPmTasks`. Rótulo da lista:
  `4G ×3 · NR Cell ×1`.
- **`throughput_dl` 5G_NRDUCELL:** `production_ready=True`, unidade `Mbit/s`,
  `monitoring_available=True`. Fecha o §0.9 (LastSlot passou a existir na task 748). A
  fórmula não muda. `throughput_ul` 5G segue pendente.

Gate: `pytest tests/ -q --basetemp=.pytest-work/tmp` → **357 passed, 10 skipped, 0 failed**.

---

## 2026-08-20 — Fase 4: clusters de sites

Plano: `docs/plans/2026-08-19-001-feat-site-merge-clusters-kpi-overview-plan.md`.

Clusters são grupos de sites N:N (um site pode estar em vários clusters — caso do
estádio com um site atendendo Sul e Oeste). Sem mudança de schema: `event.clusters =
[{id, name, color, site_ids, polygon}]` dentro do `config_json` já existente.

**Decisões travadas:**

- **`site_ids` guarda o id como o operador o selecionou** (cru da EP no laço/clique do
  cadastro, ou já fundido se veio de outro fluxo). A resolução para o id fundido 4G/5G
  (Fase 1) acontece só na leitura, em `Api._cluster_merged_site_ids(cluster, merged)` —
  usa `_find_merged_site`, então aceita as duas formas sem exigir migração.
- **Pertencimento é só `site_ids`; `polygon` nunca é reavaliado na exibição.** Fica
  salvo apenas para redesenhar o laço ao reabrir o cluster para edição. Mover um site
  para fora do polígono salvo não o remove — testado em
  `test_poligono_nao_altera_pertencimento_depois_de_salvo`.
- **`get_kpi_series(scope="cluster", scope_id=...)`** expande cluster → sites fundidos
  → `site_id` dos membros e reusa `_site_series_by_family` (que já combinava múltiplos
  `member_ids` da fusão) — combinar entre SITES do cluster é o mesmo mecanismo que já
  combinava entre MEMBROS 4G/5G de um site fundido. Lê só linhas `SITE` persistidas,
  nunca `CELL` cru. Sem seleção de célula neste escopo (sempre agregado).
  `get_sites` ganha `cluster_ids` por site (pré-computado uma vez por request, não por
  site, para não repetir a expansão fundida N vezes).
- **`server.py::parse_sites`** ganha a coluna opcional `cluster` (aceita `cluster`,
  `grupo`, `agrupamento`, `setor`, `area`/`área`), fora da lista `required`. Valores
  separados por `;` criam vários clusters para a mesma linha. A resposta ganha
  `clusters: [{id, name, site_ids}]`; o cadastro só usa isso para **semear** um evento
  novo (`editingEventId === null && eventClusters.length === 0`) — depois disso a
  interface manda, reimportar a planilha num evento existente não sobrescreve clusters
  já editados à mão.
- **Cadastro (`server_frontend/index.html`):** terceiro modo na barra de ferramentas do
  mapa (`tool-cluster-poly`), independente dos modos de polígono do evento
  (auto/desenho) — um clique nele não desliga a ferramenta de polígono do evento, só
  muda o painel visível. Três formas de montar um cluster, que se somam: laço
  (ray casting simples, ~15 linhas, `pointInPolygon`), clique no marcador
  (`toggleSiteInActiveCluster`, só ativo com `clusterMode` + cluster ativo) e busca por
  nome (autocomplete simples). O anel do marcador usa o mesmo padrão de
  `highlightDist`/`highlightRing` dos badges da Fase 2 — a maior extensão manda no
  tamanho do SVG.
- **App (`frontend/`):** `State.clusterFilter`; `#cluster-selector` no cabeçalho do
  `#site-panel`, oculto quando o evento não tem clusters. Com um cluster ativo, a lista
  de sites ganha uma linha sintética `cluster:<id>` no topo (o agregado, escopo do
  gráfico) e o mapa esconde os sites fora do cluster + desenha o polígono salvo dele.
  Selecionar a linha do cluster funciona como "Média" sempre (`selectedCell` fixo em
  `__media__`, sem seletor de célula) — o mesmo dataset por família (`series[]`) que já
  alimentava "Média" de um site fundido.

**Verificação end-to-end (não é teste automatizado, script descartável):** Playwright
dirigindo um `uvicorn server:app` real (dados isolados via `SMARTEVENTS_DATA_DIR`)
provou o fluxo completo do cadastro — criar cluster, alternar site por clique,
desenhar+aplicar laço, buscar por nome, salvar, recarregar a página, reabrir para
edição e ver os clusters (nomes e contagens) devolvidos exatamente como salvos.

Gate: `pytest tests/ -q --basetemp=.pytest-work/tmp` → **374 passed, 10 skipped, 0
failed** (357 do baseline + 5 `TestClusters` em `test_api.py` + 3 em
`test_server_parse_sites.py` + 7 em `test_server_frontend_clusters_ui.py` + 2 em
`test_frontend_cluster_filter_ui.py`).

---

## 2026-08-21 — Fase 5: visão geral com nove gráficos sincronizados

Plano: `docs/plans/2026-08-19-001-feat-site-merge-clusters-kpi-overview-plan.md`.

O botão **Ver KPIs** abre um modal full-screen com abas 4G/5G, seletor unificado de
site/cluster, janelas 15/30/60 min e Evento, e uma grade responsiva 3×3.

**Decisões travadas:**

- **Um endpoint, uma grade:** `Api.get_kpi_overview(event_id, scope, scope_id,
  technology_family, minutes)` reutiliza `get_kpi_series` para não duplicar a
  expansão de site fundido nem a agregação granular de cluster. Depois une todos
  os timestamps e projeta cada métrica nessa grade; ausência de ponto vira `None`
  no índice correto. Todas as listas de `metrics` têm sempre o comprimento de
  `labels`.
- **Composição explícita:** 4G tem os nove KPIs do bloco Monitoring, sem
  `ran_rtt`/`terrestrial_rtt`. 5G cobre treze métricas em nove painéis: cinco
  individuais e quatro pares (PRB, Throughput, Volume SA e Volume NSA).
- **Pares 5G:** DL é linha no eixo esquerdo; UL é barra no eixo direito. Todas as
  séries usam `spanGaps: false`.
- **Sincronização:** o plugin `overviewCrosshair` mantém um único índice ativo e
  desenha a linha vertical nos nove Chart.js. O tooltip externo HTML aparece só
  no painel sob o mouse; os outros oito recebem apenas o crosshair. Painel sem
  nenhum valor mantém o canvas sincronizado e sobrepõe “Sem dados no período”.
- **Escopos independem do filtro atual:** ao abrir, a tela busca os sites sem
  filtro de tecnologia e os clusters do evento. Assim, um filtro 4G aplicado no
  dashboard não esconde sites 5G do seletor da aba 5G.
- **Camada full-screen:** o modal usa `z-index: 11000`, acima do cabeçalho global
  (`10000`). O primeiro Playwright detectou que o cabeçalho interceptava os
  cliques da aba; o teste passou depois da correção.

Verificação visual: capturas Playwright em 1440×900 confirmaram as duas abas, os
nove painéis integralmente visíveis e os quatro pares 5G com escalas independentes.

Gate completo: `pytest tests/ -q --basetemp=.pytest-work/phase5-full` → **388 passed,
10 skipped, 0 failed**. Depois do ajuste exclusivamente visual de altura, os dois
testes Playwright da Fase 5 passaram novamente (**2 passed**).

---

## 2026-08-21 — Fase 1: período, unidade de throughput 5G e amostra dos alarmes

Plano: `docs/plans/2026-08-21-001-fix-kpi-monitoring-unidades-e-escala-plan.md`.
Backend puro; nenhuma tela mudou.

**Decisões travadas:**

- **O `period` da resposta do Monitoring NÃO é o Granularity Period.** O OSS devolve
  `period=5` para tasks de 1 minuto. O GP verdadeiro é configuração da task: o CSV traz
  `Period(minute)=1` e `N.Cell.Avail.Dur=60` no mesmo minuto. `_configured_pm_tasks`
  passou a carregar `period_seconds` por task (default `DEFAULT_PM_PERIOD_SECONDS = 60`)
  e `_parse_monitoring_response` usa esse valor. O `period` da resposta continua sendo
  lido apenas para um aviso de divergência, uma vez por task por sessão. Efeito: a
  availability 5G sai de 20% para 100%.
- **`N.ThpTime.*` está em microssegundo** — medido, não documentado. O OSS devolve o
  contador com `unit` vazio; os 95 valores do export são múltiplos de 500 (slot de
  0,5 ms @30 kHz) e qualquer outra hipótese viola o piso volume/período. A constante
  `THP_TIME_TO_SECONDS = 1e-6` em `core/kpi_formulas.py` é o **único** ponto a mudar se
  a documentação Huawei disser outra coisa. `throughput_ul` do 5G deixou de ser
  `production_ready=False`: DL e UL usam a mesma unidade, e agora as duas são `Mbit/s`.
- **Trava aritmética em vez de fé na unidade.** `check_throughput_floor` confere, a cada
  cálculo, se o throughput é ≥ `(volume − descontado)/período`. Violação **não** descarta
  a linha: vira diagnóstico `unit_suspect`. O piso usa o **mesmo numerador da fórmula**,
  não o volume bruto que o plano sugeria — medido nos 173 throughputs do export, o volume
  bruto acusa 1 falso positivo (célula que concentrou a transmissão no último slot) e o
  numerador líquido acusa zero, mantendo a detecção de erro de 1000×.
- **"Não aplicável" ≠ "inválido".** Nova exceção `NotApplicable(InvalidKpi)`, levantada
  por `_need` (contador fora da task) e `_ratio` (denominador zero). O parser conta
  `not_applicable` separado de `invalid`, e o cálculo de `partial` só olha `invalid` —
  um ciclo 4G saudável passa a fechar "Com dados" em vez de "Parcial" permanente.
  **Exceção deliberada:** contador que chegou defeituoso (não confiável ou não numérico)
  continua contando como `invalid`, porque a ausência tem causa conhecida.
- **Throughput de site é recalculado, não somado.** `site_aggregation` de
  `throughput_dl`/`throughput_ul` nas duas tecnologias passou de `sum` para
  `recalculate` — somar taxas dava 658 Mbit/s num site 4G. **Descontinuidade conhecida
  e aceita:** as linhas SITE gravadas antes desta data são somas; as novas são
  recálculos. Conforme o B1, o histórico não é migrado.
- **Alarme de acessibilidade exige amostra.** `sample_size(definition, counters)` expõe o
  menor denominador da fórmula (só para `accessibility` e `drop_rate`); o parser o anexa a
  cada medição em memória (`m["sample_size"]`, não persistido) e `_evaluate_kpi_alerts`
  ignora acessibilidade com amostra abaixo de `thresholds.alert_min_samples` (default 20).
  Os 454 CRITICAL do evento tinham a assinatura de denominadores 2, 3 e 5.
  **Efeito colateral aceito:** com GP de 1 minuto o 5G deixa de gerar alarme de
  acessibilidade (maior denominador observado por célula-minuto = 4). A agregação por
  janela (B6) devolve o alarme e está fora deste plano.
- **RTT 4G sai do catálogo.** `ran_rtt` e `terrestrial_rtt` receberam
  `monitoring_available=False`: o OSS aceita no máximo 25 contadores por task e os quatro
  contadores de RTT ficaram de fora. Reativar exige antes uma task PM dedicada.
- **`PRB.Avail` fora do padrão LTE é anotado, não corrigido.** Valor fora de
  `{6,15,25,50,75,100}` gera um WARNING por célula por sessão. As duas hipóteses (SFN com
  PRBs somados vs. denominador inflado) não são decidíveis com o que a task coleta, e
  dividir por um valor arbitrado criaria alarme falso — ver "Encaminhamentos" do plano.

**Ferramenta nova — `tools/kpi_crosscheck.py`.** Recalcula os KPIs a partir dos CSVs de
`csvs_reference/` e compara com o banco do evento, célula a célula e minuto a minuto. É a
rede de segurança das Fases 3 e 4, que mexem em unidade e não podem alterar valor.

    python tools/kpi_crosscheck.py --event testesantoamaro --csv-dir csvs_reference

Rodado contra o banco **anterior** à Fase 1, ele isola exatamente os quatro defeitos:
`availability` 5G com razão 0,200 em 95/95 amostras (período), `throughput_dl`/`_ul` 5G com
0,001 em 173/173 (unidade de tempo), `traffic_volume_dl_sa` com 94/95 em 1,000 e uma amostra
negativa (bug do export do OSS, agora fixada em 0). As outras 20 métricas dão 1,000. Como o
histórico não é migrado, as linhas antigas continuam erradas no banco; a razão 1,000 nessas
quatro só aparece em dado coletado a partir de agora.

Gate: `pytest tests/ -q --basetemp=.pytest-work/fase1` → **423 passed, 10 skipped, 0 failed**
(baseline anterior: 388 passed / 10 skipped).

---

## 2026-08-21 — Visão geral de KPIs: comparação multi-escopo e traço DL/UL

**Escopo virou comparação, não seleção única.** O `<select>` de escopo saiu; entraram dois
seletores múltiplos independentes na toolbar — **Clusters** (com "Todos os clusters") e
**Sites** (com busca). A união dos dois é a lista comparada. Teto de **8 escopos**
(`KPI_OVERVIEW_MAX_SCOPES` em `api/api.py` e `MAX_SCOPES` em `kpi_overview.js`), que é o
tamanho da paleta categórica: acima disso as linhas deixam de ser distinguíveis, então o
backend recusa em vez de truncar em silêncio.

**API nova: `Api.get_kpi_overview_multi(event_id, scopes, family, minutes)`.** Recebe
`[{"scope": "cluster"|"site", "scope_id": ...}]` e devolve `series[]`, cada uma com o bloco
`metrics` completo. Ela **chama `get_kpi_overview` uma vez por escopo** — a expansão de site
fundido e a agregação de cluster continuam num lugar só — e **une os timestamps depois** de
todos voltarem, para que um escopo sem ponto num minuto vire `None` naquela posição em vez
de deslocar a série do vizinho. `get_kpi_overview` continua existindo e não mudou.

**Painéis pareados: DL linha contínua, UL linha tracejada, mesmo eixo.** As barras de UL e o
eixo `yRight` foram removidos. Os dois lados de um painel pareado (PRB, Throughput, Traffic
Volume SA/NSA) **têm a mesma unidade**, então eixo duplo faria escalas diferentes parecerem
a mesma curva — e com N clusters na tela viraria ilegível. A convenção de traço fica escrita
no cabeçalho de cada card pareado (`— DL  ┄ UL`).

**Cor segue a entidade, nunca a ordem.** Cluster usa a cor cadastrada dele (a mesma do
polígono no mapa). Site usa o índice estável dele na lista do evento sobre
`SERIES_COLORS`. Só há uma exceção: se a cor-base de um site já foi tomada por um cluster
selecionado, o **site** cede e pega a próxima livre — duas séries da mesma cor no mesmo
gráfico seriam ilegíveis, e é o site que não tem cor própria a defender.

**`SERIES_COLORS` é a paleta do app reordenada, e a ordem é mecanismo de segurança.**
`#388BFD, #F85149, #ab7df6, #3FB950, #00d2ff, #D29922, #f692cc, #FF7B00`. Na ordem original
(a do `CLUSTER_COLORS` do servidor) o par adjacente `#D29922 ↔ #3FB950` dá ΔE CVD 5,1 —
indistinguível para protanopia. Reordenado, o pior par adjacente vai a ΔE 17,4 (deutan),
com visão normal 19,2 e contraste ≥ 3:1 sobre `#161B22`. **Não reordenar sem revalidar**
(`test_paleta_de_series_nao_e_reordenada_sem_revalidar` trava os valores). Os hexes em si
continuam fora da banda de luminosidade recomendada — são tokens do app, compartilhados com
marcadores do mapa e cores de status, e re-escalá-los está fora do escopo desta mudança.

**Uma legenda só, no cabeçalho.** As nove legendas por card foram substituídas por chips
clicáveis em `#kpi-overview-context`: clicar oculta o escopo **nos nove painéis de uma vez**,
e o último escopo visível não pode ser desligado. Dentro do card ficaram apenas o crosshair
sincronizado e o tooltip (2 colunas acima de 4 séries, preso dentro do card).

---

## 2026-08-24 — Fase 2: período da task e threshold com unidade no cadastro

Plano: `docs/plans/2026-08-21-001-fix-kpi-monitoring-unidades-e-escala-plan.md`.
Ao chegar nesta fase, `core/collector.py` já lia `period_seconds` por task (item A1 do
plano) como efeito colateral da Fase 1 — só faltava o campo no formulário. O trabalho real
desta fase foi a UI de período (A1) e o threshold com unidade (B2).

**A1 — UI do período.** `addPmTaskRow` ganhou `.pm-task-period-value` (inteiro) e
`.pm-task-period-unit` (`s`/`min`/`h`), gravados sempre em segundos
(`periodPartsToSeconds`). `configuredPmTasks` decompõe `period_seconds` de volta para
value+unit na leitura (`secondsToPeriodParts`), com fallback para
`DEFAULT_PM_PERIOD_SECONDS = 60` — igual ao já feito em `core/collector.py`. O rótulo da
lista de eventos (`4G ×3 · NR Cell ×1 (15 min)`) só aparece quando o período é **uniforme
dentro da tecnologia** e diferente do default; um grupo com períodos mistos fica sem
sufixo, em vez de escolher um arbitrariamente.

**B2 — threshold com unidade, e o raio de explosão que o plano não listou.** O plano
descrevia a mudança como local a `Api._metric_thresholds` mais três consumidores nomeados
(`get_kpi_series`, `get_kpi_overview`, `_evaluate_kpi_alerts`). Na prática, gravar cada
threshold como `{"value", "unit"}` em vez de número cru quebra **todo** lugar que lê
`config["thresholds"][chave]` direto, porque `numero >= objeto` e `Number(objeto)` falham
em silêncio:
- `core/scheduler.py` — `_evaluate_kpi_alerts` (utilização/disponibilidade) e
  `_evaluate_vip_alerts` (RSRP) leem a config direto, não passam por `_metric_thresholds`.
  Alarme em tempo real teria parado de disparar sem erro nenhum.
- `api/api.py` — `get_sites` (status de utilização) e `get_vips` (`signal_status`,
  `rsrp_min`) também leem a config direto.
- `frontend/js/vip.js` (linha do RSRP no modal), `frontend/js/kpi.js` e
  `frontend/js/kpi_overview.js` (linhas tracejadas de threshold nos gráficos) liam
  `data.thresholds.warning`/`.critical` como número.

**Decisão:** dois helpers em `core/kpi_formulas.py` — `threshold_value(raw, default)`
(extrai o número, aceita os dois formatos) e `threshold_object(raw, unit_default)`
(normaliza para `{"value","unit"}`, usado só em `_metric_thresholds`) — e todo ponto de
leitura acima foi passado a usar um dos dois. `_metric_thresholds` é o único lugar que
devolve o objeto; os demais continuam vendo um número, como sempre viram. Mocks de
`frontend/js/bridge.js` foram atualizados para o novo formato, para o modo `--mock` não
divergir da forma real da API. `core/models.py::Thresholds` (dataclass com campos `float`)
não foi tocado — não é instanciado em nenhum caminho de execução real, só em
`tests/test_models.py`; é scaffolding morta, fora do contrato de persistência.

**Por que não é regressão silenciosa:** `TestKpiAlertThresholdObjectFormat` em
`test_scheduler.py` prova que o alarme de utilização e o de RSRP do VIP disparam
igual com threshold no formato novo. `test_submit_payload_carries_period_seconds_and_threshold_units`
roda num Chromium real (Playwright) contra `server_frontend/index.html` servido por HTTP,
preenche o formulário completo e confere o payload de `POST /api/events` — `period_seconds`
e `thresholds` incluídos.

Gate: `pytest tests/ -q --basetemp=.pytest-work/fase2` → **476 passed, 10 skipped, 0 failed**
(baseline Fase 1: 423 passed / 10 skipped).

---

## 2026-08-24 — Fusão 4G/5G falhava em Salvador: prefixo de tecnologia no nome do site

Pedido do usuário: 4G e 5G do evento `roadshow-salvador` apareciam como sites duplicados no
mapa/KPIs — perguntou se dava para reconciliar por `end_id`/lat-long.

**Causa raiz:** a fusão de sites gêmeos (Fase 1, 2026-08-19) exige nome normalizado
**igual** + distância ≤ 50 m. Em `testesantoamaro`/`santo-amaro-5g` o nome já é idêntico
entre as duas tecnologias (`SPSMG7`), então funde. A planilha de Salvador nomeia os gêmeos
com prefixo de tecnologia embutido — `SR-SACAL5` (4G) vs `5G-SACAL5` (5G) — só o token muda,
coordenada é idêntica (medido: 291/291 pares nome-sem-prefixo batem em distância < 1 m,
zero falso positivo). Sem stripping, os dois nomes nunca coincidiam e o par nunca fundia.
**Não existe campo `end_id`/eNodeB-code separado no modelo de site do app** — o `enodebid`
importado (`server.py::parse_sites`) é o id técnico do OSS, que já é diferente por
tecnologia (confirmado: 462982 × 1511558 para o mesmo site físico). O papel que o usuário
descreveu como "End_id" é, na prática, o código do site embutido no próprio nome, sem o
prefixo de tecnologia — não uma coluna nova a importar.

**Correção:** `Api._normalize_site_name` (`api/api.py`) passou a remover um prefixo de
tecnologia conhecido (`4G-`, `5G-`, `5D-`, `SD-`, `SR-`) do início do nome antes de usá-lo
como chave de fusão. A trava de distância ≤ 50 m continua intacta — nomes que colidem só
depois do strip mas ficam longe continuam recusados com o mesmo warning de homônimo. Nomes
sem esse prefixo (`SPSMG7`, `18NLCTAL01`) saem inalterados: zero mudança de comportamento
para os eventos já fundindo corretamente.

**Validado contra o banco real** (`data/smart_events.db`, evento `roadshow-salvador`, 624
sites brutos): antes da correção, 0 pares fundiam; depois, 291 pares 4G+5G fundidos, 23
sites só-4G e 19 só-5G (sem gêmeo — `5D-` novos, corretamente não fundidos).

Regressão: `test_sites_com_prefixo_de_tecnologia_diferente_sao_fundidos` em
`tests/test_api.py::TestSiteMerge`.

Gate: `pytest tests/ -q --basetemp=.pytest-work/salvador-merge-full` → **476 passed, 10
skipped, 0 failed** (1 falha de Playwright na primeira rodada,
`test_grade_5g_tem_nove_paineis...`, não reproduziu isolada — flake de timing, mock de
frontend, sem relação com `_normalize_site_name`).

**Pendência não implementada:** se um evento futuro tiver o nome do site colidindo só
por coincidência textual após o strip (ex. dois sites reais chamados `X` e `5G-X` sem
relação), a trava de distância ≤ 50 m ainda protege, mas o prefixo removido é uma lista
fechada (`4G|5G|5D|SD|SR`) — uma nova convenção de EP com outro token não é coberta sem
editar `_SITE_NAME_PREFIX_RE`.

---

## 2026-08-24 — Instalador dedicado do RoadShow Salvador (perfil `roadshow-salvador`)

Terceiro perfil de build, ao lado de `roadshow-tim` e `vivo-barretos-2026`. Nada de código
mudou: o pipeline de build já é parametrizado por perfil desde Barretos. O trabalho foi
**declarar** o perfil e escrever o README do operador.

Arquivos novos:
- `build_profiles/roadshow-salvador.json`
- `installer/README-operador-roadshow-salvador.txt`

**Dados que vieram do próprio evento** (`server_data/events/roadshow-salvador.json`), não
inventados — é a fonte de verdade para tudo que o perfil declara:
- `client: "TIM"` ← `oss.cliente`. **Obrigatório bater**, senão `prepare_installer_seed`
  simplesmente não copia o evento e a semente sai vazia (o filtro exige `event_id` **e**
  cliente casando, `tools/prepare_installer_seed.py:103-111`).
- `required_pm_tasks` 4G=2279, NRCELL=2280, NRDUCELL=2281 ← `integration.pm_tasks`.
  `core/seed.py:144` reprova o build se o evento não tiver exatamente essas tasks.
- Regional OSS `OUTRAS` → `https://10.220.30.9:31943/` (de `server_data/clientes/tim.json`).
  Salvador é BA, então **não** é a URL de SP — repetir a de Barretos aqui seria erro silencioso.

**`require_cell_radio_metadata: false`, ao contrário de Barretos.** Medido antes de decidir:
4 células de 624 sites estão sem `tech`/`frequency` (`4G-SAFEM7-18-IDA/IDB/IDC`,
`4G-SABAM6-18-IA`). Com a trava ligada o build reprovaria. Barretos pode exigir `true` porque
a planilha dele veio completa; Salvador não. Se essas 4 células forem corrigidas no EP, dá
para ligar a trava.

**`app_id` é GUID novo** (`97CE18D7-11E6-4223-90AC-5B18989B0C53`), distinto dos outros dois.
É o que faz o Inno Setup tratar os três como aplicações independentes — reaproveitar um GUID
faria um instalador desinstalar o outro. Mesma lógica para `runtime_data_dir`
(`SmartEvents-RoadShow-Salvador`), que isola `%LOCALAPPDATA%`.

Gate: `python build.py --profile roadshow-salvador` → **Build aprovado**. Self-test 9/9 na
máquina de build; `_verify_bundle` conferiu perfil, catálogo só-TIM e eventos, e confirmou que
nenhum `credentials.json` foi embutido; `tests/test_installer.py` 7 passed.
Setup 579,3 MB, sha256 `5c88c61e…`, em `dist/roadshow-salvador/installer/`.

**Pendência conhecida (afeta os dois perfis TIM):** a semente puxa VIPs por cliente, sem
recorte por evento (`tools/prepare_installer_seed.py:130-136`), então os 3 VIPs de teste da
TIM (`20260813-teste`, `baldin-edge60pro`, `douglas`) entram no instalador de Salvador. É o
comportamento pré-existente do `roadshow-tim`, não uma regressão — mas não foi uma decisão
deliberada para Salvador.

---

## 2026-08-24 — Fase 3: unidade canônica aplicada na leitura, sem migrar histórico

Terceira fase do plano `docs/plans/2026-08-21-001-fix-kpi-monitoring-unidades-e-escala-plan.md`
(achados B1 e B8). Fecha o problema de a **mesma grandeza** estar gravada em bases diferentes
conforme a origem: `traffic_volume_dl` do 4G em **bit**, os `traffic_volume_*` do 5G em
**kbit**, o throughput das duas em **Mbit/s**, e o banco legado `vips-rio-tim-jun-2026.db` em
**MB**. Nada na linha denunciava isso — a unidade era implícita no par `(metric, technology)`.

**Decisão travada: o valor gravado não muda, nunca.** O banco continua guardando exatamente o
que o OSS entrega, e a base comum é aplicada na leitura. Isso preserva a auditoria direta
contra os CSVs do OSS que o `tools/kpi_crosscheck.py` faz linha a linha — se o gravado fosse
convertido, o crosscheck perderia a única fonte independente que existe sem VPN. O histórico
**não é migrado** (B1).

**Onde a conversão mora, e por quê:** em `core/database.py`, no helper `_canonical`, aplicado
nas seis funções de leitura de KPI. Não em `api/api.py`: `Api.get_kpi_series` tem vários
pontos de retorno (cluster, site fundido, família única) e converter em cada um é o tipo de
duplicação que produz divergência entre célula, site e cluster. A camada de banco é o funil
real, a conversão é linear, e toda agregação a jusante continua correta. Verificado: nenhum
caminho lê do banco e regrava, então não há risco de conversão dupla ao persistir.

**A base é derivada da unidade do OSS, não repetida em 26 linhas.** `_UNIT_TO_BASE` mapeia
`unit -> (base_unit, to_base)` e `KpiDefinition.__post_init__` preenche os dois campos novos.
Uma definição com unidade sem base declarada **quebra o import**, não a leitura: é preferível
o app não subir a inventar unidade canônica por suposição — foi exatamente esse silêncio que
produziu o B1. Fatores: `%`, `dBm`, `ms` e `usuários` ficam em 1,0; `bit`→`bit` ×1; `kbit`→
`bit` ×1e3; `Mbit/s`→`bit/s` ×1e6.

**Linha sem `technology` volta intacta, por decisão explícita.** `to_canonical` exige o par
`(metric, technology)`; sem tecnologia não há como saber a base gravada, e aplicar o fator do
4G por engano seria pior que não converter. Cuidado não óbvio: `definition(metric, None)`
casa com a **primeira** tecnologia do catálogo, então o guard `if not technology` é
obrigatório e não é redundante. Bancos anteriores à coluna ficam fora do contrato.

**Contrato da API mudou:** `catalog_for_api()` passa a anunciar `unit` = unidade **canônica** e
mantém a crua como `oss_unit`. `get_kpi_catalog` e `get_kpi_overview` herdam de graça — nenhum
dos dois precisou de edição, porque os dois já liam do catálogo.

**B8 — o rótulo "legado".** `traffic_volume_dl`/`_ul` do 4G perderam o "(legado)" (descrevia a
origem do código, não o dado) e a unidade "contador OSS" (que não é unidade) virou `bit`.
`monitoring_available` continua `True` de propósito: removê-las do seletor mataria a coluna
**"Participação"** da lista de sites, que só aparece com uma delas selecionada
(`frontend/js/kpi.js:355-357`).

**Efeito visível, esperado e transitório:** os números pioraram de ler. Medido no evento real
`testesantoamaro`: `traffic_volume_dl_nsa` foi de 8,29e5 para 8,29e8, `throughput_dl` de 431
para 4,31e8. Quem formata isso é a Fase 4 — **não mostrar a Fase 3 sozinha ao usuário final.**

**Ganho já disponível para a Fase 4:** os painéis pareados passaram a ter unidade idêntica nas
duas séries (`bit/s` nos dois throughputs, `bit` nos quatro volumes, `%` nas duas utilizações).
É a pré-condição do B3 (eixo único) e do fim do rótulo `"DL / UL"` no cabeçalho.

**Gate:** `pytest tests/ -q` → **498 passed, 10 skipped** (baseline era 477/10; os 10 skips são
os de VPN, iguais). `kpi_crosscheck --raw` devolveu saída **idêntica** à do HEAD anterior
(20/24), confirmando que nada no banco mudou — comparado rodando o script do HEAD num
worktree temporário contra o mesmo banco.

**Ressalva sobre o 20/24, que não é regressão da Fase 3:** o banco de `testesantoamaro` tem
linhas **anteriores e posteriores** à Fase 1 misturadas (a `availability` 5G aparece com 20,0 e
com 100,0), e os CSVs de `csvs_reference/` são de 2026-08-21, dentro da janela pré-Fase 1.
As quatro métricas que divergem (`availability` 0,2; `throughput_dl`/`_ul` 0,001;
`traffic_volume_dl_sa` inf, de 2 linhas negativas) são exatamente os bugs que a Fase 1
corrigiu, congelados nas linhas velhas. O 22/22 do plano exige recoletar aquela janela — não é
alcançável sem VPN e não é trabalho da Fase 3.

---

## 2026-08-24 — Fase 4: escala automática, eixo único e "sem tráfego" ≠ "sem dados"

Quarta e última fase do plano
`docs/plans/2026-08-21-001-fix-kpi-monitoring-unidades-e-escala-plan.md` (achados B3, B4, B5 e
B6). A Fase 3 deixou todo valor na unidade-base e, com isso, números de 7 a 9 dígitos na tela;
esta fase resolve a leitura sem tocar em nenhum valor — `kpi_crosscheck` continua **20/24**,
byte a byte igual ao da Fase 3.

**`frontend/js/units.js` é o único formatador do app.** Eram quatro implementações
divergentes (`toFixed(2)` em `kpi.js` e `vip.js`, `toLocaleString` em `kpi_overview.js`).
Agora as três telas importam `formatar`. Duas mudanças visíveis caem junto e são
intencionais: o número passa a ser **pt-BR** em toda a lista de sites e nos tooltips do
dashboard (`1.234,50`, não `1234.50`), e o sufixo de percentual perdeu o espaço (`91,00%`),
que só existia no caminho do catálogo e não no fallback.

**A escala é decimal e sobe a cada 1000** (`bit → kbit → Mbit → Gbit → Tbit`). `kbit` é 10³
por definição em telecom — o `KiB`/`MiB` de sistema de arquivos daria 2,4% de erro por degrau.
Só `bit` e `bit/s` escalam; `%`, `dBm`, `ms` e `usuários` passam intactos. A lista de
escaláveis é **derivada** do mapa de degraus, para as duas não divergirem.

**O degrau é memorizado por métrica, e a memória mora no módulo — não no `State`.** O plano
dizia `State`; na implementação isso não agrega nada (a memória não tem assinante, não é dado
de evento e não participa do pub/sub) e criaria acoplamento de `units.js` com o store. Um
`Map` no módulo tem o mesmo tempo de vida e uma superfície menor. `esquecerEscalas()` é
chamado na troca de evento pelos dois consumidores.

**Consequência não óbvia dessa decisão:** o import de `units.js` **não pode** levar o `?v=`
de cache-busting que o app usa nos demais módulos. Especificadores diferentes criariam
instâncias diferentes do módulo e cada tela teria a sua memória — o degrau deixaria de ser
comum, em silêncio. Travado por `test_importadores_usam_o_mesmo_especificador_de_units`.

**Escala fixa por métrica, com histerese (sobe em 1000, desce só abaixo de 900).** Medido no
evento real: no mesmo instante, um site tinha 3,3e7 bit e outro 2,5e9 bit — com escala por
painel sairiam "33,5 M" e "2,49 G", e 33,5 *parece* maior que 2,49. E na janela de 15 min o
degrau de um deles trocaria sozinho 17 vezes em 427 pontos.

**Os dados do gráfico continuam na unidade-base; quem divide é a formatação.** O eixo do
Chart.js opera no espaço canônico e só o `callback` do tick e o tooltip aplicam o divisor.
Isso evita conversão dupla e — mais importante — **o threshold não precisou ser tocado**:
`_thresholdAnnotation` desenha em `yLeft` no mesmo espaço dos dados, então a linha tracejada
continua exata.

**B3 (eixo único) já estava no código** desde a visão geral multi-escopo; a Fase 4 apenas o
travou com teste nos quatro painéis pareados do 5G, junto com o cabeçalho que deixou de cair
em `"DL / UL"`.

**B6 — `reason` por métrica vem da grade, não do `not_applicable`.** O plano supunha propagar
o contador do ciclo de coleta; ele nunca foi persistido por métrica (é contado em memória
pelo collector e só vai para o log). A informação equivalente está no banco: a janela só tem
`labels` se alguma métrica produziu valor, então `labels` vazio é **"no_data"**, e métrica sem
ponto numa janela com coleta é **"no_traffic"**. No multi-escopo o melhor motivo vence — um
escopo com tráfego basta para o painel não estar vazio.

**Mock com ordem de grandeza canônica.** `bridge.js` passou a multiplicar throughput e volume
por 1e6/1e8 e o catálogo mock anuncia `bit/s`/`bit`. Sem isso o Playwright validaria uma
escala que não existe em produção — nenhuma célula real faz 91 bit/s.

**Fora do escopo, com motivo:** `templates/report/report.js` **não** foi migrado para
`units.js`. Verificado: o relatório é alimentado por `sample_data.js`, um dataset estático de
strings **já formatadas** (`"2.05k"`, `"310"`); não existe caminho de valor de KPI vivo até
ele. Migrá-lo seria escrever código para um consumidor que não existe. Quando o relatório for
ligado ao dado real, é `units.js` que ele deve usar.

**Gate:** `pytest tests/ -q` → **518 passed, 10 skipped** (baseline da Fase 3 era 498/10; os
10 skips são os de VPN, iguais). `kpi_crosscheck --event testesantoamaro` → **20/24**,
idêntico ao da Fase 3 e pelas mesmas quatro divergências herdadas de linhas anteriores à
Fase 1.

---

## 2026-08-25 — Visão geral de KPIs: célula como terceiro escopo

**Célula virou escopo de primeira classe, ao lado de site e cluster.** Terceiro seletor
múltiplo (`#kpi-overview-cell-picker`, "Células") na toolbar de `kpi_overview.js`, mesmo
molde do de sites (busca + checkboxes, sem "selecionar todas" — a lista pode ser grande).
Célula já existia como conceito no modelo de dados (`Cell.cell_id`, seleção parcial de
cluster) e como sub-filtro dentro de um site (`#cell-selector` do dashboard principal); o que
faltava era torná-la comparável do mesmo jeito que site/cluster já eram.

**API nova: `Api.get_event_cells(event_id, technology_family)`.** Lista todas as células do
evento (todos os sites fundidos) com o site dono — o análogo de `get_sites`/`get_clusters`
para esse terceiro seletor. `get_kpi_series`/`get_kpi_overview`/`get_kpi_overview_multi`
ganharam `scope="cell"`: acha o site dono da célula varrendo o evento inteiro
(`_find_site_for_cell`) e cai no caminho de célula única que já existia — nenhuma lógica de
agregação foi duplicada.

**Célula é específica de família; site e cluster não são.** Trocar a aba 4G/5G precisa
rebuscar a lista de células e purgar da seleção as que não existem na família nova — mas
**sem** reusar `_refreshScopeData()` inteiro para isso: essa função também reaplica a
semente de seleção (herdada do dashboard) sempre que a comparação está vazia, e chamá-la a
cada troca de aba reintroduzia um escopo que o usuário tinha acabado de limpar (bug pego só
ao rodar a suite Playwright de `test_frontend_kpi_overview_ui.py` — os 4 painéis pareados
timeoutavam esperando 4 datasets e vinham 6, porque um site 4G-only da barra lateral do
dashboard era resemeado no meio da comparação em 5G). Extraído `_refreshCellsForFamily()`,
que só busca `get_event_cells` e purga seleção — sem tocar em site/cluster nem reaplicar a
semente.

**Gate:** `pytest tests/ -q` → **499 passed, 10 skipped** no arquivo cheio (mais os 11 de
`test_frontend_kpi_overview_ui.py`, rodados à parte com Playwright) + 5 testes novos de
backend (`TestEventCells`, `TestCellScope`) — sem regressão na baseline.

---

## 2026-08-25 — Seletor de células recorta pelo que já está selecionado

**A lista de células oferecida não é mais o evento inteiro.** Com dezenas de células por
site, listar todas misturava o que importa com o resto. `_cellScopeSiteIds()`
(`kpi_overview.js`) calcula os sites que baliza a lista: com cluster e/ou site já
selecionados na comparação, é a união deles (site direto + todo site cujo `cluster_ids`
toca algum cluster escolhido — mesma granularidade que o filtro de cluster do dashboard já
usa, não desce a seleção parcial de célula do cluster). **Sem nada selecionado**, cai para as
células de qualquer cluster do evento — não para o evento inteiro; é "o que já está
organizado", pedido explícito do usuário. Sem clusters cadastrados nem site selecionado, o
dropdown mostra "Nenhum cluster cadastrado nem site selecionado." em vez de aparecer vazio
sem explicação.

**Célula já escolhida nunca some da lista, mesmo fora do recorte atual.** Trocar o site
selecionado (ou o cluster) muda o recorte de células oferecidas; se isso escondesse uma
célula que já estava marcada, o usuário perderia o único jeito de desmarcá-la pelo dropdown.
`_renderCellOptions` sempre inclui `_selection.cell` no pool, independente do recorte
calculado.

**Não usa dado de célula-parcial do cluster (`cluster_ids` no nível de site, não de
célula).** Um cluster com seleção parcial (só algumas células de um site) ainda libera
*todas* as células daquele site no seletor, não só as escolhidas no cluster. Fiel ao que o
filtro de cluster do dashboard (Fase 4) já faz — não existe hoje um endpoint que exponha
célula→cluster no nível de célula, e criar um só para isso seria escopo maior do que o
pedido.

**Gate:** `pytest tests/test_frontend_kpi_overview_ui.py -q` → **15 passed** (11 + 4 novos:
sem seleção cai para células dos clusters; recorta por cluster selecionado; recorta por site
selecionado; célula já escolhida sobrevive à troca de site).

---

## 2026-08-26 — Clusters por portadora (DLEARFCN), nomes nos alertas, filtro pelo site selecionado

Três pedidos do operador, implementados juntos.

### 1. Clusters automáticos por EARFCN na visão geral 4G

A EP (`ep_default.xlsx`) ganhou a coluna `DLEARFCN`. `server.py::parse_sites` aceita
`dlearfcn`/`earfcn`/`dl_earfcn` (fora do `required`) e grava `cell.earfcn` como inteiro
em texto. Sem a coluna, a importação continua igual.

`Api._earfcn_clusters` gera um cluster sintético por valor de DLEARFCN, só com células
4G (5G fica de fora mesmo que o campo venha preenchido — NRARFCN não é DLEARFCN). O
recorte é granular (`members[].cell_ids`), então o agregado de KPI combina só as células
daquela portadora, não o site inteiro. Id estável: `earfcn-<valor>`; nome: `Portadora
<valor>`; `source: "earfcn"`.

Eles **não** entram no cadastro — são gerados na leitura (`_clusters_of`), para não
misturar com os clusters geográficos que o operador monta à mão. Um cluster salvo com o
mesmo id ganha da geração (o operador pode ter editado nome/cor). Sem `earfcn` nas
células, nada é inventado. Eventos já importados só passam a ter portadoras depois de
reimportar a EP.

A visão geral 4G seleciona esses clusters por padrão ao abrir (até 8, o teto da paleta),
mesmo com um site marcado na lista — o dashboard sempre deixa um site selecionado, e isso
não pode esconder o recorte por portadora. Cluster explícito no dashboard
(`cluster:<id>` ou `clusters:compare`) continua herdado. Aba 5G não muda.

### 2. Alertas mostram nome do site/célula, não o enodebID

O banco continua gravando o id técnico. `Api._enrich_alerts` (em `get_alerts` e no
download do log) resolve para o site fundido e preenche `display_name` / `serving_site` /
`site_name`. Utilização → nome do site; acessibilidade ("na célula") e RSRP de VIP →
nome da célula. eNodeB numérico na mensagem (`em 725483`) é trocado pelo nome na
resposta, sem regravar o banco.

### 3. Filtro "Somente site/cluster selecionado"

Mesmo padrão do checkbox "Somente sites do evento" dos alarmes: um novo em Alarmes e a
cópia em Alertas. Desmarcado por padrão. Quando marcado, a lista recorta pelo
`State.selectedSite` da lista inferior esquerda (site fundido + membros 4G/5G, ou os
sites do cluster / comparativo). Sem seleção, a lista pede para escolher um site. O
badge do header não muda (alertas não lidos / críticos no evento).

**Gate:** `pytest tests/ -q --ignore=tests/test_http_vpn.py` → **546 passed**.

## 2026-08-27 — Open API do MAE (V100R026C10): viabilidade como substituta da coleta

Análise do *iMaster MAE-Access Open API Developer Guide*, Issue 01 (2026-04-30, 276 páginas),
comparado contra `core/collector.py`, `core/session_renew.py` e `core/scheduler.py`.
Relatório completo: https://claude.ai/code/artifact/6faa8daa-ca43-4751-9a83-ff245a8dc111

**Fatos permanentes (não repetir a pesquisa):**

1. **A Open API vive na porta 31127**, no APIGWService — não na 31943 que o projeto usa hoje
   (`data/clientes.json`: 10.220.50.9, 10.220.30.9, 187.100.113.3). Porta diferente, liberação
   de VPN diferente. Nada mais importa antes de provar que ela responde.
2. **Autenticação é token puro, sem navegador.** `PUT /api/rest/securityManagement/v1/oauth/token`
   com `{grantType,userName,value}` → `{accessSession, roaRand, expires:1800}`. Depois,
   `X-Auth-Token: <accessSession>` em todo request; `POST /oauth/handshake` renova;
   `DELETE /oauth/token` encerra. Sessão de 30 min, renovada a cada uso.
   Isso tornaria `session_renew.py` (Playwright + Chromium + CAPTCHA/SSO + `bspsession`/`roarand`)
   desnecessário para os módulos migrados.
   ⚠ 5 senhas erradas **bloqueiam a conta** no OSS, com desbloqueio manual — proibido retry cego.
3. **A API de PM não é o Monitoring.** Ela lê o banco de resultados de PM, não o Performance
   Monitor em tempo real que o projeto usa (GP 1 min, cursor `preExecTime`, ciclo 120 s).
   O guia exige que `period` da consulta seja igual ao período da subscrição; os períodos que
   aparecem no documento são 15 e 5 minutos, `period: 10` é rejeitado como inválido, e
   **1 minuto não aparece em lugar nenhum**. Migrar KPI = painel de 5–15 min, não de 2 min.
   → É decisão de produto, não técnica. Não migrar KPI sem essa resposta.
4. **Não existe API de trace / measurement report / CHR / VIP** em nenhuma das 276 páginas.
   Enquanto o VIP existir no produto, o scraping do FARS e o Playwright continuam no código.
   Pedido pendente ao chefe: verificar se há documento de NBI separado para trace/MR.
5. **Filtro de alarmes não tem campo de NE.** Campos filtráveis: alarmId, alarmRaisedTime,
   alarmClearedTime, ackTime, csn, productName, perceivedSeverity, alarmType. Fonte do alarme só
   via `baseObjectInstance`, que aceita **um** NE. Estratégia viável: filtrar por
   `alarmRaisedTime > cursor` + severidade e recortar por site localmente.
6. **Ganhos estruturais se migrar:** `objectName` do PM volta estruturado
   (`Cell Name`, `Local Cell ID`, `eNodeB ID`) — a camada `_obj_to_cell` / `_resolve_monitoring_cell`
   / `_log_unmapped` deixa de ser necessária. `topocellsinfo` dá estado real de célula
   (bloqueada/desativada) e DN canônico, resolvendo na origem a fusão 4G/5G por prefixo de nome.
   `POST /performanceManagement/v1/measurement` (Subscribe/Cancel) permite o app provisionar as
   próprias tasks de PM, eliminando a dependência de tasks criadas à mão na GUI.

**Decisão recomendada: híbrido, não substituição.** Fase 0 provar a porta 31127 → Fase 1 alarmes →
Fase 2 topologia/NE list → Fase 3 KPI só após decidir granularidade. VIP fica como está.

**Pendências que dependem do cliente:** usuário third-party por regional (papel "NBI OpenAPI User
Group", criado por SMManagers), liberação da 31127, licença de NBI (retCode 90030 = sem licença),
certificado do APIGWService para TLS, e versão do MAE por regional (este guia é V100R026C10).

### 2026-08-27 — Fase 0 EXECUTADA: a Open API está no ar em TIM OUTRAS

Sondagem dos três OSS (`data/clientes.json`), comparando 31943 (GUI, usada hoje) e 31127 (APIGW):

| Regional | Host | 31943 | 31127 | Leitura |
|---|---|---|---|---|
| TIM SP | 10.220.50.9 | aberta | **timeout** | Rota até o host OK; porta da API descartada → firewall/ACL |
| TIM OUTRAS | 10.220.30.9 | aberta | **aberta** | Gateway no ar |
| Vivo SP | 187.100.113.3 | timeout | timeout | GUI também não responde → VPN da Vivo desconectada; **inconclusivo** |

`curl: (28)` é *timeout*, não `Connection refused` (erro 7). Timeout = pacote descartado no
caminho (firewall). Recusa = host alcançável e serviço parado. A distinção decide a quem pedir o quê.

**Confirmado em 10.220.30.9:31127, sem enviar credencial nenhuma:**
- TLS 1.2 / ECDHE-RSA-AES128-GCM-SHA256, como manda a seção 2.4 do guia.
- `GET /oauth/token` → 405 `99050 Method not allowed`
- `GET /faultSupervisonManagement/v1/alarms` → 401 `99010 Invalid credentials`
- `GET /api/rest/caminho/inexistente` → 404 `99040 Url not found: can not find api`

**Técnica de auditoria sem login (reutilizável):** 404/`99040` = rota NÃO publicada;
401 ou 405 = rota publicada. Aplicada às 20 rotas do guia em TIM OUTRAS:
**20 de 20 publicadas** — PM v1 e v2, FM + máscaras, topologia (topocellsinfo, neList,
lite/fullQuery), inventário, MML (comando e tasks), subscrição de medição, backup de NE,
iSStar e contas. **Nenhum `90030`** (licença ausente).

**Conclusão:** não há bloqueio de rede, deployment ou licença em TIM OUTRAS. O único
bloqueio é **o usuário third-party**, que só um SMManagers cria. O piloto (Fase 1 — alarmes)
pode começar por OUTRAS sem esperar a liberação de firewall de SP.

**Scripts da sondagem** (reutilizáveis, sem credencial):
`scratchpad/probe.py` (matriz host×porta) e `scratchpad/published.py` (mapa de rotas publicadas).
Rodam com o Python da `.venv` do projeto — o Python global não tem `requests`.

⚠ Não usar usuário real com senha errada para testar: 5 tentativas bloqueiam a conta no OSS.
As sondagens acima nunca enviam credencial, justamente por isso.

## 2026-08-28 — Portadora por site (Fase 0 + Fase 1 do backend)

Handoff completo em `plano-portadora-por-site.md`. Duas entregas em cima da
seção "Clusters por portadora (DLEARFCN)" (2026-08-26):

### Fase 0 — 5G entra nas portadoras globais (reversão de decisão)

O cliente vai popular o NR-ARFCN **na mesma coluna `DLEARFCN`** da EP — o filtro
"só 4G" de `Api._earfcn_clusters` foi removido. Namespace de id único: 5G usa
`earfcn-<valor>`, igual ao 4G (sem prefixo `nrarfcn-`); pressuposto: LTE EARFCN
(≤ ~65k) e NR-ARFCN de FR1 (≥ ~140k) não colidem numericamente. Cada cluster
gerado ganha `family` (`"4G"`/`"5G"`/`None`, resolvida por
`_cell_technology_family` com fallback em `_single_configured_family` — necessário
porque há eventos com células sem token de tecnologia no nome). A cor do cluster
agora é indexada **por família** (4G conta do zero, 5G conta do zero); indexar
globalmente fazia as portadoras 4G repintarem toda vez que uma 5G entrava na lista.
O teste que afirmava o oposto (`test_nao_mistura_celulas_5g...`) foi reescrito para
`test_5g_gera_portadora_propria_sem_misturar_com_4g`.

### Fase 1 — escopo `site_carrier` no backend

Novo escopo em `get_kpi_series`/`get_kpi_overview`/`get_kpi_overview_multi`:
`scope="site_carrier"`, `scope_id="<site_id>::<earfcn>"` (split com `rsplit("::", 1)`
— ids de site não contêm `::`, mas o earfcn fica garantido como último segmento).
Resolve para uma seleção parcial de site via `Api._site_carrier_selections`
(ao lado de `_cluster_raw_selections`) e reaproveita `_cluster_series_by_family` —
**nenhuma agregação nova foi escrita**. Armadilha conhecida: essa função espera ids
de site **brutos** (membros), nunca o id fundido — por isso o helper resolve o dono
de cada célula com `_owner_site_id_for_cell` antes de devolver a seleção.

`get_sites` ganhou o campo `"carriers": [{"earfcn", "family", "cell_count"}, ...]`
por site (via `Api._site_carriers`, também usado no fallback do `except`), para o
frontend não precisar reimplementar a classificação de família em JS.

Rejeitado: gerar clusters sintéticos site×portadora (`earfcn-1276@725483`) —
explosão combinatória no dropdown do dashboard.

Testes novos: `tests/test_api.py::TestSiteCarrierScope` (resolução do escopo, site
gêmeo 4G/5G só puxa o membro certo, earfcn inexistente → série vazia,
`get_kpi_overview_multi` aceitando o escopo, `carriers` em `get_sites`).

**Pendente (não fazia parte desta entrega):** Fase 2 (frontend — sub-linhas de
portadora no seletor de sites da Visão Geral, `kpi_overview.js`/`bridge.js`/CSS) e o
teste de `tests/test_server_parse_sites.py` para NR-ARFCN de 6 dígitos. Ver
checklist em `plano-portadora-por-site.md`.

**Gate:** `pytest tests/ -q --ignore=tests/test_http_vpn.py` → **551 passed**
(546 + 5 novos), zero regressões.

## 2026-08-28 — Portadora por site (Fase 2 do frontend)

Continuação da entrega acima — consome o escopo `site_carrier` e o campo
`site.carriers` do backend (Fase 1) na Visão Geral de KPIs.

### `frontend/js/kpi_overview.js`

- `_selection` ganhou `siteCarrier: Set<"<site_id>::<earfcn>">`, entrando em
  `_selectionSize()`, na purga de `_refreshScopeData` (existência do par
  site+earfcn em `site.carriers`, **sem** filtrar por família — mesmo
  precedente dos clusters manuais, que também sobrevivem à troca de aba) e em
  `_applyPreferredSelection()`.
- `_familyFilteredClusters()` (novo): esconde clusters `source: "earfcn"` de
  família diferente da aba ativa. Usado em `_carrierClusters()` e
  `_renderClusterPicker()` — sem isso "Todos os clusters" marcava portadoras
  da aba errada, que renderizavam vazias.
- `_applyPreferredSelection()`: o `if` que priorizava portadoras só em "4G"
  virou `carriers.length` puro (decisão 2 do plano — 5G também abre com
  portadoras marcadas).
- **Seletor de Sites — "separar por portadora":** site com ≥2 portadoras
  (`site.carriers`, filtradas pela família ativa) ganha um chevron
  (`.scope-picker-chevron`, dentro do `<label>` da opção; `preventDefault` +
  `stopPropagation` no click evitam que ele dispare o checkbox do site pai).
  Expandido, lista uma sub-linha (`.scope-picker-suboption`, reaproveita
  `.scope-picker-option`) por portadora com checkbox + swatch + "N células".
  **Primeira expansão do site na sessão** marca todas as portadoras dele de
  uma vez (`_autoMarkedCarrierSites`, nunca reaplicado depois de recolher/
  reabrir); se não couberem no teto de 8 escopos, a seleção inteira é limpa
  antes — expandir tem que sempre funcionar. `_expandedSites` guarda o estado
  aberto/fechado; ambos os Sets são zerados em `_open()`, junto com
  `_hiddenScopes`.
- Cor do escopo `site_carrier`: base = cor do cluster `earfcn-<v>` global
  (`_carrierClusterColorMap()`), resolvida por colisão pelo mesmo mecanismo
  `taken` de site/célula em `_selectedScopes()` — mesma cor da portadora
  global quando ela está livre, cor diferente quando não está.
- `_cellScopeSiteIds()` e o resumo do seletor de sites (`_siteSummaryText()`,
  agora separado de `_summaryText`) passam a contar `siteCarrier` também.

### `frontend/js/bridge.js` (mocks, cenário `?kpiOverview=earfcn`)

- `MOCK_EARFCN_CLUSTERS` ganhou `family` em cada entrada e uma portadora 5G
  (`earfcn-627264`).
- `carriers` nos mocks de `get_sites` via `_MOCK_SITE_CARRIERS` (ERB-07 e
  ERB-03 com 2 portadoras 4G cada — para exercitar o chevron; SPSMG7 com uma
  4G + a 5G).
- `scopeOffset` de `get_kpi_overview` e o filtro de escopo de
  `get_kpi_overview_multi` reconhecem `"site_carrier"`.

### `frontend/css/main.css`

`.scope-picker-chevron` (rotaciona 90° em `.is-open`) e `.scope-picker-suboption`
(indenta 26px, reaproveita `.scope-picker-option`).

### Verificação

Smoke manual com Playwright headless contra `?kpiOverview=earfcn` (script
descartável, não versionado): expandir ERB-07 mostra 2 sub-linhas
("Portadora 1276 · 2 células", "Portadora 1700 · 1 célula") já marcadas; os
9 painéis renderizam 5 séries sincronizadas (3 clusters + 2 site_carrier) sem
erro de console; clicar no chevron não ativa o checkbox do site; desmarcar uma
portadora e recolher/reabrir preserva a escolha (não re-marca); trocar para a
aba 5G mantém a comparação (portadoras 4G não são purgadas por família, mesmo
comportamento dos clusters manuais). Screenshot conferido visualmente.

**Gate:** `pytest tests/ -q --ignore=tests/test_http_vpn.py` → **551 passed**,
zero regressões. `test_visao_4g_prefere_clusters_de_portadora_quando_existem`
foi renomeado/reescrito (`test_visao_prefere_clusters_de_portadora_quando_existem_em_qualquer_familia`)
porque a assinatura antiga (`_family === "4G" && carriers.length`) não existe
mais no código-fonte — a asserção literal do teste precisava acompanhar a
mudança da decisão 2.

**Pendente:** Fase 3 (testes Python/Playwright dedicados ao `site_carrier` no
frontend, NR-ARFCN em `test_server_parse_sites.py`) e Fase 4 (esta entrada já
cobre boa parte, mas o plano pede também a formalização final). Smoke via
`python main.py --mock --dev` não foi rodado nesta sessão (verificação feita
via servidor estático + Playwright direto, mais rápido para iterar).

---

## 2026-08-28 — Nome de exibição do site a partir do `nename` (convenção TIM)

**Decisão do usuário:** a rede TIM nomeia site de duas formas na coluna `nename` da EP: sem
hífen, o valor já é o nome do site; com hífen, o que vem antes é o indicador de tecnologia e o
nome do site é o que vem depois (`5D-SACEO1` → `SACEO1`, `5G-SAFEL1` → `SAFEL1`). O sistema
passa a exibir o nome tratado **sempre**.

**Implementação:** `Api._display_site_name` (`api/api.py`, ao lado de `_normalize_site_name`),
aplicada em `_as_merged_site`. Um único ponto: como todo o resto (`get_sites`, `get_event_cells`,
alertas, mapa, seletores da Visão Geral) consome sites fundidos, a regra chega em todas as telas
de uma vez, inclusive nos eventos já importados — **não precisa reimportar a EP**.

- Regra literal: `rsplit("-", 1)[-1]`; sem hífen o nome sai intacto; nome terminado em hífen
  (`"5G-"`) cai de volta no bruto, para nunca exibir vazio.
- **O bruto não se perde:** `original_name` no site fundido e em `get_sites` (inclusive no
  fallback do `except`); o banco continua guardando o `nename` original. A busca do painel
  principal (`kpi.js`) e a do seletor de sites (`kpi_overview.js`) casam com os dois, então
  procurar por "5G-SAFEL1" continua achando o site.
- **Ids não mudam.** `serving_site`, `site_id` e o id do site fundido continuam vindo do
  `enodebid`/nome normalizado — só o rótulo mudou.

**Pressuposto (do usuário, sobre a lista completa da rede):** só existem essas duas formas. Um
nome com hífen que não seja `<tecnologia>-<site>` (ex.: `RJ-ZONA-SUL`) exibiria só o último
elemento. Se aparecer, é aqui que se ajusta.

**Interação com a fusão 4G/5G:** `_normalize_site_name` (chave de fusão) continua com a lista
fechada `4G|5G|5D|SD|SR` — a regra nova é só de exibição. Efeito colateral conhecido, medido no
`roadshow-salvador` (341 sites fundidos, 339 renomeados): dois sites que já não fundiam por
estarem a 5,7 km um do outro (`5D-SAPFO9` e `SD-SAPFO9`, com warning de homônimo desde sempre)
passam a exibir o mesmo rótulo `SAPFO9`. São sites distintos da EP; o id e o warning continuam
distinguindo.

**Reversão de teste documentada:** `TestAlertDisplayNames::test_utilizacao_mostra_nome_do_site`
e `TestEventCells::test_lista_todas_as_celulas_com_o_site_dono` afirmavam `SR-SPPNB2` como nome
exibido. Passaram a esperar `SPPNB2`. Cobertura nova: `tests/test_api.py::TestDisplaySiteName`.

---

## 2026-08-28 — Seletor de sites da Visão Geral: linha com portadoras

Complemento da entrega `site_carrier` (ver `plano-portadora-por-site.md`).

- O chevron "separar por portadora" passou a aparecer com **≥1** portadora (era ≥2). Com uma só
  a expansão não separa nada, mas é o que informa que o recorte existe — nos eventos reais é
  comum um site ter uma portadora por família, e o recurso ficava invisível.
- A meta da linha mostra `4G/5G · 6 portadoras`. **Enumerar os EARFCNs ali quebrou o layout:**
  `.scope-picker-option-meta` era `white-space: nowrap` sem encolher e `.scope-picker-option-name`
  era `flex: 1` com `min-width: 0` — a meta longa espremia o nome do site até **zero** e a lista
  ganhava rolagem horizontal. Correção: meta curta (os EARFCNs ficam nas sub-linhas da expansão),
  `min-width: 5em` no nome, elipse na meta, `overflow-x: hidden` nas opções e o menu do seletor
  de sites em 360px.

---

## 2026-08-28 — Troca 4G↔5G na Visão Geral: o que atravessa a aba

**Decisão (do usuário, entre "sempre compartilhar", "memória por família" e "só na primeira
troca"): sempre compartilhar.** Atravessam a troca de aba só os escopos que existem nas duas
famílias — **cluster sem família definida** (o cluster de sites, que tem células 4G e 5G) e
**site**. Portadora (cluster EARFCN e `site_carrier`), cluster recortado de uma tecnologia só e
célula são purgados.

Descartadas: "só na primeira troca" cria um modo invisível (o mesmo clique se comporta diferente
na segunda vez, sem nada na tela dizendo isso) e "abrir vazio" cai no estado de erro
"selecione ao menos um", que lê como painel quebrado no caminho mais comum ("quero ver esse
mesmo site no 5G").

**O defeito corrigido não era só cosmético.** `_selectedScopes()` varre `_scopeClusters` sem
filtro de família, enquanto o seletor usa `_familyFilteredClusters()`: o escopo da outra
tecnologia virava chip e entrava na consulta, mas **não tinha linha na lista para ser
desmarcado**. Só saía fechando o modal.

- **Backend:** `Api._selection_family()` deriva `family` para cluster **manual** a partir das
  células que ele recorta (única família ⇒ é dela; mistura ⇒ `None`). Antes só o cluster EARFCN
  declarava família, então o "cluster de células 4G" não tinha como ser filtrado.
  `family` só é consumido por `kpi_overview.js` — nenhum outro módulo lê esse campo.
- **`_familyFilteredClusters()`** deixou de olhar `source === "earfcn"`: a regra agora é só
  `!cluster.family || cluster.family === _family`, valendo para portadora e cluster manual.
- **Re-semeadura:** se a purga zerar uma comparação **que tinha algo**, `_applyPreferredSelection()`
  roda para a família nova (no evento com portadoras, a 5G abre com as portadoras dela).
  Comparação que já estava vazia continua vazia — aí o vazio foi escolha do usuário, e re-semear
  ali é justamente o que o comentário de `_refreshCellsForFamily` sempre alertou.
- **`_applyPreferredSelection()` ficou consciente de família** nas sementes cegas
  (`clusters:compare` e o fallback "primeiro cluster"), senão a re-semeadura devolveria o escopo
  invisível que a purga acabou de tirar.
- **Consequência tratada:** com a semente family-aware, um "ver KPIs" vindo de cluster de uma
  tecnologia só seria descartado se a aba resolvesse para a outra família. `_preferredFamily()`
  passou a deixar **o cluster pedido escolher a aba de abertura** — o cluster pedido sempre
  aparece, e agora na aba onde tem dado. `techFilter` do dashboard continua mandando nos demais
  casos (inclusive `"all"` ⇒ 4G).
- **Mock:** `get_clusters` do `bridge.js` descartava `family` no envelope — as portadoras do
  cenário `?kpiOverview=earfcn` chegavam sem família e o filtro nunca era exercitado em `--mock`.

Cobertura: `test_api.py::TestClusters::test_cluster_de_uma_tecnologia_so_declara_a_familia` e
três testes de UI em `test_frontend_kpi_overview_ui.py` (purga, re-semeadura, vazio deliberado).

---

## 2026-08-31 — Janela restaurável (implementação A), tela cheia e DPI por monitor

**Decisão:** a janela do shell customizado passa a se comportar como um app Windows normal.
Descartada a alternativa "manter sempre maximizada e só mover entre monitores": ela não
resolvia a queixa central (não dá para restaurar) e o Windows já oferece `Win+Shift+setas`
de graça para mover janela maximizada entre telas.

**O que ficou travado:**

- **Mínimo continua `1024 × 600` lógicos.** O "mínimo de 80%" pedido foi reinterpretado como
  *tamanho restaurado padrão*: 80% da área útil do monitor atual, centralizado, com piso no
  mínimo e teto na própria área útil. Um mínimo dinâmico de 80% seria hostil (num 4K travaria
  a janela em 3072 px) e mataria o Aero Snap de meia tela.
- O tamanho restaurado é aplicado via `SetWindowPlacement`/`rcNormalPosition`, **sem** tirar a
  janela do estado maximizado em que ela abre. `rcNormalPosition` usa *workspace coordinates*
  (origem = área útil do monitor primário), não coordenadas de tela — o offset é descontado.
- **Tela cheia** é um botão à parte (ícone de monitor), separado de maximizar: salva o
  `WINDOWPLACEMENT` atual e cobre `rcMonitor` inteiro. O placement salvo é a única fonte de
  verdade do estado `fullscreen`.
- **O processo declara Per-Monitor-DPI-Aware V2** antes de o pywebview subir a janela (ele
  chama `SetProcessDPIAware()`, System aware, e quem chama primeiro vence). Só no modo
  `--custom-titlebar`; `--native-titlebar` mantém o comportamento anterior como rollback.
- `WM_GETMINMAXINFO` é **encaminhado ao WNDPROC do WinForms antes** de limitarmos o tamanho
  maximizado à área útil — é lá que o `min_size` do pywebview é aplicado. Engolir a mensagem
  descartava o mínimo.

**Verificado no app real (2026-08-31):** `dpi=144`, área útil `1920×1128`, alvo restaurado
`1536×902` centralizado em `(192, 113)`. Note que neste notebook a 150% os 80% coincidem com
o mínimo do dashboard (1024×601 lógicos) — é esperado, não é bug.

**Pendente de validação do usuário:** matriz com dois monitores de escalas diferentes
(notebook 150% + externo 100%), que é onde o sintoma original aparecia.

---

## 2026-08-31 — Como arraste e resize funcionam no shell customizado (contrato)

Complementa a entrada do mesmo dia sobre a janela restaurável. Estes dois pontos são a
espinha dorsal do chrome e não devem ser "simplificados" sem reler a entrada correspondente
no ERRORS.md.

- **Arraste é do WebView2, não nosso.** `IsNonClientRegionSupportEnabled = True` +
  `app-region: drag` na `.window-titlebar-drag`. O `windowBeginDrag`/`begin_drag` só entra em
  ação quando `get_state()["nonclient"]` é `False`. Onde há `app-region: drag`, o DOM **não
  recebe** `pointerdown`/`dblclick` — não adiantará amarrar comportamento novo nesses eventos
  na região arrastável.
- **Resize depende do recuo do `WM_NCCALCSIZE`.** Área cliente = janela menos
  `SM_CXSIZEFRAME + SM_CXPADDEDBORDER` por lado, **somente quando restaurada**. É esse recuo
  que devolve a moldura nativa ao HWND pai. Voltar a devolver `0` incondicionalmente mata o
  resize inteiro de novo, silenciosamente.
- **O mínimo é da área cliente, não da janela.** `apply_default_geometry(min_size=(1024, 600))`
  guarda o mínimo do *conteúdo*; `WM_GETMINMAXINFO` soma a moldura ao `ptMinTrackSize`. A
  144 DPI isso vira uma janela de 1558×922 para entregar 1024×600 lógicos.
- **`CoreWebView2` só pode ser tocado na thread do formulário.** `before_show` está nela;
  `loaded` não. Fora dela, `BeginInvoke`.

Ferramenta útil: a sonda externa que restaura a janela viva e pergunta o `WM_NCHITTEST` das
oito direções via `SendMessageW` — é a única forma de provar que o hit-test real funciona,
já que o teste unitário exercita a função pura, não o caminho de mensagens.

---

## 2026-08-31 — Correção do contrato de arraste (substitui o ponto do `app-region` acima)

A entrada anterior estava certa no mecanismo e errada no momento. Ficou assim:

- **`IsNonClientRegionSupportEnabled` é armada, não ligada.**
  `WindowChromeController.arm_nonclient_regions(window)` assina
  `CoreWebView2InitializationCompleted` no `before_show` e liga a propriedade dentro do
  handler, **antes da primeira navegação**. Ligar depois (no `loaded`) não tem efeito nenhum
  sobre a página já carregada, apesar de a propriedade ler `True`.
- **`get_state()["nonclient"]` significa "ativo neste documento".** É escrito só dentro
  daquele handler. O frontend continua usando o flag para decidir se precisa do fallback
  `windowBeginDrag` — que segue sendo inútil na prática, então `nonclient: False` hoje quer
  dizer "sem arraste".
- **Como provar que o arraste funciona:** `WindowFromPoint` sobre a faixa da barra tem que
  devolver `Chrome_WidgetWin_0` (a janela auxiliar que o WebView2 cria só quando o recurso
  está ativo) e o `WM_NCHITTEST` dela tem que ser `2` (HTCAPTION). Se devolver
  `Chrome_RenderWidgetHostHWND`/`HTCLIENT`, o recurso não pegou. Teste unitário não alcança
  isso; a sonda com drag sintético (`SetCursorPos` + `mouse_event` + `GetWindowRect`) alcança.

Vem de graça do Windows, sem código nosso: arraste, duplo clique para maximizar/restaurar,
Aero Snap e menu de sistema com o botão direito.

**Limitação conhecida (não é regressão):** o flyout de Snap Layouts ao pousar o mouse sobre o
botão maximizar (Windows 11) não aparece. O botão é `no-drag`, então o ponto pertence ao
`Chrome_RenderWidgetHostHWND` e o `HTMAXBUTTON` que o `hit_test()` do HWND pai devolve
(medido: `ht_pai=9`) nunca é alcançado. O clique no botão funciona normalmente.

**Defeito conhecido e ainda aberto (anterior a esta correção, veio junto com o recuo do
`WM_NCCALCSIZE`):** cada ciclo maximizar → restaurar encolhe a janela restaurada em exatamente
a espessura da moldura (22×22 px físicos a 144 DPI), até parar no mínimo. Medido com o
código de antes e de depois desta correção, idêntico:
`1800×1000 → 1778×978 → 1756×956 → 1734×934 → 1712×922`.

---

## 2026-08-31 — Causa provada do encolhimento a cada maximizar/restaurar (ainda aberto)

Completa o "defeito conhecido" da entrada anterior, que ficara sem causa raiz. Rastreado com
log temporário no `_wnd_proc` (`WM_WINDOWPOSCHANGING/CHANGED`, `WM_SIZE`, `WM_NCCALCSIZE`,
`WM_GETMINMAXINFO`), depois removido.

**O que o trace mostra**, em cada ciclo, com a janela restaurada em 1800×1000:

```
POSCHANGED  pos=(100,100,1800,1000)  <- Windows restaura para o rcNormalPosition
SIZE        cliente=(1778,978)       <- nosso recuo: cliente = janela - 22
POSCHANGING pos=(100,100,1778,978) flags=0x0016   <- o WinForms pede janela = 1778x978
```

Ou seja: **o WinForms reaplica, como tamanho de janela, o tamanho de área cliente que ele tinha
em cache.** Ele não sabe do nosso `WM_NCCALCSIZE`: para os estilos desta janela o
`AdjustWindowRectEx` diz que cliente e janela têm o mesmo tamanho, então cada restauração
"perde" os 22 px de moldura, e o novo tamanho vira o `rcNormalPosition` do ciclo seguinte —
por isso o erro é cumulativo, não constante. Para no piso porque o `ptMinTrackSize` do
`WM_GETMINMAXINFO` segura a altura (visível no trace: pedido `…,1690,900`, `NCCALCSIZE` com
`…,1690,922`).

**Atribuição confirmada por experimento:** com o recuo do `WM_NCCALCSIZE` desligado (e nada
mais alterado), cinco ciclos deixam a janela em 1800×1000, sem desvio nenhum. Com o recuo
ligado, `1800×1000 → 1778×978 → 1756×956 → 1734×934 → 1712×922 → 1690×922`. Idêntico antes e
depois da correção do arraste — o defeito veio com o recuo, no commit `9ab2bc1`.

**Caminhos de correção considerados (nenhum aplicado ainda):**

1. Corrigir o `WM_WINDOWPOSCHANGING` que chega logo após a restauração, devolvendo os 22 px.
   Cirúrgico, mas é heurística: precisa distinguir o pedido do WinForms de um pedido legítimo.
2. Trocar o recuo por um `Padding` no formulário, mantendo `WM_NCCALCSIZE` devolvendo `0`. A
   faixa de moldura passaria a ser área cliente do HWND pai não coberta pelo WebView2 — o
   `hit_test()` continua devolvendo `HTLEFT`/`HTTOP`/… ali, e a contabilidade do WinForms volta
   a fechar (cliente == janela), eliminando a origem do desvio. É a mais estrutural e a que
   mais se parece com o que Electron/WinUI fazem.
3. Reescrever o `rcNormalPosition` após cada restauração, com `SetWindowPlacement`. Simples,
   mas trata o sintoma e briga com o WinForms a cada ciclo.

## 2026-08-31 — Conciliação de cadastros na importação de `.sepack` (decisão 5.1 do plano)

O plano de distribuição automatizada só dizia "clientes e VIPs existentes: mesclar por ID, com a
mesma política de conflito", o que na prática significava preservar o arquivo local inteiro.
Ficou definido no plano (`docs/plans/2026-08-31-001-feat-distribuicao-automatizada-eventos-plan.md`,
decisão 5.1) que **evento** continua sendo preservado inteiro em conflito, mas **cliente, regional,
VIP e logo** são cadastros compartilhados e passam a ser conciliados campo a campo, pela chave
`id`:

- campo faltante local + presente no pacote: adiciona;
- campo local preenchido + ausente/vazio no pacote: mantém o local;
- campo preenchido nos dois lados com valores diferentes: preserva o local e registra conflito
  (inclui `ip` da regional, que é ambiente-específico);
- logo é a única substituição automática: entra quando vem não vazio, com backup do anterior;
- `id` novo com nome normalizado igual a um cadastro existente: apenas aviso, sem mesclar sozinho.

**Motivo:** importar um evento novo da TIM em uma máquina que já tem a TIM cadastrada não pode
criar um segundo cliente — isso fragmentaria o cadastro e faria o app pedir credencial de novo.
Nenhuma fase nova foi criada; o trabalho está na Fase 1 (`reconcile_record()`, campo `reconciled`
no `ImportResult`, seis testes novos) e na revisão da Fase 2.

## 2026-08-31 — Fase 1 da distribuição automatizada: formato `.sepack`, geração e importação

Implementada a Fase 1 do plano
`docs/plans/2026-08-31-001-feat-distribuicao-automatizada-eventos-plan.md`. Nenhuma interface
nova; tudo por CLI e pelos modos sem janela do `main.py`.

**Entregue:**

- `core/event_package.py` — contrato do `.sepack` (envelope + payload.zip + manifesto com
  SHA-256 por arquivo), `preview_package`, `build_package`, `validate_package`,
  `inspect_package`, `reconcile_record`, `plan_import`, `import_package`, `summary_lines`.
- `core/seed.py` — `validate_collection()` (multi-cliente, lista exata de eventos); o recorte
  por perfil passou a delegar as regras estruturais a ela, sem mudar as mensagens legadas.
- `core/paths.py` — `server_data_dir()`, `distributions_dir()`, `import_reports_dir()`,
  `import_backups_dir()`, `import_lock_path()`, `is_safe_component()`.
- `tools/event_package.py` — `preview | build | inspect | import`, com `--json`.
- `main.py` — `--inspect-event-package` / `--import-event-package` resolvidos **antes** de
  qualquer coisa do pywebview.
- Testes: `tests/test_event_package.py` (26), `tests/test_main_event_package_cli.py` (8) e
  2 novos em `tests/test_installer.py`. Suíte completa: 666 passed, 10 skipped.

**Decisões travadas nesta implementação (além da decisão 5.1 já documentada):**

- **`server_data_dir()` repete a regra do `server.py`** (`data_dir()` no frozen,
  `data_dir().parent` em dev) em vez de "consertá-la". O importador precisa gravar exatamente
  onde o app lê; divergir aqui produziria importação invisível. `server.py` não foi alterado.
- **Reprodutibilidade é sobre o payload, não sobre o arquivo inteiro:** `build_package` aceita
  `package_id` e `created_at` injetáveis; com os mesmos valores e a mesma origem, os bytes são
  idênticos (ordem alfabética das entradas, timestamp ZIP fixo em 1980-01-01, deflate nível 9).
  Sem isso, "reproduzível" seria intestável.
- **O pacote é lido inteiro em memória e validado sobre essa cópia** (limite de 32 MiB). Isso já
  entrega a proteção contra TOCTOU que a Fase 4 exige para a assinatura.
- **`signature_required: true` é recusado nesta fase.** Aceitar sem poder verificar seria fingir
  confiança.
- **Códigos de saída (CLI e `main.py`):** 0 concluído/idempotente, 2 argumento inválido,
  3 pacote inválido, 4 concluído com conflito preservado, 5 erro interno (só na CLI).
- **Ordem de gravação:** logos → clientes → VIPs → eventos, para que a dependência exista antes
  de quem a referencia mesmo se o processo morrer no meio.
- **VIP legado por fallback exige cliente + regional.** Evento sem regional não gera fallback
  nenhum, em vez de arrastar todos os VIPs do cliente.
- Nenhuma dependência nova: o módulo usa só a biblioteca padrão. `requirements-build.lock` não
  precisou mudar; `main.spec` ganhou apenas `core.event_package` em `hiddenimports` e a semente
  embutida continua no lugar (a remoção é da Fase 3).

**Pendente de validação manual (não automatizável aqui):** abrir o app apontando para uma pasta
de dados importada e conferir os eventos no seletor e os clientes no gerenciador de credenciais
(passos 4 e 5 da validação funcional da fase).

## 2026-08-31 — Fase 2 da distribuição automatizada: seleção múltipla e geração pela Central

Implementada a Fase 2 do plano
`docs/plans/2026-08-31-001-feat-distribuicao-automatizada-eventos-plan.md`. O operador
seleciona eventos na lista, revisa o que entra e baixa o `.sepack` sem tocar em JSON nem
em linha de comando.

**Entregue:**

- `core/distribution_service.py` — `DistributionService` com `capabilities()`, `preview()`,
  `create_job()`, `get_job()`, `list_jobs()`, `artifact()`, `wait_for()` e
  `cleanup_incomplete()`; estados `queued → validating → packaging → ready|failed`;
  `redact()` para tudo que sai para a UI.
- `server.py` — `GET /api/distributions/capabilities`, `POST /api/distributions/preview`,
  `POST /api/distributions`, `GET /api/distributions/{job_id}` e os dois downloads
  (`/download`, `/manifest`).
- `server_frontend/index.html` — checkbox por card, "Selecionar todos visíveis", contador,
  botão **Gerar distribuição**, modal `<dialog>` de revisão, progresso por etapa com
  polling em backoff, sucesso com tamanho/SHA-256/data e falha com diagnóstico copiável.
- `core/paths.py` — `distribution_jobs_dir()`.
- Testes: `tests/test_distribution_api.py` (18) e
  `tests/test_server_frontend_distribution_ui.py` (11 estruturais + 9 Playwright).
  Suíte completa: **704 passed, 10 skipped** (era 666/10 na Fase 1).

**Decisões travadas nesta implementação:**

- **O navegador nunca manda caminho.** `_distribution_body()` recusa qualquer chave fora
  de `{event_ids, name, vip_policy, vip_ids, format}` com HTTP 400 nomeando os campos
  extras. Origem e destino saem sempre do `SERVER_DATA_DIR` do próprio servidor.
- **`job_id` é `uuid4().hex` validado por `^[0-9a-f]{32}$` antes de virar caminho.** Um id
  fora do formato é recusado com `job.invalid_id` sem nunca compor um `Path`.
- **Download só de artefato registrado no job.** `artifact()` lê o nome de
  `record["artifacts"][kind]["name"]`, revalida com `paths.is_safe_component()` e exige
  `state == "ready"` (senão 409). Não existe endpoint que aceite nome de arquivo.
- **Os endpoints são `def` com `Body(...)`, não `async def` com `Request`.** O projeto não
  tem `httpx`, então o TestClient do FastAPI não existe aqui; assinaturas assim são
  chamáveis direto no teste, como já fazia `tests/test_server_parse_sites.py`.
- **Um job de escrita por vez (`threading.Lock`), previews em paralelo.** O lock é adquirido
  fora do `try`: enquanto ele não vem, o job fica legitimamente em `queued` e a interface
  mostra "aguardando outra geração terminar".
- **Estado do job só existe em disco** (`data/distributions/jobs/<job-id>.json`, gravado por
  `os.replace` a cada transição). Uma instância nova do serviço — página recarregada ou
  processo reiniciado — recupera artefatos e manifesto sem estado em memória.
- **A gravação atômica no Windows precisa de repetição, não só de lock.** `_retrying()`
  repete leitura e `os.replace` sob `PermissionError` (o destino está aberto pelo polling da
  interface; o `.tmp` está aberto pelo antivírus), `_record_lock` é `RLock` cobrindo leitura
  e gravação, e `_force_failed()` garante que nenhum job fique preso em `queued` quando o
  registro não pode ser lido. Detalhe completo em ERRORS.md (2026-08-31).
- **`redact()` mantém o nome do arquivo e descarta a árvore do perfil**
  (`C:\Users\ana\...\dados.json` → `<caminho>/dados.json`); em `CHAVE=valor` com palavra de
  segredo, mantém a chave e oculta o valor. Rotas de API (`/api/...`) não são tratadas como
  caminho de usuário, senão o diagnóstico ficaria ilegível.
- **`cleanup_incomplete()` nunca apaga job `ready`.** Só remove job não terminal mais velho
  que a idade configurada (24 h por padrão), e roda no `create_job()`.
- **`capabilities.formats` lista só o que funciona** (`["event_package"]`), e
  `available_formats` traz o Setup completo como opção **desabilitada** com
  `reason: "disponivel apos configurar um build-base"`. A UI cruza os dois: `usable =
  item.enabled && formats.has(item.id)`. Na Fase 3 basta o backend passar a listar
  `full_setup` em `formats`.
- **Erro bloqueia Gerar; aviso não.** `btn-distribution-generate.disabled = !preview.ok`, e
  `preview.ok` é falso apenas quando há `errors`. O fallback legado de VIP é aviso.
- **A seleção vive num `Set` fora do DOM**, reaplicada em `renderEvents()` para sobreviver à
  re-renderização, e podada quando um evento some da lista (evento excluído não pode ficar
  selecionado de forma invisível). É limpa ao trocar de aba e ao concluir o download.
- **O modal é `<dialog>` nativo**, não uma div com overlay: foco preso e `Esc` vêm de graça,
  sem código de trap próprio.
- **Nenhuma dependência nova**; `main.spec` não precisou mudar
  (`core.distribution_service` entra pela análise de `server`, já em `hiddenimports`).

**Validação executada:** smoke ponta a ponta com o servidor real em HTTP sobre uma cópia do
`server_data` — capabilities, preview de 2 eventos de clientes diferentes (TIM + Vivo, 651
sites, 7690 células, aviso de fallback de VIP), job até `ready`, download com
`Content-Disposition: attachment`, download do manifesto, 404 para job desconhecido, 400
para `output` vindo do navegador, e o `.sepack` baixado passando por
`tools.event_package inspect` + `import` (2 eventos e 3 cadastros adicionados; segunda
importação idempotente).

**Pendente de validação manual (não automatizável aqui):** abrir a Central num navegador
real e conferir a largura estreita (passo 9) e a leitura de tela do modal.

**Bug pré-existente encontrado, NÃO corrigido (fora do escopo da fase):** `server.py` usa
`shutil.copyfileobj` em `upload_cliente_logo()` sem `import shutil` no módulo — o upload de
logo por cliente levanta `NameError`. Corrigir em tarefa própria.

---

## 2026-08-31 — Fase 5 da barra HTML: barra personalizada vira o padrão no Windows

**Decisão principal:** sem nenhuma flag, o SmartEvents passa a abrir com a barra de título
HTML no Windows. `--native-titlebar` continua sendo o rollback operacional (e vence
`--custom-titlebar` em caso de conflito); `--custom-titlebar` vira apenas forçador técnico.
Fora do Windows nada muda: moldura nativa.

**O padrão é condicionado ao runtime, não só ao sistema.** `resolve_window_chrome_mode()`
recebe a versão do WebView2 lida do Registro e cai para a moldura nativa quando ela é
anterior a `1.0.2210.55` — sem `IsNonClientRegionSupportEnabled` o WebView2 não entrega
arraste, duplo clique e Aero Snap ao Windows, e a barra ficaria decorativa. Comparação por
**build.patch** (os dois últimos componentes), únicos comuns entre o formato do Edge
(`120.0.2210.91`) e o do SDK (`1.0.2210.55`). Versão ausente/ilegível **não** rebaixa o
padrão: o app só chega lá com o runtime funcionando, e um erro de leitura do Registro não
justifica perder a barra.

**Diagnóstico com janela real:** `main.py --self-test-window-chrome` cria uma janela
WebView2 de verdade e repete o caminho de produção (attach em `before_show` → arma a região
não-cliente → consulta estado → troca regiões → detach). Entra no `--self-test` como o check
"moldura Win32 em janela WebView2", separado do check de APIs, para que uma falha do chrome
nunca mascare o renderer. **O relatório vai para arquivo (`--report`), não só para o
stdout:** no executável windowed (`console=False`) o stdout do filho pode não existir, e
parsear só a saída daria falha falsa no build empacotado.

**`main.spec` não precisou mudar:** a regra `('frontend', 'frontend')` já leva
`frontend/assets/logoSmartEvents-32.png` e `frontend/js/window_chrome.js`. Quem prova isso no
artefato é o novo check "assets da barra de título", que valida os arquivos via
`resource_dir()` — vale igual no fonte e no `.exe`.

**Salto de layout no startup resolvido pela URL.** O shell agora abre
`index.html?chrome=custom#desktop`; `window_chrome.js` lê `location.search` e reserva os
36 px na primeira pintura, em vez de esperar o primeiro `window_get_state`. Verificado que o
pywebview 6.2.1 serve o arquivo local pelo bottle server mesmo com `http_server=False`, então
query e fragmento chegam intactos ao documento — e o fragmento continua sendo exatamente
`#desktop`, que é o que `bridge.js` compara para não cair em modo mock. Se o attach falhar,
o `window_get_state` responde `native` e a faixa some, em vez de ficar vazia sobre o app.

**Janela inativa é responsabilidade do documento:** sem moldura nativa o Windows não tem o
que esmaecer, então `data-window-active` (focus/blur) controla o contraste do título e dos
ícones; hover e foco continuam com contraste cheio.

**pywebview travado em `>=6.2.1,<7.0`** no requirements.txt: o hook depende de internals
(`window.native.Handle`, `window.native.browser.webview`). Subir o teto exige revalidar
`--self-test-window-chrome`.

**Validação executada:** suíte completa (716 passed, 10 deselected de VPN); `--self-test`
com os três checks de chrome aprovados (os dois reprovados — Chromium visual e semente do
perfil — já falhavam antes desta fase); `--self-test-window-chrome` aprovado em Windows 11
build 26200 a 144 DPI com região não-cliente ativa; app real aberto no padrão (log:
solicitada=padrao, efetiva=custom, motivo, sistema, DPI, versão do WebView2, attach=ok) e com
`--native-titlebar`; fechamento por `WM_CLOSE` sem deixar processo python ou msedgewebview2
órfão.

**Pendente (não automatizável aqui):** build `onedir` oficial + `--self-test` no `.exe`, e a
matriz manual em Windows 10, dois monitores e escalas de 100/125/150/200%.

**Observação (pré-existente, não corrigida):** o log registra esporadicamente "Regioes de
window chrome rejeitadas ... cliente_logico=143.33x11.33" quando a janela é consultada num
instante em que o Win32 devolve um cliente minúsculo (janela minimizada/em transição). A
entrada mais antiga é de 2026-08-31 09:12, antes desta fase; é um aviso benigno (as regiões
são reenviadas no resize seguinte), mas vale investigar em tarefa própria.

---

## 2026-09-01 — Fase 2 refeita: geração sai da Central e vira Distribution Studio

**Decisão:** o SmartEvents distribuído ao usuário final **não gera distribuições**. Selecionar
eventos, revisar dependências e produzir `.sepack` (e, na Fase 3, `Setup.exe`) é operação de quem
distribui, e passa a viver numa ferramenta interna do repositório: o **Distribution Studio**
(`tools/distribution_studio.py` + `tools/distribution_studio.html`). Registrada como **decisão 8**
do plano `docs/plans/2026-08-31-001-feat-distribuicao-automatizada-eventos-plan.md`.

**Razão:** a primeira execução da Fase 2 colocou a seleção múltipla e o modal de geração dentro do
Smart Events Central. Isso confunde os dois papéis: a Central é o produto entregue, e o que será
distribuído são os **resultados** deste plano (o `.sepack` e o instalador), não a ferramenta que os
produz. Manter a geração no `server.py` também a arrastaria para dentro do `.exe`, porque `server` é
`hiddenimport` do `main.spec`.

**O que foi revertido (estado idêntico ao commit `f3534ea`):**

- `server.py`: nenhum endpoint `/api/distributions/*`, nenhum import de `core.distribution_service`;
- `server_frontend/index.html`: sem checkbox por card, sem barra de seleção, sem modal de revisão.

**O que existe agora:**

- `tools/distribution_studio.py` — app FastAPI própria, `python -m tools.distribution_studio`
  (porta padrão 8010). Prefixo `/distribution-studio`; **não existe rota em `/`**, então sem o link
  não há tela. Bind fixo em `127.0.0.1` e nenhum CORS (a Central serve a rede local; o estúdio não).
- `tools/distribution_studio.html` — página única, CSS/JS inline, fora de `frontend/` e
  `server_frontend/` (as duas pastas entram no bundle). `robots: noindex` e aviso permanente de que
  a tela é interna.
- Endpoints: `GET /distribution-studio`, e sob `.../api/`: `events`, `capabilities`, `preview`,
  `jobs`, `jobs/{id}`, `jobs/{id}/download`, `jobs/{id}/manifest`.
- `GET .../api/events` é novo: devolve só o resumo que a lista desenha (id, nome, status, datas,
  cliente, regional, contagem de sites/células). O evento inteiro não precisa atravessar a rede
  para alguém marcar uma caixa.
- O estúdio é **somente-leitura** sobre `server_data`: nenhum POST/PUT/DELETE de cadastro.

**Fatos que sustentam o isolamento (e os testes que os travam):**

- `core/distribution_service.py` não é importado por `server.py` nem citado no `main.spec` — logo,
  não entra no bundle do PyInstaller (`test_studio_never_enters_the_distributed_executable`);
- `core/event_package.py` **continua** `hiddenimport`: quem recebe o `.sepack` precisa importá-lo;
- `test_central_server_exposes_no_distribution_endpoint` e
  `test_central_frontend_has_no_selection_or_generation_ui` reprovam se a geração voltar à Central;
- `test_studio_serves_nothing_outside_its_own_prefix` reprova se qualquer rota escapar do prefixo.

**Renomeações:** `tests/test_distribution_api.py` → `tests/test_distribution_studio_api.py`;
`tests/test_server_frontend_distribution_ui.py` → `tests/test_distribution_studio_ui.py`.

**Esconder a tela não é o controle de segurança.** As decisões 2 e 6 (nenhum segredo no pacote;
endpoints aceitam apenas IDs e opções enumeradas, nunca caminho ou comando) continuam valendo
dentro do estúdio, e são elas que os testes travam. O prefixo obscuro só evita que a geração vire
um botão a um clique de quem não distribui.

**Validação executada:** suíte completa (726 passed, 10 skipped); estúdio real subido em
`127.0.0.1:8017` contra o `server_data` do repositório — `/` respondeu 404, `/distribution-studio`
respondeu 200, `/api/distributions/capabilities` (rota antiga) respondeu 404; preview de dois
eventos de clientes diferentes (Vivo + TIM) com contagens corretas e sem `source_dir` no corpo;
job real percorreu `queued → validating → packaging → ready` e produziu um `.sepack` de 4578 B,
validado por `python -m tools.event_package inspect` ("Situacao: valido"); Central subida em
`127.0.0.1:8018` respondeu 200 em `/` e em `/api/events`, e 404 tanto em
`/api/distributions/capabilities` quanto em `/distribution-studio`.

---

## 2026-09-01 — Importar `.sepack` é função da Central; gerar continua fora do produto

**Emenda à decisão 8 do plano de distribuição.** A decisão 8 proíbe **geração** na Central, não
importação — o próprio plano diz que "o aplicativo entregue ao usuário final apenas **importa** o
resultado". A primeira redação dos testes da Fase 2 extrapolou isso e baniu a palavra `sepack` de
`server.py` e de `server_frontend/index.html`, o que impedia também a importação. As asserções
foram estreitadas para banir o **gerador** (`distribution_service`, `build_package`,
`preview_package`, `suggested_filename`) e os controles de geração, não a palavra.

**O que existe agora na Central:**

- `POST /api/events/import/preview` — devolve o `ImportPlan` (o que seria adicionado, conciliado,
  preservado) mais o SHA-256 do arquivo. **Não grava nada.**
- `POST /api/events/import` — aplica o pacote já revisado. Recebe `expected_sha256` e recusa com
  409 se o arquivo mudou entre a revisão e a confirmação.
- Botão "Importar Pacote" ao lado de "Novo Evento", `input[type=file]` com `accept=".sepack"` e um
  modal de revisão com confirmação explícita.

**Decisões travadas por teste (`tests/test_server_event_import.py`):**

- **Revisão antes de gravar.** O upload é feito duas vezes (revisão e confirmação) de propósito: sem
  staging no servidor não há token, diretório temporário persistente, limpeza nem superfície de path
  traversal. O `expected_sha256` fecha a janela TOCTOU que esse desenho abriria.
- **`preserve` é constante no `server.py`.** Substituir evento em conflito continua exclusivo da CLI
  (`--conflict replace`), onde a intenção é declarada. A interface do produto não expõe caminho
  destrutivo; `test_the_central_accepts_no_path_or_conflict_policy_from_the_browser` reprova se
  a política ou um caminho passar a vir do navegador (decisão 6).
- **Limite de tamanho reusa `ep.MAX_PACKAGE_BYTES`** (32 MiB), sem constante nova; upload maior é
  413 e extensão diferente de `.sepack` é 400, ambos antes de qualquer leitura de conteúdo.
- O arquivo é copiado para um diretório temporário fora de `server_data`, para que pacote recusado
  não deixe resíduo na pasta de dados.

**Aplicativo:** o dropdown de eventos ganhou o item de rodapé "+ Adicionar evento", que chama o
`API.openServerUi()` já existente. Cadastrar evento continua sendo função exclusiva da Central; o
app apenas a abre. A aba Eventos já é a inicial da Central, então nenhum roteamento por hash foi
adicionado.

**Validação executada:** `tests/test_server_event_import.py` (10 passed) e suíte completa;
Central real subida em `127.0.0.1:8021` — `GET /` 200, preview do `.sepack` de dois clientes
(Vivo + TIM) devolvendo as 5 ações e apontando para o mesmo `server_data` que a Central lista,
importação idempotente ("Pacote ja aplicado; nenhum arquivo foi alterado."), SHA divergente 409 e
extensão errada 400; Playwright confirmou o modal de revisão renderizando as 5 linhas com o botão
Importar habilitado e o item "+ Adicionar evento" no dropdown do app sem erro de página.

---

## 2026-09-01 - Fase 3: build-base reutilizavel e Setup completo por selecao

**O que mudou.** O ciclo do programa foi separado do ciclo do conteudo. `python build.py base`
roda o PyInstaller **uma vez por versao** e produz um build-base **generico** em
`dist/base/<versao>/`; `python build.py distribution --format setup` combina esse cache com um
`.sepack` e compila um `Setup.exe` novo **sem chamar o PyInstaller**. Selecionar eventos deixou
de custar um build de ~900 MiB.

**Modulo novo `core/base_build.py`.** E a fronteira entre os dois ciclos: semente generica,
manifesto do cache, verificacao de integridade, definicoes do wrapper `.iss` e compilacao do
Setup. Importado por `build.py` e por `core/distribution_service.py` - nenhum dos dois entra no
bundle, entao o gerador continua fora do executavel distribuido (decisao 8 do plano).

**`main.spec` ganhou `SMARTEVENTS_BUILD_MODE`.** `base` recusa catalogo de cliente e semente com
cadastro (a guarda `assert_generic_seed` roda dentro do proprio spec, nao so no `build.py`);
`legacy` e o default e preserva o fluxo por perfil para rollback. Sem a variavel, o build antigo
e byte-a-byte o mesmo.

**Semente generica.** Um `seed-manifest.json` com `"generic": true` e nada mais. `validate_seed()`
aceita colecao vazia nesse modo e `seed_operator_data()` passou a criar `events/clientes/vips/logos`
vazias - o primeiro boot sem pacote abre normalmente, em vez de falhar por pasta ausente.

**Verificacao em dois niveis.** `verify_quick` (hash so do executavel) atende as `capabilities` da
tela, consultadas a cada abertura do modal; `verify_base` (hash de cada arquivo do bundle, sem
tolerar sobra) roda imediatamente antes de compilar. Sem essa divisao, abrir a tela reprocessaria
894 MiB.

**Ordem de pos-instalacao travada no `.iss`:** validar o `.sepack` -> importar com
`--conflict preserve` e relatorio -> `--self-test --expect-package-report --expect-events` -> so
entao oferecer abrir o app. Reinicio por pre-requisito grava um `post-install.cmd` que repete as
DUAS etapas; agendar so o self-test deixaria a instalacao sem os eventos. A associacao do `.sepack`
usa `HKA\Software\Classes` com `uninsdeletekey`, e o `[UninstallDelete]` continua tocando apenas
`{app}`: `%LOCALAPPDATA%\SmartEvents` sobrevive a desinstalacao.

**`sync_events_from_local_files()` (novo em `core/database.py`).** Sincronizacao incremental do
`server_data` para o banco, sem HTTP e **sem remover nada** - o oposto de
`sync_events_from_server()`, que e autoritativo e apaga evento local ausente na resposta. Usada
depois de `--import-event-package` (so os ids que o pacote tocou) e pelo self-test do instalador.

**Payload pre-comprimido: prototipado e REPROVADO.** O plano previa armazenar
`SmartEvents-base.zip` no Setup com `nocompression` + `extractarchive` para nao recomprimir ~894 MiB
a cada selecao. O compilador recusa: `Flag "external" must be used if flag "extractarchive" is
used` - o ZIP teria de viajar FORA do `Setup.exe`, quebrando o criterio de aceite "o destinatario
recebe um unico Setup autocontido". Ficou o `[Files]` recursivo, que e o caminho homologado para
atualizacao/desinstalacao. O ZIP continua sendo gerado: ele e o artefato que move o cache para
outra maquina de release sem recompilar, e a sua entrada no manifesto prova que o cache nao foi
trocado. Custo registrado: a compressao LZMA2 do bundle continua sendo paga por distribuicao; o
ganho da fase (nao rodar o PyInstaller) esta intacto.

**Capacidade resolvida em tempo de execucao.** `capabilities.formats` passa a incluir `full_setup`
somente quando ha build-base integro da versao, ISCC presente e espaco em disco; caso contrario o
formato aparece desabilitado com o motivo e a acao administrativa
(`python build.py base`). A tela **nunca** dispara o PyInstaller por um clique.

**Validacao executada:** suite completa (`769 passed, 10 skipped`, contra 741 antes da fase);
`installer/SmartEvents.iss` compilado de verdade pelo ISCC nas duas formas - com pacote (Fase 3) e
sem pacote (legado) -, ambas rc=0; `python build.py base` real produzindo o bundle generico de
894 MiB; estudio real em `127.0.0.1:8031` com `/` em 404, a pagina em 200 e `capabilities`
reportando `base_ready=false` com motivo e acao antes do cache existir.

---

## 2026-09-01 — Coluna de escopo na EP + mapa do cadastro (zoom e desempenho)

Três pedidos do operador, com uma causa raiz compartilhada entre os dois últimos.

**1. Coluna opcional de escopo na EP (`server.py::parse_sites`).** Segue exatamente o precedente
da coluna `cluster` (Fase 4): fora de `required`, apelidos em `col_mappings`
(`is_event_site/site_evento/no_evento/dentro/dentro_poligono/in_event/in_polygon`), normalizada por
`_normalize_event_site_flag`. Aceita `1/0`, `sim/não`, `dentro/fora`, `in/out`, `true/false`.

**Decisões travadas:**

- **O campo já existia; só a origem faltava.** `is_event_site` estava plumbado ponta a ponta
  (`core/models.py`, schema em `core/database.py`, agregação em `Api._as_merged_site`, filtro
  "só sites do evento" em `frontend/js/map.js`), mas `parse_sites` gravava `True` fixo. A coluna
  não criou conceito novo — passou a alimentar um campo que nascia morto.
- **Célula vazia não vota; sem nenhum voto o site fica dentro.** Preserva as planilhas legadas sem
  a coluna, que não podem mudar de comportamento.
- **Linhas divergentes do mesmo site resolvem por OR** — um "dentro" basta. É a mesma semântica de
  `Api._as_merged_site`, para a fusão 4G/5G e a EP não discordarem sobre o mesmo site.

**2. Zoom travado no mapa do cadastro — causa raiz.** `map.on('zoomend', () => plotSitesOnMap())`
e `plotSitesOnMap` terminava em `map.fitBounds(...)`. Todo zoom (roda ou botões +/−) reenquadrava
o mapa no mesmo nível: o controle funcionava e era revertido logo em seguida. Uma causa só para os
dois sintomas relatados. `fitBounds` passou a viver atrás de `plotSitesOnMap({ fit: true })`,
chamado apenas na carga explícita de dados (upload da planilha e abertura de evento para edição) —
o mesmo desenho que o app cliente já usava em `frontend/js/map.js` (`fitToEvent` separado do
`zoomend`). O cadastro tinha divergido desse padrão.

**3. Culling de viewport.** Medido nos eventos reais: Rock In Rio 2026 tem **2.479 sites / 24.086
células**. Cada `zoomend` destruía e recriava os 2.479 marcadores, remontando ~24 mil `<path>` SVG
e religando 2.479 popups, de forma síncrona. Agora só os sites dentro da viewport (mais margem de
25%) ficam no DOM, `zoomend`/`moveend` entram por debounce de 120 ms e o marcador visível é
reaproveitado via `setIcon` — mesmo padrão que `refreshClusterRings` já usava. `siteMarkersById`
virou o único registro de marcadores (o array paralelo `siteMarkers` era redundante).

**4. Polígono automático: envoltória convexa em vez de bounding box.** Só os sites com
`is_event_site !== false` entram no cálculo (`polygonSourceSites`). Numa EP de teste com evento
compacto + anel de vizinhos, a área caiu de 79,97 km² (bbox de todos) para 6,90 km² — **91% menor**.

**Decisão travada: o afastamento é a envoltória dos círculos, não um offset por normal.** A
primeira implementação usava junta em miter (normal externa por aresta + bissetriz). Ela **falhou
no corredor retilíneo** — sites alinhados numa avenida (caso RoadShow) fazem a envoltória degenerar
num sliver, o sinal da área de Gauss vira ruído numérico e a orientação "para fora" se inverte:
folga medida de 0 m com padding de 500 m. `bufferedHull` troca isso por: cada vértice do contorno
vira um círculo de `padding` (24 segmentos) e a envoltória desses pontos **é** o buffer. Sem
normal, orientação, junta nem caso degenerate — um site só, dois sites, sites colineares e nuvem
passam pelo mesmo caminho. Custo: 2.479 sites → 67 vértices em 8 ms. O erro de corda inscrita é de
0,85% (496 m para padding de 500 m), irrelevante.

**Sinal visual:** site marcado como fora aparece com setor esmaecido (opacidade 0,28) e miolo
vazado, e o popup diz "Fora do polígono (vizinho)". A mensagem do upload informa quantos sites
vieram marcados como fora — o operador confere a leitura da planilha sem abrir o mapa.

**Validação executada:** `tests/test_server_parse_sites.py` (53 passed) e
`tests/test_server_frontend_polygon_map.py` (9 passed, Chromium real). A regressão do zoom foi
verificada por reintrodução deliberada do bug: com `zoomend` reenquadrando de novo, 4 dos 9 testes
falham, incluindo o clique real no botão `+` do Leaflet.
