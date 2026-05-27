# Plano de Execução — Coleta de VIPs: Diagnóstico, Correções e Gráfico Histórico

> **Data:** 2026-05-27  
> **Escopo:** `core/collector.py`, `core/database.py`, `frontend/js/vip.js`, `frontend/index.html`

---

## 1. Diagnóstico: Problemas Identificados

Foram identificados **três problemas distintos** com causas raiz independentes, mas que se somam para gerar a percepção de "VIPs sem dados":

---

### Problema A — Dados Desatualizados (stale trace data)

**Sintoma:** Ao clicar num VIP, os dados RSRP/RSRQ exibidos são de horas atrás, mesmo o trace estando ativo no iManager.

**Causa raiz:** O fluxo atual de coleta usa `/query/result` com `msgId=1`, que devolve as mensagens na ordem padrão de inserção — **do mais antigo ao mais recente**. O limite `_MAX_MEAS_DECODES_PER_CYCLE = 50` faz com que decodifiquemos via `msg-explain-info` apenas as **50 primeiras** mensagens `RRC_MEAS_RPRT` da página. Numa task de Signaling Trace ativa há horas ou dias, essas 50 mensagens são as **mais antigas** do histórico, e não as mais recentes.

**Evidência no código (`collector.py:619-626`):**
```python
result_resp = s.get(result_url, params={
    "startRow": 0,
    "pageSize": self._TRACE_PAGE_SIZE,  # 500 mensagens, todas os tipos
    "taskId": task_id,
    "msgId": 1,                          # ← sem filtro de tipo, ordem padrão
    ...
})
```
E em `_parse_trace_response`:
```python
for idx, item in enumerate(table):      # itera em ordem de inserção (antiga→nova)
    ...
    if msg_type != "RRC_MEAS_RPRT":
        continue                        # descarta outros tipos no loop
    if decodes_used >= self._MAX_MEAS_DECODES_PER_CYCLE:
        break                           # para nos primeiros 50 encontrados
```

---

### Problema B — "Sem medições coletadas para este VIP" no modal

**Sintoma:** O card do VIP às vezes mostra RSRP/RSRQ (dados do `get_vip_latest`), mas ao clicar no VIP o gráfico exibe "Sem medições coletadas". 

**Causa raiz primária — Janela temporal no `get_vip_series`:**

`get_vip_series` em `database.py:582-591` filtra por `timestamp >= now - 60 minutos`:

```python
rows = conn.execute("""
    SELECT timestamp, rsrp, rsrq, serving_cell, in_event
    FROM vip_measurements
    WHERE event_id = ? AND vip_name = ?
      AND timestamp >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' minutes')
    ORDER BY timestamp ASC
""", (event_id, vip_name, f"-{minutes}")).fetchall()
```

O `strftime('now')` do SQLite é sempre **UTC**. O iManager do cliente retorna timestamps em **horário local (America/Sao_Paulo = UTC-3)**, conforme evidente no cookie `timezone=America%2FSao_Paulo` e `timezoneoffset=-180` nas requisições. O `_parse_trace_timestamp` trata esses timestamps sem conversão de fuso:

```python
# Formato típico: "2026-05-19 05:18:14 (148)" — descarta o (ms)
# Armazena como: "2026-05-19T05:18:14Z"  ← ERRADO, é local time, não UTC
```

**Efeito:** Uma medição coletada às 14:00 (local) é armazenada como `"2026-05-27T14:00:00Z"`. O SQLite `now` é `17:00 UTC`. A query pede `timestamp >= 16:00 UTC`. `14:00 UTC` < `16:00 UTC` → dado excluído. A janela de 60 minutos **nunca encontra dados** que sejam mais recentes do que 3h em UTC.

**Causa raiz secundária — Inserção condicional:**

Em `_parse_trace_response`:
```python
if rsrp is None:
    continue   # ← Se msg-explain-info falhar, a linha NÃO é inserida
```

