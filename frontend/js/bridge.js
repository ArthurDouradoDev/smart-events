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
const _vipErrorOnceSeen = new Set(); // nomes de VIP já vistos pelo cenário error_once
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

function _mockKpiCatalog() {
  if (_kpiTechnologyScenario === "5G_NRDUCELL") {
    return { ok: true, technologies: ["5G_NRDUCELL"], metrics: [
      {id:"utilization_dl", technology:"5G_NRDUCELL", name:"DL PRB Utility", unit:"%", site_aggregation:"recalculate"},
      {id:"utilization_ul", technology:"5G_NRDUCELL", name:"UL PRB Utility", unit:"%", site_aggregation:"recalculate"},
      {id:"throughput_ul", technology:"5G_NRDUCELL", name:"Throughput UL", unit:"unidade OSS pendente", site_aggregation:"sum"},
      {id:"interference_ul", technology:"5G_NRDUCELL", name:"UL Interference Médio", unit:"dBm", site_aggregation:"mean"},
    ]};
  }
  return { ok: true, technologies: ["4G"], metrics: [
    {id:"accessibility", technology:"4G", name:"Acessibilidade de Dados", unit:"%", site_aggregation:"recalculate"},
    {id:"availability", technology:"4G", name:"Availability", unit:"%", site_aggregation:"recalculate"},
    {id:"drop_rate", technology:"4G", name:"Drop Dados", unit:"%", site_aggregation:"recalculate"},
    {id:"utilization_dl", technology:"4G", name:"DL PRB Utility", unit:"%", site_aggregation:"recalculate"},
    {id:"throughput_dl", technology:"4G", name:"Throughput DL", unit:"Mbit/s", site_aggregation:"sum"},
  ]};
}

// Sequência determinística (sem Math.random) que atravessa dois sites,
// contém célula não mapeada, timestamp duplicado e métrica nula — para
// exercitar o tooltip contextual e os estados do popup de forma repetível.
function _mockVipSeriesRows() {
  const now = Date.now();
  const stepMs = 20 * 60 * 1000; // 20min entre pontos
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
      thresholds: { rsrp_warning:-100, rsrp_critical:-110, utilization_warning:80, utilization_critical:95 },
    }
  }),
  get_events: () => _mockEvents(),
  get_sites: (event_id, timestamp=null, metric=null, technology_family=null) => {
    const family = technology_family === "4G" || technology_family === "5G" ? technology_family : null;
    return MOCK_SITES.map((s, i, arr) => {
      const cells = family
        ? (s.cells || []).filter(c => !c.family || c.family === family)
        : (s.cells || []);
      return {
        ...s,
        cells,
        metric_value: metric === "user_count" ? Math.round(100 / arr.length) : s.utilization,
        metric_is_share: ["user_count","traffic_volume_dl","traffic_volume_ul"].includes(metric),
      };
    }).filter(s => !family || (s.cells && s.cells.length) || !(s.tech_families || []).length);
  },
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
  get_vips: (event_id, timestamp=null) => ([
    { id:"carlos-menezes", name:"Carlos Menezes", role:"CEO Empresa X",  notes:"VIP principal",        in_event:true,  serving_cell:"ERB-07", serving_site: "ERB-07", serving_site_name: "ERB-07 Interlagos", last_timestamp: new Date().toISOString(), rsrp:-85, rsrq:-7,  status:"ok",      rsrp_min:-110, rsrp_max:-40 },
    { id:"ana-rodrigues",  name:"Ana Rodrigues",  role:"Diretora",       notes:null,                   in_event:true,  serving_cell:"ERB-07", serving_site: "ERB-07", serving_site_name: "ERB-07 Interlagos", last_timestamp: new Date().toISOString(), rsrp:-97, rsrq:-12, status:"warning", rsrp_min:-110, rsrp_max:-40 },
    { id:"roberto-lima",   name:"Roberto Lima",   role:"Piloto",         notes:"Convidado especial",   in_event:true,  serving_cell:"ERB-03", serving_site: "ERB-03", serving_site_name: "ERB-03 Av. Interlagos", last_timestamp: new Date().toISOString(), rsrp:-83, rsrq:-6,  status:"ok",      rsrp_min:-110, rsrp_max:-40 },
    { id:"fernanda-costa", name:"Fernanda Costa", role:null,             notes:null,                   in_event:true,  serving_cell:"ERB-11", serving_site: "ERB-11", serving_site_name: "ERB-11 Autódromo Sul", last_timestamp: new Date().toISOString(), rsrp:-88, rsrq:-9,  status:"ok",      rsrp_min:-110, rsrp_max:-40 },
    { id:"patricia-souza", name:"Patricia Souza", role:"Conv. Especial", notes:null,                   in_event:false, serving_cell:"SR-SPCNJ9_13", serving_site: null, serving_site_name: "SR-SPCNJ9", last_timestamp: new Date(Date.now() - 15 * 60000).toISOString(), rsrp:null,rsrq:null,status:"unknown", rsrp_min:-110, rsrp_max:-40 },
  ]),
  get_kpi_series: (event_id, site_id, metric, minutes, cell_id=null, technology_family=null) => {
    const n = Math.floor(minutes) || 60;
    const labels = [];
    const now = Date.now();
    for (let i = n; i >= 0; i--) {
      labels.push(new Date(now - i * 60000).toISOString());
    }
    const gapIdx = Math.floor(n / 2);
    const gaps = [{ from_idx: gapIdx, to_idx: gapIdx + 3, seconds: 180 }];
    const thresholds = { warning: 80, critical: 95 };
    const makeValues = (offset, speed=0.2) => labels.map((_, i) => +(offset + Math.sin(i * speed) * 12).toFixed(1));
    const family = technology_family === "4G" || technology_family === "5G" ? technology_family : null;
    const site = MOCK_SITES.find(s => s.id === site_id);
    const siteCells = (site?.cells || []).filter(c => !family || !c.family || c.family === family);

    if (cell_id === "__media__") {
      const families = family
        ? [family]
        : [...new Set(siteCells.map(c => c.family).filter(Boolean))];
      const techs = families.length ? families : ["4G"];
      const series = techs.map((tech, i) => ({
        technology: tech, labels, values: makeValues(40 + i * 18, 0.18 + i * 0.04),
      }));
      return {
        ok: true, labels, values: series.length === 1 ? series[0].values : [],
        series, cells_data: {}, gaps, thresholds,
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
  get_alerts: (event_id, timestamp=null) => ([
    { id:1, severity:"CRITICAL", site_id:"ERB-07", cell_id:"ERB-07-A2", message:"Utilização crítica: 91% em ERB-07", timestamp:new Date().toISOString() },
    { id:2, severity:"WARNING",  site_id:"ERB-07", cell_id:"ERB-07",    message:"RSRP baixo para Ana Rodrigues: -97 dBm", timestamp:new Date().toISOString() },
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
  getSiteCells:     (eventId, siteId, technologyFamily=null) => API.call("get_site_cells", eventId, siteId, technologyFamily),
  getKpiSeries:     (eventId, siteId, m, w, cellId=null, technologyFamily=null) => API.call("get_kpi_series", eventId, siteId, m, w, cellId, null, technologyFamily),
  getKpiCatalog:    (eventId=null)           => API.call("get_kpi_catalog", eventId),
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
  getAppStatus:     ()                      => API.call("get_app_status"),
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
