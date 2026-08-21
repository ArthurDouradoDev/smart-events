/**
 * kpi_overview.js — Visão geral full-screen com nove gráficos sincronizados.
 */

import State from "./state.js";
import API from "./bridge.js";

const PANELS = {
  "4G": [
    { id: "accessibility", title: "Acessibilidade", metrics: ["accessibility"] },
    { id: "availability", title: "Availability", metrics: ["availability"] },
    { id: "drop_rate", title: "Drop", metrics: ["drop_rate"] },
    { id: "utilization_dl", title: "DL PRB", metrics: ["utilization_dl"] },
    { id: "utilization_ul", title: "UL PRB", metrics: ["utilization_ul"] },
    { id: "interference_ul", title: "Interferência", metrics: ["interference_ul"] },
    { id: "throughput_dl", title: "Throughput DL", metrics: ["throughput_dl"] },
    { id: "throughput_ul", title: "Throughput UL", metrics: ["throughput_ul"] },
    { id: "user_count", title: "UE médio", metrics: ["user_count"] },
  ],
  "5G": [
    { id: "accessibility", title: "Acessibilidade", metrics: ["accessibility"] },
    { id: "drop_rate", title: "Drop", metrics: ["drop_rate"] },
    { id: "user_count", title: "User Médio", metrics: ["user_count"] },
    { id: "availability", title: "Availability", metrics: ["availability"] },
    { id: "interference_ul", title: "UL Interference", metrics: ["interference_ul"] },
    { id: "prb_utility", title: "PRB Utility", metrics: ["utilization_dl", "utilization_ul"], paired: true },
    { id: "throughput", title: "Throughput", metrics: ["throughput_dl", "throughput_ul"], paired: true },
    { id: "traffic_volume_sa", title: "Traffic Volume SA", metrics: ["traffic_volume_dl_sa", "traffic_volume_ul_sa"], paired: true },
    { id: "traffic_volume_nsa", title: "Traffic Volume NSA", metrics: ["traffic_volume_dl_nsa", "traffic_volume_ul_nsa"], paired: true },
  ],
};

const LINE_COLOR = "#388BFD";
const BAR_COLOR = "#AB7DF6";
let _family = "4G";
let _minutes = 60;
let _requestId = 0;
let _activeIndex = null;
let _hoveredPanelId = null;
let _scopeSites = [];
let _scopeClusters = [];
const _charts = new Map();

const _crosshairPlugin = {
  id: "overviewCrosshair",
  afterDatasetsDraw(chart) {
    if (_activeIndex == null || !chart.chartArea || !chart.scales?.x) return;
    const x = chart.scales.x.getPixelForValue(_activeIndex);
    if (!Number.isFinite(x)) return;
    const { top, bottom, left, right } = chart.chartArea;
    if (x < left || x > right) return;
    const ctx = chart.ctx;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(230, 237, 243, 0.72)";
    ctx.setLineDash([3, 3]);
    ctx.stroke();
    ctx.restore();
  },
};

export function initKpiOverview() {
  const modal = document.getElementById("kpi-overview-modal");
  const openButton = document.getElementById("open-kpi-overview");
  const closeButton = document.getElementById("kpi-overview-close");
  if (!modal || !openButton) return;

  openButton.addEventListener("click", _open);
  closeButton?.addEventListener("click", _close);
  modal.addEventListener("click", event => {
    if (event.target === modal) _close();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !modal.classList.contains("hidden")) _close();
  });

  document.querySelectorAll("#kpi-overview-family-tabs [data-family]").forEach(button => {
    button.addEventListener("click", () => {
      if (_family === button.dataset.family) return;
      _family = button.dataset.family;
      _syncFamilyTabs();
      _renderPanelShells();
      void _load();
    });
  });

  document.querySelectorAll("#kpi-overview-time-tabs [data-window]").forEach(button => {
    button.addEventListener("click", () => {
      _minutes = Number(button.dataset.window);
      _syncTimeTabs();
      void _load();
    });
  });

  document.getElementById("kpi-overview-scope-selector")?.addEventListener("change", () => {
    _syncContextLabel();
    void _load();
  });

  State.on("change:sites", sites => {
    if (!_isOpen()) _scopeSites = sites || [];
  });
  State.on("change:clusters", clusters => {
    if (!_isOpen()) _scopeClusters = clusters || [];
  });
  State.on("change:selectedSite", () => _refreshScopeOptions(false));
  State.on("change:activeEvent", () => { if (_isOpen()) void _refreshScopeData(); });
  State.on("change:historicalEvent", () => { if (_isOpen()) void _refreshScopeData(); });
}