Se `msg-explain-info` retornar erro ou null para a maioria dos decodes (por timeout, sessão expirada, rowNo errado), o banco ficará vazio mesmo que o collector rode. Com o problema A (decodificando mensagens antigas), aumenta a chance de esses rowNos serem inválidos para o estado atual da sessão FARS.

---

### Problema C — Gráfico mostra apenas RSRP, sem RSRQ

**Sintoma:** O modal de detalhe do VIP exibe um gráfico de linha único (RSRP), sem RSRQ.

**Causa raiz:** Em `vip.js:188-201`, apenas `rsrpValues` é mapeado. Nenhum dataset de RSRQ é criado:
```javascript
const rsrpValues = series.map(r => r.rsrp);
// ← rsrq nem é lido
datasets: [{ data: rsrpValues, ... }]  // único dataset
```

Adicionalmente:
- O chart wrapper tem `height: 140px` (muito pequeno para dois datasets)
- O modal tem `width: 420px` (estreito para um gráfico legível com dois eixos)

---

## 2. Mudanças Necessárias

### Mudança 1 — Substituir `/query/result` por `/query/filter-by-cols` no `collect_vips`

**Arquivo:** `core/collector.py`

**O novo endpoint** (`POST /rest/oss/access/fars/v1/traceresult/query/filter-by-cols`) recebido em `requests/trace/filtered-request.txt` resolve o Problema A:
- Filtra **apenas** `RRC_MEAS_RPRT` server-side (sem loop de descarte)
- Ordena **do mais recente para o mais antigo** (`isAscend: false`)
- Retorna até 1000 resultados por página

**Novo fluxo de coleta** (substituindo as 3 etapas atuais):

```
Etapa 1: GET /pre-check?taskId={id}&queryType=0
         → Mantém igual. Inicializa sessão FARS.
         → Valida checkState == true.

Etapa 2: GET /query/result?startRow=0&pageSize=1&taskId={id}&msgId=1
         → pageSize=1 em vez de 500 (só precisa do sess_msg_id da resposta)
         → Extrai data.msgId = sessão FARS (ex: 4201005)

Etapa 3: POST /query/filter-by-cols   ← NOVA
         Body:
         {
           "colFilterDto": {
             "colFltExpSeq": [{
               "fieldId": "Message Type",
               "value": "RRC_MEAS_RPRT",
               "operator": {"op": 0}
             }],
             "signalList": [],
             "hasStartTime": false,
             "startTime": "",
             "hasEndTime": false,
             "endTime": "",
             "isReverse": false
           },
           "pageDto": {
             "sqlColumnName": "Time",
             "isAscend": false,          ← MAIS RECENTE PRIMEIRO
             "taskId": {task_id},
             "msgId": {sess_msg_id},     ← do passo 2
             "comparisonMsgId": -1,
             "startRow": 0,
             "pageSize": 1000,
             "templateName": [],
             "isSetBenchMarkTime": false,
             "benchMarkTimeRowNo": -1
           }
         }

Etapa 4: Para cada row (do mais recente ao mais antigo),
         até _MAX_MEAS_DECODES_PER_CYCLE:
         GET /msg-explain-info?taskId={id}&msgId={sess_msg_id}&rowNo={idx+1}
         → idx+1 é a posição 1-indexada no resultado FILTRADO/ORDENADO
         → Extrai rsrpResult/rsrqResult
```

**Nota sobre rowNo após filter-by-cols:**
Após chamar `filter-by-cols`, o backend FARS reorganiza a sessão para refletir a nova ordenação/filtragem. As chamadas subsequentes a `msg-explain-info` usam `rowNo` relativo à **view atual** (filtrada + ordenada), não ao conjunto original. Portanto `rowNo = idx + 1` onde `idx` é o índice na resposta de `filter-by-cols` — exatamente como já funciona na lógica atual para `query/result`. **Verificar em produção se o primeiro rowNo=1 retorna o dado mais recente.**

**Implementação em `collect_vips`:**

