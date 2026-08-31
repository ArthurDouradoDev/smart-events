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

---

## 2026-08-21 — `results[].period` do Monitoring tratado como Granularity Period

**Como apareceu:** o painel 5G exibia `Availability` como uma linha reta em 20%, abaixo do
threshold, por nove dias. Nenhum erro, nenhum log, nenhum ciclo em falha — o número estava
lá, redondo e plausível o bastante para ninguém conferir.

**Causa raiz:** `_parse_monitoring_response` lia `result.get("period")` e passava esse valor
às fórmulas como o Granularity Period em minutos. O campo tem nome plausível e tipo
plausível, mas o OSS devolve `5` para tasks cujo GP é de 1 minuto. `N.Cell.Avail.Dur = 60`
dividido por `5 × 60 = 300` dá exatamente 20%. O 4G não denunciou o erro porque sua fórmula
é `1 − indisponível/período`: com `Unavail.Dur = 0`, qualquer período dá 100%.

**O que havia para conferir e não foi conferido:** o export CSV da mesma task traz a coluna
`Period(minute) = 1`, o `execTime` avança de 60 em 60 segundos e `N.Cell.Avail.Dur` satura
em 60. Três contadores independentes contradiziam o campo, e o teste existente
(`test_availability_requires_the_period_returned_by_oss`) **fixava o comportamento errado no
próprio nome**.

**Correção:** o GP passou a vir da configuração da task (`period_seconds`, default 60). O
`period` da resposta continua sendo lido, mas só para um aviso de divergência.

**Regra:** campo do OSS com nome plausível não é contrato. Antes de usá-lo em fórmula,
validar contra um contador que o denuncie — um que sature ou que tenha unidade conhecida.
E teste cujo nome afirma um comportamento externo ("o período que o OSS devolve") está
documentando uma suposição, não travando um requisito.

---

## 2026-08-21 — `N.ThpTime.*` recebeu unidade por suposição e o throughput 5G saiu 1000× menor

**Como apareceu:** o painel de Throughput 5G marcava picos de ~1,0 onde o 4G, com menos
tráfego, marcava 6,8. `throughput_ul` chegou a ficar com `production_ready=False` e
`unit="unidade OSS pendente"` — a dúvida foi registrada e depois esquecida em produção.

**Causa raiz:** o OSS devolve `N.ThpTime.DL.RmvLastSlot` com `unit` vazio. A fórmula
assumiu que a divisão `kbit/tempo` já saía em Mbit/s, o que só valeria se o contador
estivesse em milissegundos. Ele está em **microssegundos**: `36.079.000` num período de
60 s são 36,1 s de tempo escalonado (plausível), enquanto em ms seriam 10 horas e em
segundos, 417 dias.

**Correção:** `THP_TIME_TO_SECONDS = 1e-6`, nomeada e comentada com a evidência, mais
`check_throughput_floor` — o resultado é conferido contra `(volume − descontado)/período` a
cada cálculo, e a violação vira o diagnóstico `unit_suspect` em vez de número publicado em
silêncio.

**Regra:** contador sem unidade declarada não pode receber unidade por suposição. Quando a
unidade só puder ser inferida, transformar a inferência em constante nomeada **e** instalar
uma trava aritmética que a verifique em cada cálculo — o piso vem de uma invariante do
próprio dado (aqui: tempo escalonado ≤ período), não de um valor esperado. Uma dúvida
anotada num campo de metadado (`production_ready=False`) não impede o número errado de
chegar à tela.

---

## 2026-08-21 — `chart.draw()` apagava as séries dos nove painéis da visão geral

**Como apareceu:** ao tirar o primeiro screenshot da visão geral com vários clusters, os nove
cards mostravam grade, eixos e as linhas de threshold — e **nenhuma série**. Os dados estavam
todos lá: `chart.data.datasets[n].data` com 61 pontos, `isDatasetVisible` true, os elementos
posicionados dentro de `chartArea`. Só não apareciam depois que o mouse saía de um canvas.

**Causa raiz:** `_syncCrosshairs` e `_clearHover` repintavam com `chart.draw()`. No Chart.js
4.4 `draw()` limpa o canvas e repinta apenas as camadas pendentes; usado sozinho, fora do
ciclo de render, deixa o canvas com tudo menos os datasets. O método que repinta o gráfico
inteiro é `chart.render()` — com `animation: false` ele é igualmente imediato. O defeito era
anterior a esta mudança e passava despercebido porque nenhum teste olhava o pixel.

