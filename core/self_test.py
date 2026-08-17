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
from core.seed import seed_operator_data, validate_seed


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
    bundled = validate_seed()
    if not bundled["ok"]:
        raise RuntimeError("; ".join(bundled["errors"]))
    operator_path = seed_operator_data()
    if not operator_path.is_dir():
        raise RuntimeError("A semente nao foi criada na pasta do operador.")
    return f"{bundled['events']} evento(s) RoadShow; cliente TIM; {bundled['vips']} VIP(s) TIM"


def run_self_test(report_path: str | Path | None = None) -> tuple[int, Path, dict]:
    checks = [
        _check("Windows x64", lambda: platform.platform() if platform.machine().upper() in {"AMD64", "X86_64"} else (_ for _ in ()).throw(RuntimeError(platform.machine()))),
        _check(".NET Framework 4.8+", _dotnet),
        _check("pythonnet / Python.Runtime.dll", _pythonnet),
        _check("Microsoft Edge WebView2 Runtime", webview2_version),
        _check("inicializacao pywebview", _webview_initialization),
        _check("Chromium headless e Chromium visual", _playwright),
        _check("gravacao nos dados do operador", _write_access),
        _check("banco SQLite local", _database),
        _check("sementes RoadShow / TIM", _seeds),
    ]
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
        "checks": checks,
    }
    destination = Path(report_path) if report_path else data_dir() / "diagnostics" / f"self-test-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return (0 if ok else 1), destination, report