```python
# Etapa 2: pageSize=1 para obter apenas o sess_msg_id
result_resp = s.get(result_url, params={
    "nocache":           int(time.time() * 1000),
    "startRow":          0,
    "pageSize":          1,           # mínimo — só precisa do msgId
    "taskId":            task_id,
    "msgId":             1,
    "isSetBenchMarkTime": "false",
    "benchMarkTimeRowNo": -1,
}, timeout=30)

result_data = result_resp.json() if result_resp.content else {}
data_block = result_data.get("data") or {}
if isinstance(data_block, list):
    logger.warning(f"query/result retornou lista para task {task_id} — sem sess_msg_id")
    break
sess_msg_id = data_block.get("msgId")
if not sess_msg_id:
    logger.warning(f"Sem sess_msg_id para task {task_id}: {data_block}")
    break

# Etapa 3: filter-by-cols — RRC_MEAS_RPRT, mais recente primeiro
filter_url = f"{self.base_url}/rest/oss/access/fars/v1/traceresult/query/filter-by-cols"
now_ms = int(time.time() * 1000)
filter_payload = {
    "colFilterDto": {
        "colFltExpSeq": [{
            "fieldId":  "Message Type",
            "value":    "RRC_MEAS_RPRT",
            "operator": {"op": 0},
        }],
        "signalList":   [],
        "hasStartTime": False,
        "startTime":    "",
        "hasEndTime":   False,
        "endTime":      "",
        "isReverse":    False,
    },
    "pageDto": {
        "sqlColumnName":        "Time",
        "isAscend":             False,     # mais recente primeiro
        "taskId":               task_id,
        "msgId":                sess_msg_id,
        "comparisonMsgId":      -1,
        "startRow":             0,
        "pageSize":             1000,
        "templateName":         [],
        "isSetBenchMarkTime":   False,
        "benchMarkTimeRowNo":   -1,
    },
}
filter_resp = s.post(
    filter_url + f"?nocache={now_ms}",
    json=filter_payload,
    timeout=60,
)
if not self._check_session_valid(filter_resp, "trace"):
    raise SessionExpiredError("Sessão trace expirada (filter-by-cols)")
if filter_resp.status_code >= 400:
    self._handle_trace_error(task_id, "filter-by-cols", filter_resp)
    break

measurements.extend(self._parse_filtered_trace_response(
    filter_resp.json(), task_id, s, vip_name, sess_msg_id
))
```

**Novo método `_parse_filtered_trace_response`:**

Similar ao `_parse_trace_response` existente, mas:
- Não precisa filtrar por `msg_type` (todos os rows já são `RRC_MEAS_RPRT`)
- O `rowNo = idx + 1` agora é relativo à view filtrada+ordenada
- Processa os primeiros `_MAX_MEAS_DECODES_PER_CYCLE` rows (= os mais recentes)

---

### Mudança 2 — Corrigir timezone nos timestamps do trace

**Arquivo:** `core/collector.py`

**Problema:** O iManager retorna horários em `America/Sao_Paulo (UTC-3)` mas o código os armazena como se fossem UTC. A query `now - 60 minutos` do SQLite (UTC) exclui esses dados.

**Solução:** Em `_parse_trace_timestamp`, detectar que o timestamp não tem informação de timezone e aplicar o offset configurado na sessão FARS. O cookie `timezoneoffset=-180` confirma UTC-3. Como a offset pode variar por cliente, usar um valor configurável — ou simplesmente não fazer a conversão e ajustar a query do banco (ver Mudança 3).

**Abordagem recomendada — Offset configurável no HttpCollector:**

```python
# Em HttpCollector.__init__, ler o offset da sessão (timezoneoffset em minutos)
# Padrão: -180 (UTC-3, Brasil)
self._oss_tz_offset_min = -180  # pode vir de event_config.oss.timezone_offset

def _parse_trace_timestamp(self, raw) -> str:
    """
    Converte timestamp do FARS para UTC ISO com Z.
    iManager retorna horário local; aplica offset para converter para UTC.
    """
    if raw is None or raw == "":
        return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    s = str(raw)
    if " (" in s:
        s = s.split(" (")[0]
    # Tenta tratar como epoch ms (já é UTC)
    try:
        ms = int(s)
        return datetime.utcfromtimestamp(ms / 1000.0).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (ValueError, TypeError):
        pass
    # Timestamp local — converter para UTC
    try:
        dt_local = datetime.fromisoformat(s.replace(" ", "T"))
        # Offset negativo = atrás do UTC; subtrair para chegar em UTC
        # Ex: UTC-3 (offset=-180) → UTC = local_time - (-180 min) = local + 180 min
        from datetime import timedelta
        dt_utc = dt_local - timedelta(minutes=self._oss_tz_offset_min)
        return dt_utc.strftime('%Y-%m-%dT%H:%M:%SZ')
    except Exception:
        return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
```

