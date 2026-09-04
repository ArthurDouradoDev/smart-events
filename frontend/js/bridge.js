/**
 * bridge.js — Wrapper sobre window.pywebview.api
 *
 * Garante que todos os calls esperem o pywebview estar pronto.
 * Em modo dev (sem pywebview), retorna dados mock para facilitar
 * desenvolvimento do frontend sem rodar Python.
 */

let _ready = false;
let MOCK = false;
let _queue = [];

function checkReady() {
  if (!_ready && window.pywebview) {
    console.log("pywebview detected immediately.");
    _ready = true;
    MOCK = false;
    _queue.forEach(fn => fn());
    _queue = [];
    return true;
  }
  return false;
}

// 1. Check immediately upon execution
checkReady();

// 2. Listen to the official pywebview ready event
window.addEventListener("pywebviewready", () => {
  if (!_ready) {
    console.log("pywebview ready via event.");
    _ready = true;
    MOCK = false;
    _queue.forEach(fn => fn());
    _queue = [];
  }
});

// 3. Stage checks around page loading stages
document.addEventListener("DOMContentLoaded", () => {
  if (checkReady()) return;

  // Quick check shortly after DOMContentLoaded
  setTimeout(() => {
    if (checkReady()) return;
  }, 100);

  // Fallback to MOCK mode if still not ready after 1 second
  setTimeout(() => {
    if (!_ready) {
      if (window.location.hash === "#desktop") {
        console.log("Running in desktop app. Waiting indefinitely for pywebview...");
        return;
      }
      console.log("pywebview not detected. Falling back to MOCK mode.");
      _ready = true;
      MOCK = true;
      _queue.forEach(fn => fn());
      _queue = [];
    }
  }, 1000);
});


// ── Mock data para desenvolvimento frontend ───────────────────────
// Cenários reproduzíveis: use `?collectionScenario=partial` (data, empty,
// partial, error, auth_required ou stale) ao abrir o frontend sem pywebview.
const _mockCollectionScenario = new URLSearchParams(window.location.search).get("collectionScenario") || "data";

// Cenários reproduzíveis do histórico de VIP: use `?vipSeriesScenario=empty`
// (default, dense, empty, error, error_once ou delay) ao abrir o frontend sem pywebview.
const _vipSeriesScenario = new URLSearchParams(window.location.search).get("vipSeriesScenario") || "default";
const _vpnScenario = new URLSearchParams(window.location.search).get("vpnScenario") || "connected";
const _eventDropdownScenario = new URLSearchParams(window.location.search).get("eventDropdownScenario") || "default";
const _kpiTechnologyScenario = new URLSearchParams(window.location.search).get("kpiTechnology") || "4G";
// Visão geral: "empty" devolve a janela sem nenhuma coleta, para exercitar a
// distinção do B6 entre "sem dados" e "sem tráfego".
const _kpiOverviewScenario = new URLSearchParams(window.location.search).get("kpiOverview") || "default";
const _kpiChartScenario = new URLSearchParams(window.location.search).get("kpiChart") || "default";
const _eventExportScenario = new URLSearchParams(window.location.search).get("exportScenario") || "success";
const _vipErrorOnceSeen = new Set(); // nomes de VIP já vistos pelo cenário error_once
let _eventExportPollCount = 0;
let _vpnProbeCount = 0;
let _eventActivated = false;
let _eventSyncAfterActivation = false;

function _mockEvents() {
  const common = {
    start_time: new Date(Date.now() - 5 * 3600 * 1000).toISOString(),
    end_time: new Date(Date.now() + 5 * 3600 * 1000).toISOString(),
    polygon: [], vips: [], thresholds: {},
  };
  if (_eventDropdownScenario === "stale_active") {
    return [
      { ...common, id: "demo-event", name: "Evento Demo", status: "ACTIVE" },
      {
        ...common,
        id: "teste-curitiba",
        name: "Teste Curitiba",
        status: _eventActivated && !_eventSyncAfterActivation ? "ENDED" : "ACTIVE",
      },
    ];
  }
  return [{
    ...common,
    id: "demo-event-ended",
    name: "Evento Demo (Histórico)",
    status: "ENDED",
  }];
}

// Fase 3: throughput e volume chegam ao frontend em bit/s e bit. O mock respeita
// a ordem de grandeza canônica, senão a tela exercitaria uma escala que não
// existe em produção — nenhuma célula real faz 91 bit/s.
const _CANONICAL_MAGNITUDE = 1e6;

function _mockMagnitude(metric) {
  return /throughput|traffic_volume/.test(String(metric || "")) ? _CANONICAL_MAGNITUDE : 1;
}

function _mockKpiCatalog() {
  if (_kpiTechnologyScenario === "5G_NRDUCELL") {
    return { ok: true, technologies: ["5G_NRDUCELL"], metrics: [
      {id:"utilization_dl", technology:"5G_NRDUCELL", name:"DL PRB Utility", unit:"%", site_aggregation:"recalculate"},
      {id:"utilization_ul", technology:"5G_NRDUCELL", name:"UL PRB Utility", unit:"%", site_aggregation:"recalculate"},
      {id:"throughput_dl", technology:"5G_NRDUCELL", name:"Throughput DL", unit:"bit/s", site_aggregation:"recalculate"},
      {id:"throughput_ul", technology:"5G_NRDUCELL", name:"Throughput UL", unit:"bit/s", site_aggregation:"recalculate"},
      {id:"interference_ul", technology:"5G_NRDUCELL", name:"UL Interference Médio", unit:"dBm", site_aggregation:"mean"},
    ]};
  }
  return { ok: true, technologies: ["4G"], metrics: [
    {id:"accessibility", technology:"4G", name:"Acessibilidade de Dados", unit:"%", site_aggregation:"recalculate"},
    {id:"availability", technology:"4G", name:"Availability", unit:"%", site_aggregation:"recalculate"},
    {id:"drop_rate", technology:"4G", name:"Drop Dados", unit:"%", site_aggregation:"recalculate"},
    {id:"utilization_dl", technology:"4G", name:"DL PRB Utility", unit:"%", site_aggregation:"recalculate"},
    {id:"throughput_dl", technology:"4G", name:"Throughput DL", unit:"bit/s", site_aggregation:"recalculate"},
  ]};
}

