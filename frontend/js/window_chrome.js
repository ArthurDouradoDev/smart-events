/**
 * Barra de titulo HTML e sincronizacao das regioes nativas de hit-test.
 *
 * O modulo nao presume que esta dentro do PyWebView: no navegador comum a
 * barra permanece oculta; ``?chromePreview=1`` ativa mocks inofensivos.
 */

import {
  windowBeginDrag,
  windowClose,
  windowGetState,
  windowMinimize,
  windowSetChromeRegions,
  windowToggleFullscreen,
  windowToggleMaximize,
} from "./bridge.js";

const REGION_DEBOUNCE_MS = 120;
const STATE_POLL_MS = 500;
// O duplo clique na barra pode chegar por dois caminhos — o handler abaixo e o
// WM_NCLBUTTONDBLCLK nativo, quando o hit-test alcanca o HWND pai. Sem esta
// janela de guarda a janela alternaria duas vezes e voltaria ao estado inicial.
const TOGGLE_GUARD_MS = 400;

let _initialized = false;
let _custom = false;
let _regionTimer = null;
let _stateTimer = null;
let _resizeObserver = null;
let _lastToggleAt = 0;

function _previewRequested() {
  return new URLSearchParams(window.location.search).get("chromePreview") === "1";
}

function _rect(element) {
  const box = element.getBoundingClientRect();
  const clean = value => Math.round(Math.max(0, value) * 1000) / 1000;
  return {
    x: clean(box.left),
    y: clean(box.top),
    width: clean(box.width),
    height: clean(box.height),
  };
}

function _setWindowState(state) {
  const value = state || "normal";
  document.documentElement.dataset.windowState = value;

  const maximize = document.getElementById("window-maximize");
  if (maximize) {
    const restores = value === "maximized" || value === "fullscreen";
    const label = restores ? "Restaurar" : "Maximizar";
    maximize.setAttribute("aria-label", label);
    maximize.title = label;
  }
  const fullscreen = document.getElementById("window-fullscreen");
  if (fullscreen) {
    const active = value === "fullscreen";
    fullscreen.setAttribute("aria-pressed", String(active));
    const label = active ? "Sair da tela cheia" : "Tela cheia";
    fullscreen.setAttribute("aria-label", label);
    fullscreen.title = label;
  }
}

function _announceLayoutChange() {
  window.dispatchEvent(new CustomEvent("smart-events:layout-resize"));
}

function _setCustomMode(enabled) {
  const bar = document.getElementById("window-titlebar");
  _custom = !!enabled;
  document.documentElement.classList.toggle("chrome-custom", _custom);
  document.documentElement.classList.toggle("chrome-native", !_custom);
  if (bar) {
    bar.hidden = !_custom;
    bar.setAttribute("aria-hidden", String(!_custom));
  }
  _announceLayoutChange();
  if (_custom) _scheduleRegions(0);
}

async function _syncState() {
  try {
    const result = await windowGetState();
    const custom = result?.mode === "custom" && result?.attached !== false;
    if (custom !== _custom) _setCustomMode(custom);
    _setWindowState(result?.state || "normal");
    return result;
  } catch (error) {
    console.error("Falha ao consultar estado da janela:", error);
    _setCustomMode(_previewRequested());
    return null;
  }
}

async function _sendRegions() {
  _regionTimer = null;
  if (!_custom) return;
  const bar = document.getElementById("window-titlebar");
  const drag = document.getElementById("window-titlebar-drag");
  const minimize = document.getElementById("window-minimize");
  const maximize = document.getElementById("window-maximize");
  const close = document.getElementById("window-close");
  if (!bar || !drag || !minimize || !maximize || !close) return;

  const payload = {
    titlebar: _rect(bar),
    draggable: [_rect(drag)],
    buttons: {
      minimize: _rect(minimize),
      // O backend devolve HTMAXBUTTON nesta regiao; e ela que faz o Windows 11
      // mostrar os Snap Layouts ao pousar o ponteiro sobre o botao.
      maximize: _rect(maximize),
      close: _rect(close),
    },
  };
  try {
    await windowSetChromeRegions(payload);
  } catch (error) {
    console.error("Falha ao atualizar regioes da barra da janela:", error);
  }
}

function _scheduleRegions(delay = REGION_DEBOUNCE_MS) {
  clearTimeout(_regionTimer);
  _regionTimer = setTimeout(_sendRegions, delay);
}

function _scheduleStateSync() {
  window.setTimeout(_syncState, 0);
  window.setTimeout(_syncState, 100);
  window.setTimeout(_syncState, 260);
}

async function _toggle(action) {
  const now = Date.now();
  if (now - _lastToggleAt < TOGGLE_GUARD_MS) return;
  _lastToggleAt = now;
  try {
    await action();
  } catch (error) {
    console.error("Falha ao alternar o estado da janela:", error);
  }
  _scheduleStateSync();
}

function _bindControls() {
  const drag = document.getElementById("window-titlebar-drag");
  const minimize = document.getElementById("window-minimize");
  const maximize = document.getElementById("window-maximize");
  const fullscreen = document.getElementById("window-fullscreen");
  const close = document.getElementById("window-close");

  minimize?.addEventListener("click", async () => {
    await windowMinimize();
    _scheduleStateSync();
  });
  maximize?.addEventListener("click", () => void _toggle(windowToggleMaximize));
  fullscreen?.addEventListener("click", () => void _toggle(windowToggleFullscreen));
  close?.addEventListener("click", () => {
    void windowClose();
  });

  // O WebView2 recebe o ponteiro antes do HWND pai. Iniciar o move loop
  // nativo permite arrastar a janela — inclusive maximizada, caso em que o
  // proprio Windows a restaura sob o cursor.
  drag?.addEventListener("pointerdown", event => {
    if (!_custom || event.button !== 0 || event.isPrimary === false) return;
    // O segundo clique de um duplo clique e do handler de dblclick abaixo:
    // enviar outro WM_NCLBUTTONDOWN aqui inicia um move loop concorrente.
    if (event.detail > 1) return;
    event.preventDefault();
    void windowBeginDrag().then(ok => {
      if (!ok) console.error("O controlador nativo recusou o arraste da janela.");
    }).catch(error => {
      console.error("Falha ao iniciar o arraste da janela:", error);
    });
  });

  drag?.addEventListener("dblclick", () => {
    if (!_custom) return;
    void _toggle(windowToggleMaximize);
  });
}

function _bindResizeTracking() {
  const bar = document.getElementById("window-titlebar");
  if (bar && "ResizeObserver" in window) {
    _resizeObserver = new ResizeObserver(() => _scheduleRegions());
    _resizeObserver.observe(bar);
  }
  window.addEventListener("resize", () => {
    _scheduleRegions();
    _scheduleStateSync();
    _announceLayoutChange();
  }, { passive: true });
  window.addEventListener("focus", _scheduleStateSync);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) _scheduleStateSync();
  });
}

export async function initWindowChrome() {
  if (_initialized) return;
  _initialized = true;
  _bindControls();
  _bindResizeTracking();

  // Preview deve aparecer sem depender do atraso de deteccao do pywebview.
  if (_previewRequested()) _setCustomMode(true);
  const state = await _syncState();
  if (state?.mode === "custom") {
    _stateTimer = window.setInterval(_syncState, STATE_POLL_MS);
  }
}

export function disposeWindowChrome() {
  clearTimeout(_regionTimer);
  clearInterval(_stateTimer);
  _resizeObserver?.disconnect();
  _resizeObserver = null;
}
