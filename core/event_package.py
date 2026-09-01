"""Formato ``.sepack``: geracao, validacao e importacao incremental de eventos.

Um pacote e um ZIP com ``envelope.json`` + ``payload.zip``. O envelope ja nasce
preparado para a assinatura da Fase 4; o payload carrega o manifesto com o hash
de cada arquivo, os eventos selecionados e os cadastros de que eles dependem.

A importacao e sempre incremental:

- evento ausente e adicionado; evento local divergente e preservado por padrao;
- eventos que nao aparecem no pacote nunca sao apagados;
- cadastros compartilhados (cliente, regional, VIP e logo) sao conciliados campo
  a campo pelo ``id``, nunca duplicados;
- uma falha de validacao impede qualquer gravacao.

Todo texto deste modulo e ASCII: as mesmas mensagens vao para o console do
operador (cp1252 no Windows) e para o dialogo do instalador.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import tempfile
import time
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core import package_signing as psig
from core import paths
from core.seed import validate_collection

logger = logging.getLogger(__name__)


# ── Contrato do formato ──────────────────────────────────────────────

SCHEMA_VERSION = 1
PACKAGE_SUFFIX = ".sepack"
ENVELOPE_NAME = "envelope.json"
PAYLOAD_NAME = "payload.zip"
SIGNATURE_NAME = "payload.zip.issig"
MANIFEST_NAME = "manifest.json"

PAYLOAD_DIRS = ("events", "clientes", "vips", "logos")
_KIND_BY_DIR = {"events": "event", "clientes": "cliente", "vips": "vip", "logos": "logo"}
_DIR_BY_KIND = {kind: directory for directory, kind in _KIND_BY_DIR.items()}
_RECORD_DIRS = ("events", "clientes", "vips")
_KIND_LABEL = {"event": "Evento", "cliente": "Cliente", "vip": "VIP", "logo": "Logo"}

CONFLICT_POLICIES = ("preserve", "replace")
VIP_POLICIES = ("auto", "explicit", "none")

# Fase 4: quem valida/importa decide a postura de assinatura, nunca o proprio
# envelope (ele e texto plano, fora do que a assinatura cobre). "development" e
# o padrao (aceita pacote nao assinado com aviso visivel); "production" exige
# assinatura valida de uma chave ativa. Uma assinatura PRESENTE e invalida e
# sempre recusada, nas duas posturas.
SIGNATURE_POLICY_DEVELOPMENT = "development"
SIGNATURE_POLICY_PRODUCTION = "production"
SIGNATURE_POLICIES = (SIGNATURE_POLICY_DEVELOPMENT, SIGNATURE_POLICY_PRODUCTION)
LOGO_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"})

# Limites de defesa contra ZIP bomb e contra pacotes que nao cabem em memoria.
# O pacote inteiro e lido de uma vez e validado sobre essa copia imutavel, o que
# tambem evita troca do arquivo entre a validacao e o uso.
MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_ENVELOPE_BYTES = 64 * 1024
MAX_PAYLOAD_ENTRIES = 1000
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024

# Timestamp fixo e ordem alfabetica mantem o payload reproduzivel a partir da
# mesma origem (o ZIP nao carrega a data local de quem gerou).
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

# Nomes de campo que denunciam segredo ou sessao dentro de um registro.
_FORBIDDEN_FIELDS = frozenset({
    "password", "passwd", "senha", "token", "cookie", "cookies", "session",
    "sessao", "credential", "credentials", "secret", "authorization",
    "roarand", "apikey",
})

_LOCK_MAX_AGE_SECONDS = 3600


# ── Estruturas ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class PackageError:
    """Erro estruturado, pronto para a interface e para o relatorio."""

    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {"code": self.code, "path": self.path, "message": self.message}


class PackageBuildError(Exception):
    """Geracao recusada; ``errors`` traz os problemas estruturados."""

    def __init__(self, errors: list[PackageError]):
        self.errors = list(errors)
        super().__init__("; ".join(item.message for item in self.errors) or "pacote invalido")


class ImportLockError(Exception):
    """Outra importacao esta em andamento na mesma pasta de dados."""


@dataclass(frozen=True)
class PackageManifest:
    schema_version: int
    package_id: str
    name: str
    created_at_utc: str
    created_by_app_version: str
    minimum_app_version: str
    event_ids: tuple[str, ...]
    clients: tuple[str, ...]
    files: dict[str, str]
    contains_credentials: bool

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "package_id": self.package_id,
            "name": self.name,
            "created_at_utc": self.created_at_utc,
            "created_by_app_version": self.created_by_app_version,
            "minimum_app_version": self.minimum_app_version,
            "event_ids": list(self.event_ids),
            "clients": list(self.clients),
            "files": dict(self.files),
            "contains_credentials": self.contains_credentials,
        }


@dataclass
class PackagePreview:
    """Tudo que entraria no pacote, sem criar artefato algum."""

    name: str
    source_dir: Path
    events: list[dict] = field(default_factory=list)
    clientes: list[dict] = field(default_factory=list)
    vips: list[dict] = field(default_factory=list)
    logos: dict[str, Path] = field(default_factory=dict)
    explicit_vip_ids: list[str] = field(default_factory=list)
    fallback_vip_ids: list[str] = field(default_factory=list)
    errors: list[PackageError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def clients(self) -> list[str]:
        return sorted({str(item.get("name") or item.get("id") or "") for item in self.clientes})

    def counts(self) -> dict:
        sites = sum(len(event.get("sites") or []) for event in self.events)
        cells = sum(
            len(site.get("cells") or [])
            for event in self.events
            for site in event.get("sites") or []
        )
        clusters = sum(len(event.get("clusters") or []) for event in self.events)
        pm_tasks = sum(
            len((event.get("integration") or {}).get("pm_tasks") or []) for event in self.events
        )
        regions = {
            str((event.get("oss") or {}).get("region") or "").strip().upper()
            for event in self.events
        }
        regions.discard("")
        return {
            "events": len(self.events),
            "clientes": len(self.clientes),
            "vips": len(self.vips),
            "logos": len(self.logos),
            "regions": len(regions),
            "sites": sites,
            "cells": cells,
            "clusters": clusters,
            "pm_tasks": pm_tasks,
        }

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "name": self.name,
            "source_dir": str(self.source_dir),
            "event_ids": [str(event.get("id") or "") for event in self.events],
            "clients": self.clients,
            "vip_ids": [str(vip.get("id") or "") for vip in self.vips],
            "explicit_vip_ids": list(self.explicit_vip_ids),
            "fallback_vip_ids": list(self.fallback_vip_ids),
            "logos": sorted(self.logos),
            "counts": self.counts(),
            "excluded": [
                "credentials.json",
                "session.json e cookies",
                "bancos smart_events*.db",
                "logs e diagnosticos locais",
                "resultados de coleta, alertas e relatorios",
            ],
            "errors": [item.to_dict() for item in self.errors],
            "warnings": list(self.warnings),
        }


@dataclass
class PackageInspection:
    """Resultado da validacao completa de um ``.sepack``."""

    path: Path
    envelope: dict | None = None
    manifest: PackageManifest | None = None
    records: dict[str, dict[str, dict]] = field(default_factory=dict)
    logos: dict[str, bytes] = field(default_factory=dict)
    errors: list[PackageError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        manifest = self.manifest.to_dict() if self.manifest else None
        return {
            "ok": self.ok,
            "path": str(self.path),
            "envelope": self.envelope,
            "manifest": manifest,
            "counts": {
                "events": len(self.records.get("event", {})),
                "clientes": len(self.records.get("cliente", {})),
                "vips": len(self.records.get("vip", {})),
                "logos": len(self.logos),
            },
            "signed": bool(self.envelope and self.envelope.get("signed")),
            "signature_status": (self.envelope or {}).get("signature_status", ""),
            "signature_key_id": (self.envelope or {}).get("signature_key_id", ""),
            "errors": [item.to_dict() for item in self.errors],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ImportConflict:
    kind: str
    record_id: str
    path: str
    field_name: str
    resolution: str  # preserved | replaced | skipped
    local: str = ""
    incoming: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "record_id": self.record_id,
            "path": self.path,
            "field": self.field_name,
            "resolution": self.resolution,
            "local": self.local,
            "incoming": self.incoming,
        }


@dataclass
class RecordReconciliation:
    """O que a conciliacao campo a campo fez com um cadastro compartilhado."""

    kind: str
    record_id: str
    record_name: str = ""
    added_fields: list[str] = field(default_factory=list)
    updated_fields: list[str] = field(default_factory=list)
    preserved_fields: list[str] = field(default_factory=list)
    conflicts: list[ImportConflict] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added_fields or self.updated_fields)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "record_id": self.record_id,
            "record_name": self.record_name,
            "added_fields": list(self.added_fields),
            "updated_fields": list(self.updated_fields),
            "preserved_fields": list(self.preserved_fields),
            "conflicts": [item.to_dict() for item in self.conflicts],
        }


@dataclass
class ImportAction:
    kind: str
    record_id: str
    path: str
    action: str  # add | unchanged | reconcile | preserve | replace | skip
    reason: str = ""
    reconciliation: RecordReconciliation | None = None
    destination: Path | None = None
    data: bytes | None = None

    def to_dict(self) -> dict:
        value = {
            "kind": self.kind,
            "record_id": self.record_id,
            "path": self.path,
            "action": self.action,
        }
        if self.reason:
            value["reason"] = self.reason
        if self.reconciliation is not None:
            value["reconciliation"] = self.reconciliation.to_dict()
        return value


@dataclass(frozen=True)
class ImportTarget:
    """Pastas usadas pela importacao, sempre derivadas de uma unica raiz."""

    server_data: Path
    reports: Path
    backups: Path

    @classmethod
    def resolve(cls, root: Path | str | None = None) -> "ImportTarget":
        if root is None:
            return cls(
                paths.server_data_dir(),
                paths.import_reports_dir(),
                paths.import_backups_dir(),
            )
        base = Path(root).expanduser().resolve()
        return cls(
            base / "server_data",
            base / "diagnostics" / "imports",
            base / "backups" / "imports",
        )


@dataclass
class ImportPlan:
    package_id: str
    name: str
    target: ImportTarget
    conflict_policy: str
    actions: list[ImportAction] = field(default_factory=list)
    conflicts: list[ImportConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[PackageError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "package_id": self.package_id,
            "name": self.name,
            "server_data": str(self.target.server_data),
            "conflict_policy": self.conflict_policy,
            "actions": [item.to_dict() for item in self.actions],
            "conflicts": [item.to_dict() for item in self.conflicts],
            "warnings": list(self.warnings),
            "errors": [item.to_dict() for item in self.errors],
        }


@dataclass
class ImportResult:
    ok: bool
    package_id: str = ""
    name: str = ""
    added: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    reconciled: list[str] = field(default_factory=list)
    preserved: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    conflicts: list[ImportConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[PackageError] = field(default_factory=list)
    actions: list[ImportAction] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)
    report_path: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.added or self.reconciled or self.replaced)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "package_id": self.package_id,
            "name": self.name,
            "added": list(self.added),
            "unchanged": list(self.unchanged),
            "reconciled": list(self.reconciled),
            "preserved": list(self.preserved),
            "replaced": list(self.replaced),
            "conflicts": [item.to_dict() for item in self.conflicts],
            "warnings": list(self.warnings),
            "errors": [item.to_dict() for item in self.errors],
            "actions": [item.to_dict() for item in self.actions],
            "backups": list(self.backups),
            "report_path": self.report_path,
        }


# ── Utilidades ───────────────────────────────────────────────────────

def app_version() -> str:
    try:
        value = (paths.resource_dir() / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    return value or "0.0.0"


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(value or ""))
    return tuple(int(part) for part in parts[:4]) or (0,)


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of(path: Path | str) -> str:
    """SHA-256 de um artefato, para manifesto e conferencia pos-transferencia."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _slug(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-") or "eventos"