// Sequência determinística (sem Math.random) que atravessa dois sites,
// contém célula não mapeada, timestamp duplicado e métrica nula — para
// exercitar o tooltip contextual e os estados do popup de forma repetível.
function _mockVipSeriesRows() {
  const now = Date.now();
  // A janela "Hoje" do modal corta em 00:00 local. Com passo fixo de 20 min, a
  // fixture inteira cairia no dia anterior quando a hora atual fosse menor que
  // 2h — o gráfico abriria vazio só por causa do relógio.
  const startOfDay = new Date();
  startOfDay.setHours(0, 0, 0, 0);
  const elapsed = now - startOfDay.getTime();
  const stepMs = Math.min(20 * 60 * 1000, Math.max(1, Math.floor(elapsed / 7)));
  const points = [
    { offset: 6, cell: "ERB-07-A1",      site: "ERB-07", siteName: "ERB-07 Interlagos",     rsrp: -82,   rsrq: -6 },
    { offset: 5, cell: "ERB-07-A1",      site: "ERB-07", siteName: "ERB-07 Interlagos",     rsrp: -85,   rsrq: -7 },
    { offset: 4, cell: "ERB-07-A2",      site: "ERB-07", siteName: "ERB-07 Interlagos",     rsrp: -90,   rsrq: -9 },
    { offset: 3, cell: "UNKNOWN-CELL-99", site: null,     siteName: null,                    rsrp: -101,  rsrq: -14 },
    { offset: 2, cell: "ERB-03-A1",      site: "ERB-03", siteName: "ERB-03 Av. Interlagos", rsrp: -88,   rsrq: -8 },
    { offset: 1, cell: "ERB-03-A1",      site: "ERB-03", siteName: "ERB-03 Av. Interlagos", rsrp: null,  rsrq: -8 },
    { offset: 1, cell: "ERB-03-A2",      site: "ERB-03", siteName: "ERB-03 Av. Interlagos", rsrp: -84,   rsrq: -6 }, // timestamp duplicado (mesmo offset do ponto anterior)
  ];
  return points.map(p => ({
    timestamp: new Date(now - p.offset * stepMs).toISOString(),
    rsrp: p.rsrp, rsrq: p.rsrq,
    serving_cell: p.cell, serving_site: p.site, serving_site_name: p.siteName,
    in_event: 1,
  }));
}
function _mockCollectionStatus() {
  const now = Date.now();
  const state = _mockCollectionScenario;
  const age = state === "stale" ? 8 * 60 * 1000 : 12000;
  const item = (interval, count) => ({
    state, last_attempt_at: new Date(now - 3000).toISOString(),
    last_cycle_ok_at: ["data", "empty", "stale"].includes(state) ? new Date(now - age).toISOString() : null,
    last_data_at: ["data", "empty", "stale"].includes(state) ? new Date(now - age).toISOString() : null,
    last_success: null, last_count: count, received: state === "partial" ? count + 2 : count, calculated: count,
    invalid: state === "partial" ? 2 : 0, duplicate: 0, inserted: count,
    coverage: {
      cells_mapped: state === "partial" ? 4 : 6, cells_expected: 6,
      mapped_objects: count,
      unmapped_objects: state === "partial" ? 2 : 0,
      unmapped_cells: state === "partial" ? ["18NLCTAL01GI", "18NLCTAL02GI"] : [],
      event_cells: ["4G-CTJA02-18-A", "4G-CTJA02-18-B"],
    },
    duration_s: 0.84, error: state === "error" ? "HTTP 500 no iManager" : null,
    cause: state === "auth_required" ? "Sessão expirada; o retry ainda não foi confirmado." : (state === "partial" ? "Duas células configuradas não responderam." : null),
    interval_s: interval,
  });
  const kpi = item(120, state === "data" ? 24 : 0);
  const vip = { ...item(60, state === "data" ? 5 : 0), mode: "incremental", vips_total: 5,
    vips_with_data: state === "data" ? 5 : 0,
    task_causes: state === "error" ? [{ task_id: 2072, vip: "VIP Demo", cause: "Task recusada pelo OSS" }] : [] };
  return {
    ok: true, recording: true, overall_state: state, kpi, vip, alarms: { ...item(180, 4), state: "data" },
    session: { needs_interactive: state === "auth_required", region: "SP",
      host: "10.220.50.9", fars_contract: "sincrono",
      monitoring: { state: state === "auth_required" ? "auth_required" : "data" },
      trace: { state: state === "auth_required" ? "auth_required" : "data" } },
    now: new Date(now).toISOString(),
  };
}

function _mockTwinCells(prefix, family, tech, count) {
  return Array.from({ length: count }, (_, i) => ({
    id: `${prefix}-${i + 1}`,
    azimuth: (i * 30) % 360,
    tech,
    family,
    frequency: family === "5G" ? "3500" : "1800",
  }));
}

const MOCK_SITES = [
  { id:"ERB-07", name:"ERB-07 Interlagos",   lat:-23.7012, lng:-46.6975, status:"critical", utilization:91, is_event_site:true,
    tech_families: ["4G"],
    cells:[{id:"ERB-07-A1",azimuth:0,family:"4G"},{id:"ERB-07-A2",azimuth:120,family:"4G"},{id:"ERB-07-A3",azimuth:240,family:"4G"}] },
  { id:"ERB-03", name:"ERB-03 Av. Interlagos",lat:-23.6958, lng:-46.6940, status:"warning",  utilization:78, is_event_site:true,
    tech_families: ["4G"],
    cells:[{id:"ERB-03-A1",azimuth:30,family:"4G"},{id:"ERB-03-A2",azimuth:150,family:"4G"},{id:"ERB-03-A3",azimuth:270,family:"4G"}] },
  { id:"ERB-11", name:"ERB-11 Autódromo Sul", lat:-23.6995, lng:-46.6920, status:"healthy",  utilization:52, is_event_site:true,
    tech_families: ["4G"],
    cells:[{id:"ERB-11-A1",azimuth:60,family:"4G"},{id:"ERB-11-A2",azimuth:180,family:"4G"},{id:"ERB-11-A3",azimuth:300,family:"4G"}] },
  { id:"ERB-15", name:"ERB-15 Buffer Norte",  lat:-23.6930, lng:-46.6980, status:"healthy",  utilization:41, is_event_site:true,
    tech_families: ["4G"],
    cells:[{id:"ERB-15-A1",azimuth:0,family:"4G"},{id:"ERB-15-A2",azimuth:120,family:"4G"},{id:"ERB-15-A3",azimuth:240,family:"4G"}] },
  {
    id: "SPSMG7", name: "SPSMG7", lat: -23.640913, lng: -46.710655,
    status: "healthy", utilization: 54, is_event_site: true,
    members: [{ site_id: "725483", family: "4G" }, { site_id: "1774059", family: "5G" }],
    tech_families: ["4G", "5G"],
    cells: [
      ..._mockTwinCells("4G-SPSMG7", "4G", "4G", 12),
      ..._mockTwinCells("5G-SPSMG7", "5G", "5G", 3),
    ],
  },
];

