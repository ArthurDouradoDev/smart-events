/**
 * kpi.js — Lista de sites e gráfico de linha temporal.
 */

import State from "./state.js";
import API   from "./bridge.js";

let _chart = null;
let _popupChart = null;
let _searchQuery = "";

const METRIC_LABELS = {
  utilization_dl:    "Utilização DL (%)",
  traffic_volume_dl: "Volume de Tráfego DL (MB)",
  traffic_volume_ul: "Volume de Tráfego UL (MB)",
  throughput_dl:     "Throughput DL (Mbps)",
  throughput_ul:     "Throughput UL (Mbps)",
  user_count:        "Usuários Ativos",
  accessibility:     "Acessibilidade (%)",
};

const STATUS_COLORS = {
  healthy:  "#3FB950",
  warning:  "#D29922",
  critical: "#F85149",
  unknown:  "#484F58",
};

const CELL_COLORS = [
  "#388BFD", // Modern Blue
  "#56d364", // Soft Emerald Green
  "#ab7df6", // Light Violet
  "#ff7b72", // Coral Red/Pink
  "#ff9b72", // Warm Orange
  "#f692cc", // Pastel Pink
  "#00d2ff", // Neon Cyan
  "#f1e05a", // Bright Amber
];

function hexToRgba(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// ── Inicialização ─────────────────────────────────────────────────

export function initKpi() {
  _initChart();
  _initPopupChart();

  const popupModal = document.getElementById("chart-popup-modal");
  const popupCloseBtn = document.getElementById("popup-chart-close");
  const expandBtn = document.getElementById("expand-chart-btn");
  const reopenBtn = document.getElementById("reopen-chart-btn");

  if (expandBtn) {
    expandBtn.addEventListener("click", () => {
      _openPopup();
    });
  }

  if (reopenBtn) {
    reopenBtn.addEventListener("click", () => {
      _openPopup();
    });
  }

  if (popupCloseBtn && popupModal) {
    popupCloseBtn.addEventListener("click", _closePopup);
    popupModal.addEventListener("click", e => {
      if (e.target === popupModal) _closePopup();
    });
  }

  State.on("change:sites",          _renderSiteList);
  State.on("change:vips",           () => _renderSiteList(State.sites || []));
  State.on("change:selectedSite",   _onSiteSelected);
  State.on("change:selectedMetric", _refreshChart);
  State.on("change:selectedCell",   _refreshChart);
  State.on("change:timeWindow",     _refreshChart);
  State.on("change:historicalTimestamp", _refreshChart);

  // Seletor de métrica
  document.getElementById("metric-selector").addEventListener("change", e => {
    State.set("selectedMetric", e.target.value);
  });

  // Seletor de célula
  const cellSel = document.getElementById("cell-selector");
  if (cellSel) {
    cellSel.addEventListener("change", e => {
      State.set("selectedCell", e.target.value);
    });
  }

  // Controla exibição do tooltip do site completo
  State.on("change:selectedCell", val => {
    const tooltip = document.getElementById("cell-info-tooltip");
    if (tooltip) {
      tooltip.classList.toggle("hidden", val !== "__all__");
    }
  });

  // Quando a métrica muda, busca a lista de sites com os valores correspondentes
  State.on("change:selectedMetric", async () => {
    const { eventId, historicalTimestamp, mode } = State;
    if (!eventId) return;
    const ts = mode === "historical" ? historicalTimestamp : null;
    const metric = State.selectedMetric;
    const sites = await API.getSites(eventId, ts, metric);
    if (sites) State.set("sites", sites);
  });

  // Abas de janela temporal (escopadas ao próprio container — as abas do
  // popup de VIP também usam .time-tab e não devem ser afetadas).
  document.querySelectorAll("#time-tabs .time-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#time-tabs .time-tab").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      State.set("timeWindow", Number(btn.dataset.window));
    });
  });

  // Campo de pesquisa de sites
  const searchInput = document.getElementById("site-search-input");
  if (searchInput) {
    searchInput.addEventListener("input", e => {
      _searchQuery = e.target.value.toLowerCase().trim();
      _renderSiteList(State.sites || []);
    });
  }

  // Reseta a pesquisa ao trocar de evento
  State.on("change:eventId", () => {
    const input = document.getElementById("site-search-input");
    if (input) input.value = "";
    _searchQuery = "";
  });
}

// ── Lista de sites ────────────────────────────────────────────────