def _short(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= 120 else text[:117] + "..."


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    return False


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular}" if count == 1 else f"{count} {plural}"


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0o644 << 16
            archive.writestr(info, entries[name], compresslevel=9)
    return buffer.getvalue()


def _write_atomic(path: Path, data: bytes) -> None:
    """Grava por arquivo temporario + ``os.replace`` na mesma pasta."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


class _ImportLock:
    """Lock exclusivo por arquivo, com limpeza de lock orfao antigo."""

    def __init__(self, path: Path):
        self.path = path
        self._handle: int | None = None

    def __enter__(self) -> "_ImportLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            age = time.time() - self.path.stat().st_mtime if self.path.exists() else 0
            if age <= _LOCK_MAX_AGE_SECONDS:
                raise ImportLockError(
                    f"Outra importacao esta em andamento (lock: {self.path})."
                ) from None
            logger.warning("Removendo lock de importacao orfao: %s", self.path)
            self.path.unlink(missing_ok=True)
            self._handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(self._handle, str(os.getpid()).encode("ascii"))
        return self

    def __exit__(self, *_exc) -> None:
        if self._handle is not None:
            os.close(self._handle)
            self._handle = None
        self.path.unlink(missing_ok=True)


def _scan_secrets(value, location: str) -> list[PackageError]:
    """Recusa qualquer campo que carregue segredo, sessao ou credencial."""
    errors: list[PackageError] = []
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, item in current.items():
                normalized = re.sub(r"[^a-z]", "", str(key).casefold())
                if normalized in _FORBIDDEN_FIELDS:
                    errors.append(PackageError(
                        "secret.forbidden_field",
                        location,
                        f"Campo proibido em {location}: {key}.",
                    ))
                stack.append(item)
        elif isinstance(current, list):
            stack.extend(current)
    return errors


# ── Leitura da origem ────────────────────────────────────────────────

def _read_records(directory: Path) -> tuple[dict[str, dict], list[PackageError]]:
    records: dict[str, dict] = {}
    errors: list[PackageError] = []
    if not directory.is_dir():
        return records, errors
    for file_path in sorted(directory.glob("*.json")):
        try:
            value = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(PackageError("source.invalid_json", str(file_path), f"JSON invalido: {exc}"))
            continue
        if not isinstance(value, dict):
            errors.append(PackageError(
                "source.invalid_json", str(file_path), "O arquivo deve conter um objeto JSON."
            ))
            continue
        record_id = str(value.get("id") or file_path.stem).strip()
        if not record_id:
            errors.append(PackageError("source.missing_id", str(file_path), "Registro sem id."))
            continue
        if record_id in records:
            errors.append(PackageError(
                "source.duplicated_id", str(file_path), f"Dois arquivos declaram o id {record_id}."
            ))
            continue
        # Cadastro legado sem ``id`` no corpo: o nome do arquivo passa a ser o id,
        # para que o pacote seja autodescritivo e o arquivo case com o registro.
        value.setdefault("id", record_id)
        records[record_id] = value
    return records, errors


def _explicit_vip_ids(event: dict, vips: dict[str, dict]) -> set[str]:
    """Associacoes declaradas, nos dois formatos usados pelo cadastro."""
    result: set[str] = set()
    for item in event.get("vips") or []:
        if isinstance(item, str):
            result.add(item.strip())
        elif isinstance(item, dict):
            value = item.get("vip_id") or item.get("id")
            if value:
                result.add(str(value).strip())
    event_id = str(event.get("id") or "").strip()
    for vip_id, vip in vips.items():
        for association in vip.get("event_vips") or []:
            if isinstance(association, dict):
                if str(association.get("event_id") or "").strip() == event_id:
                    result.add(vip_id)
    result.discard("")
    return result


def _fallback_vip_ids(event: dict, vips: dict[str, dict]) -> set[str]:
    """Compatibilidade legada: cliente + regional, nunca so o cliente."""
    oss = event.get("oss") or {}
    client = str(oss.get("cliente") or "").strip().casefold()
    region = str(oss.get("region") or "").strip().casefold()
    if not client or not region:
        return set()
    result = set()
    for vip_id, vip in vips.items():
        if str(vip.get("cliente") or "").strip().casefold() != client:
            continue
        if str(vip.get("oss") or "").strip().casefold() != region:
            continue
        result.add(vip_id)
    return result


def preview_package(
    source_dir: Path | str | None = None,
    event_ids: list[str] | tuple[str, ...] = (),
    *,
    name: str | None = None,
    vip_policy: str = "auto",
    vip_ids: list[str] | set[str] | None = None,
) -> PackagePreview:
    """Resolve tudo que entraria no pacote, sem criar artefato."""
    source = Path(source_dir) if source_dir is not None else paths.server_data_dir()
    requested = [str(value).strip() for value in event_ids if str(value).strip()]
    preview = PackagePreview(name=str(name or "").strip(), source_dir=source)

    if vip_policy not in VIP_POLICIES:
        preview.errors.append(PackageError(
            "argument.invalid", "", f"Politica de VIP desconhecida: {vip_policy}."
        ))
        return preview
    if not requested:
        preview.errors.append(PackageError("argument.invalid", "", "Nenhum evento foi selecionado."))
        return preview

    source_events, errors = _read_records(source / "events")
    preview.errors.extend(errors)
    source_clientes, errors = _read_records(source / "clientes")
    preview.errors.extend(errors)
    source_vips, errors = _read_records(source / "vips")
    preview.errors.extend(errors)

    selected_events: list[dict] = []
    for event_id in requested:
        event = source_events.get(event_id)
        if event is None:
            preview.errors.append(PackageError(
                "event.not_found", f"events/{event_id}.json", f"Evento nao encontrado: {event_id}."
            ))
            continue
        selected_events.append(event)
    preview.events = selected_events
    if not preview.name:
        first = str(selected_events[0].get("name") or "").strip() if selected_events else ""
        preview.name = first or "eventos"

    # Clientes e regionais de que os eventos dependem.
    clientes: dict[str, dict] = {}
    for event in selected_events:
        oss = event.get("oss") or {}
        client_name = str(oss.get("cliente") or "").strip()
        client = None
        for candidate_id, candidate in source_clientes.items():
            identity = {
                str(candidate.get("name") or "").strip().casefold(),
                str(candidate_id).strip().casefold(),
            }
            identity.discard("")
            if client_name.casefold() in identity:
                client = (candidate_id, candidate)
                break
        if client is None:
            preview.errors.append(PackageError(
                "reference.client_missing",
                f"events/{event.get('id')}.json",
                f"Cliente nao cadastrado para o evento {event.get('id')}: {client_name or 'N/D'}.",
            ))
            continue
        clientes[client[0]] = client[1]
    preview.clientes = [clientes[key] for key in sorted(clientes)]

    # Logos referenciados pelos clientes incluidos.
    for client in preview.clientes:
        logo_name = str(client.get("logo") or "").strip()
        if not logo_name:
            continue
        if not paths.is_safe_component(logo_name):
            preview.errors.append(PackageError(
                "logo.unsafe_name",
                f"clientes/{client.get('id')}.json",
                f"Nome de logo inseguro: {logo_name}.",
            ))
            continue
        logo_path = source / "logos" / logo_name
        if not logo_path.is_file():
            preview.errors.append(PackageError(
                "reference.logo_missing",
                f"logos/{logo_name}",
                f"Logo do cliente {client.get('id')} nao encontrado: {logo_name}.",
            ))
            continue
        preview.logos[logo_name] = logo_path

    # VIPs: associacao explicita vence; fallback legado sempre visivel.
    explicit: set[str] = set()
    fallback: set[str] = set()
    if vip_policy != "none":
        for event in selected_events:
            event_explicit = _explicit_vip_ids(event, source_vips)
            missing = sorted(value for value in event_explicit if value not in source_vips)
            for vip_id in missing:
                preview.errors.append(PackageError(
                    "reference.vip_missing",
                    f"events/{event.get('id')}.json",
                    f"VIP associado ao evento {event.get('id')} nao existe: {vip_id}.",
                ))
            event_explicit = {value for value in event_explicit if value in source_vips}
            if event_explicit:
                explicit |= event_explicit
            elif vip_policy == "auto":
                fallback |= _fallback_vip_ids(event, source_vips)
    fallback -= explicit

    selected_vip_ids = explicit | fallback
    if vip_ids is not None:
        keep = {str(value).strip() for value in vip_ids}
        removed_explicit = sorted(explicit - keep)
        if removed_explicit:
            preview.warnings.append(
                "VIPs com associacao explicita foram desmarcados: " + ", ".join(removed_explicit)
                + ". O evento perdera essa associacao no destino."
            )
        selected_vip_ids &= keep
    preview.explicit_vip_ids = sorted(explicit & selected_vip_ids)
    preview.fallback_vip_ids = sorted(fallback & selected_vip_ids)
    preview.vips = [source_vips[key] for key in sorted(selected_vip_ids)]

    if preview.fallback_vip_ids:
        preview.warnings.append(
            "Fallback de compatibilidade: "
            + _plural(len(preview.fallback_vip_ids), "VIP foi incluido", "VIPs foram incluidos")
            + " por cliente + regional, sem associacao explicita ("
            + ", ".join(preview.fallback_vip_ids)
            + "). Revise antes de gerar."
        )

    # O id vira nome de arquivo dentro do payload; um id fora do contrato geraria
    # um pacote que o proprio validador recusaria depois.
    for kind, records in (
        ("event", preview.events), ("cliente", preview.clientes), ("vip", preview.vips)
    ):
        directory = _DIR_BY_KIND[kind]
        for record in records:
            record_id = str(record.get("id") or "")
            if not paths.is_safe_component(f"{record_id}.json"):
                preview.errors.append(PackageError(
                    "record.unsafe_id",
                    f"{directory}/{record_id}.json",
                    f"Id fora do contrato do pacote: {record_id!r}.",
                ))

    # Nenhum segredo, sessao ou historico pode entrar no conjunto selecionado.
    for event in preview.events:
        preview.errors.extend(_scan_secrets(event, f"events/{event.get('id')}.json"))
    for client in preview.clientes:
        preview.errors.extend(_scan_secrets(client, f"clientes/{client.get('id')}.json"))
    for vip in preview.vips:
        preview.errors.extend(_scan_secrets(vip, f"vips/{vip.get('id')}.json"))

    collection_errors = validate_collection(
        [(Path(f"{event.get('id')}.json"), event) for event in preview.events],
        [(Path(f"{client.get('id')}.json"), client) for client in preview.clientes],
        [(Path(f"{vip.get('id')}.json"), vip) for vip in preview.vips],
        expected_event_ids=[str(event.get("id") or "") for event in preview.events],
    )
    preview.errors.extend(
        PackageError("collection.invalid", "", message) for message in collection_errors
    )
    return preview


def suggested_filename(preview: PackagePreview, created_at: datetime | None = None) -> str:
    moment = created_at or datetime.now(timezone.utc)
    return f"SmartEvents_Eventos_{_slug(preview.name)}_{moment.strftime('%Y%m%d')}{PACKAGE_SUFFIX}"


# ── Geracao ──────────────────────────────────────────────────────────

def build_package(
    preview: PackagePreview,
    destination: Path | str,
    *,
    package_id: str | None = None,
    created_at: datetime | None = None,
    force: bool = False,
    signing_key_path: Path | str | None = None,
    issigtool_path: Path | str | None = None,
) -> PackageManifest:
    """Monta o ``.sepack`` em staging e o publica por substituicao atomica.

    ``package_id`` e ``created_at`` sao injetaveis para que o mesmo conjunto de
    entrada produza bytes identicos; sem eles, cada geracao recebe um id novo.

    ``signing_key_path`` assina o ``payload.zip`` com o ISSigTool (Fase 4). Sem
    ela, o pacote sai exatamente como nas Fases 1-3: sem assinatura, marcado
    como tal no envelope -- nao ha o que fingir sem uma chave de release.
    """
    if not preview.ok:
        raise PackageBuildError(preview.errors)

    destination = Path(destination)
    if destination.exists() and not force:
        raise PackageBuildError([PackageError(
            "output.exists", str(destination), f"O arquivo ja existe: {destination}."
        )])

    moment = created_at or datetime.now(timezone.utc)
    entries: dict[str, bytes] = {}
    for event in preview.events:
        entries[f"events/{event['id']}.json"] = _json_bytes(event)
    for client in preview.clientes:
        entries[f"clientes/{client['id']}.json"] = _json_bytes(client)
    for vip in preview.vips:
        entries[f"vips/{vip['id']}.json"] = _json_bytes(vip)
    for logo_name, logo_path in preview.logos.items():
        entries[f"logos/{logo_name}"] = logo_path.read_bytes()

    # O manifesto e o ultimo a ser calculado: ele depende do hash de todo o resto.
    manifest = PackageManifest(
        schema_version=SCHEMA_VERSION,
        package_id=str(package_id or uuid.uuid4()),
        name=preview.name,
        created_at_utc=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        created_by_app_version=app_version(),
        minimum_app_version="1.0.0",
        event_ids=tuple(str(event["id"]) for event in preview.events),
        clients=tuple(preview.clients),
        files={name: _sha256(data) for name, data in sorted(entries.items())},
        contains_credentials=False,
    )
    entries[MANIFEST_NAME] = _json_bytes(manifest.to_dict())

    payload = _zip_bytes(entries)
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "payload": PAYLOAD_NAME,
        "signature": SIGNATURE_NAME,
        "signature_required": bool(signing_key_path),
        "payload_sha256": _sha256(payload),
    }
    package_entries = {
        ENVELOPE_NAME: _json_bytes(envelope),
        PAYLOAD_NAME: payload,
    }
    if signing_key_path is not None:
        package_entries[SIGNATURE_NAME] = psig.sign_payload_bytes(
            payload, signing_key_path, issigtool_path=issigtool_path
        )
    package = _zip_bytes(package_entries)

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".sepack-", dir=destination.parent))
    try:
        staged = staging / destination.name
        staged.write_bytes(package)
        os.replace(staged, destination)
    finally:
        for leftover in staging.iterdir():
            leftover.unlink(missing_ok=True)
        staging.rmdir()
    logger.info("Pacote gerado: %s (%d bytes)", destination, len(package))
    return manifest


# ── Validacao ────────────────────────────────────────────────────────

def _is_safe_entry(name: str) -> bool:
    if name == MANIFEST_NAME:
        return True
    if "\\" in name or name.startswith("/") or ":" in name:
        return False
    parts = name.split("/")
    if len(parts) != 2:
        return False
    directory, filename = parts
    if directory not in PAYLOAD_DIRS or not paths.is_safe_component(filename):
        return False
    suffix = Path(filename).suffix.casefold()
    if directory == "logos":
        return suffix in LOGO_SUFFIXES
    return suffix == ".json"


def _is_forbidden_entry(name: str) -> bool:
    base = name.rsplit("/", 1)[-1].casefold()
    if base in {"credentials.json", "settings.json", "seed-manifest.json"}:
        return True
    if base.startswith("session") and base.endswith(".json"):
        return True
    return base.endswith((".db", ".db-wal", ".db-shm", ".sqlite3", ".log"))


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return bool((info.external_attr >> 16) & 0o170000 == 0o120000)


def _parse_json(raw: bytes, location: str, errors: list[PackageError]):
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        errors.append(PackageError("json.invalid", location, f"JSON invalido em {location}: {exc}."))
        return None
    if not isinstance(value, dict):
        errors.append(PackageError(
            "json.invalid", location, f"{location} deve conter um objeto JSON."
        ))
        return None
    return value


def _read_payload_entries(payload: bytes) -> tuple[dict[str, bytes], list[PackageError]]:
    errors: list[PackageError] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        return {}, [PackageError("payload.invalid_zip", PAYLOAD_NAME, f"payload.zip ilegivel: {exc}.")]

    entries: dict[str, bytes] = {}
    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        duplicated = sorted({name for name in names if names.count(name) > 1})
        if duplicated:
            errors.append(PackageError(
                "entry.duplicated",
                ", ".join(duplicated),
                "Entradas duplicadas no payload: " + ", ".join(duplicated) + ".",
            ))
            return {}, errors
        if len(infos) > MAX_PAYLOAD_ENTRIES:
            errors.append(PackageError(
                "payload.too_many_entries",
                PAYLOAD_NAME,
                f"Payload com {len(infos)} entradas; o limite e {MAX_PAYLOAD_ENTRIES}.",
            ))
            return {}, errors

        total = 0
        for info in infos:
            name = info.filename
            if info.is_dir():
                errors.append(PackageError(
                    "entry.unsafe_path", name, f"Entrada de diretorio nao e aceita: {name}."
                ))
                continue
            if _is_symlink(info):
                errors.append(PackageError(
                    "entry.symlink", name, f"Link simbolico nao e aceito: {name}."
                ))
                continue
            if not _is_safe_entry(name):
                errors.append(PackageError(
                    "entry.unsafe_path", name, f"Caminho fora do contrato do pacote: {name}."
                ))
                continue
            if _is_forbidden_entry(name):
                errors.append(PackageError(
                    "entry.forbidden",
                    name,
                    f"Segredo, banco ou log nao pode viajar no pacote: {name}.",
                ))
                continue
            if info.file_size > MAX_FILE_BYTES:
                errors.append(PackageError(
                    "entry.too_large",
                    name,
                    f"Arquivo maior que o limite de {MAX_FILE_BYTES} bytes: {name}.",
                ))
                continue
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                errors.append(PackageError(
                    "payload.too_large",
                    PAYLOAD_NAME,
                    f"Payload descompactado excede {MAX_UNCOMPRESSED_BYTES} bytes.",
                ))
                return {}, errors
            try:
                entries[name] = archive.read(name)
            except (zipfile.BadZipFile, OSError) as exc:
                errors.append(PackageError(
                    "entry.unreadable", name, f"Entrada ilegivel: {name} ({exc})."
                ))
    return entries, errors


def _manifest_from_dict(value: dict, errors: list[PackageError]) -> PackageManifest | None:
    try:
        schema_version = int(value.get("schema_version"))
    except (TypeError, ValueError):
        errors.append(PackageError(
            "manifest.invalid", MANIFEST_NAME, "manifest.json sem schema_version."
        ))
        return None
    if schema_version > SCHEMA_VERSION:
        errors.append(PackageError(
            "schema.unsupported",
            MANIFEST_NAME,
            f"Pacote no schema {schema_version}; esta versao suporta ate {SCHEMA_VERSION}.",
        ))
        return None

    minimum = str(value.get("minimum_app_version") or "0.0.0")
    if _version_tuple(minimum) > _version_tuple(app_version()):
        errors.append(PackageError(
            "app.too_old",
            MANIFEST_NAME,
            f"O pacote exige o SmartEvents {minimum}; esta instalacao e {app_version()}.",
        ))
        return None

    files = value.get("files")
    if not isinstance(files, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in files.items()
    ):
        errors.append(PackageError("manifest.invalid", MANIFEST_NAME, "manifest.json sem files."))
        return None
    package_id = str(value.get("package_id") or "").strip()
    if not package_id or not paths.is_safe_component(package_id):
        errors.append(PackageError(
            "manifest.invalid", MANIFEST_NAME, "manifest.json sem package_id utilizavel."
        ))
        return None
    if bool(value.get("contains_credentials")):
        errors.append(PackageError(
            "manifest.invalid", MANIFEST_NAME, "O manifesto declara credenciais no pacote."
        ))
        return None

    return PackageManifest(
        schema_version=schema_version,
        package_id=package_id,
        name=str(value.get("name") or package_id),
        created_at_utc=str(value.get("created_at_utc") or ""),
        created_by_app_version=str(value.get("created_by_app_version") or ""),
        minimum_app_version=minimum,
        event_ids=tuple(str(item) for item in value.get("event_ids") or []),
        clients=tuple(str(item) for item in value.get("clients") or []),
        files=dict(files),
        contains_credentials=False,
    )


def validate_package(
    path: Path | str,
    *,
    signature_policy: str = SIGNATURE_POLICY_DEVELOPMENT,
    keys_dir: Path | str | None = None,
    issigtool_path: Path | str | None = None,
) -> PackageInspection:
    """Valida envelope, integridade, estrutura, JSONs, referencias e assinatura.

    ``signature_policy`` decide a postura, nunca o proprio envelope (ele e texto
    plano fora do que a assinatura cobre; um atacante que zere
    ``signature_required`` nao pode se beneficiar disso). Em
    ``"development"`` (padrao), pacote sem assinatura passa com aviso visivel;
    em ``"production"``, exige assinatura valida de uma chave ativa. Uma
    assinatura PRESENTE e invalida (adulterada, chave desconhecida ou
    revogada) e sempre recusada, nas duas posturas.
    """
    if signature_policy not in SIGNATURE_POLICIES:
        raise ValueError(f"Politica de assinatura desconhecida: {signature_policy!r}.")
    path = Path(path)
    inspection = PackageInspection(path=path)
    errors = inspection.errors

    try:
        size = path.stat().st_size
    except OSError as exc:
        errors.append(PackageError("package.unreadable", str(path), f"Pacote ilegivel: {exc}."))
        return inspection
    if size > MAX_PACKAGE_BYTES:
        errors.append(PackageError(
            "package.too_large", str(path), f"Pacote maior que {MAX_PACKAGE_BYTES} bytes."
        ))
        return inspection

    data = path.read_bytes()
    try:
        outer = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        errors.append(PackageError("package.invalid_zip", str(path), f"Pacote ilegivel: {exc}."))
        return inspection

    with outer:
        names = outer.namelist()
        if len(names) != len(set(names)):
            errors.append(PackageError(
                "entry.duplicated", str(path), "Entradas duplicadas no envelope."
            ))
            return inspection
        unexpected = sorted(set(names) - {ENVELOPE_NAME, PAYLOAD_NAME, SIGNATURE_NAME})
        if unexpected:
            errors.append(PackageError(
                "entry.unsafe_path",
                ", ".join(unexpected),
                "Envelope com arquivos fora do contrato: " + ", ".join(unexpected) + ".",
            ))
            return inspection
        if ENVELOPE_NAME not in names or PAYLOAD_NAME not in names:
            errors.append(PackageError(
                "envelope.missing", str(path), "Pacote sem envelope.json ou payload.zip."
            ))
            return inspection
        if outer.getinfo(ENVELOPE_NAME).file_size > MAX_ENVELOPE_BYTES:
            errors.append(PackageError("envelope.invalid", ENVELOPE_NAME, "envelope.json muito grande."))
            return inspection
        if outer.getinfo(PAYLOAD_NAME).file_size > MAX_PACKAGE_BYTES:
            errors.append(PackageError(
                "payload.too_large", PAYLOAD_NAME, "payload.zip maior que o limite."
            ))
            return inspection
        try:
            envelope_raw = outer.read(ENVELOPE_NAME)
            payload = outer.read(PAYLOAD_NAME)
        except (zipfile.BadZipFile, OSError) as exc:
            errors.append(PackageError("package.invalid_zip", str(path), f"Pacote ilegivel: {exc}."))
            return inspection
        signed = SIGNATURE_NAME in names
        signature_bytes = outer.read(SIGNATURE_NAME) if signed else b""

    envelope = _parse_json(envelope_raw, ENVELOPE_NAME, errors)
    if envelope is None:
        return inspection
    inspection.envelope = {**envelope, "signed": signed}

    try:
        envelope_schema = int(envelope.get("schema_version"))
    except (TypeError, ValueError):
        errors.append(PackageError("envelope.invalid", ENVELOPE_NAME, "envelope.json sem schema_version."))
        return inspection
    if envelope_schema > SCHEMA_VERSION:
        errors.append(PackageError(
            "schema.unsupported",
            ENVELOPE_NAME,
            f"Envelope no schema {envelope_schema}; esta versao suporta ate {SCHEMA_VERSION}.",
        ))
        return inspection
    if str(envelope.get("payload") or "") != PAYLOAD_NAME:
        errors.append(PackageError(
            "envelope.invalid", ENVELOPE_NAME, "envelope.json aponta para outro payload."
        ))
        return inspection
    declared_hash = str(envelope.get("payload_sha256") or "")
    if declared_hash and declared_hash != _sha256(payload):
        errors.append(PackageError(
            "payload.hash_mismatch", PAYLOAD_NAME, "O hash do payload.zip nao confere."
        ))
        return inspection

    # A postura de assinatura vem de quem valida (``signature_policy``), nunca do
    # proprio envelope: ``signature_required`` e texto plano fora do que a
    # assinatura cobre, e um atacante que o zere nao pode se beneficiar disso.
    if signed:
        verification = psig.verify_payload_signature(
            payload, signature_bytes, keys_dir=keys_dir, issigtool_path=issigtool_path,
        )
        inspection.envelope["signature_status"] = verification.status
        inspection.envelope["signature_key_id"] = verification.key_id
        if not verification.ok:
            errors.append(PackageError(
                f"signature.{verification.status}", ENVELOPE_NAME, verification.message,
            ))
            return inspection
        inspection.warnings.append("Pacote assinado e verificado: " + verification.key_id + ".")
    elif signature_policy == SIGNATURE_POLICY_PRODUCTION:
        errors.append(PackageError(
            "signature.missing", ENVELOPE_NAME, "O pacote nao esta assinado; producao exige assinatura.",
        ))
        return inspection
    else:
        inspection.envelope["signature_status"] = "unsigned"
        inspection.warnings.append(
            "Pacote nao assinado (modo desenvolvimento). Nao instale como artefato de producao."
        )

    entries, payload_errors = _read_payload_entries(payload)
    errors.extend(payload_errors)
    if payload_errors:
        return inspection
    if MANIFEST_NAME not in entries:
        errors.append(PackageError("manifest.missing", MANIFEST_NAME, "Payload sem manifest.json."))
        return inspection

    manifest_value = _parse_json(entries[MANIFEST_NAME], MANIFEST_NAME, errors)
    if manifest_value is None:
        return inspection
    manifest = _manifest_from_dict(manifest_value, errors)
    if manifest is None:
        return inspection
    inspection.manifest = manifest

    payload_files = {name for name in entries if name != MANIFEST_NAME}
    listed = set(manifest.files)
    for name in sorted(listed - payload_files):
        errors.append(PackageError("file.missing", name, f"Arquivo do manifesto ausente: {name}."))
    for name in sorted(payload_files - listed):
        errors.append(PackageError("file.unlisted", name, f"Arquivo fora do manifesto: {name}."))
    for name in sorted(listed & payload_files):
        if _sha256(entries[name]) != manifest.files[name]:
            errors.append(PackageError(
                "file.hash_mismatch", name, f"Hash divergente em {name}."
            ))
    if errors:
        return inspection

    records: dict[str, dict[str, dict]] = {"event": {}, "cliente": {}, "vip": {}}
    for name in sorted(payload_files):
        directory, filename = name.split("/", 1)
        if directory == "logos":
            inspection.logos[filename] = entries[name]
            continue
        value = _parse_json(entries[name], name, errors)
        if value is None:
            continue
        record_id = str(value.get("id") or "").strip()
        if record_id != filename[: -len(".json")]:
            errors.append(PackageError(
                "record.id_mismatch",
                name,
                f"O id do registro ({record_id or 'ausente'}) nao corresponde ao arquivo {name}.",
            ))
            continue
        errors.extend(_scan_secrets(value, name))
        records[_KIND_BY_DIR[directory]][record_id] = value
    inspection.records = records
    if errors:
        return inspection

    if set(manifest.event_ids) != set(records["event"]):
        errors.append(PackageError(
            "manifest.invalid",
            MANIFEST_NAME,
            "Os eventos do manifesto diferem dos eventos do payload.",
        ))
        return inspection

    errors.extend(_validate_references(records, inspection.logos))
    errors.extend(
        PackageError("collection.invalid", "", message)
        for message in validate_collection(
            [(Path(f"{key}.json"), value) for key, value in sorted(records["event"].items())],
            [(Path(f"{key}.json"), value) for key, value in sorted(records["cliente"].items())],
            [(Path(f"{key}.json"), value) for key, value in sorted(records["vip"].items())],
            expected_event_ids=list(manifest.event_ids),
        )
    )
    return inspection


def _validate_references(
    records: dict[str, dict[str, dict]], logos: dict[str, bytes]
) -> list[PackageError]:
    errors: list[PackageError] = []
    clientes = records["cliente"]
    vips = records["vip"]
    for event_id, event in sorted(records["event"].items()):
        for vip_id in sorted(_explicit_vip_ids(event, vips)):
            if vip_id not in vips:
                errors.append(PackageError(
                    "reference.vip_missing",
                    f"events/{event_id}.json",
                    f"O evento {event_id} referencia o VIP {vip_id}, ausente no pacote.",
                ))
    for client_id, client in sorted(clientes.items()):
        logo_name = str(client.get("logo") or "").strip()
        if logo_name and logo_name not in logos:
            errors.append(PackageError(
                "reference.logo_missing",
                f"clientes/{client_id}.json",
                f"O cliente {client_id} referencia o logo {logo_name}, ausente no pacote.",
            ))
    return errors


def inspect_package(
    path: Path | str,
    *,
    signature_policy: str = SIGNATURE_POLICY_DEVELOPMENT,
    keys_dir: Path | str | None = None,
    issigtool_path: Path | str | None = None,
) -> dict:
    """Resumo pronto para a CLI e para a interface."""
    return validate_package(
        path, signature_policy=signature_policy, keys_dir=keys_dir, issigtool_path=issigtool_path,
    ).to_dict()


# ── Conciliacao de cadastros compartilhados ──────────────────────────

def _normalize_regions(value) -> list[dict]:
    result: list[dict] = []
    if isinstance(value, dict):
        for region, ip in value.items():
            result.append({"region": str(region).strip().upper(), "ip": str(ip or "").strip()})
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            region = str(item.get("region") or "").strip().upper()
            if not region:
                continue
            entry = dict(item)
            entry["region"] = region
            entry["ip"] = str(item.get("ip") or item.get("base_url") or "").strip()
            result.append(entry)
    return result


def _merge_regions(
    local_value, incoming_value, record_id: str, path: str, prefer_incoming: bool
) -> tuple[list[dict], list[str], list[ImportConflict]]:
    local = _normalize_regions(local_value)
    incoming = _normalize_regions(incoming_value)
    merged = [dict(item) for item in local]
    index = {item["region"]: position for position, item in enumerate(merged)}
    added: list[str] = []
    conflicts: list[ImportConflict] = []

    for item in incoming:
        region = item["region"]
        if region not in index:
            merged.append(dict(item))
            index[region] = len(merged) - 1
            added.append(region)
            continue
        current = merged[index[region]]
        if _is_empty(item.get("ip")) or current.get("ip") == item.get("ip"):
            continue
        if _is_empty(current.get("ip")) or prefer_incoming:
            # IP local vazio (ou politica de substituicao): o do pacote entra.
            current["ip"] = item["ip"]
            added.append(region)
            continue
        conflicts.append(ImportConflict(
            kind="cliente",
            record_id=record_id,
            path=path,
            field_name=f"regionais[{region}].ip",
            resolution="preserved",
            local=_short(current.get("ip")),
            incoming=_short(item.get("ip")),
        ))
    return merged, added, conflicts


def reconcile_record(
    local: dict,
    incoming: dict,
    kind: str,
    *,
    path: str = "",
    prefer_incoming: bool = False,
) -> tuple[dict, RecordReconciliation]:
    """Concilia um cadastro compartilhado campo a campo (decisao 5.1 do plano).

    - campo ausente ou vazio no local e preenchido no pacote: adiciona;
    - campo ausente, nulo ou vazio no pacote: mantem o local;
    - mesmo campo divergente nos dois lados: preserva o local e registra conflito;
    - ``regionais``: acrescenta regiao faltante e protege o IP local divergente;
    - ``logo``: unica substituicao automatica quando o pacote traz imagem.
    """
    record_id = str(local.get("id") or incoming.get("id") or "").strip()
    merged = dict(local)
    report = RecordReconciliation(
        kind=kind,
        record_id=record_id,
        record_name=str(local.get("name") or incoming.get("name") or record_id).strip(),
    )

    for key, incoming_value in incoming.items():
        if key == "id":
            continue
        local_value = local.get(key)
        if _is_empty(incoming_value):
            continue
        if key not in local or _is_empty(local_value):
            merged[key] = incoming_value
            report.added_fields.append(key)
            continue
        if local_value == incoming_value:
            continue
        if kind == "cliente" and key == "regionais":
            regions, added, conflicts = _merge_regions(
                local_value, incoming_value, record_id, path, prefer_incoming
            )
            if added:
                merged[key] = regions
                report.added_fields.append(key)
            if conflicts:
                report.preserved_fields.append(key)
                report.conflicts.extend(conflicts)
            continue
        if kind == "cliente" and key == "logo":
            # Excecao deliberada: o logo e recurso de apresentacao e nao altera
            # o comportamento da coleta, entao a imagem nova sempre entra.
            merged[key] = incoming_value
            report.updated_fields.append(key)
            continue
        if prefer_incoming:
            merged[key] = incoming_value
            report.updated_fields.append(key)
            report.conflicts.append(ImportConflict(
                kind=kind, record_id=record_id, path=path, field_name=key,
                resolution="replaced", local=_short(local_value), incoming=_short(incoming_value),
            ))
            continue
        report.preserved_fields.append(key)
        report.conflicts.append(ImportConflict(
            kind=kind, record_id=record_id, path=path, field_name=key,
            resolution="preserved", local=_short(local_value), incoming=_short(incoming_value),
        ))
    return merged, report


def _identity_key(kind: str, record: dict) -> str:
    name = _slug(str(record.get("name") or "")).casefold()
    if kind == "vip":
        return f"{name}|{_slug(str(record.get('cliente') or '')).casefold()}"
    return name


# ── Importacao ───────────────────────────────────────────────────────

def _read_local_state(server_data: Path) -> tuple[dict, dict, list[str]]:
    records: dict[str, dict[str, tuple[Path, dict]]] = {"event": {}, "cliente": {}, "vip": {}}
    warnings: list[str] = []
    for directory in _RECORD_DIRS:
        kind = _KIND_BY_DIR[directory]
        base = server_data / directory
        if not base.is_dir():
            continue
        for file_path in sorted(base.glob("*.json")):
            try:
                value = json.loads(file_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                warnings.append(f"Arquivo local ilegivel, ignorado: {file_path.name} ({exc}).")
                continue
            if not isinstance(value, dict):
                warnings.append(f"Arquivo local sem objeto JSON, ignorado: {file_path.name}.")
                continue
            record_id = str(value.get("id") or file_path.stem).strip()
            if record_id:
                records[kind][record_id] = (file_path, value)
    logos: dict[str, Path] = {}
    logo_dir = server_data / "logos"
    if logo_dir.is_dir():
        for file_path in sorted(logo_dir.iterdir()):
            if file_path.is_file():
                logos[file_path.name] = file_path
    return records, logos, warnings


def _build_plan(
    inspection: PackageInspection, target: ImportTarget, conflict_policy: str
) -> ImportPlan:
    manifest = inspection.manifest
    plan = ImportPlan(
        package_id=manifest.package_id,
        name=manifest.name,
        target=target,
        conflict_policy=conflict_policy,
    )
    local_records, local_logos, warnings = _read_local_state(target.server_data)
    plan.warnings.extend(warnings)
    prefer_incoming = conflict_policy == "replace"

    for kind in ("cliente", "vip", "event"):
        directory = _DIR_BY_KIND[kind]
        local_by_identity = {
            _identity_key(kind, value): record_id
            for record_id, (_path, value) in local_records[kind].items()
        }
        for record_id, incoming in sorted(inspection.records.get(kind, {}).items()):
            relative = f"{directory}/{record_id}.json"
            existing = local_records[kind].get(record_id)

            if existing is None:
                # Cadastro compartilhado com o mesmo nome sob outro id e ambiguidade
                # do operador, nao decisao do importador. Evento nao entra na regra:
                # dois eventos podem legitimamente ter o mesmo nome.
                twin = (
                    None if kind == "event"
                    else local_by_identity.get(_identity_key(kind, incoming))
                )
                if twin:
                    conflict = ImportConflict(
                        kind=kind, record_id=record_id, path=relative,
                        field_name="name", resolution="skipped",
                        local=_short(twin), incoming=_short(record_id),
                    )
                    plan.conflicts.append(conflict)
                    plan.warnings.append(
                        f"{_KIND_LABEL[kind]} {record_id} tem o mesmo nome do cadastro local "
                        f"{twin}, com id diferente. Nada foi criado nem mesclado; decida "
                        f"manualmente qual cadastro vale."
                    )
                    plan.actions.append(ImportAction(
                        kind=kind, record_id=record_id, path=relative,
                        action="skip", reason="ambiguous_identity",
                    ))
                    continue
                plan.actions.append(ImportAction(
                    kind=kind, record_id=record_id, path=relative, action="add",
                    destination=target.server_data / relative, data=_json_bytes(incoming),
                ))
                continue

            local_path, local_value = existing
            if local_value == incoming:
                plan.actions.append(ImportAction(
                    kind=kind, record_id=record_id, path=relative, action="unchanged",
                ))
                continue

            if kind == "event":
                # O evento e a unidade funcional: nao se mistura meio evento local
                # com meio evento do pacote.
                if prefer_incoming:
                    plan.conflicts.append(ImportConflict(
                        kind=kind, record_id=record_id, path=relative, field_name="*",
                        resolution="replaced", local=str(local_path), incoming=relative,
                    ))
                    plan.actions.append(ImportAction(
                        kind=kind, record_id=record_id, path=relative, action="replace",
                        destination=local_path, data=_json_bytes(incoming),
                    ))
                else:
                    plan.conflicts.append(ImportConflict(
                        kind=kind, record_id=record_id, path=relative, field_name="*",
                        resolution="preserved", local=str(local_path), incoming=relative,
                    ))
                    plan.actions.append(ImportAction(
                        kind=kind, record_id=record_id, path=relative, action="preserve",
                        reason="local_changed",
                    ))
                continue

            merged, report = reconcile_record(
                local_value, incoming, kind, path=relative, prefer_incoming=prefer_incoming
            )
            plan.conflicts.extend(report.conflicts)
            if merged == local_value:
                plan.actions.append(ImportAction(
                    kind=kind, record_id=record_id, path=relative,
                    action="unchanged", reconciliation=report,
                ))
                continue
            plan.actions.append(ImportAction(
                kind=kind, record_id=record_id, path=relative, action="reconcile",
                reconciliation=report, destination=local_path, data=_json_bytes(merged),
            ))

    for logo_name in sorted(inspection.logos):
        relative = f"logos/{logo_name}"
        incoming_bytes = inspection.logos[logo_name]
        local_path = local_logos.get(logo_name)
        if local_path is None:
            plan.actions.append(ImportAction(
                kind="logo", record_id=logo_name, path=relative, action="add",
                destination=target.server_data / relative, data=incoming_bytes,
            ))
            continue
        if local_path.read_bytes() == incoming_bytes:
            plan.actions.append(ImportAction(
                kind="logo", record_id=logo_name, path=relative, action="unchanged",
            ))
            continue
        plan.actions.append(ImportAction(
            kind="logo", record_id=logo_name, path=relative, action="replace",
            reason="logo_updated", destination=local_path, data=incoming_bytes,
        ))
    return plan


def plan_import(
    package_path: Path | str,
    target: Path | str | None = None,
    conflict_policy: str = "preserve",
    *,
    signature_policy: str = SIGNATURE_POLICY_DEVELOPMENT,
    keys_dir: Path | str | None = None,
    issigtool_path: Path | str | None = None,
) -> ImportPlan:
    """Diz exatamente o que a importacao faria, sem gravar nada."""
    resolved = ImportTarget.resolve(target)
    if conflict_policy not in CONFLICT_POLICIES:
        return ImportPlan(
            package_id="", name="", target=resolved, conflict_policy=conflict_policy,
            errors=[PackageError(
                "argument.invalid", "", f"Politica de conflito desconhecida: {conflict_policy}."
            )],
        )
    inspection = validate_package(
        package_path, signature_policy=signature_policy, keys_dir=keys_dir, issigtool_path=issigtool_path,
    )
    if not inspection.ok:
        return ImportPlan(
            package_id="", name="", target=resolved, conflict_policy=conflict_policy,
            errors=list(inspection.errors), warnings=list(inspection.warnings),
        )
    plan = _build_plan(inspection, resolved, conflict_policy)
    plan.warnings.extend(inspection.warnings)
    return plan


_WRITE_ORDER = {"logo": 0, "cliente": 1, "vip": 2, "event": 3}


def import_package(
    package_path: Path | str,
    target: Path | str | None = None,
    conflict_policy: str = "preserve",
    *,
    signature_policy: str = SIGNATURE_POLICY_DEVELOPMENT,
    keys_dir: Path | str | None = None,
    issigtool_path: Path | str | None = None,
) -> ImportResult:
    """Valida, concilia e grava; qualquer falha reverte tudo que ja foi gravado."""
    resolved = ImportTarget.resolve(target)
    if conflict_policy not in CONFLICT_POLICIES:
        return ImportResult(ok=False, errors=[PackageError(
            "argument.invalid", "", f"Politica de conflito desconhecida: {conflict_policy}."
        )])

    inspection = validate_package(
        package_path, signature_policy=signature_policy, keys_dir=keys_dir, issigtool_path=issigtool_path,
    )
    if not inspection.ok:
        # Nenhum arquivo e criado quando o pacote nao passa na validacao.
        return ImportResult(
            ok=False,
            errors=list(inspection.errors),
            warnings=list(inspection.warnings),
        )

    try:
        with _ImportLock(paths.import_lock_path(resolved.server_data)):
            plan = _build_plan(inspection, resolved, conflict_policy)
            result = _apply_plan(plan, resolved)
    except ImportLockError as exc:
        return ImportResult(ok=False, errors=[PackageError("import.locked", "", str(exc))])

    result.warnings.extend(inspection.warnings)
    result.report_path = _write_report(resolved, inspection, plan, result)
    return result


def _apply_plan(plan: ImportPlan, target: ImportTarget) -> ImportResult:
    result = ImportResult(
        ok=True,
        package_id=plan.package_id,
        name=plan.name,
        conflicts=list(plan.conflicts),
        warnings=list(plan.warnings),
        actions=list(plan.actions),
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = target.backups / f"{plan.package_id}-{stamp}"
    undo: list[tuple[Path, bytes | None]] = []

    try:
        for directory in PAYLOAD_DIRS:
            (target.server_data / directory).mkdir(parents=True, exist_ok=True)
        for action in sorted(plan.actions, key=lambda item: (_WRITE_ORDER[item.kind], item.path)):
            if action.action not in {"add", "reconcile", "replace"}:
                continue
            destination = action.destination
            previous = destination.read_bytes() if destination.exists() else None
            if previous is not None:
                backup_path = backup_root / action.path
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                backup_path.write_bytes(previous)
                result.backups.append(str(backup_path))
            undo.append((destination, previous))
            _write_atomic(destination, action.data)
    except Exception as exc:  # noqa: BLE001 - qualquer falha precisa reverter
        logger.error("Importacao revertida: %s", exc)
        _rollback(undo)
        return ImportResult(
            ok=False,
            package_id=plan.package_id,
            name=plan.name,
            warnings=list(plan.warnings),
            errors=[PackageError(
                "import.failed", "", f"Importacao revertida sem alterar dados: {exc}."
            )],
        )

    for action in plan.actions:
        if action.action == "add":
            result.added.append(action.path)
        elif action.action == "unchanged":
            result.unchanged.append(action.path)
        elif action.action == "reconcile":
            result.reconciled.append(action.path)
        elif action.action == "replace":
            result.replaced.append(action.path)
        else:
            result.preserved.append(action.path)
    return result


def _rollback(undo: list[tuple[Path, bytes | None]]) -> None:
    for path, previous in reversed(undo):
        try:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous)
        except OSError as exc:
            logger.error("Falha ao reverter %s: %s", path, exc)


def _write_report(
    target: ImportTarget,
    inspection: PackageInspection,
    plan: ImportPlan,
    result: ImportResult,
) -> str:
    report = {
        "schema_version": 1,
        "package_id": result.package_id,
        "name": result.name,
        "package": str(inspection.path),
        "server_data": str(target.server_data),
        "conflict_policy": plan.conflict_policy,
        "finished_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "result": result.to_dict(),
        "manifest": inspection.manifest.to_dict() if inspection.manifest else None,
    }
    path = target.reports / f"{result.package_id or 'sem-id'}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(path, _json_bytes(report))
    except OSError as exc:
        logger.error("Nao foi possivel gravar o relatorio de importacao: %s", exc)
        return ""
    return str(path)


# ── Mensagens ────────────────────────────────────────────────────────

def summary_lines(result: ImportResult) -> list[str]:
    """Frases curtas para o console e para o dialogo do instalador."""
    if result.errors:
        return ["Pacote invalido: " + item.message for item in result.errors[:3]]

    lines: list[str] = []
    if not result.changed:
        lines.append("Pacote ja aplicado; nenhum arquivo foi alterado.")
    else:
        parts = []
        added_events = [path for path in result.added if path.startswith("events/")]
        if added_events:
            parts.append(_plural(len(added_events), "evento adicionado", "eventos adicionados"))
        other_added = len(result.added) - len(added_events)
        if other_added:
            parts.append(_plural(other_added, "cadastro adicionado", "cadastros adicionados"))
        if result.reconciled:
            parts.append(
                _plural(len(result.reconciled), "cadastro conciliado", "cadastros conciliados")
            )
        if result.replaced:
            parts.append(
                _plural(len(result.replaced), "arquivo substituido", "arquivos substituidos")
            )
        lines.append("Pacote importado: " + ", ".join(parts) + ".")

    for action in result.actions:
        if action.action == "preserve":
            lines.append(
                f"O {_KIND_LABEL[action.kind].lower()} {action.record_id} ja existe com "
                "alteracoes locais e foi preservado."
            )
        elif action.action == "reconcile" and action.reconciliation is not None:
            report = action.reconciliation
            detail = []
            if report.added_fields:
                detail.append("campos acrescentados: " + ", ".join(report.added_fields))
            if report.updated_fields:
                detail.append("campos atualizados: " + ", ".join(report.updated_fields))
            tail = (
                f"{len(report.preserved_fields)} campo(s) local(is) preservado(s)"
                if report.preserved_fields
                else "nenhum dado local alterado"
            )
            lines.append(
                f"{_KIND_LABEL[action.kind]} {report.record_name or action.record_id} "
                "ja cadastrado: "
                + ("; ".join(detail) + "; " if detail else "")
                + tail
                + "."
            )
    return lines
