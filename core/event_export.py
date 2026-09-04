"""Exportação auditável dos dados persistidos de um evento.

O módulo não reutiliza getters da tela: eles aplicam janelas, limites e
agregações. A exportação trabalha sobre backups online dos bancos SQLite e
escreve CSVs em streaming antes de publicar um único ZIP atomicamente.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
import uuid
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

from core import database as db
from core.kpi_formulas import definition, to_canonical
from core.paths import downloads_dir, event_exports_dir


logger = logging.getLogger(__name__)
BRASILIA = ZoneInfo("America/Sao_Paulo")
SCHEMA_VERSION = 1
TERMINAL_STATES = {"ready", "failed", "cancelled"}
ACTIVE_STATES = {
    "queued", "snapshotting", "exporting_metadata", "exporting_kpis",
    "exporting_vips", "exporting_alarms", "exporting_alerts", "packing",
    "validating", "cancelling",
}
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class ExportCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class ExportOptions:
    time_partition: str = "consolidated"
    technology_partition: str = "combined"
    include_vips: bool = True

    @classmethod
    def from_value(cls, value: dict | None) -> "ExportOptions":
        value = value or {}
        time_partition = str(value.get("time_partition", "consolidated"))
        technology_partition = str(value.get("technology_partition", "combined"))
        if time_partition not in {"consolidated", "daily"}:
            raise ValueError("time_partition deve ser consolidated ou daily")
        if technology_partition not in {"combined", "family", "collector"}:
            raise ValueError(
                "technology_partition deve ser combined, family ou collector"
            )
        include_vips = value.get("include_vips", True)
        if not isinstance(include_vips, bool):
            raise ValueError("include_vips deve ser booleano")
        return cls(time_partition, technology_partition, include_vips)

    def to_dict(self) -> dict:
        return {
            "time_partition": self.time_partition,
            "technology_partition": self.technology_partition,
            "include_vips": self.include_vips,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower() or "evento"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        _replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_with_retry(source: Path, destination: Path) -> None:
    """Tolera bloqueios breves do antivírus/índice de busca no Windows."""
    last_error = None
    for attempt in range(8):
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.01 * (attempt + 1))
    raise last_error


def _time_fields(value) -> dict:
    raw = "" if value is None else str(value).strip()
    if not raw:
        return {"utc": "", "brasilia": "", "date": "", "valid": False}
    try:
        normalized = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        utc_value = parsed.astimezone(timezone.utc)
        brasilia = utc_value.astimezone(BRASILIA)
        return {
            "utc": utc_value.isoformat().replace("+00:00", "Z"),
            "brasilia": brasilia.isoformat(),
            "date": brasilia.date().isoformat(),
            "valid": True,
        }
    except (TypeError, ValueError, OverflowError):
        return {"utc": raw, "brasilia": "", "date": "", "valid": False}


def _technology_family(value) -> str:
    token = str(value or "").upper().replace("-", "_").replace(" ", "_")
    if token in {"4G", "LTE"}:
        return "4G"
    if token in {"5G", "NR", "NRCELL", "NRDUCELL", "5G_NRCELL", "5G_NRDUCELL"}:
        return "5G"
    return "unknown"


def _collector_technology(value) -> str:
    token = str(value or "").upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "LTE": "4G", "4G": "4G", "5G": "5G", "NR": "5G",
        "NRCELL": "5G_NRCELL", "5G_NRCELL": "5G_NRCELL",
        "NRDUCELL": "5G_NRDUCELL", "5G_NRDUCELL": "5G_NRDUCELL",
    }
    return aliases.get(token, "unknown")


def _safe_csv_value(value) -> tuple[object, bool]:
    if value is None:
        return "", False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value, False
    if isinstance(value, bool):
        return "true" if value else "false", False
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text, True
    return text, False


class _CsvSinks:
    """Escritores CSV com LRU para não manter centenas de dias abertos."""

    def __init__(self, root: Path, max_open: int = 24):
        self.root = root
        self.max_open = max_open
        self._open: OrderedDict[str, tuple[object, csv.writer]] = OrderedDict()
        self._meta: dict[str, dict] = {}

    def write(self, relative: str, headers: list[str], row: dict) -> None:
        handle, writer = self._writer(relative, headers)
        cells = []
        neutralized = 0
        for header in headers:
            cell, changed = _safe_csv_value(row.get(header))
            cells.append(cell)
            neutralized += int(changed)
        writer.writerow(cells)
        meta = self._meta[relative]
        meta["rows"] += 1
        meta["neutralized_cells"] += neutralized

    def ensure(self, relative: str, headers: list[str]) -> None:
        self._writer(relative, headers)

    def _writer(self, relative: str, headers: list[str]):
        if relative in self._open:
            pair = self._open.pop(relative)
            self._open[relative] = pair
            return pair
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        first = relative not in self._meta
        handle = path.open("w" if first else "a", encoding="utf-8-sig", newline="")
        writer = csv.writer(handle, lineterminator="\n")
        if first:
            writer.writerow(headers)
            self._meta[relative] = {
                "rows": 0,
                "neutralized_cells": 0,
                "headers": list(headers),
            }
        self._open[relative] = (handle, writer)
        if len(self._open) > self.max_open:
            _, (old_handle, _) = self._open.popitem(last=False)
            old_handle.close()
        return handle, writer

    def close(self) -> None:
        while self._open:
            _, (handle, _) = self._open.popitem(last=False)
            handle.close()

    @property
    def metadata(self) -> dict[str, dict]:
        return copy.deepcopy(self._meta)


class EventExportService:
    def __init__(self, export_root: Path | None = None, output_dir: Path | None = None):
        self.export_root = Path(export_root or event_exports_dir())
        self.jobs_dir = self.export_root / "jobs"
        self.staging_dir = self.export_root / ".staging"
        self.output_dir = Path(output_dir) if output_dir else None
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="event-export")
        self._load_jobs()
        self._cleanup_orphans()

    def _load_jobs(self) -> None:
        for path in self.jobs_dir.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("status") in TERMINAL_STATES:
                    self._jobs[record["job_id"]] = record
            except Exception:
                continue

    def _cleanup_orphans(self) -> None:
        for path in self.staging_dir.iterdir():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)

    def preview(self, event_id: str, options: dict | None = None) -> dict:
        parsed = ExportOptions.from_value(options)
        event = db.get_event(event_id)
        if not event:
            raise ValueError("Evento não encontrado")
        event_conn = db.get_event_conn(event_id)
        global_conn = db.get_conn()
        counts = {
            "kpis": event_conn.execute(
                "SELECT COUNT(*) FROM kpi_measurements WHERE event_id = ?", (event_id,)
            ).fetchone()[0],
            "vips": global_conn.execute(
                "SELECT COUNT(*) FROM vip_measurements WHERE event_id = ?", (event_id,)
            ).fetchone()[0] if parsed.include_vips else 0,
            "alarms": event_conn.execute(
                "SELECT COUNT(*) FROM alarms WHERE event_id = ?", (event_id,)
            ).fetchone()[0],
            "alerts": event_conn.execute(
                "SELECT COUNT(*) FROM alerts WHERE event_id = ?", (event_id,)
            ).fetchone()[0],
        }
        periods = []
        period_sources = [
            (event_conn, "kpi_measurements", "timestamp"),
            (event_conn, "alarms", "collected_at"),
            (event_conn, "alerts", "timestamp"),
        ]
        if parsed.include_vips:
            period_sources.append((global_conn, "vip_measurements", "timestamp"))
        for conn, table, column in period_sources:
            row = conn.execute(
                f"SELECT MIN({column}), MAX({column}) FROM {table} WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            periods.extend(value for value in row if value)
        technologies = [
            row[0] or "unknown" for row in event_conn.execute(
                "SELECT DISTINCT technology FROM kpi_measurements "
                "WHERE event_id = ? ORDER BY technology", (event_id,)
            ).fetchall()
        ]
        normalized_periods = [_time_fields(value) for value in periods]
        valid_periods = [item for item in normalized_periods if item["valid"]]
        minimum = min(valid_periods, key=lambda item: item["utc"]) if valid_periods else None
        maximum = max(valid_periods, key=lambda item: item["utc"]) if valid_periods else None
        covered_days = 0
        if minimum and maximum:
            first_day = datetime.fromisoformat(minimum["date"]).date()
            last_day = datetime.fromisoformat(maximum["date"]).date()
            covered_days = (last_day - first_day).days + 1
        estimated = sum(counts.values()) * 190
        return {
            "event_id": event_id,
            "event_name": event.get("name", event_id),
            "event_status": event.get("status", ""),
            "counts": counts,
            "period": {
                "min_utc": minimum["utc"] if minimum else None,
                "min_brasilia": minimum["brasilia"] if minimum else None,
                "max_utc": maximum["utc"] if maximum else None,
                "max_brasilia": maximum["brasilia"] if maximum else None,
                "covered_days": covered_days,
            },
            "technologies": technologies,
            "estimated_bytes": estimated,
            "excel_row_warning": (
                parsed.time_partition == "consolidated" and counts["kpis"] > 1_048_575
            ),
            "ep_source_quality": self._ep_quality(event),
            "timezone": "America/Sao_Paulo",
            "options": parsed.to_dict(),
        }

    def start(self, event_id: str, options: dict | None = None) -> dict:
        parsed = ExportOptions.from_value(options)
        event = db.get_event(event_id)
        if not event:
            raise ValueError("Evento não encontrado")
        # Garante que os arquivos e schemas existem antes de sair da thread da API.
        db.get_event_conn(event_id)
        db.get_conn()
        with self._lock:
            for existing in self._jobs.values():
                if existing.get("event_id") == event_id and existing.get("status") in ACTIVE_STATES:
                    raise ValueError("Já existe uma exportação em andamento para este evento")
            job_id = uuid.uuid4().hex
            record = {
                "job_id": job_id,
                "event_id": event_id,
                "event_name": event.get("name", event_id),
                "status": "queued",
                "dataset": "",
                "message": "Exportação na fila",
                "current": 0,
                "total": 0,
                "percent": 0,
                "options": parsed.to_dict(),
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
                "result": None,
                "error": None,
            }
            self._jobs[job_id] = record
            self._cancel[job_id] = threading.Event()
            self._persist(record)
            self._executor.submit(self._run, job_id, event_id, parsed)
            return self.status(job_id)

    def status(self, job_id: str) -> dict:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                raise ValueError("Exportação não encontrada")
            return copy.deepcopy(record)

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                raise ValueError("Exportação não encontrada")
            if record["status"] in TERMINAL_STATES:
                return copy.deepcopy(record)
            self._cancel[job_id].set()
            self._set(job_id, status="cancelling", message="Cancelando exportação")
            return copy.deepcopy(self._jobs[job_id])

    def latest_for_event(self, event_id: str) -> dict | None:
        with self._lock:
            candidates = [
                item for item in self._jobs.values()
                if item.get("event_id") == event_id and item.get("status") == "ready"
                and Path((item.get("result") or {}).get("path", "")).is_file()
            ]
            if not candidates:
                return None
            return copy.deepcopy(max(candidates, key=lambda item: item.get("updated_at", "")))

    def _persist(self, record: dict) -> None:
        _write_atomic_json(self.jobs_dir / f"{record['job_id']}.json", record)

    def _set(self, job_id: str, **changes) -> None:
        with self._lock:
            record = self._jobs[job_id]
            record.update(changes)
            record["updated_at"] = _utc_now()
            if record.get("total"):
                record["percent"] = min(
                    99 if record["status"] not in TERMINAL_STATES else 100,
                    int(record.get("current", 0) * 100 / record["total"]),
                )
            self._persist(record)

    def _check_cancel(self, job_id: str) -> None:
        if self._cancel[job_id].is_set():
            raise ExportCancelled("Exportação cancelada")

    def _backup(self, job_id: str, source: Path, destination: Path) -> None:
        source_conn = sqlite3.connect(str(source), timeout=30)
        target_conn = sqlite3.connect(str(destination), timeout=30)
        try:
            def progress(_status, _remaining, _total):
                self._check_cancel(job_id)
            source_conn.backup(target_conn, pages=1024, progress=progress, sleep=0.01)
        finally:
            target_conn.close()
            source_conn.close()

    def _run(self, job_id: str, event_id: str, options: ExportOptions) -> None:
        staging = self.staging_dir / job_id
        try:
            staging.mkdir(parents=True, exist_ok=False)
            self._set(job_id, status="snapshotting", message="Preparando snapshot")
            snapshot_started = _utc_now()
            event_snapshot = staging / "event.db"
            global_snapshot = staging / "global.db"
            self._backup(job_id, db.get_event_db_path(event_id), event_snapshot)
            event_snapshot_at = _utc_now()
            self._backup(job_id, db.DB_PATH, global_snapshot)
            global_snapshot_at = _utc_now()
            self._check_cancel(job_id)

            event_conn = sqlite3.connect(str(event_snapshot))
            global_conn = sqlite3.connect(str(global_snapshot))
            event_conn.row_factory = sqlite3.Row
            global_conn.row_factory = sqlite3.Row
            try:
                event = self._read_event(global_conn, event_id, event_conn)
                counts = self._counts(event_conn, global_conn, event_id, options)
                total = max(1, sum(counts.values()))
                self._set(job_id, total=total, current=0)
                package_root = staging / "package"
                package_root.mkdir()
                sinks = _CsvSinks(package_root)
                warnings: list[dict] = []
                try:
                    self._export_metadata(job_id, sinks, package_root, event, warnings)
                    kpi_stats = self._export_kpis(
                        job_id, sinks, event_conn, event_id, event, options, warnings
                    )
                    self._export_vips(
                        job_id, sinks, global_conn, event_conn,
                        event_id, options, warnings
                    )
                    self._export_alarms(job_id, sinks, event_conn, event_id, warnings)
                    self._export_alerts(job_id, sinks, event_conn, event_id, warnings)
                    self._export_checkpoints(job_id, sinks, event_conn, event_id, warnings)
                finally:
                    sinks.close()

                self._check_cancel(job_id)
                summary = {
                    "event_id": event_id,
                    "counts": counts,
                    "kpi": kpi_stats,
                    "timezone": "America/Sao_Paulo",
                    "warnings": warnings,
                }
                summary_path = package_root / "auditoria" / "resumo.json"
                summary_path.parent.mkdir(parents=True, exist_ok=True)
                summary_path.write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
                )

                files = self._file_manifest(package_root, sinks.metadata)
                manifest = {
                    "schema_version": SCHEMA_VERSION,
                    "package_kind": "smart-events-data-export",
                    "event_id": event_id,
                    "event_name": event.get("name", event_id),
                    "event_status": event.get("status", ""),
                    "timezone": "America/Sao_Paulo",
                    "data_scope": "persisted_event_data",
                    "options": options.to_dict(),
                    "snapshot_started_at": snapshot_started,
                    "event_db_snapshot_at": event_snapshot_at,
                    "global_db_snapshot_at": global_snapshot_at,
                    "generated_at": _utc_now(),
                    "counts": counts,
                    "periods": kpi_stats.get("period", {}),
                    "ep_source_quality": self._ep_quality(event),
                    "files": files,
                    "warnings": warnings,
                }
                (package_root / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8",
                )

                self._set(job_id, status="packing", dataset="", message="Compactando arquivos")
                destination_dir = self.output_dir or self._configured_output_dir()
                destination_dir.mkdir(parents=True, exist_ok=True)
                destination = self._unique_destination(destination_dir, event)
                temporary_zip = destination.parent / f".{destination.name}.{job_id}.tmp"
                try:
                    with zipfile.ZipFile(
                        temporary_zip, "w", zipfile.ZIP_DEFLATED,
                        compresslevel=6, allowZip64=True,
                    ) as archive:
                        for path in sorted(package_root.rglob("*")):
                            if path.is_file():
                                self._check_cancel(job_id)
                                archive.write(path, path.relative_to(package_root).as_posix())
                    self._check_cancel(job_id)
                    self._set(job_id, status="validating", message="Validando pacote")
                    self._validate_zip(temporary_zip, manifest)
                    self._check_cancel(job_id)
                    _replace_with_retry(temporary_zip, destination)
                finally:
                    temporary_zip.unlink(missing_ok=True)

                result = {
                    "path": str(destination),
                    "filename": destination.name,
                    "size_bytes": destination.stat().st_size,
                    "sha256": _sha256_file(destination),
                    "records": sum(counts.values()),
                    "counts": counts,
                }
                shutil.rmtree(staging, ignore_errors=True)
                self._set(
                    job_id, status="ready", message="Exportação concluída",
                    current=total, percent=100, result=result, error=None,
                )
            finally:
                event_conn.close()
                global_conn.close()
        except ExportCancelled:
            shutil.rmtree(staging, ignore_errors=True)
            self._set(
                job_id, status="cancelled", message="Exportação cancelada",
                percent=0, result=None, error=None,
            )
        except Exception as exc:
            logger.exception(
                "Falha ao exportar os dados do evento %s no job %s", event_id, job_id
            )
            shutil.rmtree(staging, ignore_errors=True)
            self._set(
                job_id, status="failed", message="Não foi possível exportar os dados",
                result=None,
                error="O arquivo não pôde ser gerado. Consulte os logs do aplicativo.",
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            self._cancel.pop(job_id, None)

    def _configured_output_dir(self) -> Path:
        override = os.environ.get("SMARTEVENTS_EXPORT_OUTPUT_DIR", "").strip()
        return Path(override).expanduser().resolve() if override else downloads_dir()

    @staticmethod
    def _read_event(
        conn: sqlite3.Connection,
        event_id: str,
        event_conn: sqlite3.Connection | None = None,
    ) -> dict:
        row = conn.execute(
            "SELECT config_json, status FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        if not row:
            raise ValueError("Evento não encontrado no snapshot")
        event = json.loads(row["config_json"])
        event["status"] = row["status"]
        if not event.get("sites") and event_conn is not None:
            sites = []
            for site_row in event_conn.execute(
                "SELECT id, name, lat, lng, is_event_site, cells_json "
                "FROM sites WHERE event_id = ? ORDER BY id", (event_id,)
            ).fetchall():
                try:
                    cells = json.loads(site_row["cells_json"] or "[]")
                except (TypeError, ValueError):
                    cells = []
                sites.append({
                    "id": site_row["id"],
                    "name": site_row["name"],
                    "lat": site_row["lat"],
                    "lng": site_row["lng"],
                    "is_event_site": bool(site_row["is_event_site"]),
                    "cells": cells,
                })
            event["sites"] = sites
        return event

    @staticmethod
    def _counts(event_conn, global_conn, event_id, options) -> dict:
        def count(conn, table):
            return conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE event_id = ?", (event_id,)
            ).fetchone()[0]
        return {
            "kpis": count(event_conn, "kpi_measurements"),
            "vips": count(global_conn, "vip_measurements") if options.include_vips else 0,
            "alarms": count(event_conn, "alarms"),
            "alerts": count(event_conn, "alerts"),
        }

    @staticmethod
    def _ep_quality(event: dict) -> str:
        sites = event.get("sites") or []
        cells = [cell for site in sites for cell in (site.get("cells") or [])]
        return "preserved" if sites and all(site.get("ep") for site in sites) \
            and cells and all(isinstance(cell, dict) and cell.get("ep") for cell in cells) \
            else "reconstructed"

    @staticmethod
    def _sanitized_event(event: dict) -> dict:
        sensitive = {
            "_config_digest", "api_key", "authorization", "base_url", "cookie",
            "cookies", "import_folder", "password", "secret", "token", "username",
        }

        def clean(value):
            if isinstance(value, dict):
                return {
                    key: clean(item)
                    for key, item in value.items()
                    if str(key).casefold() not in sensitive
                }
            if isinstance(value, list):
                return [clean(item) for item in value]
            return copy.deepcopy(value)

        return clean(event)

    @staticmethod
    def _cluster_lookup(event: dict) -> dict[str, list[str]]:
        lookup: dict[str, list[str]] = {}
        for cluster in event.get("clusters") or []:
            name = str(cluster.get("name") or cluster.get("id") or "")
            for site_id in cluster.get("site_ids") or []:
                lookup.setdefault(str(site_id), []).append(name)
        return lookup

    def _ep_rows(self, event: dict) -> tuple[list[dict], dict[tuple[str, str], dict]]:
        clusters = self._cluster_lookup(event)
        rows = []
        lookup = {}
        quality = self._ep_quality(event)
        for site in event.get("sites") or []:
            site_ep = site.get("ep") if isinstance(site.get("ep"), dict) else {}
            for cell_value in site.get("cells") or []:
                cell = cell_value if isinstance(cell_value, dict) else {"id": cell_value}
                cell_ep = cell.get("ep") if isinstance(cell.get("ep"), dict) else {}
                row = {
                    "source_row": cell_ep.get("source_row") or site_ep.get("source_row"),
                    "enodebid": site_ep.get("enodebid", site.get("id")),
                    "nename": site_ep.get("nename", site.get("name")),
                    "cellid": cell_ep.get("cellid", cell.get("id")),
                    "cellname": cell_ep.get("cellname", cell.get("id")),
                    "latitude": site_ep.get("latitude", site.get("lat")),
                    "longitude": site_ep.get("longitude", site.get("lng")),
                    "azimuth": cell_ep.get("azimuth", cell.get("azimuth")),
                    "technology": cell_ep.get("technology", cell.get("tech")),
                    "frequency_mhz": cell_ep.get("band", cell.get("frequency") or cell.get("freq")),
                    "dlearfcn": cell_ep.get("dlearfcn", cell.get("earfcn")),
                    "is_event_site": site.get("is_event_site", True),
                    "clusters": ";".join(sorted(clusters.get(str(site.get("id")), []))),
                    "source_quality": quality,
                }
                rows.append(row)
                lookup[(str(site.get("id")), str(cell.get("id")))] = {
                    **row,
                    "site_name": site.get("name"),
                    "cell_id": cell.get("id"),
                    "technology_ep": cell.get("tech"),
                }
        return rows, lookup

    def _export_metadata(self, job_id, sinks, root, event, warnings):
        self._set(job_id, status="exporting_metadata", dataset="metadata",
                  message="Exportando informações do evento")
        metadata = root / "metadados"
        metadata.mkdir(parents=True, exist_ok=True)
        (metadata / "evento.json").write_text(
            json.dumps(self._sanitized_event(event), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ep_rows, _ = self._ep_rows(event)
        ep_headers = [
            "source_row", "enodebid", "nename", "cellid", "cellname", "latitude",
            "longitude", "azimuth", "technology", "frequency_mhz", "dlearfcn",
            "is_event_site", "clusters", "source_quality",
        ]
        sinks.ensure("metadados/ep_normalizada.csv", ep_headers)
        for row in ep_rows:
            sinks.write("metadados/ep_normalizada.csv", ep_headers, row)

        site_headers = ["site_id", "name", "latitude", "longitude", "is_event_site"]
        cell_headers = [
            "site_id", "cell_id", "azimuth", "beamwidth", "technology",
            "frequency_mhz", "earfcn", "obj_no",
        ]
        sinks.ensure("metadados/sites.csv", site_headers)
        sinks.ensure("metadados/celulas.csv", cell_headers)
        for site in event.get("sites") or []:
            sinks.write("metadados/sites.csv", site_headers, {
                "site_id": site.get("id"), "name": site.get("name"),
                "latitude": site.get("lat"), "longitude": site.get("lng"),
                "is_event_site": site.get("is_event_site", True),
            })
            for raw_cell in site.get("cells") or []:
                cell = raw_cell if isinstance(raw_cell, dict) else {"id": raw_cell}
                sinks.write("metadados/celulas.csv", cell_headers, {
                    "site_id": site.get("id"), "cell_id": cell.get("id"),
                    "azimuth": cell.get("azimuth"), "beamwidth": cell.get("beamwidth"),
                    "technology": cell.get("tech"),
                    "frequency_mhz": cell.get("frequency") or cell.get("freq"),
                    "earfcn": cell.get("earfcn"), "obj_no": cell.get("obj_no"),
                })

        cluster_headers = ["cluster_id", "name", "color", "site_ids"]
        sinks.ensure("metadados/clusters.csv", cluster_headers)
        for cluster in event.get("clusters") or []:
            sinks.write("metadados/clusters.csv", cluster_headers, {
                "cluster_id": cluster.get("id"), "name": cluster.get("name"),
                "color": cluster.get("color"),
                "site_ids": ";".join(map(str, cluster.get("site_ids") or [])),
            })

        dictionary_headers = ["dataset", "column", "description"]
        sinks.ensure("metadados/dicionario_de_dados.csv", dictionary_headers)
        for row in self._dictionary_rows():
            sinks.write("metadados/dicionario_de_dados.csv", dictionary_headers, row)
        (root / "LEIA-ME.txt").write_text(self._readme_text(), encoding="utf-8")

    @staticmethod
    def _dictionary_rows() -> list[dict]:
        return [
            {"dataset": "kpis", "column": "value_stored", "description": "Valor exato persistido no SQLite."},
            {"dataset": "kpis", "column": "value", "description": "Valor convertido para a unidade canônica."},
            {"dataset": "kpis", "column": "technology", "description": "Tecnologia persistida pela coleta."},
            {"dataset": "kpis", "column": "technology_family", "description": "Família 4G, 5G ou unknown."},
            {"dataset": "todos", "column": "timestamp_utc", "description": "Instante normalizado em UTC."},
            {"dataset": "todos", "column": "timestamp_brasilia", "description": "Instante no horário de Brasília."},
            {"dataset": "ep", "column": "source_quality", "description": "preserved ou reconstructed."},
        ]

    @staticmethod
    def _readme_text() -> str:
        return (
            "EXPORTAÇÃO DE DADOS — SMART EVENTS\n\n"
            "Este pacote contém todos os dados persistidos do evento no snapshot. "
            "Respostas brutas do OSS que não foram persistidas não podem ser recuperadas.\n"
            "Datas locais e divisões por dia usam America/Sao_Paulo (horário de Brasília).\n"
            "manifest.json contém contagens, hashes SHA-256, opções e avisos.\n"
            "Os CSVs usam UTF-8 com BOM, vírgula e cabeçalho.\n"
        )

    @staticmethod
    def _kpi_path(options: ExportOptions, day: str, technology: str) -> str:
        date_part = day if day else "data-invalida"
        if options.technology_partition == "combined":
            tech_part = ""
        elif options.technology_partition == "family":
            tech_part = _technology_family(technology).lower()
        else:
            tech_part = _collector_technology(technology).lower()
        if options.time_partition == "consolidated" and not tech_part:
            return "dados/kpis.csv"
        if options.time_partition == "consolidated":
            return f"dados/kpis/kpis_{tech_part}.csv"
        if not tech_part:
            return f"dados/kpis/kpis_{date_part}.csv"
        return f"dados/kpis/{date_part}/kpis_{tech_part}.csv"

    def _export_kpis(self, job_id, sinks, conn, event_id, event, options, warnings):
        self._set(job_id, status="exporting_kpis", dataset="kpis", message="Exportando KPIs")
        headers = [
            "timestamp_utc", "timestamp_brasilia", "data_brasilia", "event_id",
            "site_id", "site_name_ep", "cell_id", "cell_id_ep", "cell_name_ep",
            "metric", "value_stored", "value", "unit", "oss_unit",
            "conversion_applied", "scope", "technology", "technology_family",
            "technology_ep", "frequency_mhz", "earfcn", "is_event_site",
        ]
        _, ep_lookup = self._ep_rows(event)
        cursor = conn.execute(
            "SELECT id, site_id, cell_id, event_id, timestamp, metric, value, scope, technology "
            "FROM kpi_measurements WHERE event_id = ? "
            "ORDER BY timestamp, technology, site_id, cell_id, metric, id", (event_id,)
        )
        invalid_timestamps = 0
        unmapped = set()
        minimum = None
        maximum = None
        emitted = 0
        while True:
            batch = cursor.fetchmany(5000)
            if not batch:
                break
            self._check_cancel(job_id)
            for source in batch:
                times = _time_fields(source["timestamp"])
                if not times["valid"]:
                    invalid_timestamps += 1
                else:
                    minimum = times["utc"] if minimum is None else min(minimum, times["utc"])
                    maximum = times["utc"] if maximum is None else max(maximum, times["utc"])
                ep = ep_lookup.get((str(source["site_id"]), str(source["cell_id"])), {})
                if not ep and source["cell_id"] not in {"__site__", "__all__"}:
                    unmapped.add(f"{source['site_id']}::{source['cell_id']}")
                stored = source["value"]
                canonical = stored
                item = definition(source["metric"], source["technology"])
                if stored is not None:
                    canonical = to_canonical(source["metric"], source["technology"], stored)
                path = self._kpi_path(options, times["date"], source["technology"])
                sinks.write(path, headers, {
                    "timestamp_utc": times["utc"],
                    "timestamp_brasilia": times["brasilia"],
                    "data_brasilia": times["date"],
                    "event_id": source["event_id"], "site_id": source["site_id"],
                    "site_name_ep": ep.get("nename") or ep.get("site_name"),
                    "cell_id": source["cell_id"], "cell_id_ep": ep.get("cellid"),
                    "cell_name_ep": ep.get("cellname"), "metric": source["metric"],
                    "value_stored": stored, "value": canonical,
                    "unit": item.base_unit if item else "",
                    "oss_unit": item.unit if item else "",
                    "conversion_applied": bool(item and item.to_base != 1.0),
                    "scope": source["scope"], "technology": source["technology"],
                    "technology_family": _technology_family(source["technology"]),
                    "technology_ep": ep.get("technology_ep"),
                    "frequency_mhz": ep.get("frequency_mhz"),
                    "earfcn": ep.get("dlearfcn"),
                    "is_event_site": ep.get("is_event_site"),
                })
                emitted += 1
            self._advance(job_id, len(batch), "kpis")
        if emitted == 0:
            sinks.ensure("dados/kpis.csv", headers)
        if invalid_timestamps:
            warnings.append({"code": "invalid_kpi_timestamps", "count": invalid_timestamps})
        if unmapped:
            warnings.append({"code": "kpi_cells_without_ep", "count": len(unmapped)})
        return {"rows": emitted, "period": {"min": minimum, "max": maximum}}

    def _export_vips(
        self, job_id, sinks, conn, event_conn, event_id, options, warnings
    ):
        headers = [
            "timestamp_utc", "timestamp_brasilia", "data_brasilia", "event_id",
            "vip_id", "vip_name", "task_id", "serial_no", "serving_cell",
            "rsrp", "rsrq", "in_event",
        ]
        sinks.ensure("dados/vips.csv", headers)
        if not options.include_vips:
            return
        self._set(job_id, status="exporting_vips", dataset="vips", message="Exportando VIPs")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(vip_measurements)")}
        vip_expr = "vip_id" if "vip_id" in columns else "NULL AS vip_id"
        vip_entities = {
            row["id"]: row for row in conn.execute("SELECT id, name FROM vips").fetchall()
        }
        candidates = {}
        for association in event_conn.execute(
            "SELECT vip_id, task_id FROM event_vips WHERE event_id = ?", (event_id,)
        ).fetchall():
            vip = vip_entities.get(association["vip_id"])
            if not vip:
                continue
            candidates.setdefault((association["task_id"], vip["name"]), set()).add(vip["id"])
            candidates.setdefault((None, vip["name"]), set()).add(vip["id"])
        resolved = {
            key: next(iter(values)) for key, values in candidates.items() if len(values) == 1
        }
        cursor = conn.execute(
            f"SELECT {vip_expr}, vip_name, event_id, task_id, serial_no, timestamp, "
            "serving_cell, rsrp, rsrq, in_event FROM vip_measurements "
            "WHERE event_id = ? ORDER BY timestamp, vip_name, id", (event_id,)
        )
        invalid = 0
        unresolved_identity = 0
        for batch in self._batches(cursor):
            self._check_cancel(job_id)
            for source in batch:
                times = _time_fields(source["timestamp"])
                invalid += int(not times["valid"])
                vip_id = source["vip_id"] or resolved.get(
                    (source["task_id"], source["vip_name"])
                ) or resolved.get((None, source["vip_name"]))
                unresolved_identity += int(not vip_id)
                sinks.write("dados/vips.csv", headers, {
                    "timestamp_utc": times["utc"], "timestamp_brasilia": times["brasilia"],
                    "data_brasilia": times["date"], "event_id": source["event_id"],
                    "vip_id": vip_id, "vip_name": source["vip_name"],
                    "task_id": source["task_id"], "serial_no": source["serial_no"],
                    "serving_cell": source["serving_cell"], "rsrp": source["rsrp"],
                    "rsrq": source["rsrq"], "in_event": bool(source["in_event"]),
                })
            self._advance(job_id, len(batch), "vips")
        if invalid:
            warnings.append({"code": "invalid_vip_timestamps", "count": invalid})
        if unresolved_identity:
            warnings.append({
                "code": "unresolved_legacy_vip_identity",
                "count": unresolved_identity,
            })

    def _export_alarms(self, job_id, sinks, conn, event_id, warnings):
        self._set(job_id, status="exporting_alarms", dataset="alarms", message="Exportando alarmes")
        raw_headers = [
            "csn", "event_id", "alarm_id", "alarm_group_id", "alarm_name", "severity",
            "source", "ip", "location", "occur_time", "arrive_time", "additional_info",
            "collected_at",
        ]
        headers = raw_headers + ["collected_at_utc", "collected_at_brasilia", "data_brasilia"]
        sinks.ensure("dados/alarmes.csv", headers)
        cursor = conn.execute(
            "SELECT * FROM alarms WHERE event_id = ? ORDER BY arrive_time, csn", (event_id,)
        )
        invalid = 0
        for batch in self._batches(cursor):
            self._check_cancel(job_id)
            for source in batch:
                row = dict(source)
                times = _time_fields(source["collected_at"])
                invalid += int(not times["valid"])
                row.update({"collected_at_utc": times["utc"],
                            "collected_at_brasilia": times["brasilia"],
                            "data_brasilia": times["date"]})
                sinks.write("dados/alarmes.csv", headers, row)
            self._advance(job_id, len(batch), "alarms")
        if invalid:
            warnings.append({"code": "invalid_alarm_timestamps", "count": invalid})

    def _export_alerts(self, job_id, sinks, conn, event_id, warnings):
        self._set(job_id, status="exporting_alerts", dataset="alerts", message="Exportando alertas")
        raw_headers = [
            "id", "event_id", "level", "severity", "site_id", "cell_id", "message",
            "timestamp", "acknowledged",
        ]
        headers = raw_headers + ["timestamp_utc", "timestamp_brasilia", "data_brasilia"]
        sinks.ensure("dados/alertas.csv", headers)
        cursor = conn.execute(
            "SELECT * FROM alerts WHERE event_id = ? ORDER BY timestamp, id", (event_id,)
        )
        invalid = 0
        for batch in self._batches(cursor):
            self._check_cancel(job_id)
            for source in batch:
                row = dict(source)
                row["acknowledged"] = bool(row.get("acknowledged"))
                times = _time_fields(source["timestamp"])
                invalid += int(not times["valid"])
                row.update({"timestamp_utc": times["utc"],
                            "timestamp_brasilia": times["brasilia"],
                            "data_brasilia": times["date"]})
                sinks.write("dados/alertas.csv", headers, row)
            self._advance(job_id, len(batch), "alerts")
        if invalid:
            warnings.append({"code": "invalid_alert_timestamps", "count": invalid})

    def _export_checkpoints(self, job_id, sinks, conn, event_id, warnings):
        headers = [
            "event_id", "oss", "collector", "task_id", "object_key", "cursor",
            "updated_at", "updated_at_utc", "updated_at_brasilia",
        ]
        sinks.ensure("auditoria/checkpoints_de_coleta.csv", headers)
        rows = conn.execute(
            "SELECT * FROM collection_checkpoints WHERE event_id = ? "
            "ORDER BY oss, collector, task_id, object_key", (event_id,)
        ).fetchall()
        invalid = 0
        for source in rows:
            row = dict(source)
            times = _time_fields(source["updated_at"])
            invalid += int(not times["valid"])
            row.update({"updated_at_utc": times["utc"],
                        "updated_at_brasilia": times["brasilia"]})
            sinks.write("auditoria/checkpoints_de_coleta.csv", headers, row)
        if invalid:
            warnings.append({"code": "invalid_checkpoint_timestamps", "count": invalid})

    @staticmethod
    def _batches(cursor, size=5000) -> Iterator[list[sqlite3.Row]]:
        while True:
            batch = cursor.fetchmany(size)
            if not batch:
                return
            yield batch

    def _advance(self, job_id: str, amount: int, dataset: str) -> None:
        with self._lock:
            current = self._jobs[job_id].get("current", 0) + amount
        self._set(job_id, current=current, dataset=dataset)

    @staticmethod
    def _file_manifest(root: Path, csv_meta: dict) -> dict:
        files = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name == "manifest.json":
                continue
            relative = path.relative_to(root).as_posix()
            info = {
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
            if relative in csv_meta:
                info.update({
                    "rows": csv_meta[relative]["rows"],
                    "neutralized_cells": csv_meta[relative]["neutralized_cells"],
                })
            files[relative] = info
        return files

    @staticmethod
    def _validate_zip(path: Path, manifest: dict) -> None:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("ZIP contém entradas duplicadas")
            if "manifest.json" not in names:
                raise ValueError("ZIP sem manifest.json")
            for name in names:
                pure = Path(name)
                if pure.is_absolute() or ".." in pure.parts or "\\" in name:
                    raise ValueError(f"Caminho inseguro no ZIP: {name}")
            loaded = json.loads(archive.read("manifest.json").decode("utf-8"))
            if loaded.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("Versão do manifesto inválida")
            expected_names = set(loaded.get("files") or {}) | {"manifest.json"}
            if set(names) != expected_names:
                raise ValueError("Conteúdo do ZIP diverge do manifesto")
            csv_rows = {}
            for name, expected in loaded["files"].items():
                digest = hashlib.sha256()
                with archive.open(name) as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != expected["sha256"]:
                    raise ValueError(f"Hash inválido para {name}")
                if "rows" in expected:
                    with archive.open(name) as raw:
                        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
                        rows = max(0, sum(1 for _ in csv.reader(text)) - 1)
                    if rows != expected["rows"]:
                        raise ValueError(f"Contagem de linhas inválida para {name}")
                    csv_rows[name] = rows

            counts = loaded.get("counts") or manifest.get("counts") or {}
            kpi_rows = sum(
                rows for name, rows in csv_rows.items()
                if name == "dados/kpis.csv" or name.startswith("dados/kpis/")
            )
            dataset_rows = {
                "kpis": kpi_rows,
                "vips": csv_rows.get("dados/vips.csv", 0),
                "alarms": csv_rows.get("dados/alarmes.csv", 0),
                "alerts": csv_rows.get("dados/alertas.csv", 0),
            }
            if any(dataset_rows[key] != int(counts.get(key, 0)) for key in dataset_rows):
                raise ValueError("Contagens dos conjuntos divergem do manifesto")

    @staticmethod
    def _unique_destination(directory: Path, event: dict) -> Path:
        stamp = datetime.now(timezone.utc).astimezone(BRASILIA).strftime("%Y%m%d-%H%M%S-bsb")
        base = f"smart-events_{_slug(event.get('id') or event.get('name'))}_{stamp}"
        candidate = directory / f"{base}.zip"
        suffix = 2
        while candidate.exists():
            candidate = directory / f"{base}-{suffix}.zip"
            suffix += 1
        return candidate


_default_lock = threading.Lock()
_default_service: EventExportService | None = None
_default_root: Path | None = None


def get_event_export_service() -> EventExportService:
    """Serviço lazy; acompanha DB_PATH monkeypatchado pelos testes."""
    global _default_service, _default_root
    root = db.DB_PATH.parent / "exports"
    with _default_lock:
        if _default_service is None or _default_root != root:
            _default_service = EventExportService(export_root=root)
            _default_root = root
        return _default_service
