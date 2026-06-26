/**
 * vip.js — Renderização do painel de VIPs + modal de detalhes.
 */

import API   from "./bridge.js";
import State from "./state.js";

const STATUS_COLORS = {
  ok:       "#3FB950",
  warning:  "#D29922",
  critical: "#F85149",
  unknown:  "#484F58",
};

let _modalChart = null;
let _currentVip = null;
let _fullSeries = [];        // série completa do VIP aberto (cache p/ as abas)
let _vipWindow  = "today";   // today | 3d | 7d | all

const _WINDOWS = ["today", "3d", "7d", "all"];

export function initVip() {
  State.on("change:vips", render);
  _initModal();
  _initRefresh();
}

let _refreshing = false;

function _initRefresh() {
  const btn = document.getElementById("vip-refresh-btn");
  if (!btn) return;

  btn.addEventListener("click", async () => {
    if (_refreshing) return;
    const eventId = State.eventId;
    if (!eventId || State.mode !== "active") return;

    _refreshing = true;
    btn.classList.add("spinning");
    btn.disabled = true;
    try {
      const res = await API.refreshVips(eventId);
      if (res && res.ok === false && res.error) {
        console.error("Erro ao atualizar VIPs:", res.error);
      }
      // Recarrega os VIPs do banco (a coleta já inseriu as novas medições).
      const vips = await API.getVips(eventId);
      State.set("vips", vips);
    } catch (e) {
      console.error("Falha ao atualizar VIPs:", e);
    } finally {
      _refreshing = false;
      btn.classList.remove("spinning");
      btn.disabled = false;
    }
  });
}

export function render(vips) {
  const list    = document.getElementById("vip-list");
  const summary = document.getElementById("vip-summary");
  if (!list) return;

  const { inEvent, outEvent } = State.vipCounts;
  summary.textContent = `${inEvent} no evento · ${outEvent} fora`;

  const inEventVips  = vips.filter(v => v.in_event);
  const outEventVips = vips.filter(v => !v.in_event);

  list.innerHTML = "";

  if (inEventVips.length) {
    list.appendChild(_section("No evento"));
    inEventVips.forEach(v => list.appendChild(_card(v)));
  }

  if (outEventVips.length) {
    list.appendChild(_section("Fora do evento"));
    outEventVips.forEach(v => list.appendChild(_card(v, true)));
  }
}

function _section(label) {
  const el = document.createElement("div");
  el.className = "vip-section";
  el.textContent = label;
  return el;
}

function _card(vip, dim = false) {
  const isAtSite = vip.in_event && vip.serving_site;
  const card = document.createElement("div");
  card.className = `vip-card status-${vip.status}${dim ? " out-of-event" : ""}${isAtSite ? " vip-at-site" : ""}`;
  card.style.cursor = "pointer";

  const color = STATUS_COLORS[vip.status] || STATUS_COLORS.unknown;

  const titleAttr = vip.notes ? ` title="${_esc(vip.notes)}"` : "";
  card.innerHTML = `
    <div class="vip-header"${titleAttr}>
      <span class="vip-dot" style="background:${color}"></span>
      <span class="vip-name">${isAtSite ? '<span class="vip-crown">👑</span> ' : ""}${_esc(vip.name)}</span>
      <span class="vip-site">${_esc(vip.serving_cell || "—")}</span>
    </div>
    ${vip.role ? `<div class="vip-role">${_esc(vip.role)}</div>` : ""}
    ${vip.in_event && vip.rsrp != null ? `
      ${_signalBar("RSRP", vip.rsrp, vip.rsrp_min ?? -110, vip.rsrp_max ?? -40, vip.status, "dBm")}
      ${_signalBar("RSRQ", vip.rsrq, -20, -3, _rsrqStatus(vip.rsrq), "dB")}
    ` : ""}`;

  card.addEventListener("click", () => {
    _openModal(vip);
    if (vip.serving_site) {
      State.set("selectedSite", vip.serving_site);
    }
  });

  return card;
}

