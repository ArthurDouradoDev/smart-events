# ERRORS.md — Log de falhas e lições

Cada entrada: o que quebrou, causa raiz, correção e a regra que evita a repetição.

---

## 2026-08-12 — Marca d'água de serial apagava linhas em ciclo parcial (latente)

**Como apareceu:** não apareceu em produção. Foi encontrado analisando
`har-atualizado-filtrado-ordenado-completo.har`, ao medir a ordem real do conjunto que sai do
`filter-by-cols`.

**Sintoma que teria:** medições de VIP sumindo em silêncio, sem erro e sem log, só em tasks
longas. A cobertura pareceria saudável.

**Causa raiz:** `_build_vip_measurements` avança a marca d'água para `max(serialNo)` do lote e
descarta `serial <= last_serial`. Isso só é correto se o lote for um **prefixo ordenado** do
conjunto. Mas o `filter-by-cols` devolve o conjunto **fora de ordem** — 127 violações de ordem
crescente em 619 linhas na captura, em 128 runs curtos (média 4,8 linhas) que não acompanham o
NE. Um ciclo que parasse no meio gravava uma marca d'água alta e as linhas de serial menor
ainda não lidas eram descartadas para sempre nos ciclos seguintes.

**Por que não estava ativo:** `_VIP_PAGE_SIZE = 1000` e o conjunto filtrado da task 2072 tinha
619 linhas — cabia numa requisição, o laço de paginação nunca rodava e `backlog` era sempre
`False`. Simulando a parada em cada offset das 619 linhas reais, o pior caso perdia **50 de 85**
linhas restantes (parada no offset 534).

**Correção (duas camadas):**
1. `POST query/sort` por `Time` **ascendente** no servidor, e paginação por `result-paging`
   sobre o handle ordenado — o lote volta a ser um prefixo ordenado de verdade.
2. Rede de segurança: com `backlog`, a marca d'água de `serial` **não avança**; só o cursor
   `row`. A chave única `(task_id, serial_no)` absorve o replay.

**Regras:**
- **Marca d'água só é válida sobre conjunto ordenado.** Antes de usar `max(cursor)` de um lote,
  provar que o lote é um prefixo ordenado — não presumir a partir do nome do campo.
- **Ordem de resposta de API é contrato, e precisa ser medida, não presumida.** Aqui o conjunto
  de origem era ordenado e estável (0 violações em 1000 linhas de `query/result`) e mesmo assim
  o filtro devolveu desordem. Um filtro determinístico sobre conjunto ordenado *deveria* dar
  subsequência ordenada; não deu.

---

## 2026-08-13 — Gráfico "Site completo" perdeu as linhas por célula (regressão da Fase 2)

**Sintoma:** no popup "Visualização Detalhada" com `Site completo` selecionado, o gráfico
mostrava uma única linha reta (ex.: 100% de Acessibilidade) em vez de uma linha por célula.

**Causa raiz:** `Api.get_kpi_series` (`api/api.py`), no branch `cell_id == "__all__"`, passou a
priorizar o agregado de site já persistido (`db.get_kpi_site_series`, linhas SITE gravadas por
`core/collector.py::_site_rows`/recalculo de "recalculate"). Quando esse agregado existe, a
função retornava cedo com `"cells_data": {}` — nunca calculava a quebra por célula. No frontend
(`frontend/js/kpi.js:569`), o gráfico só desenha uma linha por célula quando `cells_data` não
está vazio; do contrário cai no fallback de uma linha única, escondendo todas as células.

**Correção:** no mesmo branch de retorno antecipado, também consultar `db.get_kpi_series`
(linhas `scope='CELL'`) e montar `cells_data` alinhado aos `labels` do agregado — igual ao que
o branch de fallback (linhas 525-536) já fazia para métricas sem agregado persistido.

