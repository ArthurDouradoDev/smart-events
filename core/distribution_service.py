"""Servico de distribuicao: revisao, jobs de geracao e inventario de saidas.

A Fase 1 entregou o formato ``.sepack`` e as operacoes de preview/geracao. Este
modulo transforma essas operacoes em **jobs**, para que a Central possa dispara-las
sem travar a pagina e recuperar o resultado depois de um F5.

Regras que este modulo garante:

- toda saida fica em ``<distributions>/<job-id>/``; nenhum caminho vem do navegador;
- so um job de escrita roda por vez (lock); revisoes podem ocorrer em paralelo;
- o estado de cada job e persistido em ``<distributions>/jobs/<job-id>.json`` a cada
  transicao, entao a pagina pode ser recarregada a qualquer momento;
- o download so aceita um artefato **registrado no proprio job**, nunca um nome livre;
- tudo que sai para a interface passa por ``redact()``: nenhum caminho do operador,
  valor de variavel de ambiente ou segredo em log de erro.

A Fase 2 oferece apenas o formato ``event_package``. As etapas ``compiling`` e
``testing`` ja existem no vocabulario de estados porque o job de Setup completo da
Fase 3 usara a mesma maquina, mas nenhum job desta fase passa por elas.

Todo texto deste modulo e ASCII: as mesmas mensagens vao para o console do operador
(cp1252 no Windows) e para a interface.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core import event_package as ep
from core import paths

logger = logging.getLogger(__name__)


# ── Contrato do servico ──────────────────────────────────────────────

SCHEMA_VERSION = 1

FORMAT_EVENT_PACKAGE = "event_package"
FORMAT_FULL_SETUP = "full_setup"

# Capacidade real desta fase. O Setup completo depende do build-base da Fase 3 e
# por isso aparece apenas como opcao desabilitada, com o motivo.
SUPPORTED_FORMATS = (FORMAT_EVENT_PACKAGE,)
FULL_SETUP_REASON = "disponivel apos configurar um build-base"

JOB_STATES = (
    "queued", "validating", "packaging", "compiling", "testing", "ready", "failed",
)
TERMINAL_STATES = frozenset({"ready", "failed"})
# Etapas efetivamente percorridas por um job de pacote nesta fase.
PACKAGE_STEPS = ("queued", "validating", "packaging", "ready")

MAX_EVENTS_PER_JOB = 50
INCOMPLETE_JOB_MAX_AGE_HOURS = 24

ARTIFACT_KINDS = ("package", "manifest")

# uuid4().hex: um job_id que nao casar aqui nunca chega a compor um caminho.
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


class DistributionError(Exception):
    """Recusa estruturada; ``status`` e o codigo HTTP que o servidor deve usar."""

    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


# ── Redacao ──────────────────────────────────────────────────────────

# Caminhos absolutos de Windows, UNC e as raizes POSIX que carregam nome de
# usuario. Rotas de API (``/api/...``) nao entram na lista de proposito.
_ABSOLUTE_PATH = re.compile(
    r"""(?:[A-Za-z]:[\\/]|\\\\|/(?:home|Users|root|tmp|var|opt|mnt)/)[^\s'"]*"""
)
# O nome da variavel fica (saber qual segredo vazaria ajuda); o valor, nunca.
# O prefixo opcional cobre ``SMARTEVENTS_TOKEN`` e afins.
_SECRET_ASSIGNMENT = re.compile(
    r"([\w.-]*(?:senha|password|passwd|token|cookie|session|sessao|secret|credential|"
    r"authorization|apikey|roarand)[\w.-]*)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def redact(text) -> str:
    """Remove caminho do operador e valor de segredo antes de expor a interface.

    O nome do arquivo sobrevive porque e ele que ajuda a diagnosticar; a arvore
    de diretorios do perfil, nao.
    """
    value = str(text or "")
    value = _ABSOLUTE_PATH.sub(
        lambda match: "<caminho>/" + (re.split(r"[\\/]", match.group(0))[-1] or "..."),
        value,
    )
    return _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<oculto>", value)


def _redact_error(item: dict) -> dict:
    return {**item, "message": redact(item.get("message"))}


# ── Utilidades ───────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _retrying(operation, description: str):
    """Repete uma operacao de arquivo que o Windows recusa por acesso momentaneo.

    O registro do job e reescrito a cada transicao enquanto a interface o consulta,
    e o antivirus abre o arquivo temporario logo apos a gravacao. Nos dois casos o
    Windows devolve ACCESS_DENIED por alguns milissegundos. Desistir na primeira
    tentativa deixaria o job sem registro; insistir sempre mascararia uma permissao
    realmente negada, entao a sequencia e curta e limitada.
    """
    last: PermissionError | None = None
    for attempt in range(8):
        try:
            return operation()
        except PermissionError as exc:
            last = exc
            time.sleep(0.01 * (attempt + 1))
    logger.error("Acesso negado apos varias tentativas em %s: %s", description, last)
    raise last


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_bytes(data)
        _retrying(lambda: os.replace(temporary, path), str(path.name))
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _event_rows(preview: ep.PackagePreview) -> list[dict]:
    """Uma linha por evento para a tela de revisao."""
    rows = []
    for event in preview.events:
        oss = event.get("oss") or {}
        sites = event.get("sites") or []
        rows.append({
            "id": str(event.get("id") or ""),
            "name": str(event.get("name") or event.get("id") or ""),
            "status": str(event.get("status") or "SCHEDULED"),
            "client": str(oss.get("cliente") or ""),
            "region": str(oss.get("region") or ""),
            "sites": len(sites),
            "cells": sum(len(site.get("cells") or []) for site in sites),
            "clusters": len(event.get("clusters") or []),
            "pm_tasks": len((event.get("integration") or {}).get("pm_tasks") or []),
        })
    return rows


def _client_rows(preview: ep.PackagePreview) -> list[dict]:
    rows = []
    for client in preview.clientes:
        regions = []
        for item in client.get("regionais") or []:
            if isinstance(item, dict) and str(item.get("region") or "").strip():
                regions.append(str(item["region"]).strip().upper())
        rows.append({
            "id": str(client.get("id") or ""),
            "name": str(client.get("name") or client.get("id") or ""),
            "regions": regions,
            "logo": str(client.get("logo") or ""),
        })
    return rows


def _vip_rows(preview: ep.PackagePreview) -> list[dict]:
    fallback = set(preview.fallback_vip_ids)
    rows = []
    for vip in preview.vips:
        vip_id = str(vip.get("id") or "")
        rows.append({
            "id": vip_id,
            "name": str(vip.get("name") or vip_id),
            "client": str(vip.get("cliente") or ""),
            "region": str(vip.get("oss") or ""),
            "source": "fallback" if vip_id in fallback else "explicit",
        })
    return rows


RECONCILIATION_NOTE = (
    "Cliente, regional, VIP e logo que ja existirem no destino sao conciliados pelo id: "
    "campos faltantes sao acrescentados, campos locais preenchidos sao mantidos e nenhum "
    "cadastro e recriado ou sobrescrito."
)


# ── Servico ──────────────────────────────────────────────────────────

class DistributionService:
    """Revisao e geracao de distribuicoes, com estado persistido em disco."""

    def __init__(self, root: Path | str | None = None, source_dir: Path | str | None = None):
        self._root = Path(root) if root is not None else None
        self._source_dir = Path(source_dir) if source_dir is not None else None
        # Um job de escrita por vez: dois artefatos nunca se misturam.
        self._write_lock = threading.Lock()
        # Serializa leitura e gravacao do registro dentro do processo: a interface
        # consulta o job enquanto o worker o reescreve a cada transicao. Reentrante
        # porque `_transition` le e grava dentro da mesma secao critica.
        self._record_lock = threading.RLock()

    # ── Caminhos ─────────────────────────────────────────────────────

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else paths.distributions_dir()

    @property
    def jobs_dir(self) -> Path:
        return self.root / "jobs"

    @property
    def source_dir(self) -> Path:
        return self._source_dir if self._source_dir is not None else paths.server_data_dir()

    def job_dir(self, job_id: str) -> Path:
        """Pasta de saida do job. O id e validado antes de virar caminho."""
        return self.root / self._checked_id(job_id)

    @staticmethod
    def _checked_id(job_id: str) -> str:
        value = str(job_id or "")
        if not _JOB_ID.fullmatch(value):
            raise DistributionError("job.invalid_id", "Identificador de job invalido.", 400)
        return value

    # ── Capacidades ──────────────────────────────────────────────────

    def capabilities(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "formats": list(SUPPORTED_FORMATS),
            "available_formats": [
                {
                    "id": FORMAT_EVENT_PACKAGE,
                    "label": "Pacote de eventos (.sepack)",
                    "description": "Para quem ja tem o SmartEvents instalado.",
                    "enabled": True,
                },
                {
                    "id": FORMAT_FULL_SETUP,
                    "label": "Instalador completo (.exe)",
                    "description": "Primeira instalacao ou atualizacao completa.",
                    "enabled": False,
                    "reason": FULL_SETUP_REASON,
                },
            ],
            "max_events": MAX_EVENTS_PER_JOB,
            "vip_policies": list(ep.VIP_POLICIES),
            "app_version": ep.app_version(),
        }

    # ── Selecao ──────────────────────────────────────────────────────

    def known_event_ids(self) -> set[str]:
        """Ids de evento realmente cadastrados na origem lida pelo servidor."""
        result: set[str] = set()
        directory = self.source_dir / "events"
        if not directory.is_dir():
            return result
        for file_path in sorted(directory.glob("*.json")):
            try:
                value = json.loads(file_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(value, dict):
                result.add(str(value.get("id") or file_path.stem).strip())
        result.discard("")
        return result

    def validate_selection(
        self, event_ids, *, fmt: str = FORMAT_EVENT_PACKAGE, vip_policy: str = "auto"
    ) -> list[str]:
        """Recusa formato, politica, quantidade e id fora do que o servidor conhece."""
        if fmt not in SUPPORTED_FORMATS:
            reason = FULL_SETUP_REASON if fmt == FORMAT_FULL_SETUP else "formato desconhecido"
            raise DistributionError(
                "format.unsupported", f"Formato nao disponivel: {fmt} ({reason}).", 400
            )
        if vip_policy not in ep.VIP_POLICIES:
            raise DistributionError(
                "argument.invalid", f"Politica de VIP desconhecida: {vip_policy}.", 400
            )
        if not isinstance(event_ids, (list, tuple)):
            raise DistributionError("argument.invalid", "event_ids deve ser uma lista.", 400)

        selected: list[str] = []
        for value in event_ids:
            if not isinstance(value, str):
                raise DistributionError("argument.invalid", "event_ids aceita apenas texto.", 400)
            value = value.strip()
            if value and value not in selected:
                selected.append(value)
        if not selected:
            raise DistributionError(
                "selection.empty", "Selecione ao menos um evento.", 400
            )
        if len(selected) > MAX_EVENTS_PER_JOB:
            raise DistributionError(
                "selection.too_large",
                f"Selecao com {len(selected)} eventos; o limite e {MAX_EVENTS_PER_JOB}.",
                400,
            )
        unknown = sorted(set(selected) - self.known_event_ids())
        if unknown:
            raise DistributionError(
                "event.unknown", "Evento nao cadastrado: " + ", ".join(unknown), 400
            )
        return selected

    # ── Revisao ──────────────────────────────────────────────────────

    def preview(
        self,
        event_ids,
        *,
        name: str | None = None,
        vip_policy: str = "auto",
        vip_ids=None,
    ) -> dict:
        """Contrato da tela de revisao. Nao cria artefato nem job."""
        selected = self.validate_selection(event_ids, vip_policy=vip_policy)
        preview = ep.preview_package(
            self.source_dir, selected, name=name, vip_policy=vip_policy, vip_ids=vip_ids
        )
        payload = preview.to_dict()
        # O caminho do server_data do operador nao acrescenta nada na tela.
        payload.pop("source_dir", None)
        payload["errors"] = [_redact_error(item) for item in payload["errors"]]
        payload["warnings"] = [redact(item) for item in payload["warnings"]]
        payload["suggested_filename"] = ep.suggested_filename(preview)
        payload["events"] = _event_rows(preview)
        payload["clientes"] = _client_rows(preview)
        payload["vips"] = _vip_rows(preview)
        payload["reconciliation_note"] = RECONCILIATION_NOTE
        payload["format"] = FORMAT_EVENT_PACKAGE
        return payload

    # ── Jobs ─────────────────────────────────────────────────────────

    def create_job(
        self,
        event_ids,
        *,
        name: str | None = None,
        fmt: str = FORMAT_EVENT_PACKAGE,
        vip_policy: str = "auto",
        vip_ids=None,
        background: bool = True,
    ) -> dict:
        """Registra o job e comeca a gerar. Retorna o registro inicial."""
        selected = self.validate_selection(event_ids, fmt=fmt, vip_policy=vip_policy)
        job_id = uuid.uuid4().hex
        record = {
            "schema_version": SCHEMA_VERSION,
            "job_id": job_id,
            "format": fmt,
            "state": "queued",
            "name": str(name or "").strip(),
            "event_ids": selected,
            "vip_policy": vip_policy,
            "vip_ids": None if vip_ids is None else sorted({str(v) for v in vip_ids}),
            "created_at_utc": _now(),
            "updated_at_utc": _now(),
            "finished_at_utc": "",
            "steps": [{"state": "queued", "at": _now()}],
            "progress": self._progress("queued"),
            "counts": {},
            "artifacts": {},
            "manifest": None,
            "warnings": [],
            "errors": [],
            "failed_step": "",
            "diagnostic": "",
        }
        self._save(record)
        self.cleanup_incomplete()

        if not background:
            self._run_job(job_id)
            return self.get_job(job_id)
        threading.Thread(
            target=self._run_job, args=(job_id,),
            name=f"distribution-{job_id[:8]}", daemon=True,
        ).start()
        return record

    def get_job(self, job_id: str) -> dict:
        return self._load(job_id)

    def list_jobs(self) -> list[dict]:
        jobs = []
        if not self.jobs_dir.is_dir():
            return jobs
        for file_path in sorted(self.jobs_dir.glob("*.json")):
            try:
                with self._record_lock:
                    value = json.loads(self._read_record(file_path).decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            if isinstance(value, dict) and value.get("job_id"):
                jobs.append(value)
        jobs.sort(key=lambda item: str(item.get("created_at_utc") or ""), reverse=True)
        return jobs

    def wait_for(self, job_id: str, timeout: float = 60.0) -> dict:
        """Bloqueia ate o job chegar a um estado terminal (usado por CLI e testes)."""
        deadline = time.monotonic() + timeout
        while True:
            record = self._load(job_id)
            if record["state"] in TERMINAL_STATES:
                return record
            if time.monotonic() >= deadline:
                raise DistributionError(
                    "job.timeout", f"O job {job_id} nao terminou a tempo.", 504
                )
            time.sleep(0.05)

    def artifact(self, job_id: str, kind: str) -> tuple[Path, dict]:
        """Caminho de um artefato **registrado** no job, nunca um nome livre."""
        if kind not in ARTIFACT_KINDS:
            raise DistributionError("artifact.unknown", f"Artefato desconhecido: {kind}.", 404)
        record = self._load(job_id)
        if record["state"] != "ready":
            raise DistributionError(
                "job.not_ready",
                f"O job ainda nao esta pronto (estado atual: {record['state']}).",
                409,
            )
        entry = (record.get("artifacts") or {}).get(kind)
        if not isinstance(entry, dict) or not entry.get("name"):
            raise DistributionError(
                "artifact.unknown", f"O job nao registrou o artefato {kind}.", 404
            )
        name = str(entry["name"])
        if not paths.is_safe_component(name):
            raise DistributionError("artifact.unknown", "Nome de artefato invalido.", 404)
        path = self.job_dir(record["job_id"]) / name
        if not path.is_file():
            raise DistributionError(
                "artifact.missing", f"O arquivo do job nao esta mais disponivel: {name}.", 410
            )
        return path, entry

    def cleanup_incomplete(self, max_age_hours: float = INCOMPLETE_JOB_MAX_AGE_HOURS) -> list[str]:
        """Descarta jobs antigos que nunca ficaram prontos. Job ``ready`` nunca sai."""
        removed: list[str] = []
        limit = time.time() - max_age_hours * 3600
        for record in self.list_jobs():
            job_id = str(record.get("job_id") or "")
            if record.get("state") == "ready" or not _JOB_ID.fullmatch(job_id):
                continue
            record_path = self.jobs_dir / f"{job_id}.json"
            try:
                if record_path.stat().st_mtime > limit:
                    continue
            except OSError:
                continue
            directory = self.root / job_id
            try:
                for leftover in sorted(directory.glob("*")) if directory.is_dir() else []:
                    leftover.unlink(missing_ok=True)
                if directory.is_dir():
                    directory.rmdir()
                record_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Nao foi possivel limpar o job %s: %s", job_id, exc)
                continue
            removed.append(job_id)
        return removed

    # ── Execucao ─────────────────────────────────────────────────────

    def _run_job(self, job_id: str) -> None:
        try:
            record = self._load(job_id)
        except DistributionError as exc:
            logger.error("Job de distribuicao %s nao pode ser lido: %s", job_id, exc.message)
            # Um job que nunca chega a um estado terminal faria a interface consultar
            # para sempre. Se o registro apenas nao pode ser lido, marcamos a falha;
            # se ele nao existe mais, nao ha job para ressuscitar.
            if exc.code != "job.unknown":
                self._force_failed(job_id, exc.message)
            return
        # Enquanto o lock nao vem, o job continua legitimamente em `queued` e a
        # interface mostra que ele esta aguardando outra geracao terminar.
        with self._write_lock:
            try:
                self._transition(job_id, "validating")
                preview = ep.preview_package(
                    self.source_dir,
                    record["event_ids"],
                    name=record["name"] or None,
                    vip_policy=record["vip_policy"],
                    vip_ids=record["vip_ids"],
                )
                if not preview.ok:
                    self._fail(
                        job_id, "validating",
                        errors=[_redact_error(item.to_dict()) for item in preview.errors],
                        warnings=[redact(item) for item in preview.warnings],
                        diagnostic="\n".join(
                            f"[{item.code}] {item.path}: {redact(item.message)}"
                            for item in preview.errors
                        ),
                    )
                    return

                self._transition(job_id, "packaging")
                directory = self.job_dir(job_id)
                directory.mkdir(parents=True, exist_ok=True)
                destination = directory / ep.suggested_filename(preview)
                manifest = ep.build_package(preview, destination, force=True)
                manifest_path = directory / "manifest.json"
                _write_atomic(manifest_path, _json_bytes(manifest.to_dict()))

                self._transition(
                    job_id, "ready",
                    name=preview.name,
                    counts=preview.counts(),
                    manifest=manifest.to_dict(),
                    warnings=[redact(item) for item in preview.warnings],
                    artifacts={
                        "package": self._artifact_entry(destination, "application/octet-stream"),
                        "manifest": self._artifact_entry(manifest_path, "application/json"),
                    },
                    finished_at_utc=_now(),
                )
            except ep.PackageBuildError as exc:
                self._fail(
                    job_id, "packaging",
                    errors=[_redact_error(item.to_dict()) for item in exc.errors],
                    diagnostic="\n".join(redact(item.message) for item in exc.errors),
                )
            except DistributionError as exc:
                self._fail(
                    job_id, "packaging",
                    errors=[{"code": exc.code, "path": "", "message": redact(exc.message)}],
                    diagnostic=redact(exc.message),
                )
            except Exception as exc:  # noqa: BLE001 - o job nunca pode derrubar o servidor
                logger.error("Job de distribuicao %s falhou: %s", job_id, exc)
                self._fail(
                    job_id, "packaging",
                    errors=[{
                        "code": "job.failed",
                        "path": "",
                        "message": redact(f"Falha ao gerar a distribuicao: {exc}"),
                    }],
                    diagnostic=redact(f"{type(exc).__name__}: {exc}"),
                )

    @staticmethod
    def _artifact_entry(path: Path, media_type: str) -> dict:
        return {
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": ep.sha256_of(path),
            "media_type": media_type,
        }

    @staticmethod
    def _progress(state: str) -> dict:
        # `failed` nao e uma etapa da barra: ele fica com indice -1 para que a
        # interface mostre a etapa que falhou, e nao a ultima como concluida.
        steps = list(PACKAGE_STEPS)
        return {
            "step": state,
            "index": steps.index(state) if state in steps else -1,
            "total": len(steps) - 1,
            "steps": steps,
        }

    def _transition(self, job_id: str, state: str, **fields) -> dict:
        if state not in JOB_STATES:
            raise DistributionError("job.invalid_state", f"Estado invalido: {state}.", 500)
        with self._record_lock:
            record = self._load(job_id)
            record.update(fields)
            record["state"] = state
            record["updated_at_utc"] = _now()
            record["steps"] = list(record.get("steps") or []) + [{"state": state, "at": _now()}]
            record["progress"] = self._progress(state)
            self._save(record)
        logger.info("Job de distribuicao %s: %s", job_id, state)
        return record

    def _fail(
        self, job_id: str, step: str, *, errors: list[dict], diagnostic: str,
        warnings: list[str] | None = None,
    ) -> None:
        fields = {
            "failed_step": step,
            "errors": errors,
            "diagnostic": diagnostic,
            "finished_at_utc": _now(),
        }
        if warnings is not None:
            fields["warnings"] = warnings
        try:
            self._transition(job_id, "failed", **fields)
        except DistributionError as exc:
            logger.error("Falha do job %s nao pode ser registrada: %s", job_id, exc.message)
            self._force_failed(job_id, diagnostic or exc.message)

    def _force_failed(self, job_id: str, message: str) -> None:
        """Ultimo recurso: encerra o job sem depender de ler o registro anterior."""
        try:
            self._save({
                "schema_version": SCHEMA_VERSION,
                "job_id": job_id,
                "format": FORMAT_EVENT_PACKAGE,
                "state": "failed",
                "name": "",
                "event_ids": [],
                "vip_policy": "auto",
                "vip_ids": None,
                "created_at_utc": _now(),
                "updated_at_utc": _now(),
                "finished_at_utc": _now(),
                "steps": [{"state": "failed", "at": _now()}],
                "progress": self._progress("failed"),
                "counts": {},
                "artifacts": {},
                "manifest": None,
                "warnings": [],
                "errors": [{"code": "job.unreadable", "path": "", "message": redact(message)}],
                "failed_step": "queued",
                "diagnostic": redact(message),
            })
        except OSError as exc:
            logger.error("Job %s ficou sem registro terminal: %s", job_id, exc)

    # ── Persistencia ─────────────────────────────────────────────────

    def _save(self, record: dict) -> None:
        with self._record_lock:
            _write_atomic(self.jobs_dir / f"{record['job_id']}.json", _json_bytes(record))

    @staticmethod
    def _read_record(path: Path) -> bytes:
        """Le o registro tolerando a troca atomica concorrente (ver ``_retrying``)."""
        return _retrying(path.read_bytes, path.name)

    def _load(self, job_id: str) -> dict:
        path = self.jobs_dir / f"{self._checked_id(job_id)}.json"
        with self._record_lock:
            if not path.is_file():
                raise DistributionError("job.unknown", f"Job nao encontrado: {job_id}.", 404)
            try:
                record = json.loads(self._read_record(path).decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                raise DistributionError(
                    "job.unreadable", f"Registro do job ilegivel: {redact(exc)}.", 500
                ) from exc
        if not isinstance(record, dict) or record.get("job_id") != job_id:
            raise DistributionError("job.unreadable", "Registro do job inconsistente.", 500)
        return record
