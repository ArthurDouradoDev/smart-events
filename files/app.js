/**
 * app.js — Orquestrador principal.
 * Inicializa módulos, gerencia ciclo de vida do evento e polling.
 */

import API     from "./bridge.js";
import State   from "./state.js";
import { initMap, initMapModules, renderSites, renderEventPolygon, fitToEvent } from "./map.js";
import { initVip }    from "./vip.js";
import { initKpi }    from "./kpi.js";
import { initAlerts, injectAlerts } from "./alerts.js";

const POLL_INTERVAL_MS = 30_000; // 30s: busca dados atualizados no banco local

let _pollTimer = null;

// ── Bootstrap ─────────────────────────────────────────────────────

async function boot() {
  initMap();
  initVip();
  initKpi();
  initAlerts();

  _setupHeaderClock();
  _setupLoadEventBtn();
  _setupHistoryBtn();

  // Verifica se há evento ativo (reabriu o programa com evento em curso)
  const { ok, event } = await API.getActiveEvent();
  if (ok && event) {
    await _enterActiveMode(event);
  } else {
    _enterStandbyMode();
    await _checkScheduledEvents();
  }
}

// ── Modos da interface ────────────────────────────────────────────

function _enterStandbyMode() {
  State.set("mode", "standby");
  document.getElementById("standby-screen").classList.add("active");
  document.getElementById("dashboard-screen").classList.remove("active");
  document.getElementById("dashboard-screen").classList.add("hidden");
  document.getElementById("event-name").textContent = "Nenhum evento ativo";
  document.getElementById("event-badge").classList.add("hidden");
  document.getElementById("event-timer").classList.add("hidden");
  document.getElementById("rec-indicator").classList.add("hidden");
}

async function _enterActiveMode(event) {
  State.merge({
    mode: "active",
    activeEvent: event,
  });

  // Troca telas
  document.getElementById("standby-screen").classList.remove("active");
  document.getElementById("dashboard-screen").classList.remove("hidden");
  document.getElementById("dashboard-screen").classList.add("active");

  // Header
  document.getElementById("event-name").textContent = event.name;
  document.getElementById("event-badge").classList.remove("hidden");
  
  const start = event.start_time ? new Date(event.start_time) : null;
  const end = event.end_time ? new Date(event.end_time) : null;
  const hasLongDuration = start && end && !isNaN(start) && !isNaN(end) &&
    (end - start) > 7 * 24 * 60 * 60 * 1000;

  if (hasLongDuration) {
    document.getElementById("event-timer").classList.add("hidden");
  } else {
    document.getElementById("event-timer").classList.remove("hidden");
  }

  document.getElementById("rec-indicator").classList.remove("hidden");

  _startTimer(event.start_time);

  // Mapa: polígono do evento
  renderEventPolygon(event.polygon);

  // Ativa coleta (inicia scheduler Python)
  const isMock = window.__MOCK_MODE__;
  await API.activateEvent(event.id, isMock);

  // Carrega dados iniciais
  await _poll();

  // Inicia polling periódico
  _startPolling();
}

async function _enterHistoricalMode(event) {
  State.merge({
    mode: "historical",
    historicalEvent: event,
  });

  document.getElementById("standby-screen").classList.remove("active");
  document.getElementById("dashboard-screen").classList.remove("hidden");
  document.getElementById("dashboard-screen").classList.add("active");
  document.getElementById("historical-banner").classList.remove("hidden");
  document.getElementById("hist-event-name").textContent = event.name;
  document.getElementById("historical-badge").classList.remove("hidden");
  document.getElementById("event-badge").classList.add("hidden");
}

// ── Polling ───────────────────────────────────────────────────────

function _startPolling() {
  if (_pollTimer) clearInterval(_pollTimer);
  _pollTimer = setInterval(_poll, POLL_INTERVAL_MS);
}