**Alternativa mais simples (se o offset for sempre UTC-3):**

Hardcode a conversão `+3h` para UTC. Isso resolve para 99% dos deployments no Brasil.

**Atenção:** Verificar se o campo `Time` na resposta do FARS retorna mesmo horário local ou UTC. Observar o cookie `timemode=client` nas requisições — isso indica que o iManager ajusta a exibição ao fuso do cliente. Se internamente o iManager armazena em UTC, o campo `Time` da API pode já vir em UTC. Testar em produção comparando o horário de uma medição com o relógio do sistema.

---

### Mudança 3 — Tornar a janela temporal do `get_vip_series` resiliente

**Arquivo:** `core/database.py`

**Problema:** Se os timestamps estiverem com offset, `get_vip_series` com `minutes=60` retorna vazio.

**Solução de fallback:** Se a query com janela temporal retornar vazio, fazer um fallback com `LIMIT N` sem filtro temporal, garantindo que o modal sempre mostre os últimos N pontos disponíveis.

```python
def get_vip_series(event_id: str, vip_name: str, minutes: int = 60) -> List[dict]:
    conn = get_event_conn(event_id)
    # Tentativa 1: janela temporal normal
    rows = conn.execute("""
        SELECT timestamp, rsrp, rsrq, serving_cell, in_event
        FROM vip_measurements
        WHERE event_id = ? AND vip_name = ?
          AND timestamp >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' minutes')
        ORDER BY timestamp ASC
    """, (event_id, vip_name, f"-{minutes}")).fetchall()
    
    if rows:
        return [dict(r) for r in rows]
    
    # Fallback: sem filtro temporal — retorna os últimos 120 registros disponíveis
    rows = conn.execute("""
        SELECT timestamp, rsrp, rsrq, serving_cell, in_event
        FROM vip_measurements
        WHERE event_id = ? AND vip_name = ?
        ORDER BY timestamp DESC
        LIMIT 120
    """, (event_id, vip_name)).fetchall()
    return [dict(r) for r in reversed(rows)]  # retorna em ordem crescente
```

**Observação:** Este fallback garante que, mesmo com timezone errado, o modal sempre mostre dados se eles existirem no banco.

---

### Mudança 4 — Adicionar RSRQ ao gráfico do modal e redimensionar

**Arquivo:** `frontend/js/vip.js`

**Problema:** Apenas RSRP é exibido. RSRQ é um indicador complementar essencial.

**Solução:** Dois datasets com eixos Y independentes:
- **Y1 (esquerda):** RSRP em dBm, escala aproximada de -140 a -40
- **Y2 (direita):** RSRQ em dB, escala aproximada de -20 a -3
- **Linhas de threshold:** RSRP warning (-100 dBm) e critical (-110 dBm)