function _isOpen() {
  return !document.getElementById("kpi-overview-modal")?.classList.contains("hidden");
}

function _open() {
  const modal = document.getElementById("kpi-overview-modal");
  if (!modal) return;
  document.getElementById("chart-popup-modal")?.classList.add("hidden");
  _family = State.techFilter === "5G" ? "5G" : "4G";
  _minutes = [0, 15, 30, 60].includes(Number(State.timeWindow)) ? Number(State.timeWindow) : 60;
  _scopeSites = State.sites || [];
  _scopeClusters = State.clusters || [];
  modal.classList.remove("hidden");
  _syncFamilyTabs();
  _syncTimeTabs();
  _refreshScopeOptions(true);
  _renderPanelShells();
  void _load();
  void _refreshScopeData();
}

function _close() {
  document.getElementById("kpi-overview-modal")?.classList.add("hidden");
  _requestId += 1;
  _clearHover();
}

function _syncFamilyTabs() {
  document.querySelectorAll("#kpi-overview-family-tabs [data-family]").forEach(button => {
    button.classList.toggle("active", button.dataset.family === _family);
  });
}

function _syncTimeTabs() {
  document.querySelectorAll("#kpi-overview-time-tabs [data-window]").forEach(button => {
    button.classList.toggle("active", Number(button.dataset.window) === _minutes);
  });
}

function _preferredScopeKey() {
  if (typeof State.selectedSite === "string" && State.selectedSite.startsWith("cluster:")) {
    return State.selectedSite;
  }
  if (_scopeSites.some(site => site.id === State.selectedSite)) {
    return `site:${State.selectedSite}`;
  }
  if (State.clusterFilter && !["all", "compare"].includes(State.clusterFilter)) {
    return `cluster:${State.clusterFilter}`;
  }
  return _scopeSites[0]?.id
    ? `site:${_scopeSites[0].id}`
    : (_scopeClusters[0]?.id ? `cluster:${_scopeClusters[0].id}` : "");
}

function _appendScopeGroup(selector, label, entries, prefix) {
  if (!entries.length) return;
  const group = document.createElement("optgroup");
  group.label = label;
  entries.forEach(entry => {
    const option = document.createElement("option");
    option.value = `${prefix}:${entry.id}`;
    option.textContent = entry.name || entry.id;
    group.appendChild(option);
  });
  selector.appendChild(group);
}

function _refreshScopeOptions(preferState) {
  const selector = document.getElementById("kpi-overview-scope-selector");
  if (!selector) return;
  const previous = selector.value;
  selector.innerHTML = "";
  _appendScopeGroup(selector, "Clusters", _scopeClusters, "cluster");
  _appendScopeGroup(selector, "Sites", _scopeSites, "site");
  const preferred = preferState ? _preferredScopeKey() : previous;
  const values = [...selector.options].map(option => option.value);
  selector.value = values.includes(preferred)
    ? preferred
    : (values.includes(_preferredScopeKey()) ? _preferredScopeKey() : values[0] || "");
  selector.disabled = values.length === 0;
  _syncContextLabel();
}

async function _refreshScopeData() {
  if (!State.eventId || !_isOpen()) return;
  const selector = document.getElementById("kpi-overview-scope-selector");
  const previous = selector?.value || "";
  try {
    const timestamp = State.mode === "historical" ? State.historicalTimestamp : null;
    const [sites, clusters] = await Promise.all([
      API.getSites(State.eventId, timestamp, State.selectedMetric, null),
      API.getClusters(State.eventId),
    ]);
    if (!_isOpen()) return;
    _scopeSites = Array.isArray(sites) ? sites : _scopeSites;
    _scopeClusters = Array.isArray(clusters) ? clusters : _scopeClusters;
    _refreshScopeOptions(false);
    if (selector?.value && selector.value !== previous) void _load();
  } catch (error) {
    console.error("Erro ao atualizar escopos da visão geral:", error);
  }
}

function _selectedScope() {
  const value = document.getElementById("kpi-overview-scope-selector")?.value || "";
  const splitAt = value.indexOf(":");
  return splitAt > 0
    ? { scope: value.slice(0, splitAt), scopeId: value.slice(splitAt + 1) }
    : { scope: "", scopeId: "" };
}

function _syncContextLabel() {
  const selector = document.getElementById("kpi-overview-scope-selector");
  const context = document.getElementById("kpi-overview-context");
  if (context) context.textContent = selector?.selectedOptions?.[0]?.textContent || "Nenhum escopo disponível";
}

