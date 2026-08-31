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
} from "./bridge.js";

const REGION_DEBOUNCE_MS = 120;
const STATE_POLL_MS = 500;

let _initialized = false;
let _custom = false;
let _regionTimer = null;
let _stateTimer = null;
let _resizeObserver = null;

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
  document.documentElement.dataset.windowState = state || "normal";
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
  const close = document.getElementById("window-close");
  if (!bar || !drag || !minimize || !close) return;

  const payload = {
    titlebar: _rect(bar),
    draggable: [_rect(drag)],
    buttons: {
      minimize: _rect(minimize),
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

function _bindControls() {
  const drag = document.getElementById("window-titlebar-drag");
  const minimize = document.getElementById("window-minimize");
  const close = document.getElementById("window-close");

  minimize?.addEventListener("click", async () => {
    await windowMinimize();
    _scheduleStateSync();
  });
  close?.addEventListener("click", () => {
    void windowClose();
  });

  // O WebView2 recebe o ponteiro antes do HWND pai. Iniciar o move loop
  // nativo permite arrastar a janela maximizada entre monitores.
  drag?.addEventListener("pointerdown", event => {
    if (_custom && event.button === 0 && event.isPrimary !== false) void windowBeginDrag();
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