function _renderSiteList(sites) {
  const el = document.getElementById("site-list");
  const summary = document.getElementById("site-summary");
  if (!el) return;

  const metric = State.selectedMetric || "utilization_dl";
  const isVolumeMetric = ["user_count", "traffic_volume_dl", "traffic_volume_ul"]
                           .includes(metric);

  // Atualiza cabeçalho da coluna de valor
  const colHeader = document.getElementById("site-metric-header");
  if (colHeader) {
    colHeader.textContent = isVolumeMetric
      ? "Participação"
      : (METRIC_LABELS[metric]?.split(" ")[0] || "Valor");
  }

  const counts = State.siteCounts;
  summary.textContent = `${counts.healthy} ok · ${counts.critical} críticos`;

  // Ordena por volume (share decrescente) ou por status de alerta
  const sorted = [...sites].sort((a, b) => {
    if (isVolumeMetric) {
      return (b.metric_value ?? 0) - (a.metric_value ?? 0);
    }
    const order = { critical: 0, warning: 1, healthy: 2, unknown: 3 };
    return (order[a.status]??3) - (order[b.status]??3);
  });

  // Filtra por busca (nome ou ID)
  const filtered = sorted.filter(site => {
    const name = (site.name || "").toLowerCase();
    const id = (site.id || "").toLowerCase();
    return name.includes(_searchQuery) || id.includes(_searchQuery);
  });

  el.innerHTML = "";
  filtered.forEach(site => {
    const item = document.createElement("div");
    item.className = `site-item ${site.status}`;
    item.dataset.id = site.id;
    if (State.selectedSite === site.id) item.classList.add("selected");

    // Usa metric_value quando disponível, fallback para utilization
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

    const hasVip = (State.vips || []).some(v => v.in_event && v.serving_site === site.id);

    item.innerHTML = `
      <span class="site-dot" style="background:${STATUS_COLORS[site.status]}"></span>
      <span class="site-name">${_esc(site.name)}${hasVip ? ' <span class="vip-crown">👑</span>' : ""}</span>
      <span class="site-util ${displayClass}">${displayVal}</span>`;

    item.addEventListener("click", () => State.set("selectedSite", site.id));
    el.appendChild(item);
  });
}

// Helper para sufixo de unidade
function _getMetricSuffix(metric) {
  if (metric.includes("utilization") || metric === "accessibility") return "%";
  if (metric.includes("throughput")) return " Mbps";
  if (metric.includes("traffic_volume")) return " MB";
  return "";
}

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
    sel.value = "__media__";
    State.set("selectedCell", "__media__");
  } else if (cells && cells.length === 1) {
    // Site com célula única: não faz sentido exibir seletor
    sel.innerHTML = `<option value="${_esc(cells[0].id)}">${_esc(cells[0].label || cells[0].id)}</option>`;
    sel.value = cells[0].id;
    State.set("selectedCell", cells[0].id);
  } else {
    sel.value = "__all__";
    State.set("selectedCell", "__all__");
  }
}

async function _onSiteSelected(siteId) {
  // Atualiza seleção visual e rola para o item selecionado
  document.querySelectorAll(".site-item").forEach(el => {
    const isSelected = el.dataset.id === siteId;
    el.classList.toggle("selected", isSelected);
    if (isSelected) {
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  });

  const site = State.sites.find(s => s.id === siteId);
  document.getElementById("chart-site-label").textContent = site?.name ?? "—";

  await _populateCellSelector(siteId);
}

// ── Gráfico ───────────────────────────────────────────────────────

function _openPopup() {
  const modal = document.getElementById("chart-popup-modal");
  if (!modal) return;
  modal.classList.remove("hidden");
  _refreshChart();
}

function _closePopup() {
  const modal = document.getElementById("chart-popup-modal");
  if (modal) modal.classList.add("hidden");
}

function _updateChartInstance(chart, labels, datasets, legendDisplay, annotations) {
  if (!chart) return;
  chart.data.labels = labels;
  chart.data.datasets = datasets;
  chart.options.plugins.legend.display = legendDisplay;
  chart.options.plugins.annotation.annotations = annotations;
  chart.update("none");
}

function _initPopupChart() {
  const ctx = document.getElementById("popup-kpi-chart");
  if (!ctx) return;

  Chart.defaults.color = "#8B949E";
  Chart.defaults.font.family = "'Segoe UI', sans-serif";
  Chart.defaults.font.size = 11;

  _popupChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 300 },
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          grid:   { color: "#21262D" },
          ticks: {
            maxTicksLimit: 8,
            callback: (_, i, ticks) => {
              const lbl = _popupChart?.data.labels[i];
              if (!lbl) return "";
              return new Date(lbl).toLocaleTimeString("pt-BR", { hour:"2-digit", minute:"2-digit" });
            },
          },
        },
        y: {
          grid:   { color: "#21262D" },
          ticks:  { maxTicksLimit: 5 },
        },
      },
      plugins: {
        legend: {
          display: false,
          position: "top",
          labels: {
            boxWidth: 8,
            boxHeight: 8,
            usePointStyle: true,
            pointStyle: "circle",
            padding: 10,
            font: {
              size: 11,
              weight: "bold"
            }
          }
        },
        tooltip: {
          callbacks: {
            title: items => {
              const lbl = items[0]?.label;
              if (!lbl) return "";
              const d = new Date(lbl);
              // No modo "Evento" (timeWindow 0) o gráfico pode abranger vários
              // dias — inclui a data para orientar; nas demais janelas, só a hora.
              return State.timeWindow === 0
                ? d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
                : d.toLocaleTimeString("pt-BR");
            },
          },
        },
        annotation: { annotations: {} },
      },
    },
  });
}

