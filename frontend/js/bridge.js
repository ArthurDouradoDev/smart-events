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
  get_events: () => ([
    {
      id: "demo-event-ended",
      name: "Evento Demo (Histórico)",
      status: "ENDED",
      start_time: new Date(Date.now() - 5 * 3600 * 1000).toISOString(),
      end_time: new Date(Date.now() - 1 * 3600 * 1000).toISOString(),
      polygon: [[-23.703,-46.701],[-23.697,-46.691],[-23.691,-46.696],[-23.694,-46.705],[-23.703,-46.701]],
      vips: [{name:"Carlos Menezes"},{name:"Ana Rodrigues"},{name:"Roberto Lima"},{name:"Fernanda Costa"},{name:"Patricia Souza"}],
      thresholds: { rsrp_warning:-100, rsrp_critical:-110, utilization_warning:80, utilization_critical:95 },
    }
  ]),
  get_sites: (event_id, timestamp=null, metric=null) => {
    const MOCK_SITES = [
      { id:"ERB-07", name:"ERB-07 Interlagos",   lat:-23.7012, lng:-46.6975, status:"critical", utilization:91, is_event_site:true,
        cells:[{id:"ERB-07-A1",azimuth:0},{id:"ERB-07-A2",azimuth:120},{id:"ERB-07-A3",azimuth:240}] },
      { id:"ERB-03", name:"ERB-03 Av. Interlagos",lat:-23.6958, lng:-46.6940, status:"warning",  utilization:78, is_event_site:true,
        cells:[{id:"ERB-03-A1",azimuth:30},{id:"ERB-03-A2",azimuth:150},{id:"ERB-03-A3",azimuth:270}] },
      { id:"ERB-11", name:"ERB-11 Autódromo Sul", lat:-23.6995, lng:-46.6920, status:"healthy",  utilization:52, is_event_site:true,
        cells:[{id:"ERB-11-A1",azimuth:60},{id:"ERB-11-A2",azimuth:180},{id:"ERB-11-A3",azimuth:300}] },
      { id:"ERB-15", name:"ERB-15 Buffer Norte",  lat:-23.6930, lng:-46.6980, status:"healthy",  utilization:41, is_event_site:true,
        cells:[{id:"ERB-15-A1",azimuth:0},{id:"ERB-15-A2",azimuth:120},{id:"ERB-15-A3",azimuth:240}] },
    ];
    return MOCK_SITES.map((s, i, arr) => ({
      ...s,
      metric_value: metric === "user_count" ? Math.round(100 / arr.length) : s.utilization,
      metric_is_share: ["user_count","traffic_volume_dl","traffic_volume_ul"].includes(metric),
    }));
  },
  get_site_cells: async (event_id, site_id) => {
    return [
      { id: `${site_id}-A`, label: `${site_id}-A`, tech: "LTE", freq: "1800" },
      { id: `${site_id}-B`, label: `${site_id}-B`, tech: "LTE", freq: "2600" },
      { id: `${site_id}-C`, label: `${site_id}-C`, tech: "NR",  freq: "3500" },
    ];
  },
  get_vips: (event_id, timestamp=null) => ([
    { id:"carlos-menezes", name:"Carlos Menezes", role:"CEO Empresa X",  notes:"VIP principal",        in_event:true,  serving_cell:"ERB-07", serving_site: "ERB-07", serving_site_name: "ERB-07 Interlagos", last_timestamp: new Date().toISOString(), rsrp:-85, rsrq:-7,  status:"ok",      rsrp_min:-110, rsrp_max:-40 },
    { id:"ana-rodrigues",  name:"Ana Rodrigues",  role:"Diretora",       notes:null,                   in_event:true,  serving_cell:"ERB-07", serving_site: "ERB-07", serving_site_name: "ERB-07 Interlagos", last_timestamp: new Date().toISOString(), rsrp:-97, rsrq:-12, status:"warning", rsrp_min:-110, rsrp_max:-40 },
    { id:"roberto-lima",   name:"Roberto Lima",   role:"Piloto",         notes:"Convidado especial",   in_event:true,  serving_cell:"ERB-03", serving_site: "ERB-03", serving_site_name: "ERB-03 Av. Interlagos", last_timestamp: new Date().toISOString(), rsrp:-83, rsrq:-6,  status:"ok",      rsrp_min:-110, rsrp_max:-40 },
    { id:"fernanda-costa", name:"Fernanda Costa", role:null,             notes:null,                   in_event:true,  serving_cell:"ERB-11", serving_site: "ERB-11", serving_site_name: "ERB-11 Autódromo Sul", last_timestamp: new Date().toISOString(), rsrp:-88, rsrq:-9,  status:"ok",      rsrp_min:-110, rsrp_max:-40 },
    { id:"patricia-souza", name:"Patricia Souza", role:"Conv. Especial", notes:null,                   in_event:false, serving_cell:"ERB-19", serving_site: null, serving_site_name: null, last_timestamp: new Date(Date.now() - 15 * 60000).toISOString(), rsrp:null,rsrq:null,status:"unknown", rsrp_min:-110, rsrp_max:-40 },
  ]),
  get_kpi_series: (event_id, site_id, metric, minutes, cell_id=null) => {
    const n = Math.floor(minutes) || 60;
    const labels = [];
    const now = Date.now();
    for (let i = n; i >= 0; i--) {
      labels.push(new Date(now - i * 60000).toISOString());
    }
    const gapIdx = Math.floor(n / 2);
    const gaps = [{ from_idx: gapIdx, to_idx: gapIdx + 3, seconds: 180 }];

    if (!cell_id || cell_id === "__all__") {
      const cells_data = {};
      const numCells = 3;
      const values = Array(n + 1).fill(0);

      for (let c = 1; c <= numCells; c++) {
        const cid = `${site_id}-Cell${c}`;
        const cvals = [];
        const offset = 30 + c * 10;
        const speed = 0.2 + c * 0.05;
        for (let i = 0; i <= n; i++) {
          const val = +(offset + Math.sin((n - i) * speed) * 15 + Math.random() * 5).toFixed(1);
          cvals.push(val);
          // Agregação simulada
          if (metric === "accessibility" || metric === "accessibility") {
            values[i] = values[i] === 0 ? val : Math.min(values[i], val);
          } else if (metric.includes("throughput")) {
            values[i] += val;
          } else {
            values[i] = Math.max(values[i], val);
          }
        }
        cells_data[cid] = cvals;
      }

      for (let i = 0; i <= n; i++) {
        values[i] = +values[i].toFixed(1);
      }

      return {
        ok: true, labels, values, cells_data, gaps,
        thresholds: { warning: 80, critical: 95 },
      };
    } else {
      const values = [];
      for (let i = n; i >= 0; i--) {
        values.push(+(40 + Math.sin(i * 0.3) * 20 + Math.random() * 8).toFixed(1));
      }
      return {
        ok: true, labels, values, gaps,
        thresholds: { warning: 80, critical: 95 },
      };
    }
  },
  get_alerts: (event_id, timestamp=null) => ([
    { id:1, severity:"CRITICAL", site_id:"ERB-07", cell_id:"ERB-07-A2", message:"Utilização crítica: 91% em ERB-07", timestamp:new Date().toISOString() },
    { id:2, severity:"WARNING",  site_id:"ERB-07", cell_id:"ERB-07",    message:"RSRP baixo para Ana Rodrigues: -97 dBm", timestamp:new Date().toISOString() },
  ]),
  get_app_status: () => ({ recording:true, db_size_mb:4.2, now:new Date().toISOString() }),
  get_collection_status: () => {
    const now = Date.now();
    return {
      ok: true,
      recording: true,
      kpi: { state:"ok", last_success:new Date(now - 12000).toISOString(), last_count:24, duration_s:0.84, error:null, interval_s:120 },
      vip: { state:"ok", last_success:new Date(now - 6000).toISOString(),  last_count:30, duration_s:1.20, error:null, interval_s:60, mode:"express", vips_total:5, vips_with_data:4 },
      session: { needs_interactive:false, region:"SP" },
      now: new Date(now).toISOString(),
    };
  },
  activate_event: (id, mock) => _mock.get_active_event(),
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
  sync_events: () => ({ ok: true, stats: { vips: { sincronizados: 0, erros: 0 }, events: { sincronizados: 1, erros: 0 } } }),
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

  get_vip_series: (event_id, vip_name, minutes) => {
    // Espalha ~200 pontos pela janela (até 7 dias no mock) para exercitar
    // os divisores de dia no gráfico do popup de VIP.
    const span = Math.min(Math.max(Math.floor(minutes) || 60, 1), 7 * 24 * 60);
    const points = 200;
    const stepMs = (span * 60000) / points;
    const series = [];
    const now = Date.now();
    for (let i = points; i >= 0; i--) {
      series.push({
        timestamp:    new Date(now - i * stepMs).toISOString(),
        rsrp:         +(-88 + (Math.random() - 0.5) * 16).toFixed(1),
        rsrq:         +(-9  + (Math.random() - 0.5) * 6).toFixed(1),
        serving_cell: "ERB-07",
        in_event:     1,
      });
    }
    return { ok: true, series };
  },
  clear_event_history: (eventId) => ({ ok: true }),
  refresh_vips: (eventId) => ({ ok: true, count: 5 }),
  check_vpn: () => ({ ok: true, connected: true, target: "10.220.50.9" }),
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
  getSites:         (eventId, timestamp=null, metric=null) => API.call("get_sites", eventId, timestamp, metric),
  getSiteCells:     (eventId, siteId)        => API.call("get_site_cells", eventId, siteId),
  getKpiSeries:     (eventId, siteId, m, w, cellId=null) => API.call("get_kpi_series", eventId, siteId, m, w, cellId),
  getVips:          (eventId, timestamp=null) => API.call("get_vips", eventId, timestamp),
  refreshVips:      (eventId)                 => API.call("refresh_vips", eventId),
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
