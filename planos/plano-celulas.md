# Plano de Execução: Reorganização de Células — SmartEvents

> **Status:** Rascunho aprovado para implementação
> **Gerado em:** 2026-05-27
> **Contexto de origem:** `reorganizacao-celulas.md`

---

## 1. Diagnóstico do problema atual

### 1.1 Como os dados chegam

Tanto o `CsvCollector` quanto o `HttpCollector` gravam **uma linha por célula** na tabela `kpi_measurements`:

```
site_id | cell_id       | metric          | value | timestamp
ERB-07  | ERB-07-Y3500-1 | utilization_dl  | 72.3  | 2026-05-27T...
ERB-07  | ERB-07-Y3500-2 | utilization_dl  | 91.5  | 2026-05-27T...
ERB-07  | ERB-07-Y3500-3 | utilization_dl  | 54.1  | 2026-05-27T...
```

O banco está correto. O problema está em **como esses dados são apresentados**.

### 1.2 Problemas identificados na exibição

#### Problema A — Lista de sites mostra sempre "utilização", independente da métrica selecionada

Em `api.py → get_sites()`, a coluna de valor é calculada por `_aggregate_utilization()`, que retorna apenas a **utilização máxima** de qualquer célula, independente do que o usuário selecionou no dropdown.

Resultado: o operador seleciona "RSRP Médio" no dropdown e a lista continua mostrando "72%" (utilização), criando desconexão visual.

#### Problema B — Gráfico agrega células de forma correta mas sem transparência

Em `api.py → get_kpi_series()`, a lógica de agregação já existe (linhas 226–238), mas o operador **não tem como ver por célula individual** — só vê o site inteiro. Não dá para saber se um pico de utilização foi em todas as células ou só em uma.

```python
# Atual: sempre consolida todas as células em um valor por timestamp
for ts, vals in sorted(ts_groups.items()):
    val = max(vals)  # ← "pega o pior" sem dar opção de ver célula individual
```

#### Problema C — Métricas de volume (usuários, tráfego) não têm contexto de relevância

Mostrar "14.230 usuários" na lista não diz nada. Um site pode ter esse número por ser o maior da rede ou porque está com congestionamento. O **share percentual** (% do total) é a forma correta para comparar relevância entre sites, conforme `reorganizacao-celulas.md`.

---

## 2. Escopo das mudanças

| Camada | Arquivo | Tipo de mudança |
|--------|---------|-----------------|
| Banco | `core/database.py` | Novo método de série por célula (sem schema change) |
| API | `api/api.py` | `get_kpi_series` aceita `cell_id`; `get_sites` aceita `metric`; novo `get_site_cells` |
| Frontend HTML | `frontend/index.html` | Adicionar `<select id="cell-selector">` nos controles do gráfico |
| Frontend JS | `frontend/js/kpi.js` | Seletor de célula + lógica dinâmica na lista + cabeçalho dinâmico |
| Frontend JS | `frontend/js/state.js` | Verificar e adicionar propriedade `selectedCell` |
| Frontend JS | `frontend/js/bridge.js` | Adicionar mock para `get_site_cells` (modo dev sem PyWebView) |

> **Schema do banco:** Nenhuma alteração de schema necessária. A coluna `cell_id` já existe em `kpi_measurements` e está indexada via `idx_kpi_site_time`. A implementação inteira é de query e apresentação.

---

## 3. Especificação das métricas na lista de sites

Conforme `reorganizacao-celulas.md`, a coluna de valor da lista muda conforme o dropdown:

| Dropdown selecionado | Valor exibido na lista | Regra de agregação | Bolinha |
|---|---|---|---|
| `utilization_dl` / `utilization_ul` | Pior célula (%) | `MAX(valores das células)` | Alerta se ≥ warn/crit |
| `throughput_dl` / `throughput_ul` | Média do site (Mbps) | `AVG(valores das células)` | Alerta por threshold |
| `accessibility` | Pior célula (%) | `MIN(valores das células)` | Alerta se < threshold |
| `rsrp` / `rsrq` | Média (dBm / dB) | `AVG(valores das células)` | Nível de cobertura |
| `user_count` | **Share % do total** | `SUM(células) / total_geral × 100` | Neutro (sem bolinha colorida) |
| `traffic_volume_dl` / `traffic_volume_ul` | **Share % do total** | `SUM(células) / total_geral × 100` | Neutro (sem bolinha colorida) |