function _destroyCharts() {
  _charts.forEach(chart => chart.destroy());
  _charts.clear();
  _activeIndex = null;
  _hoveredPanelId = null;
}

function _renderPanelShells() {
  _destroyCharts();
  const grid = document.getElementById("kpi-overview-grid");
  if (!grid) return;
  grid.innerHTML = "";
  PANELS[_family].forEach(panel => {
    const article = document.createElement("article");
    article.className = "kpi-overview-card";
    article.dataset.panelId = panel.id;
    article.dataset.metrics = panel.metrics.join(",");
    article.dataset.crosshairIndex = "";
    article.innerHTML = `
      <div class="kpi-overview-card-header">
        <h3>${_esc(panel.title)}</h3>
        <span class="kpi-overview-card-unit"></span>
      </div>
      <div class="kpi-overview-chart-wrap">
        <canvas data-overview-panel="${_esc(panel.id)}"></canvas>
        <div class="kpi-overview-no-data hidden">Sem dados no período</div>
        <div class="kpi-overview-tooltip hidden"></div>
      </div>`;
    grid.appendChild(article);
  });
}

function _showState(state, message="") {
  document.getElementById("kpi-overview-loading")?.classList.toggle("hidden", state !== "loading");
  const error = document.getElementById("kpi-overview-error");
  error?.classList.toggle("hidden", state !== "error");
  if (error && state === "error") error.textContent = message || "Não foi possível carregar os KPIs.";
}

async function _load() {
  if (!_isOpen()) return;
  const { scope, scopeId } = _selectedScope();
  if (!State.eventId || !scopeId) {
    _showState("error", "Selecione um site ou cluster para visualizar os KPIs.");
    return;
  }
  const requestId = ++_requestId;
  _showState("loading");
  try {
    const response = await API.getKpiOverview(State.eventId, scope, scopeId, _family, _minutes);
    if (requestId !== _requestId || !_isOpen()) return;
    if (!response?.ok) throw new Error(response?.error || "Resposta inválida da API");
    _showState("ready");
    _renderCharts(response);
  } catch (error) {
    if (requestId !== _requestId) return;
    console.error("Erro ao carregar visão geral de KPIs:", error);
    _showState("error", error?.message || "Não foi possível carregar os KPIs.");
  }
}

function _renderCharts(response) {
  _destroyCharts();
  const labels = response.labels || [];
  PANELS[_family].forEach(panel => {
    const card = document.querySelector(`.kpi-overview-card[data-panel-id="${panel.id}"]`);
    const canvas = card?.querySelector("canvas");
    if (!card || !canvas) return;
    const datasets = panel.metrics.map((metric, index) => _datasetFor(metric, index, panel, response));
    const hasData = datasets.some(dataset => dataset.data.some(value => value != null));
    card.classList.toggle("is-empty", !hasData);
    card.querySelector(".kpi-overview-no-data")?.classList.toggle("hidden", hasData);
    const units = [...new Set(panel.metrics.map(metric => response.units?.[metric]).filter(Boolean))];
    const unitLabel = units.length === 1 ? units[0] : "DL / UL";
    card.querySelector(".kpi-overview-card-unit").textContent = unitLabel;

    let chartRef = null;
    canvas.addEventListener("mousemove", event => {
      if (!chartRef) return;
      const elements = chartRef.getElementsAtEventForMode(event, "index", { intersect: false }, false);
      if (!elements.length) return;
      _hoveredPanelId = panel.id;
      _hideTooltips(panel.id);
      _syncCrosshairs(elements[0].index);
    }, { capture: true });
    canvas.addEventListener("mouseleave", _clearHover);

    chartRef = new Chart(canvas, {
      type: "line",
      plugins: [_crosshairPlugin],
      data: { labels, datasets },
      options: _chartOptions(panel, response),
    });
    _charts.set(panel.id, chartRef);
  });
}

function _datasetFor(metric, index, panel, response) {
  const isUl = panel.paired && index === 1;
  return {
    label: panel.paired ? (isUl ? "UL" : "DL") : panel.title,
    metric,
    unit: response.units?.[metric] || "",
    data: response.metrics?.[metric] || response.labels.map(() => null),
    type: isUl ? "bar" : "line",
    yAxisID: isUl ? "yRight" : "yLeft",
    borderColor: isUl ? BAR_COLOR : LINE_COLOR,
    backgroundColor: isUl ? "rgba(171, 125, 246, 0.30)" : "rgba(56, 139, 253, 0.08)",
    borderWidth: isUl ? 1 : 1.6,
    pointRadius: 0,
    pointHoverRadius: 3,
    tension: 0.25,
    fill: !panel.paired,
    spanGaps: false,
    barPercentage: 0.8,
    categoryPercentage: 0.9,
  };
}