const MOCK_CLUSTERS = [
  {
    id: "sul", name: "Arquibancada Sul", color: "#F85149", site_ids: ["ERB-07", "ERB-03"],
    polygon: [[-23.703, -46.701], [-23.691, -46.701], [-23.691, -46.688], [-23.703, -46.688]],
  },
  { id: "campo", name: "Campo (5G)", color: "#388BFD", site_ids: ["SPSMG7"], polygon: [] },
];

const MOCK_EARFCN_CLUSTERS = [
  { id: "earfcn-1276", name: "Portadora 1276", color: "#3FB950", source: "earfcn", family: "4G",
    site_ids: ["ERB-07", "ERB-03", "SPSMG7"] },
  { id: "earfcn-1700", name: "Portadora 1700", color: "#D29922", source: "earfcn", family: "4G",
    site_ids: ["ERB-07", "ERB-03"] },
  { id: "earfcn-627264", name: "Portadora 627264", color: "#00d2ff", source: "earfcn", family: "5G",
    site_ids: ["SPSMG7"] },
];

function _allMockClusters() {
  return _kpiOverviewScenario === "earfcn"
    ? [...MOCK_CLUSTERS, ...MOCK_EARFCN_CLUSTERS]
    : MOCK_CLUSTERS;
}

function _clusterIdsFor(siteId) {
  return _allMockClusters().filter(c => c.site_ids.includes(siteId)).map(c => c.id);
}

// Portadoras (EARFCN) por site — só no cenário "earfcn", mesmo gate de
// MOCK_EARFCN_CLUSTERS, para o seletor de sites exercitar "separar por
// portadora" sem afetar telas que não pedem esse cenário.
const _MOCK_SITE_CARRIERS = {
  "ERB-07": [{ earfcn: "1276", family: "4G", cell_count: 2 }, { earfcn: "1700", family: "4G", cell_count: 1 }],
  "ERB-03": [{ earfcn: "1276", family: "4G", cell_count: 1 }, { earfcn: "1700", family: "4G", cell_count: 2 }],
  SPSMG7: [{ earfcn: "1276", family: "4G", cell_count: 12 }, { earfcn: "627264", family: "5G", cell_count: 3 }],
};

function _mockSiteCarriers(site) {
  return _kpiOverviewScenario === "earfcn" ? (_MOCK_SITE_CARRIERS[site.id] || []) : [];
}

function _mockSites(metric=null, technology_family=null) {
  const family = technology_family === "4G" || technology_family === "5G" ? technology_family : null;
  return MOCK_SITES.map((s, _i, arr) => {
    const cells = family
      ? (s.cells || []).filter(c => !c.family || c.family === family)
      : (s.cells || []);
    // Participação é percentual e não escala; o valor cru da métrica, sim.
    const isShare = ["user_count","traffic_volume_dl","traffic_volume_ul"].includes(metric);
    return {
      ...s,
      original_name: s.original_name || s.name,
      cells,
      members: s.members || [],
      metric_value: metric === "user_count" ? Math.round(100 / arr.length)
        : isShare ? s.utilization : s.utilization * _mockMagnitude(metric),
      metric_is_share: isShare,
      cluster_ids: _clusterIdsFor(s.id),
      carriers: _mockSiteCarriers(s),
    };
  }).filter(s => !family || (s.cells && s.cells.length) || !(s.tech_families || []).length);
}

const _SITE_DYNAMIC_FIELDS = new Set([
  "status", "utilization", "metric_value", "metric_is_share",
]);

function _mockSiteLayout(technology_family=null) {
  return _mockSites(null, technology_family).map(site => Object.fromEntries(
    Object.entries(site).filter(([key]) => !_SITE_DYNAMIC_FIELDS.has(key))
  ));
}

function _mockSiteStatus(metric=null, technology_family=null) {
  return _mockSites(metric, technology_family).map(site => ({
    id: site.id,
    status: site.status,
    utilization: site.utilization,
    metric_value: site.metric_value,
    metric_is_share: site.metric_is_share,
  }));
}

