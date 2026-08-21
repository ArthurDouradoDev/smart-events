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

---

## 2026-08-13 — Fase 4 aplicada, KPI continua vazio e o log não diz por quê

**Sintoma:** depois da troca para Teste Curitiba (21:44), o log tem ciclo de VIP e de alarmes a
cada intervalo, e **nenhuma linha de Monitoring** por 35 minutos. `kpi_measurements` do evento
continua em 0 e nenhum checkpoint novo foi gravado.

**Causa raiz do "não sei":** `_collect_kpis_v2` não emitia nenhum log por ciclo. O único log do
caminho era `_log_unmapped`, que só dispara com `unmapped > 0`. Um ciclo que responde HTTP 200 sem
objetos, um ciclo sem task PM configurada e uma thread travada produzem exatamente a mesma
evidência: silêncio. O critério de saída da Fase 4 ("HTTP 200 no log; recebidos > 0, mapeados > 0")
exigia esse log e ele nunca existiu.

**Causa raiz do "zero KPI" (confirmada pelo dump):** com o log e o dump no ar, o ciclo apareceu como
`HTTP=200 recebidos=0 nao_mapeados=0 invalidos=464 linhas=0`. No corpo real, o OSS de Curitiba
identifica a célula assim:

```json
"obj": {"objectNo": "91162", "objectName": "...Cell Name=4G-CTFZ01-18-I...", "objName": null}
```

`_parse_monitoring_response` lia `objNo`/`objName`. Em Curitiba a primeira grafia não existe e a
segunda vem **explicitamente `null`** no mesmo dicionário: todo objeto caía em `int(None)` →
`TypeError` → `invalid += 1`, e `received` (incrementado só depois da conversão) ficava em zero.
116 objetos por janela × N janelas acumuladas viravam 232, 464, 696 "inválidos" por ciclo.

**Por que a suíte não pegou:** `test_os_116_objetos_reais_da_task_2225_mapeiam_116_de_116` lê
`obj["objectNo"]` do HAR **na própria linha do teste** e chama `_resolve_monitoring_cell` direto.
Ele provou o resolvedor com argumentos já corretos; ninguém exercitava o parser com a grafia real.

**Correção:** `_obj_field` aceita as duas grafias no parser; log por ciclo de Monitoring
(tasks/modo, HTTP, recebidos, mapeados, não mapeados, inválidos, linhas, cursores); dump automático
do corpo em `data/diagnostics/` nos ciclos sem medição (limitado a `MONITORING_AUTO_DUMPS` por
processo); `objNoExecTimes` omitido na descoberta, igual ao navegador (alinhamento de contrato — não
foi comprovado como bloqueio). Replay do dump real: 464 recebidos, 0 não mapeados, 5.124 linhas.

**Regras:**
- **Todo coletor loga um resumo por ciclo, mesmo no caminho feliz.** VIP e alarmes já faziam; KPI
  não fazia, e por isso foi o único que ficou impossível de diagnosticar em campo.
- **Contrato se copia do tráfego real, campo a campo.** Chave ausente ≠ chave com lista vazia; o
  fato de um OSS aceitar as duas formas não prova que o outro aceite.
- **Fase que só pode ser validada por log precisa entregar o log junto.** Marcar a fase como
  concluída sem a instrumentação do próprio critério de saída empurra o custo para o teste ao vivo.
- **Teste que desembrulha o dado na própria linha não testa o desembrulho.** Se o teste faz
  `obj["objectNo"]` para chamar a função, ele nunca vai reprovar quem lê `obj["objNo"]`. Fixture
  tem que entrar pelo mesmo ponto que a resposta do OSS entra.
- **`received` só conta depois da conversão.** Um contador de "recebidos" posterior ao parse do
  identificador mostra 0 justamente quando o corpo veio cheio; `invalid` era o único número que
  crescia e não estava no painel.

---

## 2026-08-14 — Checkpoints cruzados e fallback SP podiam contaminar outra regional

**Sintoma:** bancos de evento continham task VIP de SP sob `OUTRAS`, task PM de Curitiba sob `SP`
e logs históricos de `database is locked`. Um evento sem cliente/regional também podia listar
todos os VIPs ou resolver silenciosamente o host padrão de SP.

**Causas:** estado legado contaminado não tinha auditoria baseada no inventário do evento; escritas
em lote podiam lançar exceção antes do `commit` sem rollback explícito; `resolve_base_url` devolvia
SP como último recurso; `get_event_vips` montava a consulta sem filtros quando a identidade faltava.

**Correção:** auditoria somente leitura classifica incompatibilidade de evento, OSS, coletor e task.
A aplicação exige hash do relatório, evento encerrado, backup consistente e correspondência exata
da linha antes do `DELETE`. Escritas do caminho de coleta fazem rollback garantido. Resolução de
host e VIPs agora falha fechado. O status expõe regional/host/contrato FARS e detalhes por task.

**Regra:** uma lista vazia por configuração ausente não é sucesso operacional; não capturar a
exceção e fingir “nenhum VIP configurado”. Uma limpeza reproduzível sempre parte de relatório
imutável e backup, nunca de IDs digitados diretamente num `DELETE`.

---

## 2026-08-19 — O badge de alarme era cortado e o teste de reatividade passava sozinho

**Sintoma:** o triângulo de alarme aparecia recortado no zoom máximo. E, ao remover de propósito
`State.on("change:alarms", ...)` do `map.js`, o teste que deveria travar essa regressão continuava
passando — às vezes.