```javascript
// Em _openModal, substituir a criação do chart:

const rsrpValues = series.map(r => r.rsrp ?? null);
const rsrqValues = series.map(r => r.rsrq ?? null);

_modalChart = new Chart(canvas, {
  type: "line",
  data: {
    labels,
    datasets: [
      {
        label:          "RSRP",
        data:           rsrpValues,
        borderColor:    color,
        backgroundColor: color + "18",
        borderWidth:    1.5,
        pointRadius:    0,
        tension:        0.3,
        fill:           true,
        yAxisID:        "yRsrp",
      },
      {
        label:          "RSRQ",
        data:           rsrqValues,
        borderColor:    "#58A6FF",
        backgroundColor: "#58A6FF18",
        borderWidth:    1.5,
        pointRadius:    0,
        tension:        0.3,
        fill:           false,
        yAxisID:        "yRsrq",
      },
    ],
  },
  options: {
    responsive:          true,
    maintainAspectRatio: false,
    animation:           false,
    interaction: {
      mode:         "index",
      intersect:    false,
    },
    plugins: {
      legend: {
        display: true,
        position: "top",
        labels: {
          color: "#8B949E",
          font: { size: 9 },
          boxWidth: 12,
          padding: 8,
        },
      },
      tooltip: {
        callbacks: {
          label: (ctx) => {
            const unit = ctx.dataset.yAxisID === "yRsrp" ? " dBm" : " dB";
            return `${ctx.dataset.label}: ${ctx.raw?.toFixed(1)}${unit}`;
          },
        },
      },
      annotation: {
        annotations: {
          warn: {
            type: "line", yMin: warnTh, yMax: warnTh,
            borderColor: "#D29922", borderWidth: 1, borderDash: [4, 3],
            yScaleID: "yRsrp",
          },
          crit: {
            type: "line", yMin: critTh, yMax: critTh,
            borderColor: "#F85149", borderWidth: 1, borderDash: [4, 3],
            yScaleID: "yRsrp",
          },
        },
      },
    },
    scales: {
      x: {
        ticks: { color: "#8B949E", font: { size: 9 }, maxTicksLimit: 6 },
        grid:  { color: "#21262D" },
      },
      yRsrp: {
        type:     "linear",
        position: "left",
        ticks: { color: "#8B949E", font: { size: 9 }, callback: v => `${v}` },
        grid:  { color: "#21262D" },
        title: { display: true, text: "RSRP (dBm)", color: "#8B949E", font: { size: 9 } },
      },
      yRsrq: {
        type:     "linear",
        position: "right",
        ticks: { color: "#58A6FF", font: { size: 9 }, callback: v => `${v}` },
        grid:  { drawOnChartArea: false },   // sem grade duplicada
        title: { display: true, text: "RSRQ (dB)", color: "#58A6FF", font: { size: 9 } },
      },
    },
  },
});
```

---

### Mudança 5 — Redimensionar o modal de detalhes do VIP

**Arquivo:** `frontend/index.html`

**Mudanças no modal `vip-detail-modal`:**

| Elemento | Atual | Novo |
|----------|-------|------|
| `modal-box` width | `420px` | `660px` |
| `vip-modal-chart-wrapper` height | `140px` | `200px` |

```html
<!-- De: -->
<div class="modal-box" style="width:420px;max-width:95vw;">
  ...
  <div id="vip-modal-chart-wrapper" style="position:relative;height:140px;">

<!-- Para: -->
<div class="modal-box" style="width:660px;max-width:95vw;">
  ...
  <div id="vip-modal-chart-wrapper" style="position:relative;height:200px;">
```

---

## 3. Ordem de Execução

```
Passo 1  core/collector.py
         - Refatorar collect_vips para usar filter-by-cols
         - Adicionar _parse_filtered_trace_response
         - Otimizar query/result para pageSize=1
         - Corrigir _parse_trace_timestamp (timezone UTC-3→UTC)

Passo 2  core/database.py
         - Adicionar fallback temporal em get_vip_series

Passo 3  frontend/js/vip.js
         - Adicionar dataset RSRQ no chart
         - Dual Y-axis (yRsrp e yRsrq)
         - Annotation annotations com yScaleID

Passo 4  frontend/index.html
         - Aumentar largura do modal para 660px
         - Aumentar altura do chart wrapper para 200px
```

---

## 4. Detalhes de Cada Arquivo a Modificar

### `core/collector.py`

**Constantes a atualizar:**
```python
_MAX_MEAS_DECODES_PER_CYCLE = 50   # mantém — agora decodifica os 50 MAIS RECENTES
_TRACE_PAGE_SIZE = 500             # não mais usado para trace VIP (pode remover)
_FILTER_PAGE_SIZE = 1000           # novo: tamanho da página para filter-by-cols
```

