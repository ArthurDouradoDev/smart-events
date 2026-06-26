/**
 * credentials.js — Credenciais de acesso ao iManager (Cliente → Regional).
 *
 * Dois fluxos:
 *  - Modal sob demanda (promptCredentials): pedido no 1º acesso, quando uma regional não tem
 *    credencial e o evento vai ativar a coleta HTTP.
 *  - Gerenciador (botão do header): visualizar/atualizar credenciais por cliente, com a opção
 *    de uma credencial compartilhada (mesma conta para todas as regionais do cliente).
 */

import API from "./bridge.js";

let _promptResolve = null;   // resolver da Promise do modal sob demanda
let _promptCtx = null;       // { cliente, region }

export function initCredentials() {
  document.getElementById("credentials-btn")?.addEventListener("click", openManager);
  document.getElementById("open-credentials-btn")?.addEventListener("click", openManager);

  // Modal sob demanda
  document.getElementById("cred-modal-cancel")?.addEventListener("click", () => _closePrompt(false));
  document.getElementById("cred-modal-save")?.addEventListener("click", _savePrompt);

  // Gerenciador
  document.getElementById("cred-mgr-close")?.addEventListener("click", _closeManager);
  document.getElementById("cred-mgr-cliente")?.addEventListener("change", _renderManagerBody);
  document.getElementById("cred-mgr-shared-toggle")?.addEventListener("change", _renderManagerBody);
}

// ── Modal sob demanda (1º acesso) ────────────────────────────────

/**
 * Abre o modal pedindo a credencial de (cliente, region). Se `cliente` vier vazio (evento
 * legado sem cliente), mostra um seletor de Cliente (filtrado às que atendem a regional).
 * Resolve `{ cliente }` com o cliente usado ao salvar, ou `null` se cancelou.
 */
export async function promptCredentials(cliente, region) {
  _promptCtx = { cliente: cliente || "", region: region || "" };
  const clienteRow = document.getElementById("cred-modal-cliente-row");
  const select = document.getElementById("cred-modal-cliente");
  const target = document.getElementById("cred-modal-target");

  if (cliente) {
    clienteRow.classList.add("hidden");
    target.textContent = `${cliente} / ${region || "—"}`;
  } else {
    // Evento sem cliente: o operador escolhe a qual cliente esta regional pertence.
    target.textContent = `regional ${region || "—"}`;
    const options = await _clientesParaRegional(region);
    select.innerHTML = options.map(c => `<option value="${_esc(c)}">${_esc(c)}</option>`).join("");
    clienteRow.classList.toggle("hidden", options.length === 0);
  }

  document.getElementById("cred-modal-user").value = "";
  document.getElementById("cred-modal-pass").value = "";
  _hide("cred-modal-error");
  document.getElementById("credentials-modal").classList.remove("hidden");
  return new Promise((resolve) => { _promptResolve = resolve; });
}

async function _clientesParaRegional(region) {
  try {
    const res = await API.getClientes();
    const map = (res && res.ok && res.clientes) ? res.clientes : {};
    const reg = (region || "").toUpperCase();
    const comRegional = Object.keys(map).filter(c =>
      (map[c].regionais || []).some(r => String(r).toUpperCase() === reg));
    return comRegional.length ? comRegional : Object.keys(map);
  } catch (err) {
    console.error("Erro ao carregar clientes:", err);
    return [];
  }
}

async function _savePrompt() {
  const user = document.getElementById("cred-modal-user").value.trim();
  const pass = document.getElementById("cred-modal-pass").value;
  if (!user || !pass) {
    _showError("cred-modal-error", "Informe usuário e senha.");
    return;
  }
  const { cliente, region } = _promptCtx || {};
  const chosen = cliente || document.getElementById("cred-modal-cliente").value;
  if (!chosen) {
    _showError("cred-modal-error", "Selecione o cliente.");
    return;
  }
  try {
    const res = await API.saveCredentials(chosen, region, user, pass);
    if (res && res.ok) {
      _closePrompt({ cliente: chosen });
    } else {
      _showError("cred-modal-error", (res && res.error) || "Erro ao salvar credenciais.");
    }
  } catch (err) {
    console.error("Erro ao salvar credenciais:", err);
    _showError("cred-modal-error", "Erro ao salvar credenciais.");
  }
}

function _closePrompt(result) {
  document.getElementById("credentials-modal").classList.add("hidden");
  const resolve = _promptResolve;
  _promptResolve = null;
  _promptCtx = null;
  if (resolve) resolve(result || null);
}

// ── Gerenciador (atualização) ────────────────────────────────────

async function openManager() {
  const select = document.getElementById("cred-mgr-cliente");
  try {
    await API.syncClientes();  // garante o catálogo mais recente do servidor
  } catch (err) {
    console.error("Erro ao sincronizar clientes:", err);
  }
  try {
    const res = await API.getClientes();
    const clientes = (res && res.ok && res.clientes) ? Object.keys(res.clientes) : [];
    select.innerHTML = clientes.map(c => `<option value="${_esc(c)}">${_esc(c)}</option>`).join("");
  } catch (err) {
    console.error("Erro ao carregar clientes:", err);
    select.innerHTML = "";
  }
  document.getElementById("credentials-manager-modal").classList.remove("hidden");
  await _renderManagerBody();
}

function _closeManager() {
  document.getElementById("credentials-manager-modal").classList.add("hidden");
}

