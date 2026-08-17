"""Caminhos de recursos e dados persistentes do SmartEvents.

No executavel instalado, recursos sao somente-leitura em ``sys._MEIPASS`` e
todos os dados mutaveis pertencem ao perfil do operador.  Em desenvolvimento,
mantemos a estrutura historica do repositorio para nao alterar o fluxo local.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "SmartEvents"


def resource_dir() -> Path:
    """Raiz somente-leitura dos recursos empacotados ou do repositorio."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Pasta persistente e gravavel do operador.

    ``SMARTEVENTS_DATA_DIR`` existe para testes e diagnosticos isolados. Nunca e
    definida pelo instalador na operacao normal.
    """
    override = os.environ.get("SMARTEVENTS_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_app_data:
            raise RuntimeError("A variavel LOCALAPPDATA nao esta disponivel.")
        return Path(local_app_data) / APP_NAME
    return resource_dir() / "data"


def ensure_data_dir() -> Path:
    path = data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path