const _mock = {
  get_active_event: () => ({
    ok: true,
    event: {
      id: "demo-event",
      name: "Evento Demo",
      status: "ACTIVE",
      start_time: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
      end_time: new Date(Date.now() + 10 * 3600 * 1000).toISOString(),
      polygon: [[-23.703,-46.701],[-23.697,-46.691],[-23.691,-46.696],[-23.694,-46.705],[-23.703,-46.701]],
      vips: [{name:"Carlos Menezes"},{name:"Ana Rodrigues"},{name:"Roberto Lima"},{name:"Fernanda Costa"},{name:"Patricia Souza"}],
      thresholds: {
        rsrp_warning: { value:-100, unit:"dBm" }, rsrp_critical: { value:-110, unit:"dBm" },
        utilization_warning: { value:80, unit:"%" }, utilization_critical: { value:95, unit:"%" },
      },
    }
  }),
  get_events: () => _mockEvents(),
  get_sites: (_event_id, _timestamp=null, metric=null, technology_family=null) =>
    _mockSites(metric, technology_family),
  get_site_layout: (_event_id, technology_family=null) =>
    _mockSiteLayout(technology_family),
  get_site_status: (_event_id, metric=null, _timestamp=null, technology_family=null) =>
    _mockSiteStatus(metric, technology_family),
  get_clusters: (_eventId) => _allMockClusters().map(c => (
    {
      id: c.id, name: c.name, color: c.color, site_count: c.site_ids.length,
      cell_count: MOCK_SITES.filter(site => c.site_ids.includes(site.id))
        .reduce((total, site) => total + (site.cells || []).length, 0),
      has_partial_selection: false,
      polygon: c.polygon || [],
      ...(c.source ? { source: c.source } : {}),
      ...(c.family ? { family: c.family } : {}),
    }
  )),
  get_kpi_catalog: (_eventId=null) => _mockKpiCatalog(),
  get_site_cells: async (event_id, site_id, technology_family=null) => {
    const site = MOCK_SITES.find(s => s.id === site_id);
    if (site?.cells?.length) {
      const family = technology_family === "4G" || technology_family === "5G" ? technology_family : null;
      return site.cells
        .filter(c => !family || !c.family || c.family === family)
        .map(c => ({
          id: c.id, label: c.id, tech: c.tech, freq: c.freq || c.frequency, family: c.family,
        }));
    }
    return [
      { id: `${site_id}-A`, label: `${site_id}-A`, tech: "LTE", freq: "1800", family: "4G" },
      { id: `${site_id}-B`, label: `${site_id}-B`, tech: "LTE", freq: "2600", family: "4G" },
      { id: `${site_id}-C`, label: `${site_id}-C`, tech: "NR",  freq: "3500", family: "5G" },
    ];
  },
  get_event_cells: (_eventId, technology_family=null) => {
    const family = technology_family === "4G" || technology_family === "5G" ? technology_family : null;
    return MOCK_SITES.flatMap(s => (s.cells || [])
      .filter(c => !family || !c.family || c.family === family)
      .map(c => ({
        id: c.id, name: c.id, family: c.family, site_id: s.id, site_name: s.name,
      })));
  },
  get_vips: (event_id, timestamp=null) => ([
    { id:"carlos-menezes", name:"Carlos Menezes", role:"CEO Empresa X",  notes:"VIP principal",        in_event:true,  serving_cell:"ERB-07", serving_site: "ERB-07", serving_site_name: "ERB-07 Interlagos", last_timestamp: new Date().toISOString(), rsrp:-85, rsrq:-7,  status:"ok",      rsrp_min:-110, rsrp_max:-40 },
    { id:"ana-rodrigues",  name:"Ana Rodrigues",  role:"Diretora",       notes:null,                   in_event:true,  serving_cell:"ERB-07", serving_site: "ERB-07", serving_site_name: "ERB-07 Interlagos", last_timestamp: new Date().toISOString(), rsrp:-97, rsrq:-12, status:"warning", rsrp_min:-110, rsrp_max:-40 },
    { id:"roberto-lima",   name:"Roberto Lima",   role:"Piloto",         notes:"Convidado especial",   in_event:true,  serving_cell:"ERB-03", serving_site: "ERB-03", serving_site_name: "ERB-03 Av. Interlagos", last_timestamp: new Date().toISOString(), rsrp:-83, rsrq:-6,  status:"ok",      rsrp_min:-110, rsrp_max:-40 },
    { id:"fernanda-costa", name:"Fernanda Costa", role:null,             notes:null,                   in_event:true,  serving_cell:"ERB-11", serving_site: "ERB-11", serving_site_name: "ERB-11 Autódromo Sul", last_timestamp: new Date().toISOString(), rsrp:-88, rsrq:-9,  status:"ok",      rsrp_min:-110, rsrp_max:-40 },
    { id:"patricia-souza", name:"Patricia Souza", role:"Conv. Especial", notes:null,                   in_event:false, serving_cell:"SR-SPCNJ9_13", serving_site: null, serving_site_name: "SR-SPCNJ9", last_timestamp: new Date(Date.now() - 15 * 60000).toISOString(), rsrp:null,rsrq:null,status:"unknown", rsrp_min:-110, rsrp_max:-40 },
  ]),
  get_kpi_series: (event_id, site_id, metric, minutes, cell_id=null, technology=null,
                    technology_family=null, scope=null, scope_id=null) => {
    const n = Math.floor(minutes) || 60;
    const labels = [];
    const now = Date.now();
    for (let i = n; i >= 0; i--) {
      labels.push(new Date(now - i * 60000).toISOString());
    }
    const gapIdx = Math.floor(n / 2);
    const gaps = [{ from_idx: gapIdx, to_idx: gapIdx + 3, seconds: 180 }];
    const thresholds = { warning: { value:80, unit:"%" }, critical: { value:95, unit:"%" } };
    const magnitude = _mockMagnitude(metric);
    const makeValues = (offset, speed=0.2) => labels.map(
      (_, i) => +((offset + Math.sin(i * speed) * 12) * magnitude).toFixed(1));
    const family = technology_family === "4G" || technology_family === "5G" ? technology_family : null;

    if (scope === "cluster") {
      const cluster = _allMockClusters().find(c => c.id === scope_id);
      const memberSites = cluster ? MOCK_SITES.filter(s => cluster.site_ids.includes(s.id)) : [];
      const availableFamilies = [...new Set(memberSites.flatMap(s => s.tech_families || []))];
      const families = family
        ? availableFamilies.filter(item => item === family)
        : availableFamilies;
      const techs = families.length ? families : (family ? [] : ["4G"]);
      const series = techs.map((tech, i) => ({
        technology: tech, labels, values: makeValues(35 + i * 15, 0.16 + i * 0.03),
      }));
      return {
        ok: true, labels, values: series.length === 1 ? series[0].values : [],
        series, cells_data: {}, gaps, thresholds,
      };
    }

    if (scope === "cell") {
      const cellEntry = MOCK_SITES.flatMap(s => s.cells || []).find(c => c.id === scope_id);
      const techs = cellEntry?.family
        ? (family && cellEntry.family !== family ? [] : [cellEntry.family])
        : (family ? [family] : ["4G"]);
      const series = techs.map((tech, i) => ({
        technology: tech, labels, values: makeValues(25 + i * 12, 0.14 + i * 0.03),
      }));
      return {
        ok: true, labels, values: series.length === 1 ? series[0].values : [],
        series, cells_data: {}, gaps, thresholds,
      };
    }

    const site = MOCK_SITES.find(s => s.id === site_id);
    const siteCells = (site?.cells || []).filter(c => !family || !c.family || c.family === family);

    if (cell_id === "__media__") {
      const families = family
        ? [family]
        : [...new Set(siteCells.map(c => c.family).filter(Boolean))];
      const techs = families.length ? families : ["4G"];
      let series = techs.map((tech, i) => ({
        technology: tech, labels, values: makeValues(40 + i * 18, 0.18 + i * 0.04),
      }));
      const reasons = Object.fromEntries(techs.map(tech => [tech, "ok"]));
      if (_kpiChartScenario === "sparse" && !family && techs.includes("5G")) {
        const isolated = Math.max(1, Math.floor(labels.length / 4));
        series = series.map(item => item.technology === "5G"
          ? { ...item, values: labels.map((_, index) => index === isolated ? 61 : null) }
          : item);
      }
      if (_kpiChartScenario === "missing" && !family && techs.includes("5G")) {
        series = series.filter(item => item.technology !== "5G");
        reasons["5G"] = "no_data";
      }
      if (_kpiChartScenario === "unknown" && !family) {
        series.push({ technology: null, labels, values: makeValues(72, 0.1) });
      }
      return {
        ok: true, labels, values: series.length === 1 ? series[0].values : [],
        series, cells_data: {}, gaps, thresholds, reasons,
      };
    }

    if (!cell_id || cell_id === "__all__") {
      const cells_data = {};
      const source = siteCells.length
        ? siteCells
        : [1, 2, 3].map(c => ({ id: `${site_id}-Cell${c}` }));
      source.forEach((cell, index) => {
        cells_data[cell.id] = makeValues(30 + index * 8, 0.15 + index * 0.03);
      });
      return { ok: true, labels, values: [], series: [], cells_data, gaps, thresholds };
    }

    return { ok: true, labels, values: makeValues(40, 0.3), gaps, thresholds };
  },
  get_kpi_overview: (event_id, scope, scope_id, technology_family, minutes=60) => {
    const family = technology_family === "5G" ? "5G" : "4G";
    const metricIds = family === "4G"
      ? ["accessibility", "availability", "drop_rate", "utilization_dl", "utilization_ul",
         "interference_ul", "throughput_dl", "throughput_ul", "user_count"]
      : ["accessibility", "drop_rate", "user_count", "availability", "interference_ul",
         "utilization_dl", "utilization_ul", "throughput_dl", "throughput_ul",
         "traffic_volume_dl_sa", "traffic_volume_ul_sa",
         "traffic_volume_dl_nsa", "traffic_volume_ul_nsa"];
    // Fase 3: a API anuncia a unidade canônica, não a bruta do OSS.
    const unitsByMetric = {
      accessibility: "%", availability: "%", drop_rate: "%",
      utilization_dl: "%", utilization_ul: "%", interference_ul: "dBm",
      throughput_dl: "bit/s", throughput_ul: "bit/s", user_count: "usuários",
      traffic_volume_dl_sa: "bit", traffic_volume_ul_sa: "bit",
      traffic_volume_dl_nsa: "bit", traffic_volume_ul_nsa: "bit",
    };
    // Ordem de grandeza canônica: sem isso o Playwright validaria uma escala
    // que não existe em produção (throughput em Mbit/s, volume em Gbit).
    const magnitudeByMetric = {
      throughput_dl: 1e6, throughput_ul: 1e6,
      traffic_volume_dl_nsa: 1e8, traffic_volume_ul_nsa: 1e8,
    };
    // B6: o SA fica sem tentativa nenhuma na maior parte dos minutos — no evento
    // real ele foi 0,049% do tráfego. O painel sai vazio por falta de tráfego,
    // não por falta de coleta, e a tela precisa saber diferenciar.
    const noTrafficMetrics = new Set(
      family === "5G" ? ["traffic_volume_dl_sa", "traffic_volume_ul_sa"] : []);
    const count = minutes === 0 ? 73 : Math.max(16, Number(minutes) + 1);
    const now = Math.floor(Date.now() / 60000) * 60000;
    const labels = _kpiOverviewScenario === "empty" ? [] : Array.from(
      { length: count }, (_, index) =>
        new Date(now - (count - 1 - index) * 60000).toISOString());
    // Deslocamento derivado do id: dois clusters comparados lado a lado precisam
    // render seres visivelmente diferentes no modo mock.
    const scopeSeed = [...String(scope_id || "")].reduce((acc, ch) => acc + ch.charCodeAt(0), 0);
    const scopeOffset = (scope === "cluster" ? 7 : scope === "site_carrier" ? 13 : 0) + (scopeSeed % 11);
    const metrics = {};
    metricIds.forEach((metric, metricIndex) => {
      metrics[metric] = labels.map((_, index) => {
        // Buracos deliberados provam que todas as métricas continuam na mesma grade.
        if (metric === "availability" && index > 0 && index % 11 === 0) return null;
        if (noTrafficMetrics.has(metric)) return null;
        const base = metric.includes("interference") ? -102 : 22 + metricIndex * 5 + scopeOffset;
        const value = base + Math.sin(index * (0.12 + metricIndex * 0.008)) * (metric.includes("interference") ? 4 : 9);
        return +(value * (magnitudeByMetric[metric] || 1)).toFixed(2);
      });
    });
    const collected = labels.length > 0;
    return {
      ok: true, scope, scope_id, technology_family: family, labels, metrics,
      units: Object.fromEntries(metricIds.map(metric => [metric, unitsByMetric[metric] || ""])),
      reasons: Object.fromEntries(metricIds.map(metric => [metric,
        !collected ? "no_data" : (noTrafficMetrics.has(metric) ? "no_traffic" : "ok")])),
      thresholds: Object.fromEntries(metricIds.map(metric => [metric,
        { warning: { value:80, unit:"%" }, critical: { value:95, unit:"%" } }])),
    };
  },
  get_kpi_overview_multi: (event_id, scopes, technology_family, minutes=60) => {
    const entries = [];
    const seen = new Set();
    (scopes || []).forEach(entry => {
      const scope = entry?.scope;
      const scopeId = entry?.scope_id ?? entry?.id;
      const key = `${scope}:${scopeId}`;
      if (!["site", "cluster", "cell", "site_carrier"].includes(scope) || !scopeId || seen.has(key)) return;
      seen.add(key);
      entries.push({ scope, scope_id: String(scopeId) });
    });
    if (!entries.length) {
      return { ok: false, error: "informe ao menos um cluster, site ou célula", labels: [], series: [], units: {}, reasons: {}, thresholds: {} };
    }
    const responses = entries.map(entry => ({
      entry,
      data: _mock.get_kpi_overview(event_id, entry.scope, entry.scope_id, technology_family, minutes),
    }));
    const labels = [...new Set(responses.flatMap(({ data }) => data.labels))].sort();
    const series = responses.map(({ entry, data }) => {
      const byTimestamp = Object.fromEntries(Object.entries(data.metrics).map(([metric, values]) => [
        metric, new Map(data.labels.map((timestamp, index) => [timestamp, values[index]])),
      ]));
      return {
        scope: entry.scope,
        scope_id: entry.scope_id,
        metrics: Object.fromEntries(Object.keys(data.metrics).map(metric => [
          metric, labels.map(timestamp => byTimestamp[metric].get(timestamp) ?? null),
        ])),
      };
    });
    return {
      ok: true, technology_family: responses[0].data.technology_family, labels, series,
      units: responses[0].data.units, reasons: responses[0].data.reasons,
      thresholds: responses[0].data.thresholds, failures: [],
    };
  },
  get_alerts: (event_id, timestamp=null) => ([
    { id:1, severity:"CRITICAL", site_id:"ERB-07", cell_id:"ERB-07-A2",
      site_name:"ERB-07 Interlagos", display_name:"ERB-07 Interlagos", serving_site:"ERB-07",
      message:"Utilização crítica: 91% em ERB-07 Interlagos", timestamp:new Date().toISOString() },
    { id:2, severity:"WARNING",  site_id:"ERB-07", cell_id:"ERB-07-A1",
      site_name:"ERB-07 Interlagos", display_name:"ERB-07-A1", serving_site:"ERB-07",
      message:"RSRP baixo para Ana Rodrigues: -97 dBm", timestamp:new Date().toISOString() },
    { id:3, severity:"WARNING",  site_id:"ERB-03", cell_id:"ERB-03-A1",
      site_name:"ERB-03 Av. Interlagos", display_name:"ERB-03 Av. Interlagos", serving_site:"ERB-03",
      message:"Utilização elevada: 78% em ERB-03 Av. Interlagos", timestamp:new Date().toISOString() },
  ]),
  get_alarms: (event_id, timestamp=null) => {
    const now = Date.now();
    const mk = (i, name, sev, src, inEvent, siteName, siteId = null) => ({
      csn: 90000 + i, event_id, alarm_id: String(3600 + i), alarm_group_id: "268435456",
      alarm_name: name, severity: sev, source: src, ip: `10.0.0.${10 + i}`,
      location: "Interlagos", occur_time: new Date(now - i * 6 * 60000).toISOString(),
      arrive_time: new Date(now - i * 6 * 60000).toISOString(), additional_info: "mock",
      collected_at: new Date(now).toISOString(),
      in_event: inEvent, serving_site: inEvent ? (siteId || src) : null,
      serving_site_name: inEvent ? siteName : null,
    });
    return [
      mk(0, "RF Unit VSWR Threshold Crossed", "Critical", "ERB-07", true,  "ERB-07 Interlagos"),
      mk(1, "Cell Unavailable", "Major", "ERB-03", true,  "ERB-03 Av. Interlagos"),
      mk(2, "Cell Unavailable", "Major", "SR-XYZ99", false, null),
      mk(3, "RF Unit VSWR Threshold Crossed", "Critical", "SR-ABC12", false, null),
      // Alarme correlacionado ao site fundido (Fase 1): serving_site é o id fundido,
      // não o id 4G/5G da EP — é o que faz o triângulo cair no marcador certo.
      mk(4, "Cell Unavailable", "Minor", "5G-SPSMG7", true, "SPSMG7", "SPSMG7"),
    ];
  },
  get_alarm_catalog: () => ({
    ok: true,
    names: [
      "Cell Unavailable", "Link Fault", "RF Unit VSWR Threshold Crossed",
      "Temperature Unacceptable", "-48V Power too High Alarm", "Board Hardware Fault",
    ],
  }),
  get_alarm_filter: (event_id) => ({
    ok: true, names: ["RF Unit VSWR Threshold Crossed", "Cell Unavailable"],
  }),
  set_alarm_filter: (event_id, names) => ({ ok: true, names }),
  refresh_alarms: (event_id) => ({ ok: true, count: 5 }),
  get_app_status: () => ({ recording:true, db_size_mb:4.2, now:new Date().toISOString() }),
  get_collection_status: () => _mockCollectionStatus(),
  activate_event: (id, mock) => {
    _eventActivated = true;
    return _mock.get_active_event();
  },
  acknowledge_alert: (id) => ({ ok:true }),
  acknowledge_all_alerts: (eventId) => ({ ok:true }),
  delete_all_alerts: (eventId) => ({ ok:true }),
  download_alerts_log: (eventId) => ({ ok:true, path: "C:\\Users\\Mock\\Downloads\\mock_alerts.log" }),
  silence_alert: (key) => ({ ok:true }),
  load_event: (path) => _mock.get_active_event(),
  open_file_dialog: () => ({ ok:true, path:"events/sample_event.json" }),
  get_event_timestamps: (event_id) => {
    const timestamps = [];
    const base = Date.now() - 3600 * 1000;
    for (let i = 0; i <= 60; i += 2) {
      timestamps.push(new Date(base + i * 60000).toISOString());
    }
    return timestamps;
  },
  sync_events: () => {
    if (_eventActivated) _eventSyncAfterActivation = true;
    return { ok: true, stats: { vips: { sincronizados: 0, erros: 0 }, events: { sincronizados: 1, erros: 0 } } };
  },
  get_settings: () => ({ ok: true, settings: { server_url: "http://localhost:8000" } }),
  save_settings: (settings) => ({ ok: true, stats: { sincronizados: 1, erros: 0 } }),
  get_server_url: () => ({ ok: true, url: "http://localhost:8000" }),
  open_server_ui: (path = "/") => {
    try { window.open("http://localhost:8000" + path, "_blank"); } catch (e) {}
    return { ok: true, url: "http://localhost:8000" + path };
  },
  get_collection_logs: (limit = 800) => {
    const now = Date.now();
    const lv = ["INFO", "INFO", "WARNING", "ERROR"];
    const logs = [];
    for (let i = 8; i >= 0; i--) {
      logs.push({
        ts: new Date(now - i * 4000).toISOString().slice(0, 19).replace("T", " "),
        level: lv[i % lv.length],
        logger: "core.collector",
        msg: `[mock] ciclo de coleta KPI #${100 - i} — 24 medições inseridas`,
      });
    }
    return { ok: true, logs };
  },
  clear_collection_logs: () => ({ ok: true }),
  download_collection_logs: () => ({ ok: true, path: "C:\\Users\\Mock\\Downloads\\smart_events_coleta.log" }),
  preview_event_export: (eventId, options) => ({
    ok: true, event_id: eventId, event_name: "Evento Mock", event_status: "ACTIVE",
    counts: _eventExportScenario === "empty"
      ? { kpis: 0, vips: 0, alarms: 0, alerts: 0 }
      : { kpis: 8420531, vips: 42721, alarms: 1284, alerts: 317 },
    technologies: ["4G", "5G_NRCELL", "5G_NRDUCELL"],
    period: { min_brasilia: "2026-09-01T09:00:00-03:00", max_brasilia: "2026-09-03T18:00:00-03:00", covered_days: 3 },
    estimated_bytes: _eventExportScenario === "empty" ? 0 : 650000000,
    excel_row_warning: _eventExportScenario === "large" && options?.time_partition === "consolidated",
    ep_source_quality: "preserved", timezone: "America/Sao_Paulo",
  }),
  start_event_export: (eventId, options) => ({
    ok: _eventExportScenario !== "start-error",
    error: _eventExportScenario === "start-error" ? "Falha simulada ao iniciar a exportação." : undefined,
    job: { job_id: "mock-export-1", event_id: eventId, status: "queued",
      message: "Exportação na fila", current: 0, total: 8464853, percent: 0, options },
  }),
  get_event_export_status: (jobId) => {
    _eventExportPollCount += 1;
    if (_eventExportScenario === "failure") return {
      ok: true, job: { job_id: jobId, status: "failed", percent: 34,
        message: "Não foi possível exportar os dados", error: "Falha simulada na exportação." },
    };
    if (_eventExportScenario === "progress" && _eventExportPollCount < 4) return {
      ok: true, job: { job_id: jobId, status: "exporting_kpis",
        message: "Exportando KPIs", current: _eventExportPollCount * 2000000,
        total: 8464853, percent: _eventExportPollCount * 23 },
    };
    return { ok: true, job: { job_id: jobId, status: "ready", message: "Exportação concluída",
        current: 8464853, total: 8464853, percent: 100,
        result: { filename: "smart-events_evento-mock.zip", size_bytes: 650000000,
          records: 8464853, path: "C:\\Users\\Mock\\Downloads\\smart-events_evento-mock.zip" } } };
  },
  cancel_event_export: (jobId) => ({ ok: true, job: { job_id: jobId, status: "cancelled", percent: 0 } }),
  get_latest_event_export: () => ({ ok: true, job: null }),
  open_event_export_folder: () => ({ ok: true }),
  capture_diagnostics: () => ({ ok: true, path: "C:\\Users\\Mock\\data\\diagnostics\\monitoring_10.220.50.9_20260813-120000.json" }),

  get_vip_series: (event_id, vip_name, minutes) => {
    if (_vipSeriesScenario === "empty") return { ok: true, series: [] };
    if (_vipSeriesScenario === "error") {
      return { ok: false, error: "Falha simulada ao consultar o histórico do VIP." };
    }
    if (_vipSeriesScenario === "error_once" && !_vipErrorOnceSeen.has(vip_name)) {
      _vipErrorOnceSeen.add(vip_name);
      return { ok: false, error: "Falha simulada (única) ao consultar o histórico do VIP." };
    }
    const series = _mockVipSeriesRows();
    if (_vipSeriesScenario === "dense") {
      const now = Date.now();
      const startOfDay = new Date();
      startOfDay.setHours(0, 0, 0, 0);
      const span = Math.max(1, now - startOfDay.getTime());
      return {
        ok: true,
        series: Array.from({ length: 2400 }, (_, i) => ({
          timestamp: new Date(startOfDay.getTime() + span * i / 2399).toISOString(),
          rsrp: -98 + Math.sin(i / 17) * 18 + (i % 131 === 0 ? -16 : 0),
          rsrq: -11 + Math.cos(i / 23) * 5 + (i % 173 === 0 ? -3 : 0),
          serving_cell: "ERB-07-A1",
          serving_site: "ERB-07",
          serving_site_name: "ERB-07 Interlagos",
          in_event: 1,
        })),
      };
    }
    if (_vipSeriesScenario === "delay") {
      return new Promise(resolve => setTimeout(() => resolve({ ok: true, series }), 1500));
    }
    return { ok: true, series };
  },
  clear_event_history: (eventId) => ({ ok: true }),
  refresh_vips: (eventId) => ({ ok: true, count: 5 }),
  check_vpn: () => ({
    ok: true,
    connected: _vpnScenario !== "disconnected"
      && !(_vpnScenario === "transient" && _vpnProbeCount++ === 0),
    target: "10.220.50.9",
  }),
  reauth_session: () => ({ ok: true, base_url: "https://10.220.30.9:31943" }),
  get_clientes: () => ({
    ok: true,
    clientes: {
      TIM: { name: "TIM", logo: "", logo_url: "", regionais: ["SP", "RJ"] },
    },
  }),
  sync_clientes: () => ({ ok: true, clientes: 1 }),
  get_credentials_status: (cliente) => ({
    ok: true,
    shared: { username: "", configured: false },
    regionais: (cliente === "TIM" ? ["SP", "RJ"] : []).map(r => ({
      region: r, base_url: "https://10.220.50.9:31943",
      username: "", configured: false, uses_shared: false,
    })),
  }),
  save_credentials: () => ({ ok: true }),
  save_shared_credentials: () => ({ ok: true }),
  delete_credentials: () => ({ ok: true }),
};

