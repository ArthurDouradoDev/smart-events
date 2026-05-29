/**
 * logs.js — Drawer de logs de coleta (painel do desenvolvedor </>).
 * Mostra os logs em tempo real da coleta (collector/scheduler/renovação de sessão) e
 * permite baixá-los. Acessado pelo botão </> no header.
 */

import API from "./bridge.js";

const REFRESH_MS = 4000;
let _refreshTimer = null;

const LEVEL_COLOR = {
  ERROR: "#F85149",
  WARNING: "#D29922",
  INFO: "#8B949E",
  DEBUG: "#484F58",
};

export function initLogs() {
  document.getElementById("logs-btn")?.addEventListener("click", _toggleDrawer);
  document.getElementById("logs-drawer-close")?.addEventListener("click", _closeDrawer);
  document.getElementById("logs-download-btn")?.addEventListener("click", _downloadLogs);
  document.getElementById("logs-clear-btn")?.addEventListener("click", _clearLogs);
}

function _isOpen() {
  return !document.getElementById("logs-drawer")?.classList.contains("hidden");
}

function _toggleDrawer() {
  const drawer = document.getElementById("logs-drawer");
  if (!drawer) return;
  drawer.classList.toggle("hidden");
  if (_isOpen()) {
    _refresh();
    _startAutoRefresh();
  } else {
    _stopAutoRefresh();
  }
}

function _closeDrawer() {
  document.getElementById("logs-drawer")?.classList.add("hidden");
  _stopAutoRefresh();
}

function _startAutoRefresh() {
  _stopAutoRefresh();
  _refreshTimer = setInterval(() => {
    const auto = document.getElementById("logs-autorefresh");
    if (_isOpen() && (!auto || auto.checked)) _refresh();
  }, REFRESH_MS);
}

function _stopAutoRefresh() {
  if (_refreshTimer) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
  }
}

async function _refresh() {
  const list = document.getElementById("logs-list");
  if (!list) return;
  try {
    const res = await API.getCollectionLogs(800);
    if (!res || !res.ok) {
      list.innerHTML = `<div style="padding:16px;color:#F85149;font-size:12px">Erro ao carregar logs: ${_esc(res ? res.error : "desconhecido")}</div>`;
      return;
    }
    _render(res.logs || []);
  } catch (err) {
    console.error("Erro ao buscar logs de coleta:", err);
  }
}

function _render(logs) {
  const list = document.getElementById("logs-list");
  if (!list) return;

  // Preserva a posição se o usuário rolou para cima; auto-scroll só se já estava no fim.
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;

  if (!logs.length) {
    list.innerHTML = `<div style="padding:16px;color:#484F58;font-size:12px">Nenhum log de coleta ainda. A coleta gera registros quando um evento está ativo e conectado ao iManager.</div>`;
    return;
  }

  list.innerHTML = logs.map((r) => {
    const color = LEVEL_COLOR[r.level] || "#8B949E";
    const short = (r.logger || "").replace(/^core\./, "");
    return `<div style="font-family:var(--font-mono);font-size:11px;line-height:1.5;padding:2px 12px;border-bottom:1px solid #161B22;white-space:pre-wrap;word-break:break-word;">
      <span style="color:#484F58;">${_esc(r.ts)}</span>
      <span style="color:${color};font-weight:600;"> ${_esc(r.level)}</span>
      <span style="color:#6E7681;"> ${_esc(short)}</span>
      <span style="color:#C9D1D9;"> ${_esc(r.msg)}</span>
    </div>`;
  }).join("");

  if (atBottom) list.scrollTop = list.scrollHeight;
}

async function _downloadLogs() {
  try {
    const res = await API.downloadCollectionLogs();
    if (res && res.ok) {
      alert(`Logs de coleta baixados em:\n${res.path}`);
    } else {
      alert(`Erro ao baixar logs: ${res ? res.error : "desconhecido"}`);
    }
  } catch (err) {
    console.error("Erro ao baixar logs de coleta:", err);
    alert("Erro ao baixar logs de coleta.");
  }
}

async function _clearLogs() {
  try {
    await API.clearCollectionLogs();
    _render([]);
  } catch (err) {
    console.error("Erro ao limpar logs:", err);
  }
}

function _esc(str) {
  return String(str ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