O **status da bolinha** do site (crítico/warning/saudável) continua sendo calculado **sempre pela utilização máxima das células**, independente da métrica exibida na coluna. Isso preserva a funcionalidade existente de alerta visual.

---

## 4. Plano de execução por fase

---

### FASE 1 — Backend: `core/database.py`

**Objetivo:** Adicionar suporte a consulta de série por célula e a agregação contextual.

#### Tarefa 1.1 — Novo método `get_kpi_series_by_cell`

Adicionar após `get_kpi_series()` (linha ~425):

```python
def get_kpi_series_by_cell(
    event_id: str,
    site_id: str,
    cell_id: str,
    metric: str,
    minutes: int = 60
) -> List[dict]:
    """
    Retorna série temporal de uma métrica para uma célula específica.
    `cell_id == '__all__'` retorna todas as células (comportamento de get_kpi_series).
    """
    conn = get_event_conn(event_id)
    if cell_id == "__all__":
        return get_kpi_series(event_id, site_id, metric, minutes)

    if minutes and minutes > 0:
        rows = conn.execute("""
            SELECT cell_id, timestamp, value
            FROM kpi_measurements
            WHERE event_id = ?
              AND site_id  = ?
              AND cell_id  = ?
              AND metric   = ?
              AND timestamp >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' minutes')
            ORDER BY timestamp ASC
        """, (event_id, site_id, cell_id, metric, f"-{minutes}")).fetchall()
    else:
        rows = conn.execute("""
            SELECT cell_id, timestamp, value
            FROM kpi_measurements
            WHERE event_id = ?
              AND site_id  = ?
              AND cell_id  = ?
              AND metric   = ?
            ORDER BY timestamp ASC
        """, (event_id, site_id, cell_id, metric)).fetchall()
    return [dict(r) for r in rows]
```

#### Tarefa 1.2 — Novo método `get_latest_kpi_by_metric`

Adicionar após `get_latest_kpi()` (linha ~461). Este método retorna o último valor de uma métrica específica por site, permitindo ao frontend calcular share e exibição contextual:

```python
def get_latest_kpi_by_metric(
    event_id: str,
    metric: str,
    max_timestamp: Optional[str] = None
) -> List[dict]:
    """
    Retorna última medição de cada (site_id, cell_id) para uma métrica específica.
    Usado para calcular o valor contextual exibido na lista de sites.
    """
    conn = get_event_conn(event_id)
    ts_filter = "AND k.timestamp <= ?" if max_timestamp else ""
    params_inner = [event_id, metric]
    if max_timestamp:
        params_inner.append(max_timestamp)
    params_inner.append(event_id)

    rows = conn.execute(f"""
        SELECT k.site_id, k.cell_id, k.metric, k.value, k.timestamp
        FROM kpi_measurements k
        INNER JOIN (
            SELECT site_id, cell_id, MAX(timestamp) AS max_ts
            FROM kpi_measurements
            WHERE event_id = ? AND metric = ? {ts_filter}
            GROUP BY site_id, cell_id
        ) latest ON k.site_id = latest.site_id
                    AND k.cell_id = latest.cell_id
                    AND k.timestamp = latest.max_ts
        WHERE k.event_id = ? AND k.metric = ?
    """, params_inner + [metric]).fetchall()
    return [dict(r) for r in rows]
```

> **Nota de índice:** O índice existente `idx_kpi_site_time ON kpi_measurements(site_id, metric, timestamp)` atende às duas queries acima. Nenhum índice novo necessário.

---

### FASE 2 — Backend: `api/api.py`

**Objetivo:** Expor novos endpoints ao frontend para seleção de célula e exibição contextual na lista.

#### Tarefa 2.1 — Atualizar `get_kpi_series` para aceitar `cell_id` opcional

Assinatura atual (linha 186):
```python
def get_kpi_series(self, event_id: str, site_id: str, metric: str, minutes: int = 60) -> dict:
```

Nova assinatura:
```python
def get_kpi_series(self, event_id: str, site_id: str, metric: str,
                   minutes: int = 60, cell_id: str = "__all__") -> dict:
```

**Lógica nova dentro do método:**

Após obter `rows` (no passo 1 do método existente), adicionar:

```python
# Se célula específica solicitada, filtrar antes de agregar
if cell_id and cell_id not in ("__all__", "__media__"):
    rows = [r for r in rows if r.get("cell_id") == cell_id]
```