**Regra:** para repintar um Chart.js fora do ciclo dele, use `render()`, não `draw()`. E
gráfico não se dá por pronto com asserção sobre `chart.data` — o estado do modelo pode estar
perfeito com o canvas vazio. Olhar o desenho (screenshot) é parte do teste.

---

## 2026-08-21 — Mock do bridge dava uma grade de tempo por escopo e fragmentava as linhas

**Como apareceu:** logo depois do defeito acima, comparando dois clusters, as linhas
continuavam invisíveis. O elemento de linha tinha `segments: 61` para 61 pontos — ou seja,
61 segmentos de comprimento zero.

**Causa raiz:** a comparação faz **uma chamada por escopo**, e o mock
`get_kpi_overview` montava os labels a partir de `Date.now()` **cru**. Duas chamadas
separadas por poucos milissegundos produziam timestamps diferentes; a união virava 122
labels e cada série ficava com valor sim, valor não. Com `spanGaps: false` — correto — a
linha virava pó.

**Correção:** o mock passou a alinhar a grade ao minuto
(`Math.floor(Date.now() / 60000) * 60000`). O backend real já é alinhado pelo ciclo de
coleta. O teste `test_comparar_todos_os_clusters_gera_uma_serie_por_cluster` agora exige
zero `null` nas séries do mock.

**Regra:** dado sintético que alimenta uma **união de escopos** precisa da mesma âncora
temporal em todas as chamadas. Mock com relógio livre não é "aproximadamente igual" ao
real — ele fabrica um modo de falha que o real não tem, e some com a evidência do bug
verdadeiro.

---

## 2026-08-24 — Build rodado com o Python errado: `.build-tools\Python312` em vez de `.venv-build`

**Como apareceu:** primeira tentativa de `build.py --profile roadshow-salvador` morreu em
`require_locked_dependencies` com **50 pacotes "ausente"** — a lista inteira do
`requirements-build.lock`, incluindo o próprio PyInstaller.

**Causa raiz:** existem dois interpretadores 3.12 no repositório e eu escolhi o errado.
`.build-tools\Python312\python.exe` é o **interpretador base**, sem dependências instaladas;
quem tem o lock aplicado é o venv `.venv-build\Scripts\python.exe`, criado a partir dele
(`.venv-build/pyvenv.cfg` aponta para lá como `home`). "50 de 50 ausentes" não é ambiente
desatualizado — é ambiente **vazio**, sinal de interpretador trocado, não de lock furado.
A tentação de "consertar" com `pip install -r` teria poluído o Python base e mascarado o erro.

**Correção:** `& ".venv-build\Scripts\python.exe" build.py --profile <perfil>`, com
`$env:PLAYWRIGHT_BROWSERS_PATH = "$env:LOCALAPPDATA\ms-playwright"` — a variável não está
persistida no ambiente do usuário e `build.py:229-231` aborta sem ela.

**Regra:** o build do instalador roda **sempre** por `.venv-build\Scripts\python.exe`.
`.build-tools\Python312` só existe para criar esse venv. Um `require_*` reprovando a lista
toda de uma vez é diagnóstico de interpretador errado — verificar `pyvenv.cfg` antes de
mexer em dependência.

---

## 2026-08-24 — `definition(metric, None)` casa com a primeira tecnologia e converteria linha legada

**Como apareceu:** ao escrever `to_canonical` na Fase 3, a versão natural seria
`item = definition(metric, technology)` e pronto. Pego na revisão, **antes de rodar** — não
chegou a virar dado errado em tela, mas passaria em todo teste que usasse evento novo.

**Causa raiz:** `definition(metric, technology)` já existia com a tecnologia **opcional**, e
com `technology=None` ela devolve a **primeira** entrada do `CATALOG` com aquele `metric` —
não `None`. As linhas sem tecnologia são justamente as dos bancos anteriores à coluna
(`smart_events_vips-rio-tim-jun-2026.db`, 1.011.298 linhas com volume em **MB**). Sem guard
explícito, cada uma dessas linhas receberia o fator da primeira tecnologia catalogada e o
volume em MB sairia multiplicado como se fosse bit — errado por 10⁶, e **silenciosamente**,
porque o valor continua sendo um número plausível num gráfico.

**Correção:** `if not technology: return value` como primeira linha de `to_canonical`, com o
motivo no docstring, e um teste que fixa os dois casos vazios (`None` e `""`) —
`test_to_canonical_leaves_unknown_technology_untouched`.

**Regra:** helper de lookup com parâmetro opcional que significa "qualquer um" não pode ser
reusado num caminho onde a ausência significa "não sei". São semânticas opostas — "curinga" e
"desconhecido" — e o mesmo `None` expressa as duas. Ao reaproveitar um lookup assim, tratar a
ausência **antes** de chamá-lo, nunca esperar que ele devolva `None`.

