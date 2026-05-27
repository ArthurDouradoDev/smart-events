/**
 * bridge.js — Wrapper sobre window.pywebview.api
 *
 * Garante que todos os calls esperem o pywebview estar pronto.
 * Em modo dev (sem pywebview), retorna dados mock para facilitar
 * desenvolvimento do frontend sem rodar Python.
 */

const MOCK = window.__MOCK_MODE__ || !window.pywebview;

// ── Mock data para desenvolvimento frontend ───────────────────────
const _mock = {
  get_active_event: () => ({
    ok: true,
    event: {
      id: "gp-sp-2025",
      name: "GP São Paulo 2025",
      status: "ACTIVE",
      start_time: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
      end_time: new Date(Date.now() + 10 * 3600 * 1000).toISOString(),
      polygon: [[-23.703,-46.701],[-23.697,-46.691],[-23.691,-46.696],[-23.694,-46.705],[-23.703,-46.701]],
      vips: [{name:"Carlos Menezes"},{name:"Ana Rodrigues"},{name:"Roberto Lima"},{name:"Fernanda Costa"},{name:"Patricia Souza"}],
      thresholds: { rsrp_warning:-100, rsrp_critical:-110, utilization_warning:80, utilization_critical:95 },
    }
  }),
  get_events: () => [],
  get_sites: (event_id) => ([
    { id:"ERB-07", name:"ERB-07 Interlagos",   lat:-23.7012, lng:-46.6975, status:"critical", utilization:91, is_event_site:true,
      cells:[{id:"ERB-07-A1",azimuth:0},{id:"ERB-07-A2",azimuth:120},{id:"ERB-07-A3",azimuth:240}] },
    { id:"ERB-03", name:"ERB-03 Av. Interlagos",lat:-23.6958, lng:-46.6940, status:"warning",  utilization:78, is_event_site:true,
      cells:[{id:"ERB-03-A1",azimuth:30},{id:"ERB-03-A2",azimuth:150},{id:"ERB-03-A3",azimuth:270}] },
    { id:"ERB-11", name:"ERB-11 Autódromo Sul", lat:-23.6995, lng:-46.6920, status:"healthy",  utilization:52, is_event_site:true,
      cells:[{id:"ERB-11-A1",azimuth:60},{id:"ERB-11-A2",azimuth:180},{id:"ERB-11-A3",azimuth:300}] },
    { id:"ERB-15", name:"ERB-15 Buffer Norte",  lat:-23.6930, lng:-46.6980, status:"healthy",  utilization:41, is_event_site:true,
      cells:[{id:"ERB-15-A1",azimuth:0},{id:"ERB-15-A2",azimuth:120},{id:"ERB-15-A3",azimuth:240}] },
  ]),
  get_vips: (event_id) => ([
    { name:"Carlos Menezes", in_event:true,  serving_cell:"ERB-07", rsrp:-85, rsrq:-7,  status:"ok",      rsrp_min:-110, rsrp_max:-40 },
    { name:"Ana Rodrigues",  in_event:true,  serving_cell:"ERB-07", rsrp:-97, rsrq:-12, status:"warning", rsrp_min:-110, rsrp_max:-40 },
    { name:"Roberto Lima",   in_event:true,  serving_cell:"ERB-03", rsrp:-83, rsrq:-6,  status:"ok",      rsrp_min:-110, rsrp_max:-40 },
    { name:"Fernanda Costa", in_event:true,  serving_cell:"ERB-11", rsrp:-88, rsrq:-9,  status:"ok",      rsrp_min:-110, rsrp_max:-40 },
    { name:"Patricia Souza", in_event:false, serving_cell:"ERB-19", rsrp:null,rsrq:null,status:"unknown", rsrp_min:-110, rsrp_max:-40 },
  ]),
  get_kpi_series: (event_id, site_id, metric, minutes) => {
    const n = Math.floor(minutes);
    const labels = [], values = [];
    const now = Date.now();
    for (let i = n; i >= 0; i--) {
      labels.push(new Date(now - i * 60000).toISOString());
      values.push(+(40 + Math.sin(i * 0.3) * 20 + Math.random() * 8).toFixed(1));
    }
    // Simula um gap no meio
    const gapIdx = Math.floor(n / 2);
    return {
      ok: true, labels, values,
      gaps: [{ from_idx: gapIdx, to_idx: gapIdx + 3, seconds: 180 }],
      thresholds: { warning: 80, critical: 95 },
    };
  },
  get_alerts: (event_id) => ([
    { id:1, severity:"CRITICAL", site_id:"ERB-07", cell_id:"ERB-07-A2", message:"Utilização crítica: 91% em ERB-07", timestamp:new Date().toISOString() },
    { id:2, severity:"WARNING",  site_id:"ERB-07", cell_id:"ERB-07",    message:"RSRP baixo para Ana Rodrigues: -97 dBm", timestamp:new Date().toISOString() },
  ]),
  get_app_status: () => ({ recording:true, db_size_mb:4.2, now:new Date().toISOString() }),
  activate_event: (id, mock) => _mock.get_active_event(),
  acknowledge_alert: (id) => ({ ok:true }),
  silence_alert: (key) => ({ ok:true }),
  load_event: (path) => _mock.get_active_event(),
  open_file_dialog: () => ({ ok:true, path:"events/sample_event.json" }),
};

// ── Aguarda pywebview estar pronto ────────────────────────────────
let _ready = false;
let _queue = [];

function _whenReady(fn) {
  if (_ready || MOCK) return fn();
  _queue.push(fn);
}

window.addEventListener("pywebviewready", () => {
  _ready = true;
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
  activateEvent:    (id, mock=false)         => API.call("activate_event", id, mock),
  endEvent:         (id)                     => API.call("end_event", id),
  getSites:         (eventId)               => API.call("get_sites", eventId),
  getKpiSeries:     (eventId, siteId, m, w) => API.call("get_kpi_series", eventId, siteId, m, w),
  getVips:          (eventId)               => API.call("get_vips", eventId),
  getAlerts:        (eventId)               => API.call("get_alerts", eventId),
  acknowledgeAlert: (id)                    => API.call("acknowledge_alert", id),
  silenceAlert:     (key)                   => API.call("silence_alert", key),
  getAppStatus:     ()                      => API.call("get_app_status"),
  openFileDialog:   ()                      => API.call("open_file_dialog"),
};

export default API;