async function _renderManagerBody() {
  const cliente = document.getElementById("cred-mgr-cliente").value;
  const body = document.getElementById("cred-mgr-body");
  const useShared = document.getElementById("cred-mgr-shared-toggle").checked;
  _hide("cred-mgr-feedback");
  if (!cliente) { body.innerHTML = ""; return; }

  let status;
  try {
    status = await API.getCredentialsStatus(cliente);
  } catch (err) {
    console.error("Erro ao carregar status de credenciais:", err);
    body.innerHTML = `<div class="cred-missing">Erro ao carregar credenciais.</div>`;
    return;
  }
  if (!status || !status.ok) {
    body.innerHTML = `<div class="cred-missing">Erro ao carregar credenciais.</div>`;
    return;
  }

  if (useShared) {
    body.innerHTML = `
      <p style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">
        Esta conta será usada em todas as regionais de <strong>${_esc(cliente)}</strong>.</p>
      <label style="font-size:12px;display:block;">Usuário
        <input id="cred-shared-user" type="text" autocomplete="off" class="cred-input"
               value="${_esc(status.shared?.username || "")}">
      </label>
      <label style="font-size:12px;display:block;margin-top:10px;">Senha
        <input id="cred-shared-pass" type="password" autocomplete="off" class="cred-input"
               placeholder="${status.shared?.configured ? "••••••• (preencha para alterar)" : ""}">
      </label>
      <div style="display:flex;justify-content:flex-end;margin-top:12px;">
        <button id="cred-shared-save" class="btn btn-primary btn-sm">Salvar</button>
      </div>`;
    document.getElementById("cred-shared-save").addEventListener("click", () => _saveShared(cliente));
    return;
  }

  const rows = (status.regionais || []).map(r => {
    const statusLabel = r.uses_shared
      ? `<span class="cred-status cred-ok">usa compartilhada</span>`
      : (r.configured
          ? `<span class="cred-status cred-ok">configurada</span>`
          : `<span class="cred-status cred-missing">não configurada</span>`);
    return `
      <div class="cred-regional-row">
        <div style="flex:1;min-width:0;">
          <div style="display:flex;gap:8px;align-items:center;">
            <strong>${_esc(r.region)}</strong> ${statusLabel}
          </div>
          <input data-region="${_esc(r.region)}" class="cred-user cred-input" type="text"
                 autocomplete="off" placeholder="usuário" value="${_esc(r.username || "")}">
          <input data-region="${_esc(r.region)}" class="cred-pass cred-input" type="password"
                 autocomplete="off" placeholder="senha">
        </div>
        <div style="display:flex;flex-direction:column;gap:6px;flex-shrink:0;">
          <button data-region="${_esc(r.region)}" class="cred-save btn btn-primary btn-sm">Salvar</button>
          <button data-region="${_esc(r.region)}" class="cred-clear btn btn-ghost btn-sm">Limpar</button>
        </div>
      </div>`;
  }).join("");
  body.innerHTML = rows || `<div class="cred-missing">Cliente sem regionais cadastradas.</div>`;

  body.querySelectorAll(".cred-save").forEach(btn =>
    btn.addEventListener("click", () => _saveRegional(cliente, btn.dataset.region)));
  body.querySelectorAll(".cred-clear").forEach(btn =>
    btn.addEventListener("click", () => _clearRegional(cliente, btn.dataset.region)));
}

async function _saveShared(cliente) {
  const user = document.getElementById("cred-shared-user").value.trim();
  const pass = document.getElementById("cred-shared-pass").value;
  if (!user || !pass) { _feedback("Informe usuário e senha.", "danger"); return; }
  try {
    const res = await API.saveSharedCredentials(cliente, user, pass);
    _feedback(res && res.ok ? "Credencial compartilhada salva." : (res?.error || "Erro ao salvar."),
              res && res.ok ? "success" : "danger");
  } catch (err) {
    console.error("Erro ao salvar credencial compartilhada:", err);
    _feedback("Erro ao salvar credencial compartilhada.", "danger");
  }
}

async function _saveRegional(cliente, region) {
  const userInput = document.querySelector(`.cred-user[data-region="${region}"]`);
  const passInput = document.querySelector(`.cred-pass[data-region="${region}"]`);
  const user = (userInput?.value || "").trim();
  const pass = passInput?.value || "";
  if (!user || !pass) { _feedback(`Informe usuário e senha para ${region}.`, "danger"); return; }
  try {
    const res = await API.saveCredentials(cliente, region, user, pass);
    if (res && res.ok) {
      _feedback(`Credencial de ${region} salva.`, "success");
      await _renderManagerBody();
    } else {
      _feedback((res && res.error) || "Erro ao salvar.", "danger");
    }
  } catch (err) {
    console.error("Erro ao salvar credencial:", err);
    _feedback("Erro ao salvar credencial.", "danger");
  }
}

async function _clearRegional(cliente, region) {
  try {
    const res = await API.deleteCredentials(cliente, region);
    if (res && res.ok) {
      _feedback(`Credencial de ${region} removida.`, "success");
      await _renderManagerBody();
    } else {
      _feedback((res && res.error) || "Erro ao remover.", "danger");
    }
  } catch (err) {
    console.error("Erro ao remover credencial:", err);
    _feedback("Erro ao remover credencial.", "danger");
  }
}

// ── Helpers ──────────────────────────────────────────────────────

function _feedback(msg, type) {
  const el = document.getElementById("cred-mgr-feedback");
  if (!el) return;
  el.textContent = msg;
  el.style.color = type === "success" ? "var(--success)" : "var(--danger)";
  el.classList.remove("hidden");
}

function _showError(id, msg) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = msg;
  el.classList.remove("hidden");
}

function _hide(id) {
  document.getElementById(id)?.classList.add("hidden");
}

function _esc(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
