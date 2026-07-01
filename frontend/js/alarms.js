/**
 * alarms.js — Drawer de alarmes do iManager (filtrados por tipo).
 *
 * Fonte distinta dos "Alertas ativos" (derivados de KPI/VIP): aqui são os alarmes
 * correntes coletados do FM website via cmd 1102/1103. Painel próprio com multi-select
 * de tipos (com busca), contagens por severidade e tabela.
 */

import API   from "./bridge.js";
import State from "./state.js";

// Ordem/cores por severidade (usa os tokens do design system quando aplicável).
const SEV_ORDER = { Critical: 0, Major: 1, Minor: 2, Warning: 3 };
const SEV_COLOR = {
  Critical: "var(--danger)",
  Major:    "#FF7B00",
  Minor:    "var(--warning)",
  Warning:  "#58A6FF",
};

let _catalog = [];            // nomes disponíveis (do catálogo)
let _selected = new Set();    // tipos atualmente selecionados
let _filterLoadedFor = null;  // eventId para o qual o filtro foi carregado
let _eventOnly = true;        // mostrar só alarmes correlacionados a sites do evento

export function initAlarms() {
  State.on("change:alarms", _render);

  document.getElementById("alarms-btn")?.addEventListener("click", _toggleDrawer);
  document.getElementById("alarms-drawer-close")?.addEventListener("click", _closeDrawer);
  document.getElementById("alarms-refresh-btn")?.addEventListener("click", _refresh);

  const eventOnly = document.getElementById("alarms-event-only");
  eventOnly?.addEventListener("change", () => {
    _eventOnly = eventOnly.checked;
    _render(State.alarms);
  });

  const toggle = document.getElementById("alarms-filter-toggle");
  const panel  = document.getElementById("alarms-filter-panel");
  toggle?.addEventListener("click", () => panel?.classList.toggle("hidden"));

  const search = document.getElementById("alarms-filter-search");
  search?.addEventListener("input", () => _renderFilterOptions(search.value));

  _loadCatalog();
}

// ── Drawer ────────────────────────────────────────────────────────

function _toggleDrawer() {
  const drawer = document.getElementById("alarms-drawer");
  if (!drawer) return;
  const opening = drawer.classList.contains("hidden");
  drawer.classList.toggle("hidden");
  if (opening) _ensureFilterLoaded();
}

function _closeDrawer() {
  document.getElementById("alarms-drawer")?.classList.add("hidden");
}

let _refreshing = false;
async function _refresh() {
  const btn = document.getElementById("alarms-refresh-btn");
  const eventId = State.eventId;
  if (_refreshing || !eventId || State.mode !== "active") return;
  _refreshing = true;
  btn?.classList.add("spinning");
  if (btn) btn.disabled = true;
  try {
    const res = await API.refreshAlarms(eventId);
    if (res && res.ok === false && res.error) {
      console.error("Erro ao atualizar alarmes:", res.error);
    }
    const alarms = await API.getAlarms(eventId);
    State.set("alarms", alarms || []);
  } catch (e) {
    console.error("Falha ao atualizar alarmes:", e);
  } finally {
    _refreshing = false;
    btn?.classList.remove("spinning");
    if (btn) btn.disabled = false;
  }
}

// ── Filtro (multi-select com busca) ───────────────────────────────

async function _loadCatalog() {
  try {
    const res = await API.getAlarmCatalog();
    if (res && res.ok) _catalog = res.names || [];
  } catch (e) {
    console.error("Erro ao carregar catálogo de alarmes:", e);
  }
}

// Carrega a seleção atual do evento uma vez (recarrega ao trocar de evento).
async function _ensureFilterLoaded() {
  const eventId = State.eventId;
  if (!eventId || _filterLoadedFor === eventId) {
    _renderFilterOptions();
    return;
  }
  try {
    if (!_catalog.length) await _loadCatalog();
    const res = await API.getAlarmFilter(eventId);
    _selected = new Set((res && res.ok && res.names) ? res.names : []);
    _filterLoadedFor = eventId;
  } catch (e) {
    console.error("Erro ao carregar filtro de alarmes:", e);
  }
  _renderFilterOptions();
}

