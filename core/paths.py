"""Caminhos de recursos e dados persistentes do SmartEvents.

No executavel instalado, recursos sao somente-leitura em ``sys._MEIPASS`` e
todos os dados mutaveis pertencem ao perfil do operador.  Em desenvolvimento,
mantemos a estrutura historica do repositorio para nao alterar o fluxo local.
"""

from __future__ import annotations

import os
import json
import re
import sys
from pathlib import Path


APP_NAME = "SmartEvents"
_SAFE_DATA_DIR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,79}$")


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
        directory_name = APP_NAME
        profile_path = resource_dir() / "build-profile.json"
        if profile_path.is_file():
            try:
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                configured = str(profile.get("runtime_data_dir") or "").strip()
            except (OSError, ValueError, TypeError) as exc:
                raise RuntimeError(f"Perfil de build invalido: {profile_path}") from exc
            if not _SAFE_DATA_DIR.fullmatch(configured) or configured in {".", ".."}:
                raise RuntimeError(f"Diretorio de dados inseguro no perfil: {configured!r}")
            directory_name = configured
        return Path(local_app_data) / directory_name
    return resource_dir() / "data"


def ensure_data_dir() -> Path:
    path = data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path
