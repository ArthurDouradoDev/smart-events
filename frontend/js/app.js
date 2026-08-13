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
import { initAlarms, injectAlarms } from "./alarms.js";
import { initLogs } from "./logs.js";
import { initCredentials, promptCredentials } from "./credentials.js";

const POLL_INTERVAL_MS = 30_000; // 30s: busca dados atualizados no banco local
const VPN_CHECK_INTERVAL_MS = 15 * 60 * 1000; // 15min: verifica conexão com a VPN
const SYNC_POLL_INTERVAL_MS = 5_000; // 5s: poll leve do status de coleta (indicador no header)

let _pollTimer = null;
let _eventTimer = null;
let _vpnTimer = null;
let _syncTimer = null;
let _lastSyncStatus = null;
let _syncAuthPopupShown = false; // evita reabrir o popup a cada poll após o usuário fechá-lo
let _historicalTimestamps = [];
let _historicalIndex = -1;

// ── Bootstrap ─────────────────────────────────────────────────────

async function boot() {
  initMap();
  initVip();
  initKpi();
  initAlerts();
  initAlarms();
  initLogs();
  initCredentials();

  _setupHeaderClock();
  _setupLoadEventBtn();
  _setupHistoryBtn();
  _setupHistoricalControls();
  _setupServerButton();
  _setupEventDropdown();
  _setupClearHistoryModal();
  _setupVpnMonitor();
  _setupSyncIndicator();

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

// Mostra a logo do cliente (do catálogo) no cabeçalho; oculta se não houver.
async function _showClienteLogo(cliente) {
  const img = document.getElementById("event-cliente-logo");
  if (!img) return;
  img.classList.add("hidden");
  if (!cliente) return;
  try {
    const res = await API.getClientes();
    const url = res && res.ok && res.clientes && res.clientes[cliente]
      ? res.clientes[cliente].logo_url : "";
    if (url) {
      img.src = url;
      img.alt = cliente;
      img.classList.remove("hidden");
    }
  } catch (err) {
    console.error("Erro ao carregar logo do cliente:", err);
  }
}

function _enterStandbyMode() {
  State.set("mode", "standby");
  document.getElementById("event-cliente-logo")?.classList.add("hidden");
  document.getElementById("standby-screen").classList.add("active");
  document.getElementById("dashboard-screen").classList.remove("active");
  document.getElementById("dashboard-screen").classList.add("hidden");
  document.getElementById("event-name").textContent = "Nenhum evento ativo";
  document.getElementById("event-badge").classList.add("hidden");
  document.getElementById("event-timer").classList.add("hidden");
  document.getElementById("rec-indicator").classList.add("hidden");
  _stopSyncPolling();
}

// Ativa o evento; ao receber needs_credentials, abre o modal sob demanda e re-tenta.
// Para eventos legados sem cliente, o modal devolve o cliente escolhido, que é fixado no
// evento na re-ativação (persistido pelo backend).
async function _activateEventWithCreds(event, isMock) {
  let res = await API.activateEvent(event.id, isMock);
  while (res && res.needs_credentials) {
    const chosen = await promptCredentials(res.cliente, res.region);
    if (!chosen) return res;  // operador cancelou — coleta não inicia
    res = await API.activateEvent(event.id, isMock, chosen.cliente);
  }
  return res;
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
  _showClienteLogo(event.oss && event.oss.cliente);
  
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

  // Ativa coleta (inicia scheduler Python). Se faltarem credenciais (1º acesso),
  // pede ao operador e re-tenta a ativação.
  const isMock = window.__MOCK_MODE__;
  await _activateEventWithCreds(event, isMock);

  // Carrega dados iniciais
  await _poll();

  // Ajusta o zoom do mapa para o evento
  fitToEvent(State.sites, event.polygon);

  // Inicia polling periódico
  _startPolling();
  _startSyncPolling();
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
  _stopSyncPolling();

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
    const [sites, vips, alerts, alarms, status] = await Promise.all([
      API.getSites(id, null, State.selectedMetric),
      API.getVips(id),
      API.getAlerts(id),
      API.getAlarms(id),
      API.getAppStatus(),
    ]);

    State.merge({ sites, vips });
    renderSites(sites);
    injectAlerts(alerts);
    injectAlarms(alarms);

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
    const [sites, vips, alerts, alarms] = await Promise.all([
      API.getSites(id, timestamp, State.selectedMetric),
      API.getVips(id, timestamp),
      API.getAlerts(id, timestamp),
      API.getAlarms(id, timestamp),
    ]);

    State.merge({ sites, vips });
    renderSites(sites);
    State.set("alerts", alerts);
    injectAlarms(alarms);

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

// ── Monitor de Conexão VPN ────────────────────────────────────────

function _updateVpnIcon(connected) {
  const icon = document.getElementById("vpn-status-icon");
  if (!icon) return;
  icon.classList.remove("hidden");
  icon.classList.toggle("vpn-connected", connected);
  icon.classList.toggle("vpn-disconnected", !connected);
  icon.title = connected
    ? "VPN conectada"
    : "VPN desconectada — clique para verificar";
}

function _showVpnModal(target) {
  const modal = document.getElementById("vpn-modal");
  if (!modal) return;
  if (target) document.getElementById("vpn-target").textContent = target;
  modal.classList.remove("hidden");
  State.vpnPopupOpen = true;
}

function _hideVpnModal() {
  const modal = document.getElementById("vpn-modal");
  if (modal) modal.classList.add("hidden");
  State.vpnPopupOpen = false;
}

async function _checkVpn() {
  try {
    const res = await API.checkVpn();
    const connected = res && res.ok ? res.connected : false;
    State.set("vpnConnected", connected);
    _updateVpnIcon(connected);
    if (connected) {
      _hideVpnModal();
    } else {
      // Reabre o popup automaticamente (ex.: novo ciclo de 15 min desconectado).
      _showVpnModal(res && res.target);
    }
    return connected;
  } catch (err) {
    console.error("Erro ao verificar VPN:", err);
    return false;
  }
}

function _setupVpnMonitor() {
  const icon = document.getElementById("vpn-status-icon");
  const btnClose = document.getElementById("vpn-modal-close");
  const btnRecheck = document.getElementById("vpn-modal-recheck");

  // Fechar o popup: o ícone permanece visível (vermelho) indicando a desconexão.
  btnClose.addEventListener("click", () => _hideVpnModal());

  // Verificar conexão novamente.
  btnRecheck.addEventListener("click", async () => {
    const original = btnRecheck.textContent;
    btnRecheck.disabled = true;
    btnRecheck.textContent = "Verificando…";
    await _checkVpn();
    btnRecheck.disabled = false;
    btnRecheck.textContent = original;
  });

  // Clicar no ícone do header revalida a conexão (reabrindo o popup se desconectado).
  icon.addEventListener("click", () => _checkVpn());

  // Primeira verificação imediata + ciclo periódico (todos os modos).
  _checkVpn();
  if (_vpnTimer) clearInterval(_vpnTimer);
  _vpnTimer = setInterval(_checkVpn, VPN_CHECK_INTERVAL_MS);
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

// ── Indicador de Sincronização (status de coleta) ─────────────────

function _esc(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Interpreta um ISO sem timezone como UTC (o backend usa datetime.utcnow()).
function _parseUtc(iso) {
  if (!iso) return null;
  let s = iso;
  if (!/[zZ]$/.test(s) && !/[+-]\d{2}:\d{2}$/.test(s)) s += "Z";
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

function _agoLabel(date) {
  const sec = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (sec < 60) return `há ${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `há ${min}m`;
  return `há ${Math.floor(min / 60)}h`;
}

function _setupSyncIndicator() {
  const ind = document.getElementById("sync-indicator");
  const modal = document.getElementById("sync-modal");
  const closeBtn = document.getElementById("sync-modal-close");
  if (!ind || !modal) return;

  ind.addEventListener("click", () => {
    if (_lastSyncStatus) _renderSyncModal(_lastSyncStatus);
    modal.classList.remove("hidden");
  });
  closeBtn?.addEventListener("click", () => modal.classList.add("hidden"));
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.classList.add("hidden");
  });

  // Delegação: o corpo do popup é re-renderizado a cada poll, então o botão
  // "Reconectar" é tratado por delegação no container fixo.
  document.getElementById("sync-modal-body")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".sync-reauth");
    if (btn) _handleSyncReauth(btn);
    if (e.target.closest(".sync-logs")) {
      modal.classList.add("hidden");
      document.getElementById("logs-btn")?.click();
    }
  });
}

async function _handleSyncReauth(btn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Abrindo login…";
  try {
    const res = await API.reauthSession();
    if (res && res.ok) {
      alert("Sessão reautenticada com sucesso.\nA coleta será retomada no próximo ciclo.");
      _pollSyncStatus();
    } else {
      alert(`Reautenticação não concluída.\n${res ? (res.error || "") : "Erro desconhecido."}`);
    }
  } catch (err) {
    console.error("Erro na reautenticação:", err);
    alert("Erro ao reautenticar sessão.");
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

function _startSyncPolling() {
  if (_syncTimer) clearInterval(_syncTimer);
  _pollSyncStatus();
  _syncTimer = setInterval(_pollSyncStatus, SYNC_POLL_INTERVAL_MS);
}

function _stopSyncPolling() {
  if (_syncTimer) {
    clearInterval(_syncTimer);
    _syncTimer = null;
  }
  document.getElementById("sync-indicator")?.classList.add("hidden");
  document.getElementById("sync-modal")?.classList.add("hidden");
  _syncAuthPopupShown = false;
}

async function _pollSyncStatus() {
  if (State.mode !== "active") return;
  try {
    const st = await API.getCollectionStatus();
    if (!st || !st.ok) return;
    _lastSyncStatus = st;
    _renderSyncIndicator(st);
    const modal = document.getElementById("sync-modal");

    // Sessão inativa (requer reautenticação): abre o popup automaticamente uma vez.
    const needsAuth = !!(st.session && st.session.needs_interactive);
    if (needsAuth && !_syncAuthPopupShown) {
      _syncAuthPopupShown = true;
      modal?.classList.remove("hidden");
    } else if (!needsAuth) {
      _syncAuthPopupShown = false;
    }

    if (modal && !modal.classList.contains("hidden")) _renderSyncModal(st);
  } catch (err) {
    console.error("Erro no poll de status de coleta:", err);
  }
}

function _renderSyncIndicator(st) {
  const el = document.getElementById("sync-indicator");
  if (!el) return;
  el.classList.remove("hidden");

  const overall = st.overall_state || "idle";
  const running = overall === "running" || st.kpi.state === "running" || st.vip.state === "running";
  const needsAuth = overall === "auth_required" || !!(st.session && st.session.needs_interactive);
  const label = document.getElementById("sync-label");

  el.classList.toggle("syncing", running && !needsAuth);
  el.classList.toggle("needs-auth", needsAuth);

  if (needsAuth) {
    label.textContent = "Reautenticar";
    el.title = "Reautenticação necessária — clique para detalhes";
    return;
  }
  if (running) {
    label.textContent = "Atualizando…";
    el.title = "Atualizando dados…";
    return;
  }

  const stateLabel = _stateInfo(overall).label;
  if (["error", "partial", "stale"].includes(overall)) {
    label.textContent = stateLabel;
    el.title = "Coleta requer atenção — clique para detalhes";
    return;
  }

  const k = _parseUtc(st.kpi.last_data_at || st.kpi.last_success);
  const v = _parseUtc(st.vip.last_data_at || st.vip.last_success);
  if (v && (!k || v >= k)) label.textContent = `VIP ${_agoLabel(v)}`;
  else if (k) label.textContent = `KPI ${_agoLabel(k)}`;
  else label.textContent = "—";
  el.title = "Status de coleta — clique para detalhes";
}

function _stateInfo(state) {
  switch (state) {
    case "running": return { cls: "st-running", label: "Coletando…" };
    case "data":
    case "ok":      return { cls: "st-data",    label: "Com dados" };
    case "empty":   return { cls: "st-empty",   label: "Sem novidade" };
    case "partial": return { cls: "st-partial", label: "Parcial" };
    case "error":   return { cls: "st-error",   label: "Erro" };
    case "auth_required": return { cls: "st-auth", label: "Reautenticação necessária" };
    case "stale":   return { cls: "st-stale",   label: "Desatualizado" };
    default:        return { cls: "st-idle",    label: "Aguardando" };
  }
}

function _syncRow(label, valueHtml) {
  return `<div class="sync-row"><span class="sync-row-label">${label}</span><span class="sync-row-val">${valueHtml}</span></div>`;
}

// Mensagem para o usuário. O texto técnico (exceção, URL, host, timeout) fica
// só nos logs do desenvolvedor; aqui vai o que dá para entender e agir.
function _syncHint(s) {
  if (s.state === "auth_required") return "Sessão expirada — reconecte para retomar a coleta.";
  if (s.state === "stale") return "Sem dados novos há mais de um ciclo.";
  if (s.state === "partial") return "Coleta parcial — parte dos dados não chegou neste ciclo.";
  if (s.state !== "error") return "";
  const code = s.diagnostics?.[s.diagnostics.length - 1]?.code;
  switch (code) {
    case "network":  return "Sem conexão com o servidor — verifique a VPN.";
    case "http":     return "O servidor recusou a consulta.";
    case "contract": return "O servidor respondeu em formato inesperado.";
    case "catalog":  return "Não foi possível ler o catálogo de tarefas.";
    default:         return "Falha na coleta — detalhes nos logs técnicos.";
  }
}

function _syncLine(title, s, isVip) {
  const info = _stateInfo(s.state);
  const last = _parseUtc(s.last_data_at || s.last_cycle_ok_at || s.last_success);
  const parts = [info.label];
  if (last) parts.push(_agoLabel(last));
  if (isVip && s.vips_total) parts.push(`${s.vips_with_data}/${s.vips_total} VIPs`);
  const val = `<span class="sync-dot ${info.cls}"></span>` +
    `<span${last ? ` title="${_esc(last.toLocaleString())}"` : ""}>${parts.join(" · ")}</span>`;
  const hint = _syncHint(s);
  const hintCls = ["error", "auth_required"].includes(s.state) ? "sync-hint err" : "sync-hint";
  return _syncRow(title, val) + (hint ? `<div class="${hintCls}">${hint}</div>` : "");
}

function _syncCoverageLine(kpi) {
  const coverage = kpi?.coverage || {};
  const discarded = Number(coverage.unmapped_objects || 0);
  const received = Number(kpi?.received || 0);
  const mapped = Number(coverage.mapped_objects ?? Math.max(0, received - discarded));
  let html = _syncRow(
    "Cobertura",
    `<span>recebidos ${received} · mapeados ${mapped} · descartados ${discarded}</span>`
  );
  if (discarded > 0) {
    const ossNames = (coverage.unmapped_cells || []).slice(0, 5).map(_esc).join(", ") || "não informado";
    const eventNames = (coverage.event_cells || []).slice(0, 5).map(_esc).join(", ") || "não informado";
    html += `<div class="sync-hint err">nomes vindos do OSS: ${ossNames} | o evento espera nomes como: ${eventNames}</div>`;
  }
  return html;
}

function _syncSessionLine(sess) {
  if (!sess) return "";
  const needsAuth = !!sess.needs_interactive;
  const info = _stateInfo(needsAuth ? "auth_required" : "ok");
  const label = needsAuth ? "Reautenticação necessária" : "Ativa";
  let html = _syncRow("Sessão", `<span class="sync-dot ${info.cls}"></span>${label}`);
  if (needsAuth) {
    html += `<div class="sync-reauth-wrap"><button class="btn btn-primary sync-reauth">Reconectar sessão</button></div>`;
  }
  return html;
}

function _renderSyncModal(st) {
  const body = document.getElementById("sync-modal-body");
  if (!body) return;
  body.innerHTML =
    `<div class="sync-section">` +
    _syncLine("Sites (KPI)", st.kpi, false) +
    _syncCoverageLine(st.kpi) +
    _syncLine("VIPs", st.vip, true) +
    (st.alarms ? _syncLine("Alarmes", st.alarms, false) : "") +
    _syncSessionLine(st.session) +
    `</div>` +
    `<button class="btn sync-logs">Ver logs técnicos</button>`;
}

// ── Init ──────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", boot);