function _renderFilterOptions(query = "") {
  const box = document.getElementById("alarms-filter-options");
  if (!box) return;
  const q = query.trim().toLowerCase();
  const names = _catalog.filter(n => !q || n.toLowerCase().includes(q));

  box.innerHTML = "";
  if (!names.length) {
    box.innerHTML = `<div class="alarms-empty">Nenhum tipo encontrado.</div>`;
    return;
  }
  names.forEach(name => {
    const label = document.createElement("label");
    label.className = "alarms-filter-item";
    const checked = _selected.has(name) ? "checked" : "";
    label.innerHTML =
      `<input type="checkbox" ${checked}><span>${_esc(name)}</span>`;
    label.querySelector("input").addEventListener("change", (e) => {
      _onToggleType(name, e.target.checked);
    });
    box.appendChild(label);
  });
}

async function _onToggleType(name, checked) {
  if (checked) _selected.add(name);
  else _selected.delete(name);

  const eventId = State.eventId;
  if (!eventId || State.mode !== "active") return;
  try {
    await API.setAlarmFilter(eventId, [..._selected]);
    // A recoleta é disparada no backend; recarrega os alarmes já filtrados.
    const alarms = await API.getAlarms(eventId);
    State.set("alarms", alarms || []);
  } catch (e) {
    console.error("Erro ao salvar filtro de alarmes:", e);
  }
}

// ── Renderização da lista ─────────────────────────────────────────

function _render(alarms) {
  alarms = alarms || [];
  const list    = document.getElementById("alarms-list");
  const summary = document.getElementById("alarms-summary");
  if (!list) return;

  const inEvent = alarms.filter(a => a.in_event);
  const shown   = _eventOnly ? inEvent : alarms;

  if (summary) {
    summary.textContent = `${inEvent.length} no evento · ${alarms.length} na rede`;
  }
  // O badge do header sempre reflete os críticos NO EVENTO (sinal mais relevante).
  _updateBadge(_severityCounts(inEvent).Critical || 0);

  if (!shown.length) {
    const msg = _eventOnly && alarms.length
      ? `Nenhum alarme nos sites do evento (${alarms.length} na rede).`
      : "Nenhum alarme dos tipos selecionados.";
    list.innerHTML = `<div class="alarms-empty">${msg}</div>`;
    return;
  }

  const sorted = [...shown].sort((a, b) => {
    const s = (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9);
    if (s !== 0) return s;
    return String(b.arrive_time || "").localeCompare(String(a.arrive_time || ""));
  });

  list.innerHTML = "";
  sorted.forEach(al => list.appendChild(_row(al)));
}

function _row(al) {
  const item = document.createElement("div");
  item.className = "alarm-item";
  const color = SEV_COLOR[al.severity] || "var(--text-secondary)";
  item.style.borderLeftColor = color;
  const siteTag = al.in_event && al.serving_site_name
    ? `<span class="alarm-site">📍 ${_esc(al.serving_site_name)}</span>` : "";
  item.innerHTML = `
    <div class="alarm-item-top">
      <span class="alarm-sev" style="color:${color}">${_esc(al.severity || "—")}</span>
      <span class="alarm-time">${_formatTime(al.arrive_time)}</span>
    </div>
    <div class="alarm-name">${_esc(al.alarm_name || "—")}</div>
    <div class="alarm-source">${_esc(al.source || "—")}${al.location ? " · " + _esc(al.location) : ""}${siteTag}</div>`;

  // Clicar num alarme correlacionado a um site do evento foca esse site no mapa
  // (igual aos VIPs). Fecha o drawer para o foco ficar visível.
  if (al.serving_site) {
    item.classList.add("clickable");
    item.title = "Focar no site no mapa";
    item.addEventListener("click", () => {
      State.set("selectedSite", al.serving_site);
      _closeDrawer();
    });
  }
  return item;
}

function _severityCounts(alarms) {
  const counts = {};
  alarms.forEach(a => { counts[a.severity] = (counts[a.severity] || 0) + 1; });
  return counts;
}

function _updateBadge(criticalCount) {
  const badge = document.getElementById("alarms-count");
  if (!badge) return;
  badge.textContent = criticalCount;
  badge.classList.toggle("hidden", criticalCount === 0);
}

// ── Helpers ───────────────────────────────────────────────────────

function _formatTime(iso) {
  if (!iso) return "";
  let s = iso;
  if (!/[zZ]$/.test(s) && !/[+-]\d{2}:\d{2}$/.test(s)) s += "Z";
  const d = new Date(s);
  return isNaN(d.getTime()) ? "" : d.toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function _esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Substitui a lista de alarmes por uma foto fresca (não acumula: é snapshot corrente).
export function injectAlarms(alarms) {
  State.set("alarms", alarms || []);
}