// ── Shell da janela ───────────────────────────────────────────────
// Estes mocks sao deliberadamente separados dos dados de dominio acima. No
// navegador eles nunca minimizam ou fecham nada: apenas registram a intencao,
// permitindo validar a barra com ``?chromePreview=1``.
const _chromePreview = new URLSearchParams(window.location.search).get("chromePreview") === "1";
const _windowChromeMock = {
  calls: [],
  state: "maximized",
  regions: null,
};
window.__windowChromeMock = _windowChromeMock;

const _windowMocks = {
  window_minimize: () => {
    _windowChromeMock.calls.push({ method: "window_minimize" });
    _windowChromeMock.state = "minimized";
    return true;
  },
  window_close: () => {
    _windowChromeMock.calls.push({ method: "window_close" });
    return true;
  },
  window_get_state: () => ({
    ok: true,
    mode: _chromePreview ? "custom" : "native",
    attached: _chromePreview,
    dpi: 96,
    state: _windowChromeMock.state,
  }),
  window_set_chrome_regions: payload => {
    _windowChromeMock.calls.push({ method: "window_set_chrome_regions", payload });
    _windowChromeMock.regions = payload;
    return true;
  },
  window_begin_drag: () => {
    _windowChromeMock.calls.push({ method: "window_begin_drag" });
    return true;
  },
  window_toggle_maximize: () => {
    _windowChromeMock.calls.push({ method: "window_toggle_maximize" });
    _windowChromeMock.state = _windowChromeMock.state === "normal" ? "maximized" : "normal";
    return true;
  },
  window_toggle_fullscreen: () => {
    _windowChromeMock.calls.push({ method: "window_toggle_fullscreen" });
    _windowChromeMock.state =
      _windowChromeMock.state === "fullscreen" ? "maximized" : "fullscreen";
    return true;
  },
};