function _signalBar(label, value, min, max, status, unit) {
  const pct = Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100));
  const fillClass = status === "ok" ? "ok" : status === "warning" ? "warning" : "critical";
  return `
    <div class="signal-bar">
      <span class="signal-label">${label}</span>
      <div class="signal-track">
        <div class="signal-fill ${fillClass}" style="width:${pct.toFixed(1)}%"></div>
      </div>
      <span class="signal-value">${value.toFixed(0)}</span>
    </div>`;
}

function _rsrqStatus(rsrq) {
  if (rsrq == null) return "unknown";
  if (rsrq <= -15) return "critical";
  if (rsrq <= -12) return "warning";
  return "ok";
}

// ── Modal de detalhes ────────────────────────────────────────────

function _initModal() {
  const modal = document.getElementById("vip-detail-modal");
  const btnClose = document.getElementById("vip-modal-close");
  if (!modal || !btnClose) return;

  btnClose.addEventListener("click", _closeModal);
  modal.addEventListener("click", e => {
    if (e.target === modal) _closeModal();
  });

  // Abas de janela temporal (Hoje / 3 dias / 7 dias / Total)
  document.querySelectorAll("#vip-modal-time-tabs .time-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      if (!_currentVip) return;
      _vipWindow = btn.dataset.vipWindow;
      _syncWindowTabs();
      _renderChart(_filterSeries(_vipWindow));
    });
  });
}

function _syncWindowTabs() {
  document.querySelectorAll("#vip-modal-time-tabs .time-tab").forEach(b => {
    b.classList.toggle("active", b.dataset.vipWindow === _vipWindow);
  });
}

// Instante de corte (ms) de cada janela. "today" = 00:00 local de hoje;
// "all" = sem corte (tudo).
function _windowCutoff(win) {
  if (win === "all") return -Infinity;
  if (win === "3d")  return Date.now() - 3 * 24 * 60 * 60 * 1000;
  if (win === "7d")  return Date.now() - 7 * 24 * 60 * 60 * 1000;
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
}

// Filtra a série completa (cache) pela janela selecionada.
function _filterSeries(win) {
  const cutoff = _windowCutoff(win);
  if (cutoff === -Infinity) return _fullSeries;
  return _fullSeries.filter(r => new Date(r.timestamp).getTime() >= cutoff);
}

// Default = menor janela que contém dados (Hoje → 3d → 7d → Total);
// se não houver nenhum registro, abre em "today".
function _pickDefaultWindow() {
  for (const win of _WINDOWS) {
    if (_filterSeries(win).length) return win;
  }
  return "today";
}

function _closeModal() {
  const modal = document.getElementById("vip-detail-modal");
  if (modal) modal.classList.add("hidden");
  _currentVip = null;
  _fullSeries = [];
  if (_modalChart) {
    _modalChart.destroy();
    _modalChart = null;
  }
}

