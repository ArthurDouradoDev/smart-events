/**
 * app.js — Orquestrador principal.
 * Inicializa módulos, gerencia ciclo de vida do evento e polling.
 */

import API     from "./bridge.js";
import State   from "./state.js";
import { initMap, renderSites, renderEventPolygon, fitToEvent } from "./map.js";
import { initVip }    from "./vip.js";
import { initKpi, refreshChart }    from "./kpi.js";
import { initAlerts, injectAlerts } from "./alerts.js";
import { initLogs } from "./logs.js";

const POLL_INTERVAL_MS = 30_000; // 30s: busca dados atualizados no banco local

let _pollTimer = null;
let _eventTimer = null;
let _historicalTimestamps = [];
let _historicalIndex = -1;

// ── Bootstrap ─────────────────────────────────────────────────────

async function boot() {
  initMap();
  initVip();
  initKpi();
  initAlerts();
  initLogs();

  _setupHeaderClock();
  _setupLoadEventBtn();
  _setupHistoryBtn();
  _setupHistoricalControls();
  _setupServerButton();
  _setupEventDropdown();
  _setupClearHistoryModal();

  // Mostra a tela de espera enquanto o servidor local sobe e a sincronização ocorre.
  _enterStandbyMode();

  // Sincroniza eventos e VIPs do servidor local ao abrir, com re-tentativas:
  // no 1º arranque do .exe o servidor embutido pode levar alguns segundos para responder.
  await _bootSync();

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
    historicalEvent: null,
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

  // Ajusta o zoom do mapa para o evento
  fitToEvent(State.sites, event.polygon);

  // Inicia polling periódico
  _startPolling();
}

async function _enterHistoricalMode(event) {
  if (_pollTimer) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }
  if (_eventTimer) {
    clearInterval(_eventTimer);
    _eventTimer = null;
  }

  const timestamps = await API.getEventTimestamps(event.id);
  if (!timestamps || !timestamps.length) {
    alert("Nenhum dado gravado para este evento.");
    return;
  }

  _historicalTimestamps = timestamps;

  State.merge({
    mode: "historical",
    historicalEvent: event,
    activeEvent: null,
  });

  document.getElementById("standby-screen").classList.remove("active");
  document.getElementById("dashboard-screen").classList.remove("hidden");
  document.getElementById("dashboard-screen").classList.add("active");
  document.getElementById("historical-banner").classList.remove("hidden");
  document.getElementById("hist-event-name").textContent = event.name;
  document.getElementById("historical-badge").classList.remove("hidden");
  document.getElementById("event-badge").classList.add("hidden");
  document.getElementById("event-timer").classList.add("hidden");
  document.getElementById("rec-indicator").classList.add("hidden");

  // Header name
  document.getElementById("event-name").textContent = event.name;

  const slider = document.getElementById("hist-slider");
  if (slider) {
    slider.min = 0;
    slider.max = timestamps.length - 1;
  }

  await _updateHistoricalView(timestamps.length - 1);
  fitToEvent(State.sites, event.polygon);
}

// ── Polling ───────────────────────────────────────────────────────

function _startPolling() {
  if (_pollTimer) clearInterval(_pollTimer);
  _pollTimer = setInterval(_poll, POLL_INTERVAL_MS);
}

