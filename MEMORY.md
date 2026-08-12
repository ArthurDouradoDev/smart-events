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

## 2026-08-12 — Falhas pré-existentes conhecidas (não introduzidas pela Fase 3)

- `tests/test_alarms.py::TestMockCollectAlarms` (2 testes) espera `list`, mas a Fase 1 mudou
  `collect_alarms` para devolver `CollectionResult`. O teste nunca foi atualizado.
- `tests/test_credentials.py::test_resolve_base_url_pelo_catalogo` resolve RJ para o IP de SP.
  Problema de catálogo em `credentials.json`/`clientes.json`.

Ambas falham no HEAD e são independentes da coleta de VIP.