async function _openModal(vip) {
  const modal       = document.getElementById("vip-detail-modal");
  const dotEl       = document.getElementById("vip-modal-dot");
  const nameEl      = document.getElementById("vip-modal-name");
  const roleEl      = document.getElementById("vip-modal-role");
  const cellEl      = document.getElementById("vip-modal-cell");
  const rsrpEl      = document.getElementById("vip-modal-rsrp");
  const rsrqEl      = document.getElementById("vip-modal-rsrq");
  const siteEl      = document.getElementById("vip-modal-site");
  const lastSeenEl  = document.getElementById("vip-modal-last-seen");
  if (!modal) return;

  _currentVip = vip;

  // Preenche cabeçalho
  const color = STATUS_COLORS[vip.status] || STATUS_COLORS.unknown;
  dotEl.style.background = color;
  nameEl.textContent = vip.name;
  roleEl.textContent = vip.role || "";
  cellEl.textContent = vip.serving_cell || "—";
  rsrpEl.textContent = vip.rsrp != null ? `${vip.rsrp.toFixed(0)} dBm` : "—";
  rsrqEl.textContent = vip.rsrq != null ? `${vip.rsrq.toFixed(1)} dB`  : "—";

  if (siteEl) {
    const siteDisplay = vip.serving_site_name || vip.serving_site || vip.serving_cell || "—";
    siteEl.textContent = siteDisplay;
    siteEl.title = siteDisplay !== "—" ? siteDisplay : "";
  }

  if (lastSeenEl) {
    if (vip.last_timestamp) {
      const d = new Date(vip.last_timestamp);
      const dateStr = d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" });
      const timeStr = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      lastSeenEl.textContent = `${dateStr} ${timeStr}`;
    } else {
      lastSeenEl.textContent = "—";
    }
  }

  modal.classList.remove("hidden");

  // Busca a série completa do VIP uma única vez; as abas filtram o cache.
  _fullSeries = [];
  try {
    const res = await API.getVipSeries(State.eventId, vip.name, 525600);
    if (res && res.ok) _fullSeries = res.series || [];
  } catch (e) {
    console.error("Erro ao buscar série VIP:", e);
  }

  // Se o VIP em foco mudou enquanto a busca corria, descarta este resultado.
  if (_currentVip !== vip) return;

  _vipWindow = _pickDefaultWindow();
  _syncWindowTabs();
  _renderChart(_filterSeries(_vipWindow));
}

// Detecta a virada de dia entre pontos consecutivos e devolve anotações
// de linha vertical (com rótulo da data) para o Chart.js.
function _dayDividers(series) {
  const dividers = {};
  for (let i = 1; i < series.length; i++) {
    const prev = new Date(series[i - 1].timestamp);
    const cur  = new Date(series[i].timestamp);
    if (prev.toDateString() !== cur.toDateString()) {
      dividers[`day_${i}`] = {
        type: "line",
        xMin: i - 0.5, xMax: i - 0.5,
        borderColor: "#484F58", borderWidth: 1, borderDash: [3, 3],
        label: {
          display: true,
          content: cur.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }),
          position: "start", font: { size: 9 }, color: "#8B949E",
          backgroundColor: "rgba(13,17,23,0.75)",
        },
      };
    }
  }
  return dividers;
}

function _renderChart(series) {
  const vip       = _currentVip;
  const noDataEl  = document.getElementById("vip-modal-no-data");
  const wrapperEl = document.getElementById("vip-modal-chart-wrapper");
  if (!vip) return;

  const color = STATUS_COLORS[vip.status] || STATUS_COLORS.unknown;

  // Destrói chart anterior
  if (_modalChart) {
    _modalChart.destroy();
    _modalChart = null;
  }

  if (!series.length) {
    noDataEl.classList.remove("hidden");
    wrapperEl.classList.add("hidden");
    return;
  }

  noDataEl.classList.add("hidden");
  wrapperEl.classList.remove("hidden");

  const labels = series.map(r => {
    const d = new Date(r.timestamp);
    return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  });
  const rsrpValues = series.map(r => r.rsrp ?? null);
  const rsrqValues = series.map(r => r.rsrq ?? null);
  const dayLines   = _dayDividers(series);

  const config = State.activeEvent || State.historicalEvent;
  const thresholds = config?.thresholds || {};
  const warnTh = thresholds.rsrp_warning ?? -100;
  const critTh = thresholds.rsrp_critical ?? -110;

  const canvas = document.getElementById("vip-modal-chart");
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
            title: (items) => {
              const r = series[items[0]?.dataIndex];
              if (!r) return "";
              return new Date(r.timestamp).toLocaleString("pt-BR", {
                day: "2-digit", month: "2-digit",
                hour: "2-digit", minute: "2-digit",
              });
            },
            label: (ctx) => {
              const unit = ctx.dataset.yAxisID === "yRsrp" ? " dBm" : " dB";
              return `${ctx.dataset.label}: ${ctx.raw?.toFixed(1)}${unit}`;
            },
          },
        },
        annotation: {
          annotations: {
            ...dayLines,
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
}

function _esc(str) {
  return String(str ?? "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
