"""Assinatura e verificacao do payload do ``.sepack`` (Fase 4).

Usa o ``ISSigTool.exe`` do Inno Setup (ECDSA P-256, formato ``.issig``) em vez de
reimplementar criptografia: o mesmo binario ja e uma dependencia de build do
projeto (compilacao do Setup). A chave PRIVADA nunca mora no repositorio nem
neste modulo -- o caminho dela vem de variavel de ambiente, e nenhuma funcao
aqui grava, loga ou devolve o conteudo dela. A chave publica ativa (e as
revogadas) ficam versionadas em ``keys/sepack_signing/`` e SAO embarcadas no
executavel: quem importa o pacote precisa delas para verificar.

Verificacao trabalha sempre sobre bytes ja lidos em memoria (nunca reabre o
caminho original do pacote), o que fecha a janela de TOCTOU entre validar e
confiar no payload.

Todo texto e ASCII, como o resto do pacote de distribuicao.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_ISSIGTOOL_PATH = "SMARTEVENTS_ISSIGTOOL_PATH"
ENV_SIGNING_KEY_PATH = "SMARTEVENTS_SEPACK_SIGNING_KEY"

REGISTRY_NAME = "registry.json"
_KEY_ID_RE = re.compile(r"^[0-9a-f]{16,128}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_UNKNOWN_KEY = "unknown_key"
STATUS_RETIRED_KEY = "retired_key"
STATUS_INVALID = "invalid"
STATUS_TOOL_UNAVAILABLE = "tool_unavailable"


class SigningError(Exception):
    """Falha ao assinar ou ao ler a configuracao de assinatura."""


@dataclass(frozen=True)
class PublicKeyRecord:
    """Uma entrada do registro de chaves publicas aceitas (sem material secreto)."""

    key_id: str
    label: str
    active_from: str
    retired_at: str | None = None

    def is_active(self, *, at: datetime | None = None) -> bool:
        moment = at or datetime.now(timezone.utc)
        if self.retired_at:
            retired = datetime.strptime(self.retired_at, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if moment >= retired:
                return False
        if self.active_from:
            started = datetime.strptime(self.active_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if moment < started:
                return False
        return True

    def to_dict(self) -> dict:
        return {
            "key_id": self.key_id,
            "label": self.label,
            "active_from": self.active_from,
            "retired_at": self.retired_at,
        }


@dataclass(frozen=True)
class SignatureVerification:
    """Resultado tipado da verificacao, pronto para virar badge na interface."""

    status: str
    key_id: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def to_dict(self) -> dict:
        return {"status": self.status, "key_id": self.key_id, "message": self.message}


# ── Caminhos ─────────────────────────────────────────────────────────

def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_issigtool_path() -> Path:
    """Local do ``ISSigTool.exe``.

    No executavel instalado, ``core.paths.resource_dir()`` aponta para o bundle
    do PyInstaller, que carrega SOMENTE este binario (ver ``main.spec``), nunca a
    instalacao completa do Inno Setup. Em desenvolvimento e no build, usa o
    mesmo cache local que ``core.base_build.iscc_path()`` ja usa para o ISCC.
    """
    override = os.environ.get(ENV_ISSIGTOOL_PATH, "").strip()
    if override:
        return Path(override)
    from core import paths

    if getattr(sys, "frozen", False):
        return paths.resource_dir() / "issigtool" / "ISSigTool.exe"
    return _repo_root() / ".build-tools" / "InnoSetup7" / "ISSigTool.exe"


def is_issigtool_available(issigtool_path: Path | str | None = None) -> bool:
    tool = Path(issigtool_path) if issigtool_path is not None else default_issigtool_path()
    return tool.is_file()


def default_keys_dir() -> Path:
    """Onde ficam as chaves publicas aceitas (embarcadas no executavel)."""
    from core import paths

    return paths.resource_dir() / "keys" / "sepack_signing"


def public_key_path(key_id: str, keys_dir: Path | str | None = None) -> Path:
    base = Path(keys_dir) if keys_dir is not None else default_keys_dir()
    return base / f"{key_id}.iskeypub"


def registry_path(keys_dir: Path | str | None = None) -> Path:
    base = Path(keys_dir) if keys_dir is not None else default_keys_dir()
    return base / REGISTRY_NAME


def load_registry(keys_dir: Path | str | None = None) -> dict[str, PublicKeyRecord]:
    """Le o registro de chaves aceitas. Registro ausente ou vazio nao e erro."""
    path = registry_path(keys_dir)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SigningError(f"Registro de chaves de assinatura ilegivel: {exc}") from exc
    if not isinstance(raw, list):
        raise SigningError("Registro de chaves de assinatura deve ser uma lista.")
    result: dict[str, PublicKeyRecord] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        key_id = str(item.get("key_id") or "").strip()
        if not _KEY_ID_RE.fullmatch(key_id):
            continue
        active_from = str(item.get("active_from") or "").strip()
        retired_at = item.get("retired_at")
        retired_at = str(retired_at).strip() if retired_at else None
        for value in (active_from, retired_at):
            if value and not _DATE_RE.fullmatch(value):
                raise SigningError(f"Data invalida no registro de chaves: {value!r}.")
        result[key_id] = PublicKeyRecord(
            key_id=key_id,
            label=str(item.get("label") or key_id),
            active_from=active_from,
            retired_at=retired_at,
        )
    return result


# ── Execucao do ISSigTool ────────────────────────────────────────────

def _run_issigtool(
    args: list[str], *, issigtool_path: Path | str | None = None, timeout: float = 30.0
) -> subprocess.CompletedProcess:
    tool = Path(issigtool_path) if issigtool_path is not None else default_issigtool_path()
    if not tool.is_file():
        raise SigningError(f"ISSigTool.exe ausente: {tool}")
    return subprocess.run(
        [str(tool), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _parse_issig_text(text: str) -> dict[str, str]:
    """Parser minimo do formato ``chave valor`` do ``.issig``/``.iskeypub``."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or " " not in line:
            continue
        name, _, value = line.partition(" ")
        value = value.strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1]
        fields[name.strip()] = value
    return fields