**Causas:** (1) o SVG do badge era dimensionado por `offset + raio do VIP + 2`, mas o triângulo é
mais largo que o círculo quando `scale = 2` (zoom ≥ 18) — a extensão maior é que manda no `viewBox`.
(2) O `fitBounds` da abertura dispara um `zoomend`, e o handler de `zoomend` re-renderiza todos os
marcadores; se o teste publicasse `State.alarms` antes de o mapa assentar, esse re-render acidental
criava o badge e o teste passava sem o listener.

**Correção:** o `viewBox` passou a usar `max` das extensões dos dois badges; o teste espera o mapa
assentar (`leaflet-zoom-anim` fora + folga) antes de publicar o estado. Verificado nos dois
sentidos: com a correção revertida, cada teste falha 3/3.

**Regra:** teste de reatividade em mapa tem que rodar com o mapa parado — qualquer animação
pendente do Leaflet re-renderiza tudo e valida o listener que não existe. E ícone com dois
elementos em lados opostos dimensiona pela maior extensão, nunca pela do primeiro que foi escrito.

---

## 2026-08-20 — Fixture do VIP dependia da hora do relógio

**Sintoma:** `test_vip_modal_time_windows_switch_without_leaking_state` falhava 3/3 rodando à
00:24 e passava durante o dia. Reproduzia em `HEAD` limpo — nada a ver com a mudança em curso.

**Causa:** `_mockVipSeriesRows` gerava 7 pontos com passo fixo de 20 min a partir de agora, e a
janela "Hoje" do modal corta em 00:00 local. Antes das 2h20 a fixture inteira caía no dia anterior;
o gráfico abria vazio e o `#vip-modal-chart-wrapper` nunca ficava visível.

**Correção:** o passo virou `min(20 min, max(1, elapsed_desde_meia_noite / 7))`. A série cabe no
dia corrente em qualquer horário e, depois das 2h20, o espaçamento é o mesmo de antes.

**Regra:** fixture de tempo que alimenta uma janela ancorada em meia-noite tem que ser gerada a
partir da meia-noite, não de `Date.now()` para trás. Suíte que só passa em parte do dia é suíte
que não reprova quando deveria.

---

## 2026-08-20 — "Parcial" escondia que a task do OSS estava parada há 3 dias

**Sintoma:** painel do evento de Curitiba (OSS OUTRAS) em `Parcial`, com
`recebidos 0 · mapeados 0 · descartados 0`, sessão ativa e alarmes coletando normalmente.
A leitura natural — e errada — é que a coleta quebrou no app.

**Causa:** a task PM 2225 não executa no OSS desde 16/08 12:54, e a task de trace 14837 não
gera registro desde 14/08 19:57. O OSS diz isso na própria resposta (`state: -1`,
`results: []`, `execTime` de 4 dias atrás), mas o coletor descartava essa informação e
devolvia a causa genérica "Cobertura ou fórmulas de Monitoring parciais.", que o frontend
nem sequer exibia — `_syncHint` tinha a frase "Coleta parcial" fixa no código.

**Correção:** `_parse_monitoring_response` registra as tasks que voltaram sem `results` com
o `execTime` que o OSS reporta; `_describe_idle_tasks` transforma isso em causa datada; o
painel passa a exibir `s.cause`. Sem falso positivo na descoberta: exige `recebidos == 0`
**e** `execTime` presente.

**Regra:** quando o OSS responde 200 e o ciclo não produz medição, a resposta quase sempre
já contém a causa — ler `state`/`execTime` antes de classificar como "parcial". Um status
que não distingue "a task parou lá" de "o app quebrou aqui" faz o operador procurar o
problema no lugar errado. E antes de acusar mudança de código, comparar a data em que o dado
parou com a data do commit: aqui a coleta parou 4 dias antes do commit suspeito, e o outro
OSS seguia coletando com o mesmo código.

---

## 2026-08-20 — Mock de `get_kpi_series` sempre lia `technology_family` como `null`

**Como apareceu:** não em produção — achado ao estender `bridge.js` para o escopo de
cluster (Fase 4), que precisava adicionar `scope`/`scope_id` no fim da lista de
argumentos posicionais.

**Causa raiz:** `API.getKpiSeries` monta a chamada real como
`API.call("get_kpi_series", eventId, siteId, m, w, cellId, null, technologyFamily)` —
os 7 argumentos posicionais batem exatamente com a assinatura Python
`(event_id, site_id, metric, minutes, cell_id, technology, technology_family)`. Mas o
`_mock.get_kpi_series` só declarava 6 parâmetros
`(event_id, site_id, metric, minutes, cell_id=null, technology_family=null)` — faltava o
parâmetro `technology` no meio. Isso deslocava tudo uma posição: `technology_family`
no mock recebia sempre `args[5]`, que é o `null` fixo da chamada real (o campo
`technology`, nunca usado por essa shortcut), e o valor de `technologyFamily` que o
usuário realmente escolheu (`args[6]`) caía num parâmetro a mais, descartado.

**Por que não quebrava nada visível:** o filtro de tecnologia em modo mock nunca
recortava por família de verdade (sempre devolvia `family = null`), mas como o mock
mistura 4G/5G nas mesmas séries mesmo sem filtro, nenhum teste dependia de ver o
recorte aplicado — o sintoma (filtro sem efeito) nunca foi verificado, só o formato
geral da resposta.

**Correção:** o parâmetro que faltava foi inserido na posição certa
(`technology=null` antes de `technology_family=null`), o que também deixou `scope`/
`scope_id` (adicionados no fim para o escopo de cluster) nas posições corretas.

**Regra:** ao adicionar uma função mock que espelha uma chamada posicional real, contar
os argumentos da chamada de verdade um a um — um parâmetro nomeado errado no meio da
lista não dá erro de sintaxe, só desloca silenciosamente todo argumento posterior.

