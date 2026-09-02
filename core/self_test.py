"""Diagnostico autocontido usado pelo instalador e pelo suporte."""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

from core.paths import data_dir, resource_dir
from core.seed import SEED_DIRS, seed_operator_data, validate_seed


WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
DOTNET_48_RELEASE = 528040


def _check(name: str, function) -> dict:
    try:
        detail = function()
        return {"name": name, "ok": True, "detail": detail}
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "detail": str(exc),
            "traceback": traceback.format_exc(),
        }


def _dotnet() -> str:
    if os.name != "nt":
        raise RuntimeError("O SmartEvents instalavel requer Windows.")
    import winreg

    key_path = r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ) as key:
        release = int(winreg.QueryValueEx(key, "Release")[0])
    if release < DOTNET_48_RELEASE:
        raise RuntimeError(f".NET Framework 4.8 ausente (Release={release}).")
    return f"Release={release}"


def _pythonnet() -> str:
    import clr  # noqa: F401
    import System

    assemblies = list(System.AppDomain.CurrentDomain.GetAssemblies())
    runtime = next((a for a in assemblies if a.GetName().Name == "Python.Runtime"), None)
    if runtime is None:
        raise RuntimeError("A assembly Python.Runtime nao foi carregada.")
    location = str(runtime.Location or "embutida")
    return f"CLR={System.Environment.Version}; Python.Runtime={runtime.GetName().Version}; {location}"


def webview2_version() -> str:
    if os.name != "nt":
        raise RuntimeError("WebView2 so e validado no Windows.")
    import winreg

    locations = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
    ]
    for hive, path in locations:
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
                version = str(winreg.QueryValueEx(key, "pv")[0]).strip()
            if version and version != "0.0.0.0":
                return version
        except OSError:
            continue
    raise RuntimeError("Microsoft Edge WebView2 Runtime nao foi localizado no Registro.")


def _child_command(flag: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, flag]
    return [sys.executable, str(resource_dir() / "main.py"), flag]


def _webview_initialization() -> str:
    completed = subprocess.run(
        _child_command("--self-test-webview"),
        capture_output=True,
        text=True,
        timeout=45,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout or "sem detalhes").strip()
        raise RuntimeError(f"Falha ao inicializar pywebview/WebView2: {output}")
    return "janela WebView2 criada, carregada e encerrada"


def _window_chrome() -> dict:
    """Valida disponibilidade das APIs sem instalar um hook em janela alguma.

    A prova com janela real fica no processo separado abaixo; assim uma falha do
    chrome nunca mascara o renderer, e vice-versa.
    """
    from core.window_chrome import window_chrome_diagnostic

    result = window_chrome_diagnostic()
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "diagnostico Win32 sem detalhes")
    return result


