/**
 * state.js — Store central da aplicação.
 * Padrão pub/sub simples: módulos assinam eventos e reagem a mudanças.
 */

const State = {
  // ── Dados do evento ──────────────────────────────────────────────
  activeEvent:    null,    // EventConfig | null
  mode:           "standby", // standby | active | historical

  // ── Dados do dashboard ───────────────────────────────────────────
  sites:          [],
  vips:           [],
  alerts:         [],

  // ── Seleção e UI ─────────────────────────────────────────────────
  selectedSite:   null,   // site id
  selectedMetric: "utilization_dl",
  selectedCell:   "__all__", // "__all__" = site completo, "__media__" = média, ou cell_id específico
  timeWindow:     60,     // minutos

  // ── Gravação ─────────────────────────────────────────────────────
  isRecording:    false,
  dbSizeMb:       0,

  // ── Histórico ────────────────────────────────────────────────────
  historicalEvent:     null,
  historicalTimestamp: null,

  // ── Internos ─────────────────────────────────────────────────────
  _listeners: {},

  /** Assina um evento. Retorna função para cancelar. */
  on(event, fn) {
    if (!this._listeners[event]) this._listeners[event] = [];
    this._listeners[event].push(fn);
    return () => {
      this._listeners[event] = this._listeners[event].filter(f => f !== fn);
    };
  },

  /** Emite um evento para todos os assinantes. */
  emit(event, data) {
    (this._listeners[event] || []).forEach(fn => {
      try { fn(data); } catch (e) { console.error(`State.emit ${event}:`, e); }
    });
  },

  /** Atualiza uma chave e emite evento "change:<key>" + "change". */
  set(key, value) {
    this[key] = value;
    this.emit(`change:${key}`, value);
    this.emit("change", { key, value });
  },

  /** Atualiza múltiplas chaves de uma vez. Emite apenas "change" no final. */
  merge(updates) {
    Object.entries(updates).forEach(([k, v]) => {
      this[k] = v;
      this.emit(`change:${k}`, v);
    });
    this.emit("change", updates);
  },

  // ── Helpers de leitura ───────────────────────────────────────────

  get eventId() {
    return this.activeEvent?.id || this.historicalEvent?.id || null;
  },

  get alertCount() {
    return this.alerts.filter(a => !a.acknowledged).length;
  },

  get siteCounts() {
    const counts = { healthy: 0, warning: 0, critical: 0, unknown: 0 };
    this.sites.forEach(s => counts[s.status] = (counts[s.status] || 0) + 1);
    return counts;
  },

  get vipCounts() {
    const inEvent  = this.vips.filter(v => v.in_event).length;
    const outEvent = this.vips.length - inEvent;
    return { inEvent, outEvent };
  },
};

export default State;
