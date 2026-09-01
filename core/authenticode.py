"""Assinatura Authenticode de executaveis (Fase 4).

Cobre ``SmartEvents.exe`` (assinado diretamente apos o PyInstaller) e
``Setup.exe``/desinstalador (assinados pelo proprio Inno Setup via a diretiva
``SignTool``, que e a forma documentada pelo projeto -- ver
https://jrsoftware.org/ishelp/topic_setup_signtool.htm).

Este modulo nunca guarda nem loga a senha do certificado: ela sai de uma
variavel de ambiente e so existe na linha de comando passada ao ``signtool``,
igual a qualquer pipeline de assinatura de codigo no Windows. Nenhuma funcao
aqui escreve a senha em arquivo, manifesto ou excecao.

Sem configuracao (nenhuma variavel de ambiente definida), tudo aqui vira no-op
e a build resultante e honestamente marcada como ``signed: false`` -- a
ausencia de certificado nao bloqueia desenvolvimento, so impede chamar o
artefato de "producao".
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_SIGNTOOL_PATH = "SMARTEVENTS_SIGNTOOL_PATH"
ENV_PFX_PATH = "SMARTEVENTS_CODE_SIGN_PFX"
ENV_PFX_PASSWORD = "SMARTEVENTS_CODE_SIGN_PFX_PASSWORD"
ENV_TIMESTAMP_URL = "SMARTEVENTS_CODE_SIGN_TIMESTAMP_URL"

DEFAULT_TIMESTAMP_URL = "http://timestamp.digicert.com"
ISS_SIGN_TOOL_NAME = "smarteventscodesign"


class AuthenticodeError(Exception):
    """Falha ao assinar ou ao verificar um executavel. Nunca carrega a senha."""


@dataclass(frozen=True)
class AuthenticodeConfig:
    """Configuracao de assinatura desta maquina. Nao tem ``__repr__`` com a senha."""

    signtool_path: Path
    pfx_path: Path
    pfx_password: str
    timestamp_url: str

    def __repr__(self) -> str:  # nunca expor a senha em log/traceback
        return (
            f"AuthenticodeConfig(signtool_path={self.signtool_path!r}, "
            f"pfx_path={self.pfx_path!r}, pfx_password=<oculta>, "
            f"timestamp_url={self.timestamp_url!r})"
        )


@dataclass(frozen=True)
class AuthenticodeResult:
    ok: bool
    message: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "message": self.message}


def load_config() -> AuthenticodeConfig | None:
    """Le a configuracao do ambiente. ``None`` quando nao esta configurada (dev)."""
    signtool = os.environ.get(ENV_SIGNTOOL_PATH, "").strip()
    pfx = os.environ.get(ENV_PFX_PATH, "").strip()
    password = os.environ.get(ENV_PFX_PASSWORD, "")
    if not signtool or not pfx:
        return None
    signtool_path = Path(signtool)
    pfx_path = Path(pfx)
    if not signtool_path.is_file() or not pfx_path.is_file():
        return None
    return AuthenticodeConfig(
        signtool_path=signtool_path,
        pfx_path=pfx_path,
        pfx_password=password,
        timestamp_url=os.environ.get(ENV_TIMESTAMP_URL, "").strip() or DEFAULT_TIMESTAMP_URL,
    )


def is_configured() -> bool:
    return load_config() is not None


def _sign_args(config: AuthenticodeConfig, target: str) -> list[str]:
    return [
        str(config.signtool_path), "sign",
        "/f", str(config.pfx_path),
        "/p", config.pfx_password,
        "/fd", "sha256",
        "/tr", config.timestamp_url,
        "/td", "sha256",
        target,
    ]


def sign_file(path: Path | str, config: AuthenticodeConfig, *, timeout: float = 120.0) -> AuthenticodeResult:
    """Assina um executavel ja no disco (usado para ``SmartEvents.exe``).

    A senha entra apenas no argv do subprocesso, nunca em texto gravado; erros
    sao filtrados para nunca ecoar a senha de volta.
    """
    target = Path(path)
    if not target.is_file():
        raise AuthenticodeError(f"Arquivo ausente para assinatura: {target.name}")
    try:
        completed = subprocess.run(
            _sign_args(config, str(target)),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.SubprocessError as exc:
        raise AuthenticodeError(f"Falha ao executar o signtool para {target.name}.") from None
    if completed.returncode != 0:
        detail = _redact(
            ((completed.stdout or "") + (completed.stderr or "")).strip(), config.pfx_password
        )
        return AuthenticodeResult(False, f"signtool falhou ({completed.returncode}): {detail[-500:]}")
    return AuthenticodeResult(True, "Assinado com sucesso.")


def verify_file(path: Path | str, *, signtool_path: Path | str | None = None, timeout: float = 60.0) -> AuthenticodeResult:
    """Confere a assinatura ja aplicada (``signtool verify /pa /v``)."""
    target = Path(path)
    tool = Path(signtool_path) if signtool_path is not None else Path(
        os.environ.get(ENV_SIGNTOOL_PATH, "")
    )
    if not tool.is_file():
        return AuthenticodeResult(False, "signtool indisponivel para verificacao.")
    if not target.is_file():
        return AuthenticodeResult(False, f"Arquivo ausente: {target.name}")
    try:
        completed = subprocess.run(
            [str(tool), "verify", "/pa", "/v", str(target)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.SubprocessError:
        return AuthenticodeResult(False, "Falha ao executar o signtool para verificacao.")
    if completed.returncode == 0:
        return AuthenticodeResult(True, "Assinatura Authenticode valida.")
    detail = ((completed.stdout or "") + (completed.stderr or "")).strip()
    return AuthenticodeResult(False, f"Assinatura invalida ou ausente: {detail[-500:]}")


def _redact(text: str, password: str | None = None) -> str:
    """Remove qualquer ocorrencia literal da senha, por seguranca.

    A senha a ocultar vem do ``config`` em uso, nunca do ambiente atual:
    o processo que assina pode ter recebido uma ``AuthenticodeConfig``
    construida diretamente (nao via ``load_config()``), e o texto ainda
    precisa ser redigido nesse caso.
    """
    value = password if password is not None else os.environ.get(ENV_PFX_PASSWORD, "")
    if value:
        text = text.replace(value, "<oculta>")
    return text


def iss_sign_tool_definition(config: AuthenticodeConfig) -> str:
    """Valor ``/S<nome>=<comando>`` para o ISCC assinar Setup.exe/desinstalador.

    A diretiva ``[Setup] SignTool={#MySignTool}`` no ``.iss`` referencia este
    nome; o comando em si nunca e escrito no proprio arquivo ``.iss`` -- so
    trafega como argumento de processo do ISCC, a mesma exposicao que qualquer
    pipeline de assinatura aceita.
    """
    command = " ".join([
        f'"{config.signtool_path}"', "sign",
        "/f", f'"{config.pfx_path}"',
        "/p", config.pfx_password,
        "/fd", "sha256",
        "/tr", config.timestamp_url,
        "/td", "sha256",
        "$f",
    ])
    return f"/S{ISS_SIGN_TOOL_NAME}={command}"
