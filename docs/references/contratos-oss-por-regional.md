# Contratos do iManager por regional (SP × OUTRAS)

Referência das diferenças **medidas** entre os dois OSS que o Smart Events acessa. Tudo aqui saiu de
corpo de resposta real — HAR do navegador ou dump do próprio cliente HTTP do aplicativo. Nada foi
inferido a partir de documentação ou de analogia com a outra regional.

| | SP | OUTRAS (Curitiba) |
|---|---|---|
| Host | `10.220.50.9:31943` | `10.220.30.9:31943` |
| Evento de referência | TesteSantoAmaro | Teste Curitiba |
| Task PM | 747 | 2225 (116 objetos) |
| Tasks de Trace | 2072, 2073 | 14837 |

**Regra que vale para os dois:** a regional se detecta por **capacidade/resposta do endpoint**, nunca
por IP no código. Ver `_detect_trace_window` (tenta o síncrono primeiro, cai para o assíncrono em
404/405/500) e o cache `_trace_contract`/`_cache_trace_contract`, chaveado por
`(host, versão da sessão)`, em `core/collector.py`.

---

## 1. Monitoring / PM — a célula tem duas grafias

`POST /rest/oss/access/pm/v1/monitor/task/result`

Dentro de `data[].results[].objRes[].obj`:

| Atributo | SP | OUTRAS |
|---|---|---|
| número do objeto | `objNo` | **`objectNo`** (string, ex.: `"91162"`) |
| nome do objeto | `objName` | **`objectName`** — e `objName` vem **`null`** no mesmo dicionário |

Exemplo real de OUTRAS:

```json
"obj": {
  "objectNo": "91162",
  "objectName": "SR-CTFZ01-eNodeB Function Name=4G-CTFZ01, Local Cell ID=129, Cell Name=4G-CTFZ01-18-I, eNodeB ID=266270, Cell FDD TDD indication=CELL_FDD",
  "objName": null,
  "bamstr": null
}
```

O nome da célula sai de `Cell Name=` dentro dessa string, e casa **exato** com o inventário do
evento (116/116 na task 2225, sem normalização).

**Custo de ter lido só uma grafia:** todo objeto caía em `int(None)` → `TypeError` → contado como
inválido antes de `received`. HTTP 200, 116 objetos por janela, zero medição e zero log por 35
minutos. `_obj_field` (`core/collector.py`) lê as duas; não reduzir a uma só sem recapturar os dois
OSS.

### Igual nos dois

- O eco de cursores no nível da task usa `objNo`: `data[].objNoExecTimes[] = {"objNo", "preExecTime"}`.
- `counterRes` é lista de dicts `{id, name, unit, value, type, reliable}`.
- Descoberta: o navegador abre a task com `[{"taskId": N, "preExecTime": 0}]` — **sem** a chave
  `objNoExecTimes`. O aplicativo faz o mesmo.

### Forma do corpo em OUTRAS

`data[0].results[]` traz **uma janela por `execTime`** (`period: 5`), cada uma com os 116 `objRes`.
O OSS acumula até ~6 janelas, então uma consulta de descoberta devolve 464–696 objetos. A repetição
é esperada e absorvida pela chave única de `kpi_measurements`.

As métricas `ran_rtt` e `terrestrial_rtt` não têm contador na task 2225 e geram `invalid_formula`
em todo ciclo — o ciclo fica `partial` mesmo com dados bons. Não é defeito.

---

## 2. FARS / Trace — contrato síncrono e assíncrono

SP filtra de forma síncrona; OUTRAS usa start + polling. **O delta são dois endpoints**, não a
sequência inteira:

| Passo | SP | OUTRAS |
|---|---|---|
| pre-check | `GET traceresult/pre-check` | igual — `{"checkState":true,"checkResult":[]}` |
| abrir consulta | `GET query/result` com `msgId=1` → `data.msgId` | igual |
| **janela da task** | `GET query/fetch-field-values` | **`GET query/fetch-field`** |
| **filtrar** | `POST query/filter-by-cols` | **`POST query/filter-by-cols-start` + polling em `POST query/filter-by-cols-result`** |
| ordenar | `POST query/sort` → novo `msgId` | igual, **síncrono** |
| paginar | `GET query/result-paging` | igual, **síncrono** |

Não existe `sort-start`/`sort-result` nem `result-paging-start`. Em OUTRAS,
`fetch-field-values` responde **HTTP 500** — é assim que a regional se identifica.

### `fetch-field` é síncrono e basta

```json
{
  "startTime": "2026-08-13 09:37:02",
  "endTime": "2026-08-13 18:15:04",
  "filterMap": {"Trace Type": null, "Message Direction": null, "Call ID": null,
                "Mode": null, "Cell ID": null, "Source": null, "Message Type": null},
  "signalList": []
}
```

Mesmas chaves e mesmo formato do `fetch-field-values` de SP. O coletor só usa `startTime`/`endTime`.

O navegador faz polling em `GET query/fetch-field-values-result` (`{"process":10,"filterMap":{}}`)
logo depois, **mas só para preencher os combos de filtro da tela**. O aplicativo filtra por um valor
fixo (`RRC_MEAS_RPRT`) e nunca lê `filterMap`. **Esse polling não deve ser implementado** — o plano
original mandava fazê-lo e era trabalho perdido.

### O corpo do filtro é idêntico nos dois contratos

Só a URL muda. `pageDto.msgId` é o handle aberto em `query/result`:

```json
{
  "colFilterDto": {
    "colFltExpSeq": [{"fieldId": "Message Type", "value": "RRC_MEAS_RPRT", "operator": {"op": 0}}],
    "signalList": [], "hasStartTime": false, "startTime": "2026-08-13 09:37:02",
    "hasEndTime": false, "endTime": "2026-08-13 18:15:04", "isReverse": false
  },
  "pageDto": {
    "sqlColumnName": "", "isAscend": "", "taskId": 14837, "msgId": 325008,
    "comparisonMsgId": -1, "startRow": 0, "pageSize": 1000,
    "templateName": [], "isSetBenchMarkTime": false, "benchMarkTimeRowNo": -1
  }
}
```

`startTime`/`endTime` precisam vir preenchidos mesmo com `hasStartTime`/`hasEndTime` em `false`: o
servidor desserializa os dois campos e responde 500 se vierem vazios (ERRORS.md, 12/08).

### Polling do filtro

`POST query/filter-by-cols-result`, corpo `{"msgId": <o msgId de entrada, não um novo>}`.
Envelopes de progresso capturados, ~0,6 a 1,0 s de intervalo:

```json
{"status":1,"errorMsg":null,"progress":10,"value":null}
{"status":1,"errorMsg":null,"progress":65,"value":null}
{"status":1,"errorMsg":null,"progress":75,"value":null}
```

O envelope de **conclusão** nunca foi capturado byte a byte — o DevTools já havia descartado o corpo
e o dump forçado do aplicativo nunca disparou, porque a leitura tolerante reconheceu o handle de
primeira. O que está confirmado em produção:

- conclusão = `value` deixou de ser `null` **ou** `status` saiu de `1`;
- erro = `errorMsg` não nulo;
- o envelope de conclusão carrega o `msgId` filtrado e um `recordCount` — ambos são achados por
  busca recursiva (`_find_async_msg_id`, `_find_async_record_count`);
- se nenhum `msgId` for reconhecido, o ciclo **falha e grava o envelope** em `data/diagnostics/`;
  seguir para o `sort` com o handle antigo devolveria o conjunto não filtrado.

Encadeamento de handles observado: `325008` (aberto) → filtro → `328008` → sort → `329008` → páginas.
Cada passo que muda o conjunto materializa um snapshot novo.

Parâmetros em uso (`core/collector.py`): poll a cada `0.5 s`, prazo total `120 s`, página de `1000`,
bootstrap de `100`. O filtro de 388.997 mensagens levou ~3,5 s na captura.

### `null` onde SP manda número

Página vazia em OUTRAS:

```json
{"recordCount":null,"data":{"lastSerialNo":null,"msgId":328008,"tableData":[],"serialNo":null}}
```

`recordCount` vem `null`, não `0`. Qualquer leitura precisa de `int(... or 0)`. Atenção especial ao
early-return do bootstrap em `_open_trace_query`: tratar `null` como `0` ali transformaria uma task
cheia em "ciclo vazio" silencioso.

### Decodificação

Em OUTRAS, 5 a 7% das mensagens `RRC_MEAS_RPRT` voltam em formato que `decode_meas_report` não
reconhece (ex.: 327 de 5.000 num ciclo, 261 de 5.000 no seguinte). O cursor avança sobre elas de
propósito — `decode_meas_report` é função pura dos bytes, então uma mensagem que falha hoje falha
sempre, e parar ali travaria a coleta. A contagem vai para o log e para os diagnósticos.

---

## 3. O padrão, e como não repetir o prejuízo

As duas falhas que custaram dias tiveram a mesma forma: **um campo com grafia diferente entre as
regionais, lido por um só nome, falhando em silêncio.** Não foi sessão, não foi rede, não foi
mapeamento de célula.

Ao integrar qualquer endpoint novo, ou a mesma rota num OSS novo:

1. **Copiar o contrato do tráfego real, campo a campo.** Chave ausente ≠ chave com lista vazia ≠
   chave com `null`. Um OSS aceitar as duas formas não prova que o outro aceite.
2. **Gravar o corpo real antes de confiar no parser.** `_dump_raw` + o dump automático dos ciclos
   sem medição (`MONITORING_AUTO_DUMPS = 3`) existem para isso.
3. **Logar um resumo por ciclo, mesmo no caminho feliz.** Foi a ausência disso que tornou o KPI o
   único coletor impossível de diagnosticar em campo.
4. **Fixture entra pelo mesmo ponto que a resposta real entra.** Um teste que faz `obj["objectNo"]`
   na própria linha e chama o resolvedor nunca reprova quem lê `obj["objNo"]` — foi exatamente o
   que deixou a suíte verde com a produção em zero.
5. **Contadores antes da conversão.** `received` incrementado só depois de `int(obj_no)` mostra
   `0` justamente quando o corpo veio cheio.

---

## Fontes

- `har-oss-outros/har-monitoring-oss-tsl.har` — abertura e resultado da PM 2225.
- `har-oss-outros/har-vips-oss-tsl.har` — 83 entradas do fluxo de Trace da task 14837.
- `data/diagnostics/monitoring_10.220.30.9_*.json` — corpos reais de PM em OUTRAS.
- `data/logs/smart_events.log` — ciclos ao vivo das duas regionais.
- `docs/plans/2026-08-13-001-fix-multi-regional-collection-plan.md` — plano das fases 1 a 6.
- `ERRORS.md` (12–14/08) e `MEMORY.md` (14/08).

Validado em produção em 14/08: KPIs e VIPs coletando nas duas regionais
(`[vip] contrato=assincrono host=10.220.30.9`, task 14837 com `recordCount=16712`,
`linhas_lidas=5000`; PM 2225 com `recebidos=232 mapeados=232 nao_mapeados=0 linhas=2593`).
