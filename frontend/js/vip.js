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

const STATUS_LABELS = {
  ok:       "OK",
  warning:  "Atenção",
  critical: "Crítico",
  unknown:  "Desconhecido",
};

// Desenha uma linha vertical pontilhada e semitransparente sobre o ponto
// ativo do gráfico do popup de VIP. Plugin local (não registrado
// globalmente) para não afetar os gráficos de kpi.js.
const _vipHoverLinePlugin = {
  id: "vipHoverLine",
  afterDraw(chart) {
    const active = chart.getActiveElements();
    if (!active.length) return;
    const { ctx, chartArea } = chart;
    const x = active[0].element.x;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, chartArea.top);
    ctx.lineTo(x, chartArea.bottom);
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = "rgba(139, 148, 158, 0.5)";
    ctx.stroke();
    ctx.restore();
  },
};

let _modalChart = null;
let _currentVip = null;
let _fullSeries = [];        // série completa do VIP aberto (cache p/ as abas)
let _vipWindow  = "today";   // today | 3d | 7d | all
let _requestGen = 0;         // token de geração: invalida respostas tardias
let _lastFocusedEl = null;   // elemento a receber foco de volta ao fechar

const _WINDOWS = ["today", "3d", "7d", "all"];

export function initVip() {
  State.on("change:vips", render);
  // Trocar o evento ativo/histórico invalida a associação célula-site do
  // popup aberto (ela depende do evento visualizado); fechar é mais seguro
  // do que tentar recalcular em cima de um VIP que pode nem existir mais.
  State.on("change:activeEvent", _closeModal);
  State.on("change:historicalEvent", _closeModal);
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

  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `Detalhes de ${vip.name}`);

  card.addEventListener("click", () => {
    _openModal(vip, card);
    if (vip.serving_site) {
      State.set("selectedSite", vip.serving_site);
    }
  });
  card.addEventListener("keydown", e => {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    card.click();
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
  const btnRetry = document.getElementById("vip-modal-retry");
  if (!modal || !btnClose) return;

  btnClose.addEventListener("click", _closeModal);
  modal.addEventListener("click", e => {
    if (e.target === modal) _closeModal();
  });

  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    if (modal.classList.contains("hidden")) return;
    _closeModal();
  });

  if (btnRetry) {
    btnRetry.addEventListener("click", () => {
      if (!_currentVip) return;
      _loadSeries(_currentVip);
    });
  }

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
    const active = b.dataset.vipWindow === _vipWindow;
    b.classList.toggle("active", active);
    b.setAttribute("aria-pressed", active ? "true" : "false");
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
  const wasOpen = modal && !modal.classList.contains("hidden");
  if (modal) modal.classList.add("hidden");
  _currentVip = null;
  _fullSeries = [];
  _requestGen++; // invalida qualquer requisição em curso
  if (_modalChart) {
    _modalChart.destroy();
    _modalChart = null;
  }
  if (wasOpen && _lastFocusedEl) {
    _lastFocusedEl.focus();
  }
  _lastFocusedEl = null;
}

async function _openModal(vip, triggerEl) {
  const modal       = document.getElementById("vip-detail-modal");
  const modalBox    = modal?.querySelector(".modal-box");
  const dotEl       = document.getElementById("vip-modal-dot");
  const nameEl      = document.getElementById("vip-modal-name");
  const roleEl      = document.getElementById("vip-modal-role");
  const statusEl    = document.getElementById("vip-modal-status");
  const cellEl      = document.getElementById("vip-modal-cell");
  const rsrpEl      = document.getElementById("vip-modal-rsrp");
  const rsrqEl      = document.getElementById("vip-modal-rsrq");
  const siteEl      = document.getElementById("vip-modal-site");
  const lastSeenEl  = document.getElementById("vip-modal-last-seen");
  if (!modal) return;

  _lastFocusedEl = triggerEl || document.activeElement;
  _currentVip = vip;

  // Preenche cabeçalho e faixa de resumo com o estado atual do VIP (não
  // depende da série histórica, então aparece imediatamente).
  const color = STATUS_COLORS[vip.status] || STATUS_COLORS.unknown;
  dotEl.style.background = color;
  nameEl.textContent = vip.name;
  nameEl.title = vip.name;
  roleEl.textContent = vip.role || "";
  roleEl.title = vip.role || "";

  if (statusEl) {
    statusEl.textContent = STATUS_LABELS[vip.status] || STATUS_LABELS.unknown;
    statusEl.className = `vip-modal-status status-${vip.status || "unknown"}`;
  }

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
  modalBox?.focus();

  await _loadSeries(vip);
}