def _titlebar_assets() -> str:
    """Confere no artefato os arquivos que a barra de titulo carrega em runtime."""
    frontend = resource_dir() / "frontend"
    required = [
        frontend / "assets" / "logoSmartEvents-32.png",
        frontend / "js" / "window_chrome.js",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("Arquivos ausentes no pacote: " + ", ".join(missing))
    icon = required[0]
    if icon.stat().st_size <= 0:
        raise RuntimeError(f"Icone da barra de titulo vazio: {icon}")
    return f"{len(required)} arquivo(s); icone={icon.stat().st_size} bytes"


def _event_package_contract() -> str:
    """Prova que o bundle conhece a propria versao antes de validar `.sepack`."""
    from core import event_package

    version = event_package.app_version()
    if version == "0.0.0":
        raise RuntimeError("VERSION ausente do bundle; pacotes validos seriam recusados.")
    return f"SmartEvents {version}; schema de pacote {event_package.SCHEMA_VERSION}"


def _window_chrome_window() -> str:
    """Prova o ciclo attach/consulta/detach numa janela WebView2 de verdade."""
    diagnostics = data_dir() / "diagnostics"
    diagnostics.mkdir(parents=True, exist_ok=True)
    report_path = diagnostics / "window-chrome-probe.json"
    report_path.unlink(missing_ok=True)
    completed = subprocess.run(
        _child_command("--self-test-window-chrome") + ["--report", str(report_path)],
        capture_output=True,
        text=True,
        timeout=60,
        encoding="utf-8",
        errors="replace",
    )
    # No executavel windowed (``console=False``) o stdout do filho pode nao
    # existir; o arquivo e a fonte confiavel e o stdout so complementa.
    payload = {}
    if report_path.is_file():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except ValueError:
            payload = {}
    if not payload:
        payload = _last_json_line(completed.stdout)
    if completed.returncode != 0 or not payload.get("ok"):
        detail = payload.get("error") or (completed.stderr or completed.stdout or "sem detalhes").strip()
        raise RuntimeError(f"Falha no chrome sobre janela WebView2 real: {detail}")
    return (
        f"attach/detach OK; modo={payload.get('mode')}; dpi={payload.get('dpi')}; "
        f"estado={payload.get('state')}; regiao nao-cliente={payload.get('nonclient')}"
    )


def _last_json_line(output: str | None) -> dict:
    for line in reversed((output or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


def run_window_chrome_probe(report_path: str | Path | None = None) -> int:
    """Instala o chrome Win32 numa janela WebView2 real e o desmonta em seguida.

    Espelha o caminho de producao: attach em ``before_show``, consulta e troca de
    regioes com o documento carregado, detach antes de destruir a janela. O
    relatorio sai como uma linha JSON no stdout, lida por ``_window_chrome_window``.
    """
    import threading
    import webview

    from core.window_chrome import WindowChromeController, host_diagnostic

    controller = WindowChromeController()
    report: dict = {"ok": False, "error": "a janela nao chegou a carregar", **host_diagnostic()}
    finished = threading.Event()

    try:
        window = webview.create_window(
            "SmartEvents - diagnostico da barra",
            html="<html><body>SmartEvents window chrome</body></html>",
            hidden=True,
            frameless=True,
            width=600,
            height=400,
        )

        def on_before_show():
            report["attached"] = controller.attach(window)
            if not report["attached"]:
                report["error"] = controller.last_error or "attach recusado"
                return
            # Mesma ordem da producao: a regiao nao-cliente precisa ser armada
            # antes de o CoreWebView2 existir, e e dela que dependem arraste,
            # duplo clique e Aero Snap na barra HTML.
            report["nonclient_armed"] = controller.arm_nonclient_regions(window)

        def on_loaded():
            try:
                if report.get("attached"):
                    state = controller.get_state()
                    # Regioes pequenas o suficiente para caber em qualquer DPI
                    # desta janela de 600x400 pixels logicos.
                    accepted = controller.set_regions({
                        "titlebar": {"x": 0, "y": 0, "width": 200, "height": 36},
                        "draggable": [{"x": 0, "y": 0, "width": 100, "height": 36}],
                        "buttons": {
                            "minimize": {"x": 108, "y": 0, "width": 30, "height": 36},
                            "maximize": {"x": 138, "y": 0, "width": 30, "height": 36},
                            "close": {"x": 168, "y": 0, "width": 30, "height": 36},
                        },
                    })
                    detached = controller.detach()
                    report.update({
                        "ok": bool(state.get("ok")) and accepted and detached,
                        "mode": state.get("mode"),
                        "dpi": state.get("dpi"),
                        "state": state.get("state"),
                        "nonclient": state.get("nonclient"),
                        "regions_accepted": accepted,
                        "detached": detached,
                    })
                    if report["ok"]:
                        report.pop("error", None)
                    else:
                        report["error"] = controller.last_error or "ciclo incompleto"
            except Exception as exc:
                report["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                finished.set()
                threading.Timer(0.25, window.destroy).start()

        window.events.before_show += on_before_show
        window.events.loaded += on_loaded
        # Sem watchdog uma janela que nunca dispara ``loaded`` prenderia o
        # diagnostico ate o timeout do processo pai.
        threading.Timer(30.0, lambda: finished.is_set() or window.destroy()).start()
        webview.start(gui="edgechromium", private_mode=True)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        controller.detach()

    serialized = json.dumps(report, ensure_ascii=False)
    if report_path:
        try:
            destination = Path(report_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(serialized, encoding="utf-8")
        except OSError as exc:
            print(f"Falha ao gravar o relatorio da prova: {exc}")
    print(serialized)
    return 0 if report.get("ok") else 1


def run_webview_probe() -> int:
    """Cria uma WebView real e a fecha assim que o DOM termina de carregar."""
    try:
        import threading
        import webview

        window = webview.create_window(
            "SmartEvents - diagnostico",
            html="<html><body>SmartEvents OK</body></html>",
            hidden=True,
            width=320,
            height=200,
        )

        def close_window():
            threading.Timer(0.25, window.destroy).start()

        window.events.loaded += close_window
        webview.start(gui="edgechromium", private_mode=True)
        return 0
    except Exception:
        traceback.print_exc()
        return 1


def _playwright() -> str:
    from core.session_renew import _ensure_browsers_path
    from playwright.sync_api import sync_playwright

    _ensure_browsers_path()
    browsers_path = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""))
    if not browsers_path.is_dir():
        raise RuntimeError(f"Pasta dos navegadores ausente: {browsers_path}")
    names = {p.name for p in browsers_path.iterdir() if p.is_dir()}
    if not any(name.startswith("chromium-") for name in names):
        raise RuntimeError("Chromium visual nao foi empacotado.")
    if not any(name.startswith("chromium_headless_shell-") for name in names):
        raise RuntimeError("Chromium headless shell nao foi empacotado.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content("<title>SmartEvents self-test</title>")
            if page.title() != "SmartEvents self-test":
                raise RuntimeError("O Chromium nao renderizou a pagina de teste.")
        finally:
            browser.close()
    return f"Chromium headless OK; bundle={browsers_path}"


def _write_access() -> str:
    target = data_dir()
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="self-test-", suffix=".tmp", dir=target, delete=False) as handle:
        handle.write(b"SmartEvents")
        probe = Path(handle.name)
    try:
        if probe.read_bytes() != b"SmartEvents":
            raise RuntimeError("Conteudo lido difere do conteudo gravado.")
    finally:
        probe.unlink(missing_ok=True)
    return str(target)


def _database() -> str:
    diagnostics = data_dir() / "diagnostics"
    diagnostics.mkdir(parents=True, exist_ok=True)
    path = diagnostics / "self-test.sqlite3"
    path.unlink(missing_ok=True)
    try:
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE probe (value TEXT NOT NULL)")
            connection.execute("INSERT INTO probe VALUES (?)", ("SmartEvents",))
            connection.commit()
        finally:
            connection.close()
        connection = sqlite3.connect(path)
        try:
            value = connection.execute("SELECT value FROM probe").fetchone()[0]
        finally:
            connection.close()
        if value != "SmartEvents":
            raise RuntimeError("Falha ao reler o banco SQLite de diagnostico.")
    finally:
        path.unlink(missing_ok=True)
    return "criacao, escrita e leitura SQLite OK"


def _seeds() -> str:
    """Diagnostico GENERICO do programa: a semente embutida precisa ser valida.

    O build-base da Fase 3 nao carrega evento nem cliente: uma semente generica
    com zero cadastro e um resultado correto, nao uma falha. Conferir os eventos
    de uma distribuicao e outro assunto — ver ``_expected_events``.
    """
    bundled = validate_seed()
    if not bundled["ok"]:
        raise RuntimeError("; ".join(bundled["errors"]))
    operator_path = seed_operator_data()
    if not operator_path.is_dir():
        raise RuntimeError("A semente nao foi criada na pasta do operador.")
    missing = [name for name in SEED_DIRS if not (operator_path / name).is_dir()]
    if missing:
        raise RuntimeError("Pastas ausentes na estrutura do operador: " + ", ".join(missing))
    profile = bundled.get("profile") or {}
    profile_name = str(profile.get("name") or profile.get("id") or "legado")
    if bundled.get("generic"):
        return f"build-base generico ({profile_name}); nenhum evento embutido; estrutura pronta"
    client = str(profile.get("client") or "N/D")
    return (
        f"perfil {profile_name}; {bundled['events']} evento(s); "
        f"cliente {client}; {bundled['vips']} VIP(s)"
    )


def _package_report(report_path: Path) -> str:
    """Confere o relatorio da importacao feita pelo instalador antes do self-test."""
    if not report_path.is_file():
        raise RuntimeError(f"Relatorio de importacao ausente: {report_path.name}")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Relatorio de importacao ilegivel: {exc}") from exc
    if not isinstance(report, dict) or not report.get("ok"):
        errors = report.get("errors") if isinstance(report, dict) else None
        detail = "; ".join(
            str(item.get("message") or item) for item in (errors or [])[:3]
        )
        raise RuntimeError(f"A importacao do pacote falhou: {detail or 'sem detalhes'}")
    return (
        f"pacote {report.get('package_id') or 'N/D'}; "
        f"adicionados={len(report.get('added') or [])}; "
        f"conciliados={len(report.get('reconciled') or [])}; "
        f"preservados={len(report.get('preserved') or [])}; "
        f"conflitos={len(report.get('conflicts') or [])}"
    )


def _expected_events(expected: list[str]) -> str:
    """Os eventos da distribuicao existem no ``server_data`` e no banco local.

    A sincronizacao usada e a incremental, sem HTTP: o instalador nao sobe
    servidor, e um caminho autoritativo aqui poderia apagar evento que o operador
    ja tinha de outro pacote.
    """
    from core import database as db
    from core.paths import server_data_dir

    # A mesma pasta que o aplicativo le e que a importacao grava; `seed_operator_data`
    # so garante a estrutura, e em desenvolvimento aponta para outro lugar.
    server_data = server_data_dir()
    on_disk = set()
    for file_path in sorted((server_data / "events").glob("*.json")):
        try:
            value = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get("id"):
            on_disk.add(str(value["id"]))
    missing_files = sorted(set(expected) - on_disk)
    if missing_files:
        raise RuntimeError("Eventos ausentes no server_data: " + ", ".join(missing_files))

    db.init_db()
    stats = db.sync_events_from_local_files(expected)
    missing_db = sorted(event_id for event_id in expected if not db.get_event(event_id))
    if missing_db:
        raise RuntimeError("Eventos ausentes no banco local: " + ", ".join(missing_db))
    return f"{len(expected)} evento(s) no server_data e no banco; sincronizados={stats['sincronizados']}"


def run_self_test(
    report_path: str | Path | None = None,
    *,
    expect_events: list[str] | None = None,
    expect_package_report: str | Path | None = None,
) -> tuple[int, Path, dict]:
    """Diagnostico do programa; as expectativas de distribuicao sao opcionais.

    Sem ``expect_*`` o self-test e o generico do build-base — ele nao exige evento
    algum. O instalador acrescenta as duas verificacoes de distribuicao depois de
    importar o ``.sepack``.
    """
    expected_events = [str(value).strip() for value in (expect_events or []) if str(value).strip()]
    checks = [
        _check("Windows x64", lambda: platform.platform() if platform.machine().upper() in {"AMD64", "X86_64"} else (_ for _ in ()).throw(RuntimeError(platform.machine()))),
        _check(".NET Framework 4.8+", _dotnet),
        _check("pythonnet / Python.Runtime.dll", _pythonnet),
        _check("Microsoft Edge WebView2 Runtime", webview2_version),
        _check("inicializacao pywebview", _webview_initialization),
        _check("controlador de moldura Win32", _window_chrome),
        _check("moldura Win32 em janela WebView2", _window_chrome_window),
        _check("assets da barra de titulo", _titlebar_assets),
        _check("versao para pacotes de eventos", _event_package_contract),
        _check("Chromium headless e Chromium visual", _playwright),
        _check("gravacao nos dados do operador", _write_access),
        _check("banco SQLite local", _database),
        _check("semente do perfil de instalacao", _seeds),
    ]
    if expect_package_report:
        package_report = Path(expect_package_report)
        checks.append(
            _check("importacao do pacote de eventos", lambda: _package_report(package_report))
        )
    if expected_events:
        checks.append(
            _check("eventos da distribuicao", lambda: _expected_events(expected_events))
        )
    ok = all(item["ok"] for item in checks)
    now = datetime.now(timezone.utc)
    report = {
        "schema_version": 1,
        "application": "SmartEvents",
        "ok": ok,
        "timestamp_utc": now.isoformat(),
        "executable": sys.executable,
        "python": sys.version,
        "data_dir": str(data_dir()),
        "expected_events": expected_events,
        "checks": checks,
    }
    destination = Path(report_path) if report_path else data_dir() / "diagnostics" / f"self-test-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return (0 if ok else 1), destination, report