def parse_signature(signature_bytes: bytes) -> dict[str, str]:
    """Extrai os campos de uma assinatura ``.issig`` (texto), sem validar nada."""
    try:
        text = signature_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SigningError(f"Assinatura em formato inesperado: {exc}") from exc
    return _parse_issig_text(text)


# ── Assinatura (uso do repositorio / build, nunca do executavel distribuido) ──

def sign_payload_bytes(
    payload_bytes: bytes,
    private_key_path: Path | str,
    *,
    issigtool_path: Path | str | None = None,
) -> bytes:
    """Assina bytes do ``payload.zip`` e devolve o conteudo do ``.issig``.

    Trabalha sobre uma copia temporaria propria; a chave privada e referenciada
    apenas pelo caminho (nunca lida por este modulo) e seu caminho nunca entra
    em mensagem de log ou excecao.
    """
    key_path = Path(private_key_path)
    if not key_path.is_file():
        raise SigningError("Chave privada de assinatura nao encontrada.")
    with tempfile.TemporaryDirectory(prefix="sepack-sign-") as tmp:
        payload_path = Path(tmp) / "payload.zip"
        payload_path.write_bytes(payload_bytes)
        completed = _run_issigtool(
            ["--key-file=" + str(key_path), "--allow-overwrite", "--quiet", "sign", str(payload_path)],
            issigtool_path=issigtool_path,
        )
        if completed.returncode != 0:
            # A saida do ISSigTool ecoa o nome do arquivo que recebemos (o
            # payload temporario, nao a chave), entao e segura para diagnostico.
            detail = (completed.stdout or "").strip() or (completed.stderr or "").strip()
            raise SigningError(f"Falha ao assinar o payload (codigo {completed.returncode}): {detail}")
        signature_path = payload_path.with_name(payload_path.name + ".issig")
        if not signature_path.is_file():
            raise SigningError("ISSigTool nao gerou o arquivo de assinatura.")
        return signature_path.read_bytes()


def signing_key_configured() -> Path | None:
    """Caminho da chave privada de release, se configurada nesta maquina."""
    override = os.environ.get(ENV_SIGNING_KEY_PATH, "").strip()
    if not override:
        return None
    path = Path(override)
    return path if path.is_file() else None


# ── Verificacao (roda dentro do executavel distribuido) ─────────────

def verify_payload_signature(
    payload_bytes: bytes,
    signature_bytes: bytes,
    *,
    keys_dir: Path | str | None = None,
    issigtool_path: Path | str | None = None,
    at: datetime | None = None,
) -> SignatureVerification:
    """Verifica a assinatura sobre bytes ja carregados em memoria (sem TOCTOU).

    So confia em uma chave que estiver no registro local E ativa na data de
    verificacao. Uma chave desconhecida ou revogada e recusada mesmo que a
    assinatura matematica seja valida -- rotacao de chave e decisao do
    registro, nao do proprio pacote.
    """
    try:
        fields = parse_signature(signature_bytes)
    except SigningError as exc:
        return SignatureVerification(STATUS_INVALID, "", str(exc))
    key_id = fields.get("key-id", "")
    if not _KEY_ID_RE.fullmatch(key_id or ""):
        return SignatureVerification(STATUS_INVALID, key_id, "Assinatura sem key-id valido.")

    try:
        registry = load_registry(keys_dir)
    except SigningError as exc:
        return SignatureVerification(STATUS_INVALID, key_id, str(exc))
    record = registry.get(key_id)
    if record is None:
        return SignatureVerification(STATUS_UNKNOWN_KEY, key_id, "Chave de assinatura desconhecida.")
    if not record.is_active(at=at):
        return SignatureVerification(STATUS_RETIRED_KEY, key_id, "Chave de assinatura revogada.")

    key_file = public_key_path(key_id, keys_dir)
    if not key_file.is_file():
        return SignatureVerification(
            STATUS_UNKNOWN_KEY, key_id, "Chave publica ausente nesta instalacao."
        )
    if not is_issigtool_available(issigtool_path):
        return SignatureVerification(
            STATUS_TOOL_UNAVAILABLE, key_id, "Verificador de assinatura indisponivel."
        )

    try:
        with tempfile.TemporaryDirectory(prefix="sepack-verify-") as tmp:
            tmp_dir = Path(tmp)
            # Nomeado exatamente como no envelope: o proprio .issig registra o
            # nome do arquivo assinado e o verificador confere a correspondencia.
            payload_path = tmp_dir / "payload.zip"
            payload_path.write_bytes(payload_bytes)
            (tmp_dir / "payload.zip.issig").write_bytes(signature_bytes)
            completed = _run_issigtool(
                ["--key-file=" + str(key_file), "verify", str(payload_path)],
                issigtool_path=issigtool_path,
            )
    except SigningError as exc:
        return SignatureVerification(STATUS_TOOL_UNAVAILABLE, key_id, str(exc))

    if completed.returncode == 0:
        return SignatureVerification(STATUS_OK, key_id, "Assinatura valida.")
    output = ((completed.stdout or "") + (completed.stderr or "")).upper()
    if "MISSINGSIGFILE" in output:
        return SignatureVerification(STATUS_MISSING, key_id, "Assinatura ausente.")
    return SignatureVerification(
        STATUS_INVALID, key_id, "Assinatura invalida: o conteudo ou a chave nao conferem."
    )
