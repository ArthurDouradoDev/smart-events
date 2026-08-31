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
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,119}$")


def is_safe_component(name: str) -> bool:
    """Nome de arquivo aceitavel dentro de um pacote ou pasta de dados.

    Mesma classe de caracteres ja exigida de ``runtime_data_dir``; rejeita
    separadores, ``.`` e ``..`` para que nenhum nome escape do diretorio alvo.
    """
    text = str(name)
    if text in {".", ".."} or "/" in text or "\\" in text:
        return False
    return _SAFE_COMPONENT.fullmatch(text) is not None


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


def server_data_dir() -> Path:
    """Pasta ``server_data`` efetivamente lida pelo Smart Events Central.

    Repete a regra de ``server.py``: no executavel os dados ficam sob a pasta
    persistente do operador; em desenvolvimento, ao lado do repositorio. A
    importacao de pacotes precisa gravar exatamente onde o aplicativo le.
    """
    root = data_dir() if getattr(sys, "frozen", False) else data_dir().parent
    return root / "server_data"


def distributions_dir() -> Path:
    """Saidas geradas pelo proprio aplicativo (pacotes e instaladores)."""
    return data_dir() / "distributions"


def distribution_jobs_dir() -> Path:
    """Registro persistente dos jobs de distribuicao, fora das pastas de saida."""
    return distributions_dir() / "jobs"


def import_reports_dir() -> Path:
    return data_dir() / "diagnostics" / "imports"


def import_backups_dir() -> Path:
    return data_dir() / "backups" / "imports"


def import_lock_path(server_data: Path) -> Path:
    """Lock exclusivo da pasta de dados, gravado ao lado dela."""
    return server_data.parent / f"{server_data.name}.import.lock"
