r"""Prova manual do frame Win32 usado pela barra HTML.

Uso::

    .\.venv\Scripts\python.exe tools\window_chrome_smoke.py

Conclusoes incorporadas ao experimento:

* o ``HWND`` do WinForms esta disponivel em ``before_show`` por
  ``window.native.Handle`` no pywebview 6.2.1;
* Windows 10 e 11 usam o mesmo caminho de codigo para resize, Snap e
  maximizacao; a matriz manual ainda deve ser repetida nos dois sistemas;
* no Windows 11, Snap Layout depende de ``HTMAXBUTTON`` sobre a regiao exata do
  botao maximizar. O Windows 10 nao oferece esse flyout, mas preserva o Snap;
* WebView2 em modo windowed usa um HWND filho para entrada. Por isso a faixa
  HTML inicia o move loop do HWND pai explicitamente; ``easy_drag`` continua
  desligado e as bordas permanecem no hit-test nativo;
* coordenadas de ``WM_NCHITTEST`` sao assinadas e os retangulos HTML precisam
  ser convertidos de 96 DPI para pixels fisicos.

O script e propositalmente independente do frontend e do banco do Smart Events.
Passe ``--auto-close`` para uma verificacao automatizada curta de inicializacao
e desmontagem; a homologacao completa continua sendo manual.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.window_chrome import WindowChromeController  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("window_chrome_smoke")


HTML = r"""
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root { color-scheme: dark; font-family: "Segoe UI", sans-serif; }
    * { box-sizing: border-box; }
    html, body { width: 100%; height: 100%; margin: 0; overflow: hidden; }
    body { background: #0d1117; color: #d8dee9; display: grid; grid-template-rows: 36px 1fr; }
    #titlebar { display: grid; grid-template-columns: 1fr repeat(3, 46px); background: #161b22;
      border-bottom: 1px solid #30363d; user-select: none; }
    #drag { display: flex; align-items: center; padding: 0 12px; font-size: 12px; letter-spacing: .03em; }
    button { border: 0; color: #d8dee9; background: transparent; font: 16px "Segoe UI Symbol", sans-serif; }
    button:hover { background: #30363d; }
    #close:hover { background: #c42b1c; color: white; }
    main { display: grid; place-items: center; padding: 24px; }
    .card { width: min(560px, 90%); border: 1px solid #30363d; border-radius: 10px;
      background: #161b22; padding: 24px; text-align: center; }
    code { color: #79c0ff; }
  </style>
</head>
<body>
  <header id="titlebar">
    <div id="drag">Smart Events — prova Win32</div>
    <button id="minimize" aria-label="Minimizar">&#x2212;</button>
    <button id="maximize" aria-label="Maximizar ou restaurar">&#x25A1;</button>
    <button id="close" aria-label="Fechar">&#x00D7;</button>
  </header>
  <main>
    <section class="card">
      <h1>Window chrome smoke</h1>
      <p>Teste arraste, duplo clique, Snap, Alt+Space e resize nas oito direcoes.</p>
      <p id="state"><code>aguardando pywebview...</code></p>
    </section>
  </main>
  <script>
    const rect = element => {
      const r = element.getBoundingClientRect();
      return {x: r.x, y: r.y, width: r.width, height: r.height};
    };
    let updateTimer;
    async function updateRegions() {
      const bar = document.querySelector('#titlebar');
      const payload = {
        titlebar: rect(bar),
        draggable: [rect(document.querySelector('#drag'))],
        buttons: {
          minimize: rect(document.querySelector('#minimize')),
          maximize: rect(document.querySelector('#maximize')),
          close: rect(document.querySelector('#close'))
        }
      };
      const accepted = await pywebview.api.set_regions(payload);
      const state = await pywebview.api.get_state();
      document.querySelector('#state').innerHTML = `<code>${accepted ? 'regioes OK' : 'regioes rejeitadas'} · ${JSON.stringify(state)}</code>`;
    }
    function scheduleRegions() {
      clearTimeout(updateTimer);
      updateTimer = setTimeout(updateRegions, 60);
    }
    window.addEventListener('pywebviewready', () => {
      document.querySelector('#drag').addEventListener('pointerdown', event => {
        if (event.button === 0 && event.detail === 1) pywebview.api.begin_drag();
      });
      document.querySelector('#drag').addEventListener('dblclick', () => pywebview.api.toggle_maximize());
      document.querySelector('#minimize').addEventListener('click', () => pywebview.api.minimize());
      document.querySelector('#maximize').addEventListener('click', () => pywebview.api.toggle_maximize());
      document.querySelector('#close').addEventListener('click', () => pywebview.api.close());
      new ResizeObserver(scheduleRegions).observe(document.querySelector('#titlebar'));
      updateRegions();
    });
  </script>
</body>
</html>
"""


class SmokeApi:
    def __init__(self, controller: WindowChromeController) -> None:
        self.controller = controller

    def minimize(self) -> bool:
        return self.controller.minimize()

    def begin_drag(self) -> bool:
        return self.controller.begin_drag()

    def toggle_maximize(self) -> bool:
        return self.controller.toggle_maximize()

    def close(self) -> bool:
        return self.controller.close()

    def get_state(self) -> dict[str, Any]:
        return self.controller.get_state()

    def set_regions(self, payload: dict[str, Any]) -> bool:
        accepted = self.controller.set_regions(payload)
        log.info("Regioes HTML recebidas | accepted=%s | payload=%s", accepted, payload)
        return accepted


def main() -> int:
    if sys.platform != "win32":
        log.error("Esta prova requer Windows.")
        return 2

    import webview

    controller = WindowChromeController(log=log)
    api = SmokeApi(controller)
    window = webview.create_window(
        "Smart Events - Window Chrome Smoke",
        html=HTML,
        js_api=api,
        width=760,
        height=480,
        min_size=(480, 320),
        resizable=True,
        frameless=True,
        easy_drag=False,
        background_color="#0D1117",
    )

    def before_show() -> None:
        if not controller.attach(window):
            log.error("Attach falhou; diagnostico: %s", json.dumps(controller.get_state(), ensure_ascii=False))
        else:
            log.info("Attach aprovado: %s", json.dumps(controller.get_state(), ensure_ascii=False))

    def loaded() -> None:
        if "--auto-close" in sys.argv:
            threading.Timer(1.0, controller.close).start()

    window.events.before_show += before_show
    window.events.loaded += loaded
    try:
        webview.start(gui="edgechromium", private_mode=True)
    finally:
        controller.detach()

    state = controller.get_state()
    log.info("Smoke encerrado: %s", json.dumps(state, ensure_ascii=False))
    return 0 if not controller.attached else 1


if __name__ == "__main__":
    raise SystemExit(main())
