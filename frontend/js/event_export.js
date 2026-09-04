/** Exportação dos dados persistidos do evento pelo indicador REC/DADOS. */
import API from "./bridge.js";
import State from "./state.js";

const TERMINAL = new Set(["ready", "failed", "cancelled"]);
let currentJob = null;
let latestJob = null;
let pollTimer = null;
let previewTimer = null;
let returnFocus = null;

const el = id => document.getElementById(id);

function options() {
  return {
    time_partition: document.querySelector('input[name="export-time"]:checked')?.value || "consolidated",
    technology_partition: document.querySelector('input[name="export-tech"]:checked')?.value || "combined",
    include_vips: Boolean(el("export-include-vips")?.checked),
  };
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("pt-BR");
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes <= 0) return "0 KB";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MB`;
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("pt-BR", {
    timeZone: "America/Sao_Paulo",
  });
}

function openModal() {
  if (!State.eventId) return;
  returnFocus = document.activeElement;
  el("event-data-modal")?.classList.remove("hidden");
  const event = State.activeEvent || State.historicalEvent || {};
  el("event-data-name").textContent = event.name || State.eventId;
  el("event-data-mode").textContent = State.mode === "active"
    ? "A coleta continuará enquanto o snapshot é preparado."
    : "Dados históricos gravados neste computador.";
  el("event-export-options")?.classList.add("hidden");
  el("event-export-result")?.classList.add("hidden");
  void refreshLatest();
  if (currentJob && currentJob.eventId === State.eventId) {
    renderJob(currentJob.data);
    if (!TERMINAL.has(currentJob.data.status)) startPolling();
  }
  setTimeout(() => el("event-data-close")?.focus(), 0);
}

function closeModal() {
  el("event-data-modal")?.classList.add("hidden");
  if (returnFocus && typeof returnFocus.focus === "function") returnFocus.focus();
}

async function refreshLatest() {
  const target = el("event-export-latest");
  if (!target || !State.eventId) return;
  const eventId = State.eventId;
  const response = await API.getLatestEventExport(eventId);
  if (State.eventId !== eventId) return;
  if (!response?.ok || !response.job) {
    latestJob = null;
    target.textContent = "Nenhuma exportação concluída para este evento.";
    return;
  }
  latestJob = response.job;
  const date = new Date(response.job.updated_at);
  target.textContent = `Última exportação: ${date.toLocaleString("pt-BR", {
    timeZone: "America/Sao_Paulo",
  })} — concluída`;
}

function showOptions() {
  el("event-export-options")?.classList.remove("hidden");
  el("event-export-result")?.classList.add("hidden");
  schedulePreview(0);
}

function schedulePreview(delay = 250) {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(refreshPreview, delay);
}

async function refreshPreview() {
  if (!State.eventId) return;
  const summary = el("event-export-preview");
  const warning = el("event-export-warning");
  summary.textContent = "Calculando volume disponível…";
  warning.classList.add("hidden");
  const response = await API.previewEventExport(State.eventId, options());
  if (!response?.ok) {
    summary.textContent = response?.error || "Não foi possível preparar a exportação.";
    return;
  }
  const counts = response.counts || {};
  const period = response.period || {};
  const details = [
    `${formatNumber(counts.kpis)} KPIs`,
    `${formatNumber(counts.vips)} VIPs`,
    `${formatNumber(counts.alarms)} alarmes`,
    `${formatNumber(counts.alerts)} alertas`,
    `estimativa ${formatBytes(response.estimated_bytes)}`,
    `EP ${response.ep_source_quality === "preserved" ? "preservada" : "reconstruída"}`,
  ];
  if (period.covered_days) details.push(`${formatNumber(period.covered_days)} dia(s) em Brasília`);
  if (response.technologies?.length) details.push(`tecnologias: ${response.technologies.join(", ")}`);
  const minDate = formatDate(period.min_brasilia);
  const maxDate = formatDate(period.max_brasilia);
  if (minDate && maxDate) details.push(`período: ${minDate} a ${maxDate}`);
  summary.textContent = details.join(" · ");
  const messages = [];
  if (response.event_status === "ACTIVE") messages.push("Será gerado um snapshot da gravação ativa.");
  if (response.excel_row_warning) messages.push("O consolidado ultrapassa o limite de linhas do Excel; considere separar por dia.");
  if (messages.length) {
    warning.textContent = messages.join(" ");
    warning.classList.remove("hidden");
  }
}

async function startExport() {
  if (!State.eventId) return;
  const button = el("event-export-start");
  button.disabled = true;
  const response = await API.startEventExport(State.eventId, options());
  button.disabled = false;
  if (!response?.ok) {
    showError(response?.error || "Não foi possível iniciar a exportação.");
    return;
  }
  currentJob = { id: response.job.job_id, eventId: State.eventId, data: response.job };
  el("event-export-options")?.classList.add("hidden");
  renderJob(response.job);
  startPolling();
}

function startPolling() {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (!currentJob) return;
    const response = await API.getEventExportStatus(currentJob.id);
    if (!response?.ok) {
      showError(response?.error || "Não foi possível consultar a exportação.");
      clearInterval(pollTimer);
      return;
    }
    currentJob.data = response.job;
    renderJob(response.job);
    if (TERMINAL.has(response.job.status)) clearInterval(pollTimer);
  }, 600);
}

function renderJob(job) {
  const result = el("event-export-result");
  result.classList.remove("hidden");
  el("event-export-progress-label").textContent = job.message || "Exportando…";
  const percent = Math.max(0, Math.min(100, Number(job.percent || 0)));
  el("event-export-progress-bar").style.width = `${percent}%`;
  el("event-export-progress-bar").setAttribute("aria-valuenow", String(percent));
  el("event-export-progress-detail").textContent = job.total
    ? `${formatNumber(job.current)} de ${formatNumber(job.total)} registros · ${percent}%`
    : `${percent}%`;
  const cancel = el("event-export-cancel");
  const retry = el("event-export-retry");
  const folder = el("event-export-open-folder");
  cancel.classList.toggle("hidden", TERMINAL.has(job.status) || job.status === "validating");
  retry.classList.toggle("hidden", !["failed", "cancelled"].includes(job.status));
  folder.classList.toggle("hidden", job.status !== "ready");
  el("event-export-result-error").classList.add("hidden");
  el("rec-indicator")?.classList.toggle("export-running", !TERMINAL.has(job.status));
  if (job.status === "ready" && job.result) {
    el("event-export-progress-label").textContent = "Exportação concluída";
    el("event-export-progress-detail").textContent =
      `${formatBytes(job.result.size_bytes)} · ${formatNumber(job.result.records)} registros · ${job.result.filename}`;
    void refreshLatest();
  } else if (job.status === "failed") {
    showError(job.error || "A exportação falhou.");
  } else if (job.status === "cancelled") {
    el("event-export-progress-detail").textContent = "Nenhum arquivo parcial foi mantido.";
  }
}

function showError(message) {
  const result = el("event-export-result");
  result.classList.remove("hidden");
  const error = el("event-export-result-error");
  error.textContent = message;
  error.classList.remove("hidden");
}

async function cancelExport() {
  if (!currentJob) return;
  const response = await API.cancelEventExport(currentJob.id);
  if (response?.ok) {
    currentJob.data = response.job;
    renderJob(response.job);
    if (TERMINAL.has(response.job.status)) clearInterval(pollTimer);
  }
}

async function openFolder() {
  if (!currentJob) return;
  const response = await API.openEventExportFolder(currentJob.id);
  if (!response?.ok) showError(response?.error || "Não foi possível abrir a pasta.");
}

function openClearFlow() {
  const warning = el("clear-export-warning");
  if (warning) {
    warning.textContent = latestJob
      ? `Última exportação concluída em ${new Date(latestJob.updated_at).toLocaleString("pt-BR", {
          timeZone: "America/Sao_Paulo",
        })}.`
      : "Nenhuma exportação concluída para este evento. Exporte antes de limpar se precisar preservar os dados.";
  }
  closeModal();
  el("clear-history-modal")?.classList.remove("hidden");
}

function handleKeydown(event) {
  const modal = el("event-data-modal");
  if (!modal || modal.classList.contains("hidden")) return;
  if (event.key === "Escape") {
    closeModal();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...modal.querySelectorAll(
    'button:not([disabled]):not(.hidden), input:not([disabled]), [tabindex="0"]'
  )].filter(node => node.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

export function notifyEventExportEventChanged() {
  clearTimeout(previewTimer);
  clearInterval(pollTimer);
  if (currentJob && currentJob.eventId !== State.eventId) currentJob = null;
  latestJob = null;
  closeModal();
  el("rec-indicator")?.classList.remove("export-running");
}

export function initEventExport() {
  const indicator = el("rec-indicator");
  if (!indicator) return;
  indicator.setAttribute("role", "button");
  indicator.setAttribute("tabindex", "0");
  indicator.setAttribute("aria-label", "Dados gravados do evento");
  indicator.addEventListener("click", openModal);
  indicator.addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openModal();
    }
  });
  el("event-data-close")?.addEventListener("click", closeModal);
  el("event-data-modal")?.addEventListener("click", event => {
    if (event.target === el("event-data-modal")) closeModal();
  });
  el("event-data-export")?.addEventListener("click", showOptions);
  el("event-data-clear")?.addEventListener("click", openClearFlow);
  el("clear-export-first")?.addEventListener("click", () => {
    el("clear-history-modal")?.classList.add("hidden");
    openModal();
    showOptions();
  });
  el("event-export-start")?.addEventListener("click", startExport);
  el("event-export-back")?.addEventListener("click", () => el("event-export-options")?.classList.add("hidden"));
  el("event-export-cancel")?.addEventListener("click", cancelExport);
  el("event-export-retry")?.addEventListener("click", showOptions);
  el("event-export-open-folder")?.addEventListener("click", openFolder);
  document.querySelectorAll('input[name="export-time"], input[name="export-tech"], #export-include-vips')
    .forEach(input => input.addEventListener("change", () => schedulePreview()));
  document.addEventListener("keydown", handleKeydown);
}