function _initChart() {
  const ctx = document.getElementById("kpi-chart");
  if (!ctx) return;

  Chart.defaults.color = "#8B949E";
  Chart.defaults.font.family = "'Segoe UI', sans-serif";
  Chart.defaults.font.size = 11;

  _chart = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [{
        data: [],
        borderColor:     "#388BFD",
        backgroundColor: "rgba(56,139,253,0.08)",
        borderWidth:     2,
        pointRadius:     0,
        pointHoverRadius:4,
        tension:         0.3,
        fill:            true,
        spanGaps:        false,  // não conecta pontos sobre gaps
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 300 },
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          grid:   { color: "#21262D" },
          ticks: {
            maxTicksLimit: 8,
            callback: (_, i, ticks) => {
              const lbl = _chart?.data.labels[i];
              if (!lbl) return "";
              return new Date(lbl).toLocaleTimeString("pt-BR", { hour:"2-digit", minute:"2-digit" });
            },
          },
        },
        y: {
          grid:   { color: "#21262D" },
          ticks:  { maxTicksLimit: 5 },
        },
      },
      plugins: {
        legend: {
          display: false,
          position: "top",
          labels: {
            boxWidth: 8,
            boxHeight: 8,
            usePointStyle: true,
            pointStyle: "circle",
            padding: 10,
            font: {
              size: 11,
              weight: "bold"
            }
          }
        },
        tooltip: {
          callbacks: {
            title: items => {
              const lbl = items[0]?.label;
              if (!lbl) return "";
              const d = new Date(lbl);
              // No modo "Evento" (timeWindow 0) o gráfico pode abranger vários
              // dias — inclui a data para orientar; nas demais janelas, só a hora.
              return State.timeWindow === 0
                ? d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
                : d.toLocaleTimeString("pt-BR");
            },
          },
        },
        annotation: { annotations: {} },
      },
    },
  });
}

function _detectGapsJS(labels, maxGapSeconds = 90) {
  const gaps = [];
  for (let i = 1; i < labels.length; i++) {
    const t0 = new Date(labels[i - 1]).getTime();
    const t1 = new Date(labels[i]).getTime();
    const delta = (t1 - t0) / 1000;
    if (delta > maxGapSeconds) {
      gaps.push({ from_idx: i - 1, to_idx: i, seconds: delta });
    }
  }
  return gaps;
}

