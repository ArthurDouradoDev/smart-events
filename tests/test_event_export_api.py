from pathlib import Path
from types import SimpleNamespace

import api.api as api_module
from api.api import Api


class _Service:
    def __init__(self, path):
        self.path = path
        self.cancelled = False

    def preview(self, event_id, options):
        return {"event_id": event_id, "counts": {"kpis": 1}, "options": options}

    def start(self, event_id, options):
        return {"job_id": "job-1", "event_id": event_id, "status": "queued"}

    def status(self, job_id):
        return {"job_id": job_id, "status": "ready", "result": {"path": str(self.path)}}

    def cancel(self, job_id):
        self.cancelled = True
        return {"job_id": job_id, "status": "cancelled"}

    def latest_for_event(self, event_id):
        return {"job_id": "job-1", "event_id": event_id, "status": "ready"}


def test_export_api_routes_to_service(monkeypatch, tmp_path):
    artifact = tmp_path / "event.zip"
    artifact.write_bytes(b"zip")
    service = _Service(artifact)
    monkeypatch.setattr(api_module, "get_event_export_service", lambda: service)
    api = Api()

    assert api.preview_event_export("event-a", {"time_partition": "daily"})["ok"] is True
    started = api.start_event_export("event-a", {})
    assert started["job"]["job_id"] == "job-1"
    assert api.get_event_export_status("job-1")["job"]["status"] == "ready"
    assert api.cancel_event_export("job-1")["job"]["status"] == "cancelled"
    assert service.cancelled is True
    assert api.get_latest_event_export("event-a")["job"]["event_id"] == "event-a"


def test_open_folder_requires_ready_registered_file(monkeypatch):
    class Missing(_Service):
        def status(self, job_id):
            return {"job_id": job_id, "status": "failed", "result": None}

    monkeypatch.setattr(api_module, "get_event_export_service", lambda: Missing(Path("missing.zip")))
    result = Api().open_event_export_folder("job-1")
    assert result == {"ok": False, "error": "Arquivo exportado não está disponível."}


def test_export_api_returns_validation_error(monkeypatch):
    class Broken:
        def preview(self, *_args, **_kwargs):
            raise ValueError("opção inválida")

    monkeypatch.setattr(api_module, "get_event_export_service", lambda: Broken())
    result = Api().preview_event_export("event-a", {})
    assert result == {"ok": False, "error": "opção inválida"}


def test_export_api_sanitizes_internal_error(monkeypatch, tmp_path):
    class Broken:
        def preview(self, *_args, **_kwargs):
            raise RuntimeError(f"segredo em {tmp_path}")

    monkeypatch.setattr(api_module, "get_event_export_service", lambda: Broken())
    result = Api().preview_event_export("event-a", {})

    assert result == {"ok": False, "error": "Não foi possível preparar a exportação."}
    assert str(tmp_path) not in result["error"]