Para `cell_id == "__media__"` (opção Média), o comportamento é calcular a **média** de todas as células, independente do tipo de métrica (pois o usuário está explicitamente pedindo a média). Adicionar no passo 3 (agregação):

```python
# Se o usuário escolheu "Média" explicitamente, sempre calcula AVG
if cell_id == "__media__":
    val = sum(vals) / len(vals)
elif "availability" in metric or "accessibility" in metric:
    val = sum(vals) / len(vals)
elif "throughput" in metric:
    val = sum(vals)          # throughput é somado (capacidade do site)
elif "rsrp" in metric or "rsrq" in metric:
    val = sum(vals) / len(vals)
else:
    val = max(vals)          # utilização, user_count → pior/máximo
```

#### Tarefa 2.2 — Novo método `get_site_cells`

Adicionar na seção "Dados do mapa / sites" de `api.py`:

```python
def get_site_cells(self, event_id: str, site_id: str) -> list:
    """
    Retorna a lista de células de um site específico.
    Usado para popular o seletor de célula no gráfico.
    """
    try:
        config = db.get_event(event_id) or _active_event
        if not config:
            return []
        for site in config.get("sites", []):
            if site["id"] == site_id:
                cells = site.get("cells", [])
                out = []
                for c in cells:
                    if isinstance(c, str):
                        out.append({"id": c, "label": c})
                    else:
                        out.append({
                            "id":    c.get("id", ""),
                            "label": c.get("id", ""),
                            "tech":  c.get("tech"),
                            "freq":  c.get("frequency"),
                        })
                return out
        return []
    except Exception as e:
        logger.error(f"get_site_cells error: {e}")
        return []
```

#### Tarefa 2.3 — Atualizar `get_sites` para exibir valor contextual da métrica selecionada

Assinatura atual:
```python
def get_sites(self, event_id: str, timestamp: Optional[str] = None) -> list:
```

Nova assinatura:
```python
def get_sites(self, event_id: str, timestamp: Optional[str] = None,
              metric: str = "utilization_dl") -> list:
```

**Lógica adicionada** (após calcular `util_by_site`):

```python
# Calcula valor contextual para a métrica selecionada (exibição na lista)
VOLUME_METRICS = {"user_count", "traffic_volume_dl", "traffic_volume_ul"}

if metric != "utilization_dl":
    latest_metric = db.get_latest_kpi_by_metric(event_id, metric, timestamp)
else:
    latest_metric = latest  # reusa os dados já carregados

metric_by_site = self._aggregate_metric_for_list(latest_metric, metric)
```

**Novo método interno `_aggregate_metric_for_list`** (adicionar junto com `_aggregate_utilization`):

```python
def _aggregate_metric_for_list(self, kpi_rows: list, metric: str) -> dict:
    """
    Agrega valores de células para exibição na coluna da lista de sites.
    Retorna dict: { site_id: {"value": float, "is_share": bool} }
    
    Regras:
    - user_count / traffic_volume_*: retorna soma das células (share calculado depois)
    - accessibility: mínimo das células (pior caso)
    - rsrp / rsrq: média das células
    - outros (utilização, throughput): máximo das células (pior caso)
    """
    VOLUME_METRICS = {"user_count", "traffic_volume_dl", "traffic_volume_ul"}
    MEAN_METRICS   = {"rsrp", "rsrq", "throughput_dl", "throughput_ul"}
    MIN_METRICS    = {"accessibility"}

    # Acumula valores por site
    site_vals = {}
    for r in kpi_rows:
        if r.get("metric") != metric:
            continue
        site = r["site_id"]
        val = r.get("value")
        if val is None:
            continue
        if site not in site_vals:
            site_vals[site] = []
        site_vals[site].append(val)

    result = {}
    total = 0.0

    for site, vals in site_vals.items():
        if not vals:
            continue
        if metric in VOLUME_METRICS:
            agg = sum(vals)
            total += agg
        elif metric in MIN_METRICS:
            agg = min(vals)
        elif metric in MEAN_METRICS:
            agg = sum(vals) / len(vals)
        else:
            agg = max(vals)  # utilization_dl, utilization_ul
        result[site] = agg

    # Para volume: converte soma em share percentual
    if metric in VOLUME_METRICS and total > 0:
        return {site: round((val / total) * 100, 1)
                for site, val in result.items()}
    
    return {site: round(val, 1) for site, val in result.items()}
```