function _callWindowMethod(method, ...args) {
  return new Promise((resolve, reject) => {
    _whenReady(() => {
      try {
        if (MOCK) {
          resolve(_windowMocks[method](...args));
          return;
        }
        const fn = window.pywebview?.api?.[method];
        if (typeof fn !== "function") {
          throw new Error(`Funcao de janela indisponivel: ${method}`);
        }
        const result = fn(...args);
        if (result && typeof result.then === "function") result.then(resolve).catch(reject);
        else resolve(result);
      } catch (error) {
        reject(error);
      }
    });
  });
}

export const windowMinimize = () => _callWindowMethod("window_minimize");
export const windowClose = () => _callWindowMethod("window_close");
export const windowGetState = () => _callWindowMethod("window_get_state");
export const windowSetChromeRegions = payload =>
  _callWindowMethod("window_set_chrome_regions", payload);
export const windowBeginDrag = () => _callWindowMethod("window_begin_drag");
export const windowToggleMaximize = () => _callWindowMethod("window_toggle_maximize");
export const windowToggleFullscreen = () => _callWindowMethod("window_toggle_fullscreen");

// ── Aguarda pywebview estar pronto ────────────────────────────────
function _whenReady(fn) {
  if (_ready || MOCK) return fn();
  _queue.push(fn);
}

