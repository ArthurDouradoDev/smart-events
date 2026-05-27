/**
 * alerts.js — Toasts e drawer de alertas.
 */

import State from "./state.js";
import API   from "./bridge.js";

const TOAST_DURATION_MS = { WARNING: 10000, CRITICAL: 0 }; // 0 = não some

let _shownIds = new Set();

// ── Inicialização ─────────────────────────────────────────────────

export function initAlerts() {
  State.on("change:alerts", _renderDrawer);
  State.on("change:alerts", _renderToasts);
  State.on("change:alerts", _updateBadge);

  document.getElementById("alert-btn").addEventListener("click", _toggleDrawer);
  document.getElementById("alert-drawer-close")?.addEventListener("click", _closeDrawer);
  document.getElementById("alert-clear-all-btn")?.addEventListener("click", _clearAllAlerts);
}

// ── Badge do header ───────────────────────────────────────────────

function _updateBadge(alerts) {
  const count = alerts.filter(a => !a.acknowledged).length;
  const badge = document.getElementById("alert-count");
  badge.textContent = count;
  badge.classList.toggle("hidden", count === 0);
}

// ── Toasts ────────────────────────────────────────────────────────

function _renderToasts(alerts) {
  if (State.mode === "historical") return;
  const newAlerts = alerts.filter(a => !_shownIds.has(a.id) && !a.acknowledged);
  newAlerts.forEach(alert => {
    _shownIds.add(alert.id);
    
    // Ignora alertas de severidade WARNING (Atenção) para exibição em Toasts
    if (alert.severity === "WARNING") return;
    
    const msg = (alert.message || "").toLowerCase();
    const isVipAlert = msg.includes("vip") || msg.includes("rsrp") || msg.includes("rsrq");
    const isAvailAlert = msg.includes("disponibilidade") || msg.includes("availability") || msg.includes("indisponibilidade") || msg.includes("offline");
    
    if (isVipAlert || isAvailAlert) {
      _showToast(alert);
    }
  });
}

function _showToast(alert) {
  const container = document.getElementById("toast-container");

  const toast = document.createElement("div");
  toast.className = `toast ${alert.severity.toLowerCase()}`;
  toast.innerHTML = `
    <div class="toast-body">
      <div class="toast-title">${_severityLabel(alert.severity)} ${_esc(alert.site_id)}</div>
      <div class="toast-msg">${_esc(alert.message)}</div>
    </div>
    <button class="toast-close" aria-label="Fechar">&#10005;</button>`;

  toast.querySelector(".toast-close").addEventListener("click", () => toast.remove());
  container.appendChild(toast);

  const duration = TOAST_DURATION_MS[alert.severity.toUpperCase()];
  if (duration > 0) setTimeout(() => toast.remove(), duration);
}

// ── Drawer ────────────────────────────────────────────────────────

function _renderDrawer(alerts) {
  const list = document.getElementById("alert-list");
  if (!list) return;

  const clearBtn = document.getElementById("alert-clear-all-btn");
  const activeCount = alerts.filter(a => !a.acknowledged).length;
  
  if (clearBtn) {
    if (State.mode === "historical" || activeCount === 0) {
      clearBtn.classList.add("hidden");
    } else {
      clearBtn.classList.remove("hidden");
    }
  }

  if (!alerts.length) {
    list.innerHTML = `<div style="padding:16px;color:#484F58;font-size:12px">Nenhum alerta ativo.</div>`;
    return;
  }

  list.innerHTML = "";
  [...alerts].sort((a, b) => {
    const sev = { CRITICAL: 0, WARNING: 1 };
    return (sev[a.severity] ?? 2) - (sev[b.severity] ?? 2);
  }).forEach(alert => {
    const item = document.createElement("div");
    item.className = `alert-item ${alert.severity.toLowerCase()}`;
    item.innerHTML = `
      <div class="alert-item-body">
        <div class="alert-item-msg">${_esc(alert.message)}</div>
        <div class="alert-item-time">${_formatTime(alert.timestamp)}</div>
      </div>
      ${State.mode === "historical" ? "" : `<button class="alert-ack" data-id="${alert.id}">OK</button>`}`;

    if (State.mode !== "historical") {
      item.querySelector(".alert-ack").addEventListener("click", async () => {
        await API.acknowledgeAlert(alert.id);
        State.set("alerts", State.alerts.filter(a => a.id !== alert.id));
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

async function _clearAllAlerts() {
  const eventId = State.eventId;
  if (!eventId) return;

  if (confirm("Deseja realmente limpar todos os alertas ativos deste evento?")) {
    try {
      await API.acknowledgeAllAlerts(eventId);
      State.set("alerts", []);
    } catch (err) {
      console.error("Erro ao limpar alertas:", err);
      alert("Erro ao limpar alertas.");
    }
  }
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