**Atualizar o retorno de `get_sites`** para incluir o valor contextual e o tipo de indicador:

```python
sites_out.append({
    "id":              site["id"],
    "name":            site["name"],
    "lat":             site["lat"],
    "lng":             site["lng"],
    "cells":           site.get("cells", []),
    "status":          status,               # sempre baseado em utilização
    "utilization":     round(util, 1) if util is not None else None,
    "metric_value":    metric_by_site.get(site["id"]),  # ← NOVO: valor da métrica selecionada
    "metric_is_share": metric in VOLUME_METRICS,         # ← NOVO: se é share %
    "is_event_site":   site.get("is_event_site", True),
})
```

---

### FASE 3 — Frontend: `frontend/index.html`

**Objetivo:** Adicionar o seletor de célula na área de controles do gráfico.

#### Tarefa 3.1 — Inserir `#cell-selector` em `#chart-controls`

Localizar o bloco `#chart-controls` (linha 179) e adicionar o select após `#metric-selector`:

```html
<!-- ANTES (linha ~181-191) -->
<div id="chart-controls">
  <span id="chart-site-label" class="chart-site-name">—</span>
  <select id="metric-selector">
    ...métricas...
  </select>
  <div id="time-tabs">...

<!-- DEPOIS -->
<div id="chart-controls">
  <span id="chart-site-label" class="chart-site-name">—</span>
  <select id="metric-selector">
    ...métricas...
  </select>
  <select id="cell-selector" title="Selecionar célula">
    <option value="__all__">Site completo</option>
    <!-- populado dinamicamente por kpi.js -->
  </select>
  <div id="time-tabs">...
```

> **Estilo:** O `#cell-selector` deve seguir o mesmo `class` do `#metric-selector` para herdar o CSS existente. Se não houver uma classe explícita, verificar `frontend/css/main.css` para o seletor `select` genérico dentro de `#chart-controls`.

---

### FASE 4 — Frontend: `frontend/js/state.js`

**Objetivo:** Registrar a nova propriedade de estado `selectedCell`.

#### Tarefa 4.1 — Verificar e adicionar `selectedCell`

Abrir `state.js` e verificar se há um objeto de estado inicial. Adicionar:

```javascript
// Na inicialização do estado (junto com selectedSite, selectedMetric, etc.)
selectedCell: "__all__",   // "__all__" = site completo, "__media__" = média, ou cell_id específico
```

Garantir que `State.set("selectedCell", ...)` dispara o evento `change:selectedCell`.

---

### FASE 5 — Frontend: `frontend/js/kpi.js`

Esta é a maior mudança de frontend. Divide-se em 3 sub-tarefas.

#### Tarefa 5.1 — Seletor de célula: popular e reagir

**Adicionar listener para `change:selectedSite`** (já existe em `_onSiteSelected`) — ao selecionar site, popular o `#cell-selector`:

```javascript
async function _populateCellSelector(siteId) {
  const sel = document.getElementById("cell-selector");
  if (!sel) return;

  const { eventId } = State;
  if (!siteId || !eventId) {
    sel.innerHTML = '<option value="__all__">Site completo</option>';
    return;
  }

  const cells = await API.getSiteCells(eventId, siteId);
  sel.innerHTML = '<option value="__all__">Site completo</option>';

  if (cells && cells.length > 1) {
    // Adiciona opção "Média" apenas quando há mais de 1 célula
    sel.innerHTML += '<option value="__media__">— Média —</option>';
    cells.forEach(cell => {
      const label = cell.label || cell.id;
      const tech = cell.tech ? ` (${cell.tech})` : "";
      sel.innerHTML += `<option value="${_esc(cell.id)}">${_esc(label)}${_esc(tech)}</option>`;
    });
  } else if (cells && cells.length === 1) {
    // Site com célula única: não faz sentido exibir seletor
    sel.innerHTML = `<option value="${_esc(cells[0].id)}">${_esc(cells[0].label || cells[0].id)}</option>`;
  }

  // Reseta para "Site completo" ao trocar de site
  sel.value = "__all__";
  State.set("selectedCell", "__all__");
}
```

Modificar `_onSiteSelected` para chamar `_populateCellSelector`:

```javascript
function _onSiteSelected(siteId) {
  // ... código existente de scroll e label ...
  _populateCellSelector(siteId);  // ← ADICIONAR
  _refreshChart();
}
```

**Adicionar listener no `initKpi`:**