// Busca a série completa do VIP (cache para as abas de janela) e alimenta
// o gráfico. Protegido por token de geração: uma resposta de uma chamada
// anterior (VIP fechado/trocado nesse meio tempo) nunca é renderizada.
async function _loadSeries(vip) {
  const gen = ++_requestGen;

  _fullSeries = [];
  if (_modalChart) {
    _modalChart.destroy();
    _modalChart = null;
  }
  _showState("loading");

  let res;
  try {
    res = await API.getVipSeries(State.eventId, vip.name, 525600);
  } catch (e) {
    console.error("Erro ao buscar série VIP:", e);
    if (gen !== _requestGen) return;
    _showState("error");
    return;
  }

  if (gen !== _requestGen) return; // resposta tardia de uma chamada obsoleta

  if (!res || res.ok === false) {
    _showState("error");
    return;
  }

  _fullSeries = res.series || [];
  _vipWindow = _pickDefaultWindow();
  _syncWindowTabs();
  _renderChart(_filterSeries(_vipWindow));
}

// Alterna entre os estados mutuamente exclusivos da região do gráfico.
function _showState(state) {
  const wrapperEl = document.getElementById("vip-modal-chart-wrapper");
  const loadingEl = document.getElementById("vip-modal-loading");
  const noDataEl  = document.getElementById("vip-modal-no-data");
  const errorEl   = document.getElementById("vip-modal-error");
  const tooltipEl = document.getElementById("vip-modal-tooltip");

  wrapperEl?.classList.toggle("hidden", state !== "chart");
  loadingEl?.classList.toggle("hidden", state !== "loading");
  noDataEl?.classList.toggle("hidden", state !== "empty");
  errorEl?.classList.toggle("hidden", state !== "error");
  tooltipEl?.classList.add("hidden");
}

// Callback "external" do Chart.js: renderiza o tooltip como HTML real (em
// vez de desenhado no canvas) para que o conteúdo — data/hora, site, célula
// e métricas do registro apontado — seja inspecionável no DOM.
function _renderTooltip(context, series) {
  const tooltipEl = document.getElementById("vip-modal-tooltip");
  if (!tooltipEl) return;
  const tooltipModel = context.tooltip;

  const dataPoint = tooltipModel.opacity !== 0 ? tooltipModel.dataPoints?.[0] : null;
  const r = dataPoint ? series[dataPoint.dataIndex] : null;
  if (!r) {
    tooltipEl.classList.add("hidden");
    return;
  }

  const dt = new Date(r.timestamp).toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
  const rsrpTxt = r.rsrp != null ? `${r.rsrp.toFixed(1)} dBm` : "—";
  const rsrqTxt = r.rsrq != null ? `${r.rsrq.toFixed(1)} dB`  : "—";
  const cellTxt = r.serving_cell || "—";
  // Site do próprio registro apontado (nunca o site atual do VIP): cada
  // instante carrega sua própria associação, resolvida pela API no
  // contexto do evento visualizado.
  const siteTxt = r.serving_site_name || r.serving_site
    || (r.serving_cell ? "Site não identificado" : "—");

  tooltipEl.innerHTML = `
    <div class="vip-tooltip-time">${_esc(dt)}</div>
    <div class="vip-tooltip-row"><span>Site</span><span class="vip-tooltip-site">${_esc(siteTxt)}</span></div>
    <div class="vip-tooltip-row"><span>Célula</span><span class="vip-tooltip-cell">${_esc(cellTxt)}</span></div>
    <div class="vip-tooltip-row"><span>RSRP</span><span class="vip-tooltip-rsrp">${_esc(rsrpTxt)}</span></div>
    <div class="vip-tooltip-row"><span>RSRQ</span><span class="vip-tooltip-rsrq">${_esc(rsrqTxt)}</span></div>
  `;
  tooltipEl.classList.remove("hidden");

  const wrapper = tooltipEl.parentElement;
  let left = tooltipModel.caretX + 12;
  const top = tooltipModel.caretY;
  if (wrapper && left + tooltipEl.offsetWidth + 4 > wrapper.clientWidth) {
    left = tooltipModel.caretX - tooltipEl.offsetWidth - 12;
  }
  tooltipEl.style.left = `${Math.max(0, left)}px`;
  tooltipEl.style.top  = `${Math.max(0, top)}px`;
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
  const vip = _currentVip;
  if (!vip) return;

  // Destrói chart anterior
  if (_modalChart) {
    _modalChart.destroy();
    _modalChart = null;
  }

  if (!series.length) {
    _showState("empty");
    return;
  }

  _showState("chart");

  const color = STATUS_COLORS[vip.status] || STATUS_COLORS.unknown;

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
    plugins: [_vipHoverLinePlugin],
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
          enabled: false,
          external: (context) => _renderTooltip(context, series),
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