window.addEventListener("pywebviewready", () => {
  _ready = true;
  MOCK = false; // Always call python backend when pywebview is ready
  _queue.forEach(fn => fn());
  _queue = [];
});

// ── API pública ───────────────────────────────────────────────────
const API = {
  /**
   * Chama um método Python. Retorna Promise.
   * Em modo mock, retorna dados sintéticos imediatamente.
   */
  call(method, ...args) {
    return new Promise((resolve, reject) => {
      _whenReady(() => {
        try {
          if (MOCK) {
            const fn = _mock[method];
            resolve(fn ? fn(...args) : null);
          } else {
            const result = window.pywebview.api[method](...args);
            if (result && typeof result.then === "function") {
              result.then(resolve).catch(reject);
            } else {
              resolve(result);
            }
          }
        } catch (err) {
          reject(err);
        }
      });
    });
  },

  // Shortcuts
  getActiveEvent:   ()                       => API.call("get_active_event"),
  getEvents:        ()                       => API.call("get_events"),
  loadEvent:        (path)                   => API.call("load_event", path),
  activateEvent:    (id, mock=false, cliente=null) => API.call("activate_event", id, mock, cliente),
  endEvent:         (id)                     => API.call("end_event", id),
  getSites:         (eventId, timestamp=null, metric=null, technologyFamily=null) => API.call("get_sites", eventId, timestamp, metric, technologyFamily),
  getSiteLayout:    (eventId, technologyFamily=null) => API.call("get_site_layout", eventId, technologyFamily),
  getSiteStatus:    (eventId, metric=null, timestamp=null, technologyFamily=null) => API.call("get_site_status", eventId, metric, timestamp, technologyFamily),
  getSiteCells:     (eventId, siteId, technologyFamily=null) => API.call("get_site_cells", eventId, siteId, technologyFamily),
  getEventCells:    (eventId, technologyFamily=null) => API.call("get_event_cells", eventId, technologyFamily),
  getKpiSeries:     (eventId, siteId, m, w, cellId=null, technologyFamily=null, scope=null, scopeId=null) =>
    API.call("get_kpi_series", eventId, siteId, m, w, cellId, null, technologyFamily, scope, scopeId),
  getKpiOverview:   (eventId, scope, scopeId, technologyFamily, minutes=60) =>
    API.call("get_kpi_overview", eventId, scope, scopeId, technologyFamily, minutes),
  getKpiOverviewMulti: (eventId, scopes, technologyFamily, minutes=60) =>
    API.call("get_kpi_overview_multi", eventId, scopes, technologyFamily, minutes),
  getKpiCatalog:    (eventId=null)           => API.call("get_kpi_catalog", eventId),
  getClusters:      (eventId)                => API.call("get_clusters", eventId),
  getVips:          (eventId, timestamp=null) => API.call("get_vips", eventId, timestamp),
  refreshVips:      (eventId)                 => API.call("refresh_vips", eventId),
  getAlarms:        (eventId, timestamp=null) => API.call("get_alarms", eventId, timestamp),
  getAlarmCatalog:  ()                        => API.call("get_alarm_catalog"),
  getAlarmFilter:   (eventId)                 => API.call("get_alarm_filter", eventId),
  setAlarmFilter:   (eventId, names)          => API.call("set_alarm_filter", eventId, names),
  refreshAlarms:    (eventId)                 => API.call("refresh_alarms", eventId),
  getAlerts:        (eventId, timestamp=null) => API.call("get_alerts", eventId, timestamp),
  acknowledgeAlert: (id)                    => API.call("acknowledge_alert", id),
  acknowledgeAllAlerts: (eventId)           => API.call("acknowledge_all_alerts", eventId),
  deleteAllAlerts: (eventId)                => API.call("delete_all_alerts", eventId),
  downloadAlertsLog: (eventId)              => API.call("download_alerts_log", eventId),
  silenceAlert:     (key)                   => API.call("silence_alert", key),
  getAppStatus:     (eventId=null)          => API.call("get_app_status", eventId),
  getCollectionStatus: ()                   => API.call("get_collection_status"),
  openFileDialog:   ()                      => API.call("open_file_dialog"),
  getEventTimestamps: (eventId)             => API.call("get_event_timestamps", eventId),
  syncEvents:       ()                       => API.call("sync_events"),
  getSettings:      ()                       => API.call("get_settings"),
  saveSettings:     (settings)               => API.call("save_settings", settings),
  getServerUrl:     ()                       => API.call("get_server_url"),
  openServerUi:     (path="/")               => API.call("open_server_ui", path),
  getCollectionLogs: (limit=800)             => API.call("get_collection_logs", limit),
  clearCollectionLogs: ()                    => API.call("clear_collection_logs"),
  downloadCollectionLogs: ()                 => API.call("download_collection_logs"),
  previewEventExport: (eventId, options)      => API.call("preview_event_export", eventId, options),
  startEventExport: (eventId, options)        => API.call("start_event_export", eventId, options),
  getEventExportStatus: (jobId)               => API.call("get_event_export_status", jobId),
  cancelEventExport: (jobId)                  => API.call("cancel_event_export", jobId),
  getLatestEventExport: (eventId)             => API.call("get_latest_event_export", eventId),
  openEventExportFolder: (jobId)              => API.call("open_event_export_folder", jobId),
  captureDiagnostics: ()                     => API.call("capture_diagnostics"),

  getVipSeries:     (eventId, vipName, minutes)  => API.call("get_vip_series", eventId, vipName, minutes),
  clearEventHistory: (eventId)                  => API.call("clear_event_history", eventId),
  checkVpn:         ()                          => API.call("check_vpn"),
  reauthSession:    ()                          => API.call("reauth_session"),

  // Credenciais (Cliente → Regional)
  getClientes:           ()                            => API.call("get_clientes"),
  syncClientes:          ()                            => API.call("sync_clientes"),
  getCredentialsStatus:  (cliente)                     => API.call("get_credentials_status", cliente),
  saveCredentials:       (cliente, region, u, p)       => API.call("save_credentials", cliente, region, u, p),
  saveSharedCredentials: (cliente, u, p)               => API.call("save_shared_credentials", cliente, u, p),
  deleteCredentials:     (cliente, region)             => API.call("delete_credentials", cliente, region),
};

export default API;