**Regra:** um retorno antecipado que populava só parte da resposta esperada (`cells_data`)
quebrou um consumidor que dependia daquele campo para decidir o modo de render. Ao adicionar um
"fast path"/cache para um campo de uma resposta multi-campo, os demais campos que o chamador já
dependia precisam continuar sendo preenchidos, não zerados silenciosamente.
- **Um teste que passa com a correção revertida não testa a correção.** O teste
  `test_ciclos_parciais_sucessivos_nao_perdem_nenhuma_linha` passava dos dois jeitos, porque a
  fixture já vinha ordenada. Só `test_conjunto_fora_de_ordem_nao_perde_linhas_em_ciclo_parcial`
  (sessão que devolve o conjunto embaralhado) reproduz a perda — 6 seriais somem sem ele.
  Sempre reverter a correção e confirmar que o teste falha.

---

## 2026-08-12 — Fase 3 entregou zero medições de VIP, em silêncio

**Sintoma:** após a Fase 3, o log mostrava `Iniciando coleta de VIPs no modo: incremental`
cinco vezes seguidas sem nenhuma linha posterior, e o banco não recebia nenhuma medição.

**Causas raiz (três, encadeadas):**

1. **Contrato errado.** A Fase 3 foi construída sobre `query/subscribe-result`, capturado em
   `novo-curl-vip.md` para a task 2073. Esse endpoint serve à assinatura ao vivo de uma sessão
   de consulta já aberta, não à leitura de histórico. O fluxo real do navegador para a task do
   evento é `query/result` → `fetch-field-values` → `filter-by-cols`.
2. **`msgId` de outro ciclo.** `_initialize_vip_subscription` alocava um `msgId` novo a cada
   ciclo e o combinava com o `lastSerialNo` persistido do ciclo anterior. Handle novo + cursor
   antigo → `tableData` vazio.
3. **`rowNo` no espaço de índice errado.** `_decode_subscription_batch` passava a posição da
   linha **dentro do lote** para `msg-explain-info`, enquanto o docstring do próprio
   `_fetch_msg_explain_info` documentava que `rowNo` é a posição na página do `query/result`.

**Por que ficou invisível:** `_fetch_msg_explain_info` capturava toda falha em `logger.debug` e
devolvia `None`; `_apply_result` não logava nada. Um ciclo inteiro terminava sem produzir
nenhuma linha de log em nível INFO.

**Correção:** fluxo reescrito para `filter-by-cols` com `msgId` alocado e consumido no mesmo
ciclo; RSRP/RSRQ decodificados localmente do `messageBody` (`core/rrc_decode.py`); log INFO
obrigatório no fim de todo ciclo de VIP, inclusive quando o resultado é zero.

**Regras:**
- Nunca trocar um contrato HTTP externo com base numa captura de **outra task** ou de outro
  modo de operação. Capturar o fluxo da task realmente configurada, do início ao fim.
- Nunca criar fixture inventada para um contrato não capturado. As fixtures
  `vip_subscribe_result.json` e `vip_msg_explain_info.json` eram sintéticas e faziam a suíte
  passar enquanto a produção devolvia zero.
- Todo ciclo de coleta loga o resultado em INFO, inclusive o resultado vazio. Falha de decode
  é `warning`, nunca `debug`.

---

## 2026-08-12 — `filter-by-cols` respondia HTTP 500 com datas vazias

**Sintoma:** `500 Internal Server Error`, corpo
`{"exceptionId":"framwork.remote.SystemError","exceptionType":"ROA_EXFRAME_EXCEPTION",...}`.
Os mesmos 500 apareciam nos logs antigos e levaram à conclusão errada de que o endpoint estava
quebrado — foi o motivo declarado para removê-lo na Fase 3.

**Causa raiz:** `colFilterDto.startTime`/`endTime` enviados como `""`. Mesmo com
`hasStartTime`/`hasEndTime` em `false`, o servidor desserializa os dois campos. Os valores
corretos vêm de `GET /traceresult/query/fetch-field-values?taskId=&msgId=` — passo que existia
no HAR e havia sido ignorado.

**Correção:** `_open_trace_query` passou a ler a janela da task e devolvê-la junto do `msgId`;
`_fetch_meas_report_page` envia as datas reais (método hoje chamado
`_filter_meas_reports`). Teste de regressão:
`test_a_janela_da_task_e_lida_antes_de_filtrar`.

