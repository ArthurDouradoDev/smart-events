/**
 * vip.js — Renderização do painel de VIPs.
 */

import State from "./state.js";

const STATUS_COLORS = {
  ok:       "#3FB950",
  warning:  "#D29922",
  critical: "#F85149",
  unknown:  "#484F58",
};

export function initVip() {
  State.on("change:vips", render);
}

export function render(vips) {
  const list = document.getElementById("vip-list");
  const summary = document.getElementById("vip-summary");
  if (!list) return;

  const { inEvent, outEvent } = State.vipCounts;
  summary.textContent = `${inEvent} no evento · ${outEvent} fora`;

  const inEventVips  = vips.filter(v => v.in_event);
  const outEventVips = vips.filter(v => !v.in_event);

  list.innerHTML = "";

  if (inEventVips.length) {
    list.appendChild(_section("No evento"));
    inEventVips.forEach(v => list.appendChild(_card(v)));
  }

  if (outEventVips.length) {
    list.appendChild(_section("Fora do evento"));
    outEventVips.forEach(v => list.appendChild(_card(v, true)));
  }
}

function _section(label) {
  const el = document.createElement("div");
  el.className = "vip-section";
  el.textContent = label;
  return el;
}

function _card(vip, dim = false) {
  const card = document.createElement("div");
  card.className = `vip-card status-${vip.status}${dim ? " out-of-event" : ""}`;

  const color = STATUS_COLORS[vip.status] || STATUS_COLORS.unknown;

  card.innerHTML = `
    <div class="vip-header">
      <span class="vip-dot" style="background:${color}"></span>
      <span class="vip-name">${_esc(vip.name)}</span>
      <span class="vip-site">${_esc(vip.serving_cell || "—")}</span>
    </div>
    ${vip.in_event && vip.rsrp != null ? `
      ${_signalBar("RSRP", vip.rsrp, vip.rsrp_min ?? -110, vip.rsrp_max ?? -40, vip.status, "dBm")}
      ${_signalBar("RSRQ", vip.rsrq, -20, -3, _rsrqStatus(vip.rsrq), "dB")}
    ` : ""}`;

  return card;
}

function _signalBar(label, value, min, max, status, unit) {
  const pct = Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100));
  const fillClass = status === "ok" ? "ok" : status === "warning" ? "warning" : "critical";
  return `
    <div class="signal-bar">
      <span class="signal-label">${label}</span>
      <div class="signal-track">
        <div class="signal-fill ${fillClass}" style="width:${pct.toFixed(1)}%"></div>
      </div>
      <span class="signal-value">${value.toFixed(0)}</span>
    </div>`;
}

function _rsrqStatus(rsrq) {
  if (rsrq == null) return "unknown";
  if (rsrq <= -15) return "critical";
  if (rsrq <= -12) return "warning";
  return "ok";
}

function _esc(str) {
  return String(str ?? "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