function _chartOptions(panel, response) {
  const primaryMetric = panel.metrics[0];
  const threshold = response.thresholds?.[primaryMetric] || {};
  const annotations = {};
  if (Number.isFinite(Number(threshold.warning))) {
    annotations.warning = _thresholdAnnotation(Number(threshold.warning), "#D29922");
  }
  if (Number.isFinite(Number(threshold.critical))) {
    annotations.critical = _thresholdAnnotation(Number(threshold.critical), "#F85149");
  }
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    normalized: true,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: {
        display: !!panel.paired,
        position: "top",
        align: "end",
        labels: { boxWidth: 8, boxHeight: 8, color: "#8B949E", font: { size: 9 }, padding: 8 },
      },
      tooltip: {
        enabled: false,
        external: context => _renderTooltip(context, panel),
      },
      annotation: { annotations },
    },
    scales: {
      x: {
        grid: { color: "#21262D" },
        ticks: {
          color: "#8B949E", font: { size: 9 }, maxTicksLimit: 4, maxRotation: 0,
          callback: (_, index) => _formatTime(response.labels?.[index]),
        },
      },
      yLeft: {
        position: "left",
        grid: { color: "#21262D" },
        ticks: { color: "#8B949E", font: { size: 9 }, maxTicksLimit: 4, callback: _formatNumber },
      },
      yRight: {
        display: !!panel.paired,
        position: "right",
        grid: { drawOnChartArea: false },
        ticks: { color: BAR_COLOR, font: { size: 9 }, maxTicksLimit: 4, callback: _formatNumber },
      },
    },
  };
}

function _thresholdAnnotation(value, color) {
  return { type: "line", yMin: value, yMax: value, yScaleID: "yLeft", borderColor: color, borderWidth: 1, borderDash: [4, 3] };
}

function _syncCrosshairs(index) {
  if (_activeIndex === index && [..._charts.values()].every(chart => chart)) return;
  _activeIndex = index;
  document.querySelectorAll(".kpi-overview-card").forEach(card => {
    card.dataset.crosshairIndex = index == null ? "" : String(index);
  });
  _charts.forEach(chart => chart.draw());
}

function _clearHover() {
  _hoveredPanelId = null;
  _activeIndex = null;
  document.querySelectorAll(".kpi-overview-card").forEach(card => { card.dataset.crosshairIndex = ""; });
  _hideTooltips();
  _charts.forEach(chart => chart.draw());
}

function _hideTooltips(exceptPanelId=null) {
  document.querySelectorAll(".kpi-overview-card").forEach(card => {
    if (card.dataset.panelId !== exceptPanelId) {
      card.querySelector(".kpi-overview-tooltip")?.classList.add("hidden");
    }
  });
}

function _renderTooltip(context, panel) {
  const card = context.chart.canvas.closest(".kpi-overview-card");
  const tooltip = card?.querySelector(".kpi-overview-tooltip");
  const model = context.tooltip;
  if (!tooltip || _hoveredPanelId !== panel.id || model.opacity === 0 || !model.dataPoints?.length) {
    tooltip?.classList.add("hidden");
    return;
  }
  const index = model.dataPoints[0].dataIndex;
  const timestamp = context.chart.data.labels[index];
  const rows = context.chart.data.datasets.map(dataset => {
    const value = dataset.data[index];
    return `<div class="kpi-overview-tooltip-row"><span>${_esc(dataset.label)}</span><strong>${value == null ? "—" : `${_formatNumber(value)} ${_esc(dataset.unit)}`}</strong></div>`;
  }).join("");
  tooltip.innerHTML = `<div class="kpi-overview-tooltip-time">${_esc(_formatDateTime(timestamp))}</div>${rows}`;
  tooltip.classList.remove("hidden");
  const wrap = card.querySelector(".kpi-overview-chart-wrap");
  let left = model.caretX + 12;
  if (wrap && left + tooltip.offsetWidth + 6 > wrap.clientWidth) left = model.caretX - tooltip.offsetWidth - 12;
  tooltip.style.left = `${Math.max(2, left)}px`;
  tooltip.style.top = `${Math.max(2, model.caretY - 8)}px`;
}

function _formatNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toLocaleString("pt-BR", { maximumFractionDigits: 2 }) : "—";
}

function _formatTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

function _formatDateTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function _esc(value) {
  const element = document.createElement("span");
  element.textContent = String(value ?? "");
  return element.innerHTML;
}

export const KPI_OVERVIEW_PANELS = PANELS;
