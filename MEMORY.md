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