async function _refreshChart(arg) {
  // fromPoll: atualização automática do ciclo de 30s — não deve reabrir o popup que o usuário fechou.
  const fromPoll = !!(arg && arg.fromPoll);
  if (!_chart) return;
  const { eventId, selectedSite, selectedMetric, selectedCell, timeWindow, mode, historicalTimestamp } = State;
  if (!eventId || !selectedSite) return;

  const cellId = selectedCell || "__all__";
  const queryWindow = mode === "historical" ? 0 : (timeWindow || 0);
  const data = await API.getKpiSeries(eventId, selectedSite, selectedMetric, queryWindow, cellId);
  if (!data.ok) return;

  let labels = data.labels;
  let values = data.values;
  let cellsData = data.cells_data;
  let gaps = data.gaps;

  if (mode === "historical" && historicalTimestamp) {
    const maxTime = new Date(historicalTimestamp).getTime();
    const minTime = timeWindow > 0 ? maxTime - (timeWindow * 60 * 1000) : 0;

    const filteredIndices = [];
    for (let i = 0; i < labels.length; i++) {
      const tsTime = new Date(labels[i]).getTime();
      if (tsTime <= maxTime && tsTime >= minTime) {
        filteredIndices.push(i);
      }
    }

    labels = filteredIndices.map(idx => labels[idx]);
    values = filteredIndices.map(idx => values[idx]);

    if (cellsData) {
      const newCellsData = {};
      Object.keys(cellsData).forEach(cid => {
        newCellsData[cid] = filteredIndices.map(idx => cellsData[cid][idx]);
      });
      cellsData = newCellsData;
    }

    gaps = _detectGapsJS(labels, 90);
  }

  const cellLabel = cellId === "__all__"   ? "Site completo" :
                    cellId === "__media__" ? "Média das células" :
                    cellId;

  let datasets = [];
  let legendDisplay = false;

  if (cellId === "__all__" && cellsData && Object.keys(cellsData).length > 0) {
    const cellIds = Object.keys(cellsData).sort();

    cellIds.forEach((cid, index) => {
      const color = CELL_COLORS[index % CELL_COLORS.length];
      const cellValues = cellsData[cid];
      const adjustedCellValues = _applyGaps(cellValues, gaps);

      datasets.push({
        label: cid,
        data: adjustedCellValues,
        borderColor:     color,
        backgroundColor: hexToRgba(color, 0.02),
        borderWidth:     2,
        pointRadius:     0,
        pointHoverRadius:4,
        tension:         0.3,
        fill:            false,
        spanGaps:        false,
      });
    });
    legendDisplay = datasets.length > 1;
  } else {
    const adjustedValues = _applyGaps(values, gaps);
    datasets.push({
      label: `${METRIC_LABELS[selectedMetric] || selectedMetric} — ${cellLabel}`,
      data: adjustedValues,
      borderColor:     "#388BFD",
      backgroundColor: "rgba(56,139,253,0.08)",
      borderWidth:     2,
      pointRadius:     0,
      pointHoverRadius:4,
      tension:         0.3,
      fill:            true,
      spanGaps:        false,
    });
    legendDisplay = false;
  }

  // Linhas de threshold
  const annotations = {};
  if (data.thresholds?.warning != null) {
    annotations.warnLine = {
      type: "line", yMin: data.thresholds.warning, yMax: data.thresholds.warning,
      borderColor: "#D29922", borderWidth: 1, borderDash: [5, 4],
      label: { display: true, content: "Atenção", position: "end",
               font: { size: 10 }, color: "#D29922", backgroundColor: "transparent" },
    };
  }
  if (data.thresholds?.critical != null) {
    annotations.critLine = {
      type: "line", yMin: data.thresholds.critical, yMax: data.thresholds.critical,
      borderColor: "#F85149", borderWidth: 1, borderDash: [5, 4],
      label: { display: true, content: "Crítico", position: "end",
               font: { size: 10 }, color: "#F85149", backgroundColor: "transparent" },
    };
  }

  // Faixas de gap
  gaps?.forEach((gap, i) => {
    annotations[`gap_${i}`] = {
      type: "box",
      xMin: gap.from_idx, xMax: gap.to_idx,
      backgroundColor: "rgba(48,54,61,0.55)",
      borderColor: "transparent",
      label: { display: true, content: "sem dados",
               font: { size: 9 }, color: "#484F58", backgroundColor: "transparent" },
    };
  });

  // UI Updates and Toggles
  const popupModal = document.getElementById("chart-popup-modal");
  const isPopupOpen = popupModal && !popupModal.classList.contains("hidden");

  if (popupModal) {
    const site = State.sites?.find(s => s.id === selectedSite);
    const siteName = site?.name ?? selectedSite;
    const metricLabel = METRIC_LABELS[selectedMetric] || selectedMetric;
    const titleEl = document.getElementById("popup-chart-title");
    if (titleEl) {
      titleEl.textContent = `Visualização Detalhada — ${siteName} — ${metricLabel} (${cellLabel})`;
    }
  }

  if (cellId === "__all__") {
    // Show placeholder, hide main chart canvas
    document.getElementById("kpi-chart")?.classList.add("hidden");
    document.getElementById("chart-placeholder")?.classList.remove("hidden");

    // Auto-open popup if closed — exceto em atualizações automáticas do poll
    // (não reabrir um popup que o usuário fechou de propósito).
    if (!fromPoll && popupModal && popupModal.classList.contains("hidden")) {
      popupModal.classList.remove("hidden");
    }

    _updateChartInstance(_popupChart, labels, datasets, legendDisplay, annotations);
  } else {
    // Hide placeholder, show main chart canvas
    document.getElementById("kpi-chart")?.classList.remove("hidden");
    document.getElementById("chart-placeholder")?.classList.add("hidden");

    _updateChartInstance(_chart, labels, datasets, legendDisplay, annotations);

    if (isPopupOpen) {
      _updateChartInstance(_popupChart, labels, datasets, legendDisplay, annotations);
    }
  }
}

// ── Helpers ───────────────────────────────────────────────────────

function _applyGaps(values, gaps) {
  if (!gaps?.length) return values;
  const out = [...values];
  gaps.forEach(g => {
    // Insere null no ponto do gap para quebrar a linha
    for (let i = g.from_idx + 1; i < g.to_idx && i < out.length; i++) {
      out[i] = null;
    }
  });
  return out;
}

function _esc(str) {
  return String(str ?? "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// Exporta refreshChart para o app.js chamar após o polling (sem reabrir popup fechado).
export function refreshChart() {
  return _refreshChart({ fromPoll: true });
}
