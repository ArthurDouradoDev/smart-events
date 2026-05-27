"""
main.py — Ponto de entrada do Smart Events.
Inicializa o banco, a janela PyWebView e expõe a API Python ao JS.

Uso:
  python main.py           # produção (sem mock)
  python main.py --mock    # desenvolvimento com dados sintéticos
  python main.py --dev     # abre DevTools e habilita console
"""

import logging
import sys
from pathlib import Path

# Adiciona o root ao path para imports absolutos
sys.path.insert(0, str(Path(__file__).parent))

import webview
from api.api import Api
from core import database as db

logging.basicConfig(
    level=logging.DEBUG if "--dev" in sys.argv else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

FRONTEND = Path(__file__).parent / "frontend" / "index.html"


def main():
    mock_mode = "--mock" in sys.argv
    dev_mode = "--dev" in sys.argv

    logger.info(f"Iniciando Smart Events | mock={mock_mode} | dev={dev_mode}")

    db.init_db()

    api = Api()
    if mock_mode:
        # Injeta flag para que o frontend saiba que está em modo mock
        api._mock_mode = True

    window = webview.create_window(
        title="Smart Events",
        url=str(FRONTEND),
        js_api=api,
        width=1440,
        height=900,
        min_size=(1024, 600),
        resizable=True,
        fullscreen=False,
        background_color="#0D1117",
    )

    def _on_loaded():
        logger.info("Interface carregada")
        if mock_mode:
            window.evaluate_js("window.__MOCK_MODE__ = true;")

    window.events.loaded += _on_loaded

    webview.start(
        debug=dev_mode,
        http_server=False,   # serve arquivos locais diretamente
        storage_path=str(Path(__file__).parent / "data"),
    )


if __name__ == "__main__":
    main()