async function _poll() {
  if (State.mode !== "active") return;
  const id = State.eventId;
  if (!id) return;

  try {
    const [sites, vips, alerts, status] = await Promise.all([
      API.getSites(id, null, State.selectedMetric),
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

    // Atualiza o gráfico de KPIs automaticamente a cada ciclo (sem reabrir popup fechado).
    refreshChart();
  } catch (err) {
    console.error("Erro no poll:", err);
  }
}

// ── Timer do evento ───────────────────────────────────────────────

function _startTimer(startTimeIso) {
  if (_eventTimer) {
    clearInterval(_eventTimer);
    _eventTimer = null;
  }
  const start = startTimeIso ? new Date(startTimeIso).getTime() : Date.now();

  _eventTimer = setInterval(() => {
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
    State.merge({
      mode: "standby",
      historicalEvent: null,
      historicalTimestamp: null,
    });
    _enterStandbyMode();
    _checkScheduledEvents();
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

// ── Controles de Histórico ────────────────────────────────────────

function _setupHistoricalControls() {
  const slider = document.getElementById("hist-slider");
  const prevBtn = document.getElementById("hist-prev");
  const nextBtn = document.getElementById("hist-next");

  if (slider) {
    slider.addEventListener("input", e => {
      _updateHistoricalView(Number(e.target.value));
    });
  }

  if (prevBtn) {
    prevBtn.addEventListener("click", () => {
      if (_historicalIndex > 0) {
        _updateHistoricalView(_historicalIndex - 1);
      }
    });
  }

  if (nextBtn) {
    nextBtn.addEventListener("click", () => {
      if (_historicalIndex < _historicalTimestamps.length - 1) {
        _updateHistoricalView(_historicalIndex + 1);
      }
    });
  }
}

async function _updateHistoricalView(index) {
  if (index < 0 || index >= _historicalTimestamps.length) return;
  _historicalIndex = index;

  const timestamp = _historicalTimestamps[index];

  const elTime = document.getElementById("hist-timestamp");
  if (elTime) {
    const dt = new Date(timestamp);
    elTime.textContent = dt.toLocaleString("pt-BR");
  }

  const slider = document.getElementById("hist-slider");
  if (slider) {
    slider.value = index;
  }

  const prevBtn = document.getElementById("hist-prev");
  const nextBtn = document.getElementById("hist-next");
  if (prevBtn) prevBtn.disabled = (index === 0);
  if (nextBtn) nextBtn.disabled = (index === _historicalTimestamps.length - 1);

  State.set("historicalTimestamp", timestamp);

  try {
    const id = State.eventId;
    const [sites, vips, alerts] = await Promise.all([
      API.getSites(id, timestamp, State.selectedMetric),
      API.getVips(id, timestamp),
      API.getAlerts(id, timestamp),
    ]);

    State.merge({ sites, vips });
    renderSites(sites);
    State.set("alerts", alerts);

    if (State.selectedSite) {
      const still = sites.find(s => s.id === State.selectedSite);
      if (!still && sites.length) State.set("selectedSite", sites[0].id);
    } else if (sites.length) {
      State.set("selectedSite", sites[0].id);
    }
  } catch (err) {
    console.error("Erro ao atualizar visualização histórica:", err);
  }
}

// ── Configurações de Sincronização ────────────────────────────────

// Sincronização inicial com re-tentativas até os eventos aparecerem no banco local.
async function _bootSync(maxAttempts = 10, delayMs = 1500) {
  for (let i = 0; i < maxAttempts; i++) {
    try {
      await API.syncEvents();
      const events = await API.getEvents();
      if (Array.isArray(events) && events.length > 0) return true;
    } catch (err) {
      console.error("Falha na sincronização inicial:", err);
    }
    await new Promise((r) => setTimeout(r, delayMs));
  }
  return false;
}

// Sincroniza eventos+VIPs do servidor local e reavalia eventos agendados (com debounce).
let _autoSyncTimer = null;
async function _autoSync() {
  try {
    const res = await API.syncEvents();
    if (res && res.ok) await _checkScheduledEvents();
  } catch (err) {
    console.error("Falha no auto-sync:", err);
  }
}

function _setupServerButton() {
  const btnServer = document.getElementById("server-btn");
  if (!btnServer) return;

  // Abre a página de edição do servidor local no navegador padrão.
  btnServer.addEventListener("click", async () => {
    try {
      await API.openServerUi();
    } catch (err) {
      console.error("Falha ao abrir o painel do servidor:", err);
    }
  });

  // Ao voltar o foco para o app (ex.: depois de editar no navegador), re-sincroniza.
  window.addEventListener("focus", () => {
    clearTimeout(_autoSyncTimer);
    _autoSyncTimer = setTimeout(_autoSync, 400);
  });
}

// ── Configurações de Limpeza de Histórico ──────────────────────────

function _setupClearHistoryModal() {
  const recIndicator = document.getElementById("rec-indicator");
  const clearModal = document.getElementById("clear-history-modal");
  const confirmModal = document.getElementById("confirm-delete-modal");
  
  const btnCloseClear = document.getElementById("btn-close-clear-history");
  const btnTriggerConfirm = document.getElementById("btn-confirm-clear-history-trigger");
  
  const btnCloseConfirm = document.getElementById("btn-close-confirm-delete");
  const btnExecuteClear = document.getElementById("btn-execute-clear-history");

  if (!recIndicator || !clearModal || !confirmModal) return;

  // Clicar no REC abre o primeiro modal
  recIndicator.addEventListener("click", () => {
    if (State.mode !== "active" || !State.eventId) return;
    clearModal.classList.remove("hidden");
  });

  // Fechar o primeiro modal
  btnCloseClear.addEventListener("click", () => {
    clearModal.classList.add("hidden");
  });

  clearModal.addEventListener("click", (e) => {
    if (e.target === clearModal) {
      clearModal.classList.add("hidden");
    }
  });

  // Confirmar no primeiro modal abre o segundo (popup de confirmação de exclusão)
  btnTriggerConfirm.addEventListener("click", () => {
    clearModal.classList.add("hidden");
    confirmModal.classList.remove("hidden");
  });

  // Fechar o segundo modal
  btnCloseConfirm.addEventListener("click", () => {
    confirmModal.classList.add("hidden");
  });

  confirmModal.addEventListener("click", (e) => {
    if (e.target === confirmModal) {
      confirmModal.classList.add("hidden");
    }
  });

  // Executar a exclusão definitiva
  btnExecuteClear.addEventListener("click", async () => {
    confirmModal.classList.add("hidden");
    const eventId = State.eventId;
    if (!eventId) return;

    try {
      const res = await API.clearEventHistory(eventId);
      if (res && res.ok) {
        // Recarrega os dados imediatamente chamando poll
        await _poll();
        // Atualiza o gráfico de KPIs
        refreshChart();
      } else {
        alert("Erro ao limpar histórico do evento: " + (res?.error || "desconhecido"));
      }
    } catch (err) {
      console.error("Erro ao limpar histórico:", err);
      alert("Erro ao processar requisição de limpeza.");
    }
  });
}

// ── Dropdown de Seleção de Eventos ────────────────────────────────

function _setupEventDropdown() {
  const container = document.querySelector(".event-dropdown-container");
  const trigger = document.getElementById("event-dropdown-btn");
  const menu = document.getElementById("event-dropdown-menu");

  if (!trigger || !menu) return;

  // Alterna abertura/fechamento
  trigger.addEventListener("click", async (e) => {
    e.stopPropagation();
    const isOpen = container.classList.contains("open");
    if (!isOpen) {
      await _populateEventDropdown();
      container.classList.add("open");
      menu.classList.remove("hidden");
    } else {
      container.classList.remove("open");
      menu.classList.add("hidden");
    }
  });

  // Fecha ao clicar fora
  document.addEventListener("click", (e) => {
    if (!container.contains(e.target)) {
      container.classList.remove("open");
      menu.classList.add("hidden");
    }
  });
}

function _renderDropdownItems(events) {
  const menu = document.getElementById("event-dropdown-menu");
  if (!menu) return;

  if (!events || !events.length) {
    menu.innerHTML = `<div style="padding: 8px; text-align: center; color: var(--text-secondary); font-size: 11px;">Nenhum evento encontrado</div>`;
    return;
  }

  // Ordenação: ACTIVE primeiro, depois SCHEDULED, depois finalizados
  const statusOrder = { "ACTIVE": 1, "SCHEDULED": 2, "ENDED": 3, "ARCHIVED": 4 };
  events.sort((a, b) => {
    const orderA = statusOrder[a.status] || 99;
    const orderB = statusOrder[b.status] || 99;
    if (orderA !== orderB) return orderA - orderB;
    return new Date(b.start_time) - new Date(a.start_time);
  });

  menu.innerHTML = "";

  events.forEach(event => {
    const item = document.createElement("div");
    item.className = "event-dropdown-item";
    
    const isSelected = State.eventId === event.id;
    if (isSelected) {
      item.classList.add("selected");
    }

    let dateText = "—";
    if (event.start_time) {
      const start = new Date(event.start_time);
      const options = { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" };
      dateText = start.toLocaleString("pt-BR", options);
      if (event.end_time) {
        const end = new Date(event.end_time);
        dateText += ` - ${end.toLocaleString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`;
      }
    }

    let badgeClass = "badge-scheduled";
    let badgeLabel = "Agendado";
    if (event.status === "ACTIVE") {
      badgeClass = "badge-active";
      badgeLabel = "Ativo";
    } else if (["ENDED", "ARCHIVED"].includes(event.status)) {
      badgeClass = "badge-historical";
      badgeLabel = "Histórico";
    }

    item.innerHTML = `
      <div class="event-item-info">
        <span class="event-item-name">${event.name}</span>
        <span class="event-item-date">${dateText}</span>
      </div>
      <span class="badge ${badgeClass}">${badgeLabel}</span>
    `;

    item.addEventListener("click", async () => {
      const container = document.querySelector(".event-dropdown-container");
      container.classList.remove("open");
      menu.classList.add("hidden");
      await _switchEvent(event);
    });

    menu.appendChild(item);
  });
}

async function _populateEventDropdown() {
  const menu = document.getElementById("event-dropdown-menu");
  if (!menu) return;

  menu.innerHTML = `<div style="padding: 8px; text-align: center; color: var(--text-secondary); font-size: 11px;">Carregando eventos...</div>`;

  let events = [];
  try {
    events = await API.getEvents();
    _renderDropdownItems(events);
  } catch (err) {
    console.error("Erro ao carregar eventos locais no dropdown:", err);
    menu.innerHTML = `<div style="padding: 8px; text-align: center; color: var(--danger); font-size: 11px;">Erro ao carregar eventos</div>`;
  }

  // Sincroniza em segundo plano a partir do servidor central e atualiza o dropdown se novos eventos forem obtidos
  try {
    const syncRes = await API.syncEvents();
    const syncedCount = syncRes?.stats?.events?.sincronizados ?? 0;
    if (syncRes && syncRes.ok && syncedCount > 0) {
      events = await API.getEvents();
      const container = document.querySelector(".event-dropdown-container");
      if (container && container.classList.contains("open")) {
        _renderDropdownItems(events);
      }
    }
  } catch (syncErr) {
    console.warn("Falha na sincronização em segundo plano do dropdown:", syncErr);
  }
}

async function _switchEvent(event) {
  if (State.eventId === event.id) {
    if (State.mode === "active" && (event.status === "ACTIVE" || event.status === "SCHEDULED")) return;
    if (State.mode === "historical" && ["ENDED", "ARCHIVED"].includes(event.status)) return;
  }

  // Ocultar elementos de UI ativos
  document.getElementById("historical-banner").classList.add("hidden");
  document.getElementById("historical-badge").classList.add("hidden");
  document.getElementById("event-badge").classList.add("hidden");
  document.getElementById("event-timer").classList.add("hidden");
  document.getElementById("rec-indicator").classList.add("hidden");

  if (_pollTimer) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }
  if (_eventTimer) {
    clearInterval(_eventTimer);
    _eventTimer = null;
  }

  State.set("selectedSite", null);

  if (event.status === "ACTIVE" || event.status === "SCHEDULED") {
    event.status = "ACTIVE";
    await _enterActiveMode(event);
  } else if (["ENDED", "ARCHIVED"].includes(event.status)) {
    await _enterHistoricalMode(event);
  }
}

// ── Init ──────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", boot);
