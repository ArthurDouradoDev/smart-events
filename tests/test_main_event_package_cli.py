"""Modos de linha de comando do pacote .sepack: main.py e tools/event_package.py.

O ponto sensivel do ``main.py`` e nao inicializar o pywebview nesses modos: o
instalador e a associacao do ``.sepack`` chamam o executavel sem interface.
"""

import json
import subprocess
import sys
from pathlib import Path

from core import event_package as ep
from tests.test_event_package import _build, _client, _event, _server_data, _source, _write_json


ROOT = Path(__file__).resolve().parent.parent

# Executa main.py como __main__ num processo limpo e devolve, junto do codigo de
# saida, se o modulo webview chegou a ser importado.
_RUNNER = """
import json, runpy, sys

main_py, out_path = sys.argv[1], sys.argv[2]
sys.argv = ["main.py"] + sys.argv[3:]
code = 0
try:
    runpy.run_path(main_py, run_name="__main__")
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else 1
with open(out_path, "w", encoding="utf-8") as handle:
    json.dump({"exit": code, "webview": "webview" in sys.modules}, handle)
"""


def _run_main(tmp_path, *arguments):
    runner = tmp_path / "runner.py"
    runner.write_text(_RUNNER, encoding="utf-8")
    outcome = tmp_path / "outcome.json"
    environment = {
        **_clean_environment(),
        "SMARTEVENTS_DATA_DIR": str(tmp_path / "appdata"),
    }
    completed = subprocess.run(
        [sys.executable, str(runner), str(ROOT / "main.py"), str(outcome), *arguments],
        cwd=str(ROOT),
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert outcome.is_file(), completed.stderr
    return json.loads(outcome.read_text(encoding="utf-8"))


def _clean_environment():
    import os

    environment = dict(os.environ)
    environment.pop("SMARTEVENTS_DATA_DIR", None)
    environment["PYTHONPATH"] = str(ROOT)
    return environment


def _package_with_one_event(tmp_path):
    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM")],
        clientes=[_client("tim", "TIM")],
    )
    return _build(tmp_path, source, ["alvo"])


# ── main.py ──────────────────────────────────────────────────────────

def test_import_mode_writes_report_and_never_loads_pywebview(tmp_path):
    package = _package_with_one_event(tmp_path)
    report = tmp_path / "relatorio.json"

    outcome = _run_main(
        tmp_path, "--import-event-package", str(package), "--report", str(report)
    )

    assert outcome["exit"] == 0
    assert outcome["webview"] is False
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert "events/alvo.json" in payload["added"]
    # O modo sem GUI grava exatamente onde o aplicativo le.
    assert (tmp_path / "server_data" / "events" / "alvo.json").is_file()


def test_import_mode_returns_conflict_code_when_local_event_is_preserved(tmp_path):
    package = _package_with_one_event(tmp_path)
    first = ep.import_package(package, tmp_path)
    assert first.ok

    local_event = tmp_path / "server_data" / "events" / "alvo.json"
    changed = json.loads(local_event.read_text(encoding="utf-8"))
    changed["name"] = "Alvo do operador"
    _write_json(local_event, changed)

    report = tmp_path / "conflito.json"
    outcome = _run_main(
        tmp_path, "--import-event-package", str(package),
        "--conflict", "preserve", "--report", str(report),
    )

    assert outcome["exit"] == 4
    assert outcome["webview"] is False
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert "events/alvo.json" in payload["preserved"]
    assert json.loads(local_event.read_text(encoding="utf-8"))["name"] == "Alvo do operador"


def test_inspect_mode_rejects_invalid_package(tmp_path):
    invalid = tmp_path / "quebrado.sepack"
    invalid.write_bytes(b"nao e um zip")
    report = tmp_path / "inspecao.json"

    outcome = _run_main(
        tmp_path, "--inspect-event-package", str(invalid), "--report", str(report)
    )

    assert outcome["exit"] == 3
    assert outcome["webview"] is False
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["errors"]


def test_missing_package_argument_is_rejected(tmp_path):
    outcome = _run_main(tmp_path, "--import-event-package", "--show-dialog")

    assert outcome["exit"] == 2
    assert outcome["webview"] is False


# ── tools/event_package.py ───────────────────────────────────────────

def test_tools_cli_preview_and_build_and_import(tmp_path, capsys):
    from tools import event_package as cli

    source = _source(
        tmp_path,
        events=[_event("alvo", "Alvo", "TIM"), _event("outro", "Outro", "Vivo")],
        clientes=[_client("tim", "TIM"), _client("vivo", "Vivo")],
    )
    output = tmp_path / "saida"

    assert cli.main(["--json", "preview", "--events", "alvo", "--source", str(source)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["ok"] is True and preview["event_ids"] == ["alvo"]

    assert cli.main([
        "build", "--events", "alvo", "outro", "--source", str(source), "--output", str(output)
    ]) == 0
    package = next(output.glob("*.sepack"))

    assert cli.main(["inspect", str(package)]) == 0
    capsys.readouterr()

    target = tmp_path / "operador"
    assert cli.main(["import", str(package), "--data-dir", str(target)]) == 0
    assert (target / "server_data" / "events" / "alvo.json").is_file()
    # Reimportar e idempotente e continua com codigo 0.
    assert cli.main(["import", str(package), "--data-dir", str(target)]) == 0


def test_tools_cli_exit_codes_for_invalid_selection_and_package(tmp_path, capsys):
    from tools import event_package as cli

    source = _source(tmp_path, clientes=[_client("tim", "TIM")])

    assert cli.main(["preview", "--events", "inexistente", "--source", str(source)]) == 3
    assert "ERRO" in capsys.readouterr().out

    invalid = tmp_path / "quebrado.sepack"
    invalid.write_bytes(b"nao e um zip")
    assert cli.main(["inspect", str(invalid)]) == 3
    capsys.readouterr()

    assert cli.main(["import", str(invalid), "--data-dir", str(tmp_path / "operador")]) == 3
    capsys.readouterr()


def test_tools_cli_returns_conflict_code_when_local_data_is_preserved(tmp_path, capsys):
    from tools import event_package as cli

    package = _package_with_one_event(tmp_path)
    target = tmp_path / "operador"
    _server_data(
        target / "server_data",
        events=[_event("alvo", "Alvo divergente", "TIM")],
        clientes=[_client("tim", "TIM")],
    )

    assert cli.main(["import", str(package), "--data-dir", str(target)]) == 4
    saida = capsys.readouterr().out
    assert "preservado" in saida
    local = json.loads(
        (target / "server_data" / "events" / "alvo.json").read_text(encoding="utf-8")
    )
    assert local["name"] == "Alvo divergente"


def test_tools_cli_writes_requested_json_report(tmp_path, capsys):
    from tools import event_package as cli

    package = _package_with_one_event(tmp_path)
    report = tmp_path / "saida" / "import.json"

    assert cli.main([
        "import", str(package), "--data-dir", str(tmp_path / "operador"),
        "--report", str(report),
    ]) == 0
    capsys.readouterr()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["ok"] is True and payload["package_id"]
