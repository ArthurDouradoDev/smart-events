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