**Regra:** ao reproduzir uma requisição capturada, replicar **todos** os passos que a
antecedem no HAR. Um 500 num endpoint documentado é quase sempre corpo malformado, não
endpoint indisponível — ler o corpo do erro antes de descartar a rota.

---

## 2026-08-12 — Milissegundos do FARS gravados 10× maiores

**Sintoma:** timestamps de VIP com milissegundos errados em ~9% das mensagens (35 de 386).

**Causa raiz:** `_parse_trace_timestamp` fazia `match.group(1).ljust(3, "0")`. O FARS não
zero-preenche o campo: `"(98)"` são 98 ms, mas viravam 980 ms. Provado pela ordem dos seriais na
captura: `12:28:46 (778)` → `12:28:47 (98)` → `12:28:47 (179)`.

**Correção:** `.zfill(3)`.

**Regra:** preenchimento de campo numérico textual é `zfill`, não `ljust`. Quando houver dúvida
sobre a semântica de um campo capturado, usar uma segunda ordenação independente (aqui, o
`serialNo`) para desempatar.


---

## 2026-08-13 — Regional nova coletava ZERO KPI em silêncio (e o cursor avançava)

**Sintoma:** ao trocar o projeto de SantoAmaro (OSS de SP) para Curitiba (OSS 10.220.30.9), os
alarmes vinham normalmente e o monitoring não gravava nada. Nenhum erro, nenhum log: o ciclo
aparecia como `partial`.

**Prova nos bancos:** `data/smart_events_teste-curitiba.db` com `kpi_measurements` = **0 linhas**
e, ao mesmo tempo, 117 checkpoints da task PM 2225 com `cursor` avançando a cada ciclo. Ou seja:
o OSS respondeu, os objetos chegaram, e todos foram descartados.

**Causa raiz:** `_resolve_monitoring_cell` exigia que a tecnologia da célula do evento fosse
IGUAL à da task. A tecnologia da célula é inferida do nome por `_normalize_cell_technology`
(procura os tokens `4G`/`LTE`/`5G`/`NR`/`NCI`). As células que aquele OSS de fato devolve chamam-se
`18NLCTAL01GI` — sem token nenhum → tecnologia `None` → `None != "4G"` → nenhum candidato →
100% não mapeado. Em SP nunca apareceu porque lá as células chamam-se `4G-SPSMG7-18-C`, com o
token no nome. A regional errada no cadastro (`region: "SP"` apontando para a base_url do host
30.9) era um segundo problema real, mas **não** era este: corrigi-la para `OUTRAS` não mudou o
resultado (0 linhas, cursor continuou avançando) — foi o que separou as duas causas.

**Agravante:** `Scheduler._apply_result` grava os checkpoints sempre que `result.cursors` existe,
independentemente de ter havido medição. Como o `preExecTime` limita a próxima consulta, a janela
descartada não volta mais. O dado dos ciclos de teste está perdido.

**Correção:** tecnologia desconhecida (`None`) passa a ser candidata a qualquer task, e a
tecnologia gravada no mapeamento e nas linhas é a da task consultada. Mais `_log_unmapped`, um
WARNING por ciclo com os nomes vindos do OSS e exemplos das células do evento lado a lado.

**Regras:**
- **Heurística sobre nome não pode ser pré-condição de mapeamento.** O token de tecnologia no
  nome da célula é convenção de UM OSS, não contrato. Quando um atributo é inferido, a ausência
  dele tem que significar "desconhecido — não decide", nunca "diferente de tudo".
- **Um filtro `campo == valor` sobre atributo opcional exclui silenciosamente todo `None`.**
  Ao filtrar por atributo inferido, decidir explicitamente o que fazer com o desconhecido.
- **Cobertura zero é falha, não `partial`.** Um ciclo que recebe N objetos e persiste 0 linhas
  precisa dizer isso em voz alta; `coverage.unmapped_cells` era calculado e não era consumido
  por ninguém — diagnóstico que não chega ao operador não existe.
- **Nunca avance cursor sobre dado descartado** (pendente de correção): checkpoint é promessa
  de que o dado foi persistido, e aqui ele estava sendo dado como cumprido sobre o lixo.