```javascript
document.getElementById("cell-selector").addEventListener("change", e => {
  State.set("selectedCell", e.target.value);
});

State.on("change:selectedCell", _refreshChart);
```

#### Tarefa 5.2 — Atualizar `_refreshChart` para passar `cell_id`

Localizar `_refreshChart` (linha 204). Modificar a chamada a `API.getKpiSeries`:

```javascript
async function _refreshChart() {
  if (!_chart) return;
  const { eventId, selectedSite, selectedMetric, selectedCell,
          timeWindow, mode, historicalTimestamp } = State;
  if (!eventId || !selectedSite) return;

  const cellId = selectedCell || "__all__";  // ← NOVO
  const queryWindow = mode === "historical" ? 0 : (timeWindow || 0);

  // ← NOVO: passa cellId como 5º argumento
  const data = await API.getKpiSeries(eventId, selectedSite, selectedMetric, queryWindow, cellId);
  // ... resto do método inalterado ...
}
```

**Atualizar o label do dataset** para refletir a seleção:

```javascript
// Após definir _chart.data.datasets[0].data = adjustedValues:
const cellLabel = cellId === "__all__"   ? "Site completo" :
                  cellId === "__media__" ? "Média das células" :
                  cellId;
_chart.data.datasets[0].label =
  `${METRIC_LABELS[selectedMetric] || selectedMetric} — ${cellLabel}`;
```

#### Tarefa 5.3 — Lista de sites com valor contextual dinâmico

**Modificar `_renderSiteList`** para usar `metric_value` e `metric_is_share`:

```javascript
function _renderSiteList(sites) {
  const el = document.getElementById("site-list");
  const summary = document.getElementById("site-summary");
  if (!el) return;

  const metric = State.selectedMetric || "utilization_dl";
  const isVolumeMetric = ["user_count", "traffic_volume_dl", "traffic_volume_ul"]
                           .includes(metric);

  // Atualiza cabeçalho da coluna de valor (NOVO)
  const colHeader = document.getElementById("site-metric-header");
  if (colHeader) {
    colHeader.textContent = isVolumeMetric
      ? "Participação"
      : (METRIC_LABELS[metric]?.split(" ")[0] || "Valor");
  }

  // ... ordenação e filtro existentes ...

  filtered.forEach(site => {
    const item = document.createElement("div");
    item.className = `site-item ${site.status}`;
    item.dataset.id = site.id;
    if (State.selectedSite === site.id) item.classList.add("selected");

    // NOVO: usa metric_value quando disponível, fallback para utilization
    let displayVal = "—";
    let displayClass = site.status;

    if (site.metric_value != null) {
      if (site.metric_is_share) {
        displayVal = `${site.metric_value}%`;
        displayClass = "";  // sem colorização para métricas de volume
      } else {
        const suffix = _getMetricSuffix(metric);
        displayVal = `${site.metric_value}${suffix}`;
        displayClass = site.status;
      }
    } else if (site.utilization != null) {
      displayVal = `${site.utilization}%`;
    }

    item.innerHTML = `
      <span class="site-dot" style="background:${STATUS_COLORS[site.status]}"></span>
      <span class="site-name">${_esc(site.name)}</span>
      <span class="site-util ${displayClass}">${displayVal}</span>`;

    item.addEventListener("click", () => State.set("selectedSite", site.id));
    el.appendChild(item);
  });
}

// Helper para sufixo de unidade (NOVO)
function _getMetricSuffix(metric) {
  if (metric.includes("utilization") || metric === "accessibility") return "%";
  if (metric.includes("throughput")) return " Mbps";
  if (metric.includes("traffic_volume")) return " MB";
  if (metric === "rsrp") return " dBm";
  if (metric === "rsrq") return " dB";
  return "";
}
```

**Adicionar cabeçalho dinâmico ao HTML** (`index.html`, dentro de `#site-panel`):

```html
<div class="panel-header">
  <span class="panel-label">Sites</span>
  <span id="site-metric-header" class="panel-meta-right">Utilização</span>
  <span id="site-summary" class="panel-meta"></span>
</div>
```

#### Tarefa 5.4 — Reagir à mudança de métrica para recarregar a lista

Adicionar em `initKpi`:

```javascript
State.on("change:selectedMetric", async () => {
  // Quando métrica muda, pede ao backend os sites com o valor contextual correto
  const { eventId, historicalTimestamp, mode } = State;
  if (!eventId) return;
  const ts = mode === "historical" ? historicalTimestamp : null;
  const metric = State.selectedMetric;
  const sites = await API.getSites(eventId, ts, metric);  // ← passa metric
  if (sites) State.set("sites", sites);
});
```

> **Compatibilidade:** A chamada `API.getSites` precisará do 3º parâmetro `metric`. Verificar `bridge.js` para garantir que o mock também aceita o parâmetro (ou simplesmente o ignora).

---

### FASE 6 — Frontend: `frontend/js/bridge.js`

**Objetivo:** Registrar o novo método `getSiteCells` e atualizar assinatura de `getSites` nos mocks de dev.

#### Tarefa 6.1 — Verificar/adicionar `getSiteCells` no bridge

Localizar a seção de mocks de `bridge.js` e adicionar:

```javascript
// No objeto de mocks (ou na função de fallback quando PyWebView não está disponível):
getSiteCells: async (eventId, siteId) => {
  // Retorna células fictícias para dev
  return [
    { id: `${siteId}-A`, label: `${siteId}-A`, tech: "LTE", freq: "1800" },
    { id: `${siteId}-B`, label: `${siteId}-B`, tech: "LTE", freq: "2600" },
    { id: `${siteId}-C`, label: `${siteId}-C`, tech: "NR",  freq: "3500" },
  ];
},
```

#### Tarefa 6.2 — Atualizar assinatura de `getSites` no wrapper

```javascript
// Antes:
getSites: async (eventId, timestamp) => pywebview.api.get_sites(eventId, timestamp),

// Depois:
getSites: async (eventId, timestamp, metric) =>
  pywebview.api.get_sites(eventId, timestamp, metric ?? "utilization_dl"),
```

E no mock de dev:

```javascript
getSites: async (eventId, timestamp, metric) => {
  // Retorna sites com metric_value e metric_is_share além dos campos já existentes
  return MOCK_SITES.map((s, i, arr) => ({
    ...s,
    metric_value: metric === "user_count" ? Math.round(100 / arr.length) : s.utilization,
    metric_is_share: ["user_count","traffic_volume_dl","traffic_volume_ul"].includes(metric),
  }));
},
```

---

### FASE 7 — Coleta de dados: ajustes no `MockCollector`

O `MockCollector` já gera dados por célula corretamente (linhas 820–846 de `collector.py`). No entanto, o mock do VIP usa `site_id` em vez de `cell_id` para `serving_cell`:

```python
# Linha 857 (atual — incorreto para teste realista)
serving = random.choice(site_ids) if site_ids else ""
```

Corrigir para usar `cell_id` real:

```python
# CORRETO: usar célula real para simular comportamento do collector HTTP
all_cells = [
    c if isinstance(c, str) else c.get("id", "")
    for s in self.event.get("sites", [])
    for c in s.get("cells", [{"id": f"{s['id']}-A"}])
]
serving = random.choice(all_cells) if all_cells else ""
in_event = self._cell_in_event(serving)
```

---

## 5. Ordem de implementação recomendada

A ordem abaixo minimiza dependências quebradas em cada etapa:

```
Tarefa 1.1  →  database.py: get_kpi_series_by_cell
Tarefa 1.2  →  database.py: get_latest_kpi_by_metric
Tarefa 2.2  →  api.py: get_site_cells (novo método independente)
Tarefa 2.3  →  api.py: _aggregate_metric_for_list + get_sites atualizado
Tarefa 2.1  →  api.py: get_kpi_series aceita cell_id
Tarefa 6.1  →  bridge.js: getSiteCells mock
Tarefa 6.2  →  bridge.js: getSites assinatura atualizada
Tarefa 3.1  →  index.html: #cell-selector no HTML
Tarefa 4.1  →  state.js: selectedCell
Tarefa 5.1  →  kpi.js: _populateCellSelector + listener
Tarefa 5.2  →  kpi.js: _refreshChart passa cell_id
Tarefa 5.3  →  kpi.js: _renderSiteList contextual
Tarefa 5.4  →  kpi.js: change:selectedMetric recarrega lista
Tarefa 7    →  collector.py: MockCollector VIP usa cell_id
```

---

## 6. Casos especiais e decisões de design

### 6.1 Sites sem células cadastradas no JSON do evento