async function _poll() {
  const id = State.eventId;
  if (!id) return;

  try {
    const [sites, vips, alerts, status] = await Promise.all([
      API.getSites(id),
      API.getVips(id),
      API.getAlerts(id),
      API.getAppStatus(),
    ]);

    State.merge({ sites, vips });
    renderSites(sites);
    injectAlerts(alerts);

    if (status) {
      State.merge({ isRecording: status.recording, dbSizeMb: status.db_size_mb });
      document.getElementById("rec-size").textContent =
        `REC ${status.db_size_mb} MB`;
    }

    // Atualiza seleção de site se ainda válida
    if (State.selectedSite) {
      const still = sites.find(s => s.id === State.selectedSite);
      if (!still && sites.length) State.set("selectedSite", sites[0].id);
    } else if (sites.length) {
      State.set("selectedSite", sites[0].id);
    }
  } catch (err) {
    console.error("Erro no poll:", err);
  }
}

// ── Timer do evento ───────────────────────────────────────────────

function _startTimer(startTimeIso) {
  const start = startTimeIso ? new Date(startTimeIso).getTime() : Date.now();

  setInterval(() => {
    const elapsed = Math.max(0, Date.now() - start);
    const h = Math.floor(elapsed / 3_600_000);
    const m = Math.floor((elapsed % 3_600_000) / 60_000);
    const s = Math.floor((elapsed % 60_000) / 1_000);
    document.getElementById("event-timer").textContent =
      `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
  }, 1_000);
}

// ── Relógio ───────────────────────────────────────────────────────

function _setupHeaderClock() {
  const el = document.getElementById("clock");
  const tick = () => {
    el.textContent = new Date().toLocaleTimeString("pt-BR", { hour:"2-digit", minute:"2-digit" });
  };
  tick();
  setInterval(tick, 10_000);
}

// ── Botão "Carregar evento" ───────────────────────────────────────

function _setupLoadEventBtn() {
  document.getElementById("load-event-btn").addEventListener("click", async () => {
    const { ok, path } = await API.openFileDialog();
    if (!ok || !path) return;

    const result = await API.loadEvent(path);
    if (!result.ok) {
      alert(`Erro ao carregar evento: ${result.error}`);
      return;
    }
    await _enterActiveMode(result.event);
  });
}

// ── Botão "Ver histórico" ─────────────────────────────────────────

function _setupHistoryBtn() {
  const btn = document.getElementById("open-history-btn");
  if (!btn) return;

  btn.addEventListener("click", async () => {
    const events = await API.getEvents();
    const ended = events.filter(e => ["ENDED","ARCHIVED"].includes(e.status));
    if (!ended.length) {
      alert("Nenhum evento encerrado com dados coletados.");
      return;
    }
    // Por ora, abre o mais recente
    await _enterHistoricalMode(ended[ended.length - 1]);
  });

  document.getElementById("hist-exit")?.addEventListener("click", () => {
    document.getElementById("historical-banner").classList.add("hidden");
    document.getElementById("historical-badge").classList.add("hidden");
    _enterStandbyMode();
  });
}

// ── Verifica eventos agendados ────────────────────────────────────

async function _checkScheduledEvents() {
  const events = await API.getEvents();
  const nextScheduled = events
    .filter(e => e.status === "SCHEDULED")
    .sort((a,b) => new Date(a.start_time) - new Date(b.start_time))[0];

  const sub = document.getElementById("next-event-label");
  const histBtn = document.getElementById("open-history-btn");

  if (nextScheduled) {
    const dt = new Date(nextScheduled.start_time);
    const diff = dt - Date.now();
    const days = Math.floor(diff / 86_400_000);
    sub.textContent = `Próximo: ${nextScheduled.name} — em ${days > 0 ? days + " dias" : "breve"}`;
  } else {
    sub.textContent = "Nenhum evento agendado.";
  }

  const hasHistory = events.some(e => ["ENDED","ARCHIVED"].includes(e.status));
  histBtn.classList.toggle("hidden", !hasHistory);

  // Spinner vira ícone estático quando não há evento
  document.querySelector(".standby-ring")?.classList.add("idle");
}

// ── Init ──────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", boot);