---

## 2026-08-24 — `Number(null)` é 0 e fazia o buraco de coleta derrubar o degrau da escala

**Como apareceu:** primeira execução de `tests/test_frontend_units.py` na Fase 4. Onze testes
passaram e só `test_painel_sem_pontos_nao_troca_o_degrau` falhou: uma série de `2e9 bit`
(escala `Gbit`) que passava a chegar toda em `null` voltava rotulada como `bit`, não `Gbit`.

**Causa raiz:** o seletor do maior valor da série filtrava por `Number.isFinite(Number(item))`,
e `Number(null)` é **0** — finito. Um buraco de coleta entrava na conta como zero legítimo, o
maior valor virava 0 e a histerese descia todos os degraus de uma vez. Em produção isso não
apareceria como número errado (o valor exibido continua correto), mas como **unidade piscando
no cabeçalho** a cada minuto sem dado — exatamente a instabilidade que a escala fixa por
métrica existe para eliminar. Vale para `undefined` e `""` pelo mesmo motivo: `Number("")` é 0.

**Correção:** descartar `item == null || item === ""` **antes** de converter, em
`_maiorAbsoluto` (`frontend/js/units.js`), com o motivo no comentário. Série sem nenhum ponto
devolve `null` e o degrau anterior é mantido.

**Regra:** em JS, `Number.isFinite(Number(x))` **não** é teste de "x é um número": `null`, `""`
e `[]` passam como 0 e `false` passa como 0. Ao decidir qualquer coisa a partir de uma série
que representa tempo (onde ausência é um valor de primeira classe), descartar a ausência
explicitamente antes da conversão — e escrever o teste com a série toda vazia, que é o caso
que o dado de mock quase nunca produz.

---

## 2026-08-25 — Reusar `_refreshScopeData()` na troca de aba 4G/5G reintroduzia escopo já limpo

**Como apareceu:** ao adicionar `scope="cell"` na visão geral, células precisam ser
rebuscadas a cada troca de aba 4G/5G (são específicas de família; site e cluster não). O
jeito óbvio foi chamar `_refreshScopeData()` (já buscava sites/clusters/células) também no
clique da aba de tecnologia. `pytest tests/test_frontend_kpi_overview_ui.py` pegou o problema
na hora: 5 dos 11 testes travaram em `page.wait_for_function` esperando 4 datasets num painel
pareado com 2 clusters selecionados — vinham 6.

**Causa raiz:** `_refreshScopeData()` tem uma reseed embutida — `if (_pendingSeed ||
!_selectionSize()) { _applyPreferredSelection(); }` — pensada para quando a visão geral abre
com a comparação vazia. `_applyPreferredSelection()` herda `State.selectedSite` (o site
selecionado na barra lateral do dashboard, sem relação com a comparação da visão geral). Os
testes limpam a seleção da visão geral e então trocam de aba; nesse instante
`_selectionSize()` é 0, então a troca de aba disparava a reseed e reintroduzia o site da
barra lateral (um site só-4G) como 3º escopo dentro da comparação em 5G — 3 escopos × 2
(DL/UL) = 6 datasets, todos os testes que faziam "limpar → trocar aba → escolher cluster"
travavam esperando 4.

**Correção:** extraído `_refreshCellsForFamily()` — só busca `get_event_cells` e purga da
seleção as células que sumiram na família nova, sem tocar em site/cluster nem chamar
`_applyPreferredSelection()`. A troca de aba passou a chamar essa função, não
`_refreshScopeData()` inteiro.

**Regra:** uma função que reseed-a "se a seleção estiver vazia" não pode ser reusada por um
caminho que só precisa atualizar *uma parte* do estado — reusar o todo por conveniência
reintroduz o efeito colateral (a reseed) em contextos onde "seleção vazia" é intencional, não
um estado inicial. Extrair a fatia específica do refresh é mais barato que o bug. E: quando o
projeto já tem uma suite Playwright cobrindo a superfície mexida, rodá-la é o jeito de pegar
isso — os testes de API sozinhos (backend) não veem timing/estado assíncrono de UI.

---

## 2026-08-26 — Alterações no frontend não apareciam nem após reiniciar `python main.py`

**Como apareceu:** usuário reportou que um botão novo em `frontend/index.html`
(`popup-chart-clear-cells`) não aparecia no app, mesmo depois de fechar e reabrir o `python
main.py` várias vezes. O código em disco estava correto (`node --check` limpo, HTML
verificado à mão).