Alguns sites no evento podem ter `cells: []` (sem células explícitas). Nesses casos:
- `get_site_cells` retorna lista vazia
- `#cell-selector` exibe apenas "Site completo"
- O gráfico continua funcionando normalmente, agrupando pelo `site_id`

### 6.2 Métricas não disponíveis para um site específico (sem dados)

Se `metric_value` retornar `null` para um site, a lista exibe "—" (comportamento atual). O status da bolinha é mantido pela utilização (independente da métrica selecionada).

### 6.3 Seleção de célula ao trocar de métrica

Ao mudar a métrica no dropdown, o seletor de célula **não é resetado** — mantém a célula selecionada. Isso é intencional: o operador pode querer comparar RSRP e throughput da mesma célula ao longo do tempo.

### 6.4 Modo histórico (timeline slider)

O método `get_sites` já aceita `timestamp` para modo histórico. A adição do parâmetro `metric` é orthogonal — deve funcionar igual: retorna o valor contextual filtrado até o timestamp do slider.

### 6.5 Opção "Média" e a linha de threshold no gráfico

Quando `cell_id == "__media__"`, o gráfico exibe a média das células. As linhas de threshold (warning/critical) continuam sendo exibidas normalmente, pois estão vinculadas à métrica, não à célula.

### 6.6 Rolagem da lista com sorting automático para métricas de share

Para `user_count` e `traffic_volume_*`, a lista pode ser re-ordenada por `metric_value DESC` para mostrar os sites mais relevantes no topo (conforme sugerido em `reorganizacao-celulas.md`). Isso é **opcional neste sprint** — pode ser implementado depois sem alterar a estrutura. Para ativar, basta mudar a ordenação em `_renderSiteList` de:

```javascript
const order = { critical: 0, warning: 1, healthy: 2, unknown: 3 };
const sorted = [...sites].sort((a, b) => (order[a.status]??3) - (order[b.status]??3));
```

Para:

```javascript
const isVolumeMetric = ["user_count","traffic_volume_dl","traffic_volume_ul"].includes(metric);
const sorted = [...sites].sort((a, b) => {
  if (isVolumeMetric) {
    // Para volume: ordena por share decrescente (mais relevante no topo)
    return (b.metric_value ?? 0) - (a.metric_value ?? 0);
  }
  // Para desempenho: mantém ordem por status de alerta
  const order = { critical: 0, warning: 1, healthy: 2, unknown: 3 };
  return (order[a.status]??3) - (order[b.status]??3);
});
```

---

## 7. Checklist de testes pós-implementação

- [ ] Abre `--mock` e visualiza lista de sites com "Utilização DL" selecionado → bolinha e valor corretos
- [ ] Troca dropdown para "Usuários Ativos" → coluna da lista muda para porcentagem, cabeçalho muda para "Participação"
- [ ] Troca dropdown para "RSRP Médio" → coluna mostra dBm, valores fazem sentido como média
- [ ] Seleciona um site → `#cell-selector` popula com as células do site + "— Média —"
- [ ] Seleciona uma célula específica → gráfico atualiza para mostrar só aquela célula
- [ ] Seleciona "— Média —" → gráfico mostra curva média de todas as células
- [ ] Seleciona "Site completo" → gráfico volta ao comportamento original
- [ ] Troca de site com célula selecionada → `#cell-selector` reseta para "Site completo"
- [ ] Modo histórico: trocar métrica com slider em posição intermediária → lista atualiza corretamente
- [ ] Linhas de threshold continuam visíveis no gráfico em todos os modos
- [ ] Sites sem células cadastradas: seletor exibe só "Site completo", sem erro no console
- [ ] Abrir no browser puro (sem PyWebView) → mock do `getSiteCells` retorna células fictícias

---

## 8. O que está fora deste plano (decisões para depois)

- **RSRP/RSRQ por célula no VIP**: o VIP já registra `serving_cell`, mas não há gráfico de célula para VIP. Deixar para sprint separado.
- **Notificação de alerta granular por célula**: o sistema de alertas atual já identifica `cell_id` na tabela `alerts`, mas o texto dos alertas pode ser melhorado para indicar qual célula específica disparou. Deixar para sprint separado.
- **Comparação multi-célula no mesmo gráfico**: exibir duas células sobrepostas no mesmo Chart.js. Requer refactor maior do `_initChart` para múltiplos datasets. Deixar para sprint separado.
- **Ordenação automática por share**: descrito na seção 6.6 como opcional — implementar apenas se solicitado.
