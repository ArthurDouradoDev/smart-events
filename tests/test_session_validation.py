import json
from types import SimpleNamespace

import pytest

from core import database as db
from core.collector import HttpCollector
from core.session_renew import EXIT_SUCCESS, _probe_authenticated


class _CollectorResponse:
    def __init__(self, status, payload, content_type="application/json"):
        self.status_code = status
        self._payload = payload
        self.headers = {"Content-Type": content_type}
        self.url = "https://oss.example/probe"
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _CollectorSession:
    def __init__(self, response):
        self.response = response
        self.urls = []

    def get(self, url, **_kwargs):
        self.urls.append(url)
        return self.response


class _PlaywrightResponse:
    def __init__(self, url, status, payload, content_type="application/json"):
        self.url = url
        self.status = status
        self.headers = {"content-type": content_type}
        self._body = json.dumps(payload)

    def text(self):
        return self._body


class _PlaywrightRequest:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def get(self, url, **_kwargs):
        self.urls.append(url)
        response = next(item for pattern, item in self.responses if pattern in url)
        response.url = url
        return response


@pytest.fixture(autouse=True)
def _reset_renewal_state():
    HttpCollector.reset_interactive_state()
    yield
    HttpCollector.reset_interactive_state()


def _collector(sample_event, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_args, **_kwargs: [])
    collector = HttpCollector(sample_event, "https://oss.example")
    collector._session_file = tmp_path / "session_oss_example.json"
    return collector


def _write_session(path, monitoring_roarand="monitoring-new", trace_roarand="trace-new"):
    cookies = [
        {"name": "bspsession", "value": "session", "domain": "oss.example", "path": "/"},
        {"name": "roarand", "value": monitoring_roarand, "domain": "oss.example", "path": "/"},
    ]
    path.write_text(json.dumps({
        "monitoring": {
            "bspsession": "session", "roarand": monitoring_roarand, "cookies": cookies,
        },
        "trace": {
            "bspsession": "session", "roarand": trace_roarand, "cookies": cookies,
            "task_id": 14837,
        },
    }), encoding="utf-8")


def test_roarand_mudou_mas_probe_401_nao_e_sucesso_e_playwright_continua(
        sample_event, tmp_path, monkeypatch, caplog):
    collector = _collector(sample_event, tmp_path, monkeypatch)
    _write_session(collector._session_file)
    collector._session_built_roarand["monitoring"] = "monitoring-old"
    session = _CollectorSession(_CollectorResponse(401, {"error": "unauthorized"}))
    monkeypatch.setattr(collector, "_get_session", lambda _module: session)
    monkeypatch.setattr(db, "insert_alert", lambda *_args, **_kwargs: None)
    subprocess_calls = []
    monkeypatch.setattr(
        "core.collector.subprocess.run",
        lambda *args, **kwargs: subprocess_calls.append((args, kwargs))
        or SimpleNamespace(returncode=1, stdout="", stderr=""),
    )

    with caplog.at_level("INFO"):
        renewed = collector._renew_session("monitoring")

    assert renewed is False
    assert subprocess_calls, "o probe recusado deve continuar até o renovador Playwright"
    assert "arquivo recarregado" in caplog.text
    assert "probe recusado" in caplog.text
    assert "Playwright usado" in caplog.text


def test_probe_monitoring_http_200_json_valido_aceita_sessao_sem_playwright(
        sample_event, tmp_path, monkeypatch, caplog):
    collector = _collector(sample_event, tmp_path, monkeypatch)
    _write_session(collector._session_file)
    collector._session_built_roarand["monitoring"] = "monitoring-old"
    session = _CollectorSession(_CollectorResponse(200, {"success": True, "data": []}))
    monkeypatch.setattr(collector, "_get_session", lambda _module: session)
    monkeypatch.setattr(
        "core.collector.subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("Playwright não deveria ser usado"),
    )

    with caplog.at_level("INFO"):
        renewed = collector._renew_session("monitoring")

    assert renewed is True
    assert any("/pm/v1/monitor/task/view-tree" in url for url in session.urls)
    assert "probe aceito" in caplog.text
    assert "Playwright usado" not in caplog.text


def test_renovacao_trace_nao_libera_monitoring_invalido(
        sample_event, tmp_path, monkeypatch):
    collector = _collector(sample_event, tmp_path, monkeypatch)
    _write_session(collector._session_file)
    collector._session_built_roarand["trace"] = "trace-old"
    monitoring_key = collector._module_state_key("monitoring")
    HttpCollector._needs_interactive[monitoring_key] = "monitoring-invalid"
    session = _CollectorSession(_CollectorResponse(200, {"checkState": False}))
    monkeypatch.setattr(collector, "_get_session", lambda _module: session)

    assert collector._renew_session("trace") is True
    assert monitoring_key in HttpCollector._needs_interactive
    assert any("/fars/v1/traceresult/pre-check" in url for url in session.urls)


def test_reauth_interativa_trace_nao_limpa_estado_do_monitoring(
        sample_event, tmp_path, monkeypatch):
    collector = _collector(sample_event, tmp_path, monkeypatch)
    monitoring_key = collector._module_state_key("monitoring")
    trace_key = collector._module_state_key("trace")
    HttpCollector._needs_interactive[monitoring_key] = "monitoring-invalid"
    HttpCollector._needs_interactive[trace_key] = "trace-invalid"
    commands = []
    monkeypatch.setattr(HttpCollector, "_build_renew_cmd", lambda: ["renew"])
    monkeypatch.setattr(
        "core.collector.subprocess.run",
        lambda command, **_kwargs: commands.append(command)
        or SimpleNamespace(returncode=EXIT_SUCCESS, stdout="", stderr=""),
    )

    result = HttpCollector.run_interactive_reauth(
        collector.base_url,
        collector._session_file,
        state_module="trace",
    )

    assert result["ok"] is True
    assert commands[0][commands[0].index("--module") + 1] == "trace"
    assert trace_key not in HttpCollector._needs_interactive
    assert monitoring_key in HttpCollector._needs_interactive


def test_playwright_probe_both_exige_contrato_valido_dos_dois_modulos():
    monitoring = _PlaywrightResponse("", 200, {"success": True, "data": []})
    trace = _PlaywrightResponse("", 200, {"checkState": False})
    request = _PlaywrightRequest([
        ("/pm/v1/monitor/task/view-tree", monitoring),
        ("/fars/v1/traceresult/pre-check", trace),
    ])
    page = SimpleNamespace(request=request)
    session_data = {
        "monitoring": {"roarand": "pm-token"},
        "trace": {"roarand": "trace-token", "task_id": 14837},
    }

    assert _probe_authenticated(page, "https://oss.example", "both", session_data) is True
    assert len(request.urls) == 2


def test_playwright_probe_monitoring_nao_aceita_sucesso_do_trace():
    monitoring = _PlaywrightResponse("", 401, {"error": "unauthorized"})
    trace = _PlaywrightResponse("", 200, {"checkState": True})
    request = _PlaywrightRequest([
        ("/pm/v1/monitor/task/view-tree", monitoring),
        ("/fars/v1/traceresult/pre-check", trace),
    ])
    page = SimpleNamespace(request=request)
    session_data = {
        "monitoring": {"roarand": "pm-token"},
        "trace": {"roarand": "trace-token", "task_id": 14837},
    }

    assert _probe_authenticated(page, "https://oss.example", "monitoring", session_data) is False
    assert len(request.urls) == 1
    assert "/pm/v1/monitor/task/view-tree" in request.urls[0]