**Métodos a modificar:**
- `collect_vips`: substituir lógica de 3 passos (manter passo 1, otimizar passo 2, adicionar passo 3 com filter-by-cols)
- `_parse_trace_timestamp`: adicionar conversão de timezone
- `_parse_trace_response`: pode ser mantido para compatibilidade ou marcado como legado

**Método novo:**
- `_parse_filtered_trace_response(response_json, task_id, session, vip_name, sess_msg_id)`: parse do resultado do filter-by-cols, semelhante ao `_parse_trace_response` mas sem filtrar por tipo (todos já são RRC_MEAS_RPRT)

**Atributo novo no `__init__` de `HttpCollector`:**
```python
self._oss_tz_offset_min = event_config.get("oss", {}).get("timezone_offset_min", -180)
```

### `core/database.py`

**Método a modificar:**
- `get_vip_series`: adicionar fallback com `ORDER BY timestamp DESC LIMIT 120`

### `frontend/js/vip.js`

**Função a modificar:**
- `_openModal`: refatorar criação do Chart.js para dois datasets + dual axis

**Sem mudanças em:** `render`, `_card`, `initVip`, `_closeModal`, `_section`

### `frontend/index.html`

**Duas linhas a editar:**
- Linha 316: `width:420px` → `width:660px`
- Linha 347: `height:140px` → `height:200px`

---

## 5. Pontos de Atenção e Riscos

### Risco 1 — `rowNo` no filter-by-cols
**Hipótese:** após `filter-by-cols`, o backend FARS renumera as linhas na view filtrada+ordenada, então `rowNo=1` = linha 1 da view (mais recente RRC_MEAS_RPRT). **Verificar em produção:** o primeiro `msg-explain-info` com `rowNo=1` deve retornar dados frescos. Se retornar erro ou dados antigos, tentar usar o campo `serialNo` da resposta do filter-by-cols como `rowNo`.

### Risco 2 — Timezone dos timestamps
**Hipótese atual:** iManager retorna timestamps em `America/Sao_Paulo (UTC-3)`. **Verificar:** comparar o timestamp de uma medição com o horário local e com `datetime.utcnow()` no Python. Se o iManager já entregar em UTC, a conversão é desnecessária.

### Risco 3 — Compatibilidade do filter-by-cols com sessão pre-check
A documentação interna (CLAUDE.md) alerta: "Não use sort sem pre-check antes — retorna 500". O mesmo vale para `filter-by-cols` — exige que a sessão tenha sido inicializada via `pre-check` + `query/result`. O novo fluxo mantém esses dois passos, então isso deve estar coberto.

### Risco 4 — `checkState` false no pre-check
O código atual já verifica `pre_data.get("checkState", False)` e loga warning se falso. Isso pode ocorrer quando a task FARS do iManager ainda não possui dados coletados. O comportamento de `break` (sem inserir medição) está correto.

---

## 6. O Que NÃO Muda

- Fluxo de autenticação (Playwright, renovação de sessão via `_renew_session`)
- Método `_extract_rsrp_rsrq_from_json` (funcionando)
- Método `_check_session_valid` (funcionando)
- `insert_vip_batch` em database.py (funcionando)
- `get_vip_latest` em database.py (sem filtro temporal — correto para card do VIP)
- `get_vips` em api.py (funcionando)
- Lógica de resolução de site (`resolve_site_id`)
- MockCollector, CsvCollector (não afetados)

---

## 7. Critérios de Sucesso

1. Ao clicar num VIP com trace ativo no iManager, os campos RSRP/RSRQ mostram dados com timestamp dos **últimos 5 minutos** (não de horas atrás).
2. O modal do VIP exibe o gráfico com duas linhas (RSRP e RSRQ) e dois eixos Y.
3. Com o fallback temporal, o gráfico mostra dados mesmo que haja discrepância de timezone.
4. O gráfico cobre um período representativo (última hora ou os últimos 120 pontos coletados).
5. Linhas de threshold (warning/critical) são visíveis sobre o dataset RSRP.
