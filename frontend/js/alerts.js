/**
 * alerts.js — Drawer de alertas e download de logs.
 */

import State from "./state.js";
import API   from "./bridge.js";

// ── Inicialização ─────────────────────────────────────────────────

let _selectedOnly = false;

export function initAlerts() {
  State.on("change:alerts", _renderDrawer);
  State.on("change:alerts", _updateBadge);
  State.on("change:selectedSite", () => _renderDrawer(State.alerts));
  State.on("change:sites", () => {
    if (_selectedOnly) _renderDrawer(State.alerts);
  });

  document.getElementById("alert-btn").addEventListener("click", _toggleDrawer);
  document.getElementById("alert-drawer-close")?.addEventListener("click", _closeDrawer);
  document.getElementById("alert-mark-read-btn")?.addEventListener("click", _markAllAsRead);
  document.getElementById("alert-delete-all-btn")?.addEventListener("click", _deleteAllAlerts);
  document.getElementById("alert-download-btn")?.addEventListener("click", _downloadAlertsLog);

  const selectedOnly = document.getElementById("alerts-selected-only");
  selectedOnly?.addEventListener("change", () => {
    _selectedOnly = selectedOnly.checked;
    _renderDrawer(State.alerts);
  });
}

// ── Badge do header ───────────────────────────────────────────────

function _updateBadge(alerts) {
  const count = alerts.filter(a => !a.acknowledged).length;
  const badge = document.getElementById("alert-count");
  badge.textContent = count;
  badge.classList.toggle("hidden", count === 0);
}

// ── Drawer ────────────────────────────────────────────────────────

function _inSelectedScope(alert, ids) {
  if (!ids) return false;
  return [alert.serving_site, alert.site_id].some(value => value && ids.has(value));
}

function _alertTitle(alert) {
  return alert.display_name || alert.site_name || alert.cell_name || alert.site_id || "";
}

function _renderDrawer(alerts) {
  alerts = alerts || [];
  const list = document.getElementById("alert-list");
  if (!list) return;

  const markReadBtn = document.getElementById("alert-mark-read-btn");
  const deleteBtn = document.getElementById("alert-delete-all-btn");
  const downloadBtn = document.getElementById("alert-download-btn");
  const activeCount = alerts.filter(a => !a.acknowledged).length;
  
  const hideButtons = State.mode === "historical" || activeCount === 0;
  
  if (markReadBtn) markReadBtn.classList.toggle("hidden", hideButtons);
  if (deleteBtn) deleteBtn.classList.toggle("hidden", hideButtons);
  if (downloadBtn) downloadBtn.classList.toggle("hidden", alerts.length === 0);

  const scopeIds = _selectedOnly ? State.selectedScopeIds : null;
  const shown = _selectedOnly
    ? alerts.filter(alert => _inSelectedScope(alert, scopeIds))
    : alerts;

  if (!alerts.length) {
    list.innerHTML = `<div style="padding:16px;color:#484F58;font-size:12px">Nenhum alerta ativo.</div>`;
    return;
  }
  if (_selectedOnly && !scopeIds) {
    list.innerHTML = `<div style="padding:16px;color:#484F58;font-size:12px">Selecione um site ou cluster na lista.</div>`;
    return;
  }
  if (!shown.length) {
    list.innerHTML = `<div style="padding:16px;color:#484F58;font-size:12px">Nenhum alerta no site/cluster selecionado.</div>`;
    return;
  }

  list.innerHTML = "";
  [...shown].sort((a, b) => {
    const sev = { CRITICAL: 0, WARNING: 1 };
    return (sev[a.severity] ?? 2) - (sev[b.severity] ?? 2);
  }).forEach(alert => {
    const item = document.createElement("div");
    item.className = `alert-item ${alert.severity.toLowerCase()}`;
    const showActions = State.mode !== "historical" && !alert.acknowledged;
    const ackBtn = showActions
      ? `<button class="alert-ack" data-id="${alert.id}">OK</button>`
      : "";
    item.innerHTML = `
      <div class="alert-item-body">
        <div class="alert-item-title">${_severityLabel(alert.severity)} ${_esc(_alertTitle(alert))}</div>
        <div class="alert-item-msg">${_esc(_displayMessage(alert))}</div>
        <div class="alert-item-time">${_formatTime(alert.timestamp)}</div>
      </div>
      ${ackBtn}`;

    if (showActions) {
      item.querySelector(".alert-ack").addEventListener("click", async () => {
        await API.acknowledgeAlert(alert.id);
        const updated = State.alerts.map(a => a.id === alert.id ? {...a, acknowledged: true} : a);
        State.set("alerts", updated);
      });
    }

    list.appendChild(item);
  });
}

function _toggleDrawer() {
  document.getElementById("alert-drawer").classList.toggle("hidden");
}
function _closeDrawer() {
  document.getElementById("alert-drawer").classList.add("hidden");
}

async function _markAllAsRead() {
  const eventId = State.eventId;
  if (!eventId) return;

  if (confirm("Deseja realmente marcar todos os alertas ativos como lidos?")) {
    try {
      await API.acknowledgeAllAlerts(eventId);
      const updated = State.alerts.map(a => ({...a, acknowledged: true}));
      State.set("alerts", updated);
    } catch (err) {
      console.error("Erro ao marcar alertas como lidos:", err);
      alert("Erro ao marcar alertas como lidos.");
    }
  }
}

async function _deleteAllAlerts() {
  const eventId = State.eventId;
  if (!eventId) return;

  if (confirm("Deseja realmente excluir permanentemente todos os alertas deste evento?")) {
    try {
      await API.deleteAllAlerts(eventId);
      State.set("alerts", []);
    } catch (err) {
      console.error("Erro ao excluir alertas:", err);
      alert("Erro ao excluir alertas.");
    }
  }
}

async function _downloadAlertsLog() {
  const eventId = State.eventId;
  if (!eventId) return;

  try {
    const res = await API.downloadAlertsLog(eventId);
    if (res && res.ok) {
      alert(`Logs de alertas baixados com sucesso em:\n${res.path}`);
    } else {
      alert(`Erro ao baixar logs: ${res ? res.error : "Erro desconhecido"}`);
    }
  } catch (err) {
    console.error("Erro ao baixar logs de alertas:", err);
    alert("Erro ao baixar logs de alertas.");
  }
}

// ── Reautenticação de sessão (CAPTCHA/SSO) ────────────────────────

// Compatibilidade visual com alertas persistidos por versões antigas.
function _isReauthAlert(alert) {
  const msg = String(alert?.message ?? "").toLowerCase();
  return msg.includes("reautentic") || msg.includes("captcha");
}

function _displayMessage(alert) {
  if (_isReauthAlert(alert)) {
    return "Renovação automática da sessão em andamento; nenhuma ação manual é necessária.";
  }
  return alert.message;
}

// ── Helpers ───────────────────────────────────────────────────────

function _severityLabel(sev) {
  return sev === "CRITICAL" ? "▲" : "⚠";
}

function _formatTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("pt-BR");
}

function _esc(str) {
  return String(str ?? "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// Exporta para o app.js injetar novos alertas vindos do backend
export function injectAlerts(newAlerts) {
  const existing = new Set(State.alerts.map(a => a.id));
  const combined = [
    ...newAlerts.filter(a => !existing.has(a.id)),
    ...State.alerts,
  ];
  State.set("alerts", combined);
}
