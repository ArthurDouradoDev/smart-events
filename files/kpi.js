/**
 * kpi.js — Lista de sites e gráfico de linha temporal.
 */

import State from "./state.js";
import API   from "./bridge.js";

let _chart = null;
let _searchQuery = "";

const METRIC_LABELS = {
  utilization:    "Utilização (%)",
  availability:   "Availability (%)",
  throughput_dl:  "Throughput DL (Mbps)",
  throughput_ul:  "Throughput UL (Mbps)",
  rsrp:           "RSRP Médio (dBm)",
  rsrq:           "RSRQ Médio (dB)",
};

const STATUS_COLORS = {
  healthy:  "#3FB950",
  warning:  "#D29922",
  critical: "#F85149",
  unknown:  "#484F58",
};

// ── Inicialização ─────────────────────────────────────────────────

export function initKpi() {
  _initChart();

  State.on("change:sites",          _renderSiteList);
  State.on("change:selectedSite",   _onSiteSelected);
  State.on("change:selectedMetric", _refreshChart);
  State.on("change:timeWindow",     _refreshChart);

  // Seletor de métrica
  document.getElementById("metric-selector").addEventListener("change", e => {
    State.set("selectedMetric", e.target.value);
  });

  // Abas de janela temporal
  document.querySelectorAll(".time-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".time-tab").forEach(b => b.classList.remove("active"));
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

  const counts = State.siteCounts;
  summary.textContent = `${counts.healthy} ok · ${counts.critical} críticos`;

  // Ordena: crítico → warning → healthy → unknown
  const order = { critical: 0, warning: 1, healthy: 2, unknown: 3 };
  const sorted = [...sites].sort((a, b) => (order[a.status]??3) - (order[b.status]??3));

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

    const util = site.utilization != null ? `${site.utilization}%` : "—";
    item.innerHTML = `
      <span class="site-dot" style="background:${STATUS_COLORS[site.status]}"></span>
      <span class="site-name">${_esc(site.name)}</span>
      <span class="site-util ${site.status}">${util}</span>`;

    item.addEventListener("click", () => State.set("selectedSite", site.id));
    el.appendChild(item);
  });
}

function _onSiteSelected(siteId) {
  // Atualiza seleção visual
  document.querySelectorAll(".site-item").forEach(el => {
    el.classList.toggle("selected", el.dataset.id === siteId);
  });

  const site = State.sites.find(s => s.id === siteId);
  document.getElementById("chart-site-label").textContent = site?.name ?? "—";

  _refreshChart();
}

// ── Gráfico ───────────────────────────────────────────────────────

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
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: items => {
              const lbl = items[0]?.label;
              return lbl ? new Date(lbl).toLocaleTimeString("pt-BR") : "";
            },
          },
        },
        annotation: { annotations: {} },
      },
    },
  });
}

async function _refreshChart() {
  if (!_chart) return;
  const { eventId, selectedSite, selectedMetric, timeWindow } = State;
  if (!eventId || !selectedSite) return;

  const data = await API.getKpiSeries(eventId, selectedSite, selectedMetric, timeWindow || 0);
  if (!data.ok) return;

  // Insere nulls nos gaps para quebrar a linha
  const values = _applyGaps(data.values, data.gaps);

  _chart.data.labels = data.labels;
  _chart.data.datasets[0].data = values;
  _chart.data.datasets[0].label = METRIC_LABELS[selectedMetric] || selectedMetric;

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
  data.gaps?.forEach((gap, i) => {
    annotations[`gap_${i}`] = {
      type: "box",
      xMin: gap.from_idx, xMax: gap.to_idx,
      backgroundColor: "rgba(48,54,61,0.55)",
      borderColor: "transparent",
      label: { display: true, content: "sem dados",
               font: { size: 9 }, color: "#484F58", backgroundColor: "transparent" },
    };
  });

  _chart.options.plugins.annotation.annotations = annotations;
  _chart.update("none");
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

// Exporta refreshChart para o app.js chamar após polling
export { _refreshChart as refreshChart };