**Causa raiz:** a janela roda em WebView2, e `main.py` fixa `storage_path` em
`data/webview` com `private_mode=False` (proposital — evita relogar a cada fechamento). Isso
persiste **todo** o profile do WebView2 entre execuções, inclusive o cache HTTP em disco
(`data/EBWebView/Default/Cache`), não só cookies/sessão. O servidor local (`webview/http.py`
do pywebview, via Bottle) tenta desabilitar cache manualmente com
`bottle.response.set_header('Cache-Control', 'no-cache', ...)`, mas a rota devolve
`bottle.static_file(...)` — um `HTTPResponse` próprio que não herda os headers setados no
`bottle.response` global, então o cache nunca é de fato desabilitado. Resultado: o WebView2
guarda `index.html`, `main.css` e qualquer asset sem query string de versão e não busca de
novo por dias (confirmado: arquivos de cache com mtime de 10 dias antes do teste), mesmo com o
servidor local sempre servindo o conteúdo atual do disco quando consultado direto (via
`curl`).

**Como foi confirmado:** subiu uma instância com `SMARTEVENTS_DATA_DIR` apontando para uma
pasta de dados nova (profile do WebView2 limpo) e o botão apareceu — provando que o código
estava certo e o problema era só o cache persistido. Inspeção via CDP
(`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=...`) confirmou o DOM real
sem o botão na pasta de dados antiga.

**Correção:** `main.py` agora seta
`os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--disk-cache-size=1")` antes
de criar a janela — reduz o cache HTTP do WebView2 a praticamente zero, forçando busca no
disco a cada carga, sem tocar em cookies/Local Storage (guardados em outro lugar do profile,
a sessão de login continua persistindo). Testado com a pasta `data/webview` real (já com
cache de dias) e o conteúdo atualizado passou a aparecer.

**Regra:** `storage_path` persistente no WebView2 persiste o profile inteiro, não só o que se
quer manter (sessão). Qualquer arquivo servido sem query string de cache-busting
(`index.html`, `main.css`, `lib/*.js`) pode ficar preso em cache por tempo indefinido depois
dessa mudança — ao depurar "editei mas não aparece" neste app, isso é suspeito #1 antes de
desconfiar do código.

---

## 2026-08-31 — Janela maximizada abria "torta", com ~30% do app fora da tela

**Como apareceu:** com a barra HTML ativa (`--custom-titlebar`), mover a janela para o
monitor secundário com `Win+Shift+seta` a deixava pequena; o duplo clique na barra
"maximizava" para um retângulo maior que o monitor — cerca de 70% do app ocupando a tela
inteira, com a barra de controles e o painel de VIPs para fora à direita e o rodapé cortado.

**Causa raiz (duas, somadas):**

1. **Espaços de coordenadas divergentes.** O pywebview chama `SetProcessDPIAware()`
   (*System* DPI aware) ao iniciar. Com monitores de escalas diferentes (notebook 144 DPI +
   externo 96 DPI), o Windows *virtualiza* a janela no monitor secundário: `GetWindowRect`,
   `GetMonitorInfoW` e o `WM_NCHITTEST` respondiam no espaço do DPI do sistema, enquanto o
   WebView2 — processo próprio, per-monitor aware — desenhava no DPI real do monitor. O
   retângulo calculado para maximizar estava certo num espaço e errado no outro.
2. **`prepare_maximized_drag` reposicionava a janela à mão** antes de entrar no move loop
   nativo (restaurar → ancorar sob o cursor → arrastar → re-maximizar). Feita com as
   coordenadas divergentes acima, era ela quem produzia o salto e a re-maximização no
   retângulo errado.

**Correção:** o processo declara `DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2` **antes** de
importar/subir o pywebview (a consciência de DPI só pode ser definida uma vez; quem chama
primeiro vence), e `prepare_maximized_drag` foi removido — o move loop do Windows já faz
drag-to-restore sob o cursor e Aero Snap no monitor de destino, sem ajuda.

**Regra:** neste app, **nenhuma** conta de geometria Win32 é confiável enquanto o processo for
System DPI aware e houver mais de um monitor com escalas diferentes. Antes de suspeitar da
aritmética de retângulos, confirmar no log qual DPI o `attach` reportou (`Window chrome
anexado | dpi=…`): 96 num monitor a 150% é o sintoma. E não reimplementar comportamento que o
move loop nativo já entrega — `WM_NCLBUTTONDOWN`/`HTCAPTION` é o ponto de entrada, o resto é
do Windows.
