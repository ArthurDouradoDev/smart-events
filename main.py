"""
main.py — Ponto de entrada do Smart Events.
Inicializa o banco, a janela PyWebView e expõe a API Python ao JS.

Uso:
  python main.py           # produção (sem mock)
  python main.py --mock    # desenvolvimento com dados sintéticos
  python main.py --dev     # abre DevTools e habilita console
"""

import logging
import os
import sys
import socket
import subprocess
import atexit
import time
import threading
from pathlib import Path

# Em um .exe sem console (windowed, console=False), sys.stdout/sys.stderr são None. Qualquer
# escrita (incluindo o StreamHandler do logging) quebraria com AttributeError, e mensagens com
# caracteres não-ASCII quebrariam com UnicodeEncodeError no cp1252. Garantimos streams válidos
# em UTF-8 ANTES de configurar o logging — vale tanto para o processo GUI quanto para o --serve.
if sys.stdout is None or sys.stderr is None:
    _devnull = open(os.devnull, "w", encoding="utf-8", errors="replace")
    if sys.stdout is None:
        sys.stdout = _devnull
    if sys.stderr is None:
        sys.stderr = _devnull

# Adiciona o root ao path para imports absolutos
sys.path.insert(0, str(Path(__file__).parent))

from core import database as db

logging.basicConfig(
    level=logging.DEBUG if "--dev" in sys.argv else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

# Captura os logs de coleta num buffer em memória (painel </> do frontend + download).
from core.log_buffer import install as _install_log_buffer
_install_log_buffer()

FRONTEND = Path(__file__).parent / "frontend" / "index.html"


_JOB_HANDLE = None  # mantém o handle do Job Object vivo durante a sessão


def _setup_windows_job():
    """
    Coloca o processo num Job Object com KILL_ON_JOB_CLOSE para que TODOS os processos-filho
    (subprocessos --get-session do Playwright e seus Chromium) sejam encerrados junto com o app.

    Evita processos órfãos após fechar a janela e o popup "Failed to remove temporary directory
    _MEI..." do PyInstaller onefile (o filho é finalizado pelo SO, sem tentar limpar seu _MEI).
    """
    global _JOB_HANDLE
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        ULONG_PTR = ctypes.c_size_t

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ULONG_PTR),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # argtypes/restype corretos evitam truncamento dos HANDLEs (64-bit) no marshalling.
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return
        if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            return
        _JOB_HANDLE = job  # manter vivo; fechar o handle dispara o kill da árvore
        logger.info("Job Object configurado — processos-filho serão encerrados junto com o app.")
    except Exception as e:
        logger.warning(f"Não foi possível configurar o Job Object: {e}")


def _get_arg(flag: str, default=None):
    """Lê o valor de um argumento no formato `--flag valor`."""
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return default


def _run_get_session() -> int:
    """Modo renovação de sessão (subprocesso): roda o Playwright e grava o session.json. Sem janela."""
    from core.session_renew import run
    return run(
        headless="--headless" in sys.argv,
        module=_get_arg("--module", "both"),
        base_url=_get_arg("--base-url", "https://10.220.50.9:31943"),
        session_file=_get_arg("--session-file", None),
    )


def _run_server_only():
    """Modo servidor-only (subprocesso): roda apenas o FastAPI em 127.0.0.1:<port> e não cria janela."""
    import uvicorn
    from server import app

    # log_config=None evita que o uvicorn instancie seu formatter colorido (que chama
    # sys.stdout.isatty()); os streams já foram garantidos no topo do módulo.
    port = int(_get_arg("--port", "8000"))
    logger.info(f"Iniciando servidor embutido em http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)


def _find_free_port(preferred: int = 8000) -> int:
    """Tenta a porta preferida; se ocupada, usa uma porta efêmera livre."""
    for candidate in (preferred, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", candidate))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    return preferred


def _start_embedded_server() -> str:
    """
    Sobe o servidor FastAPI numa thread daemon do PRÓPRIO processo (sem subprocesso).

    Evita o popup "Failed to remove temporary directory _MEI..." do PyInstaller onefile, que
    ocorria ao encerrar um subprocesso do servidor (cada subprocesso tem sua própria pasta _MEI
    que o bootloader não conseguia limpar ao ser finalizado). Rodando em thread, não há segundo
    processo nem segunda pasta temporária. Retorna a url.
    """
    import uvicorn
    from server import app

    port = _find_free_port(8000)
    url = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)
    server = uvicorn.Server(config)
    # Handlers de sinal só podem ser instalados na main thread; aqui rodamos em thread daemon.
    server.install_signal_handlers = lambda: None
    threading.Thread(target=server.run, daemon=True, name="embedded-server").start()
    logger.info(f"Servidor embutido iniciando em {url} (thread)")
    return url


def _wait_server_ready(port: int, timeout: float = 8.0) -> bool:
    """Aguarda o servidor aceitar conexões em 127.0.0.1:<port>."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def _prepopulate_mock_history():
    """Pré-popula o banco de dados com um evento finalizado e seu histórico para testes."""
    from datetime import datetime, timedelta
    import random
    import json
    
    event_id = "gp-sp-2025-ended"
    
    # Verifica se já existe para não reinserir repetidamente e inchar o banco
    if db.get_event(event_id):
        return
        
    logger.info("Pré-populando banco de dados com dados históricos de teste...")
    
    # Evento finalizado há 1 hora
    end_dt = datetime.utcnow() - timedelta(hours=1)
    start_dt = end_dt - timedelta(hours=4)
    
    event_config = {
        "id": event_id,
        "name": "GP São Paulo 2025 (Histórico)",
        "status": "ENDED",
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "polygon": [
            [-23.7030, -46.7010],
            [-23.6970, -46.6910],
            [-23.6910, -46.6960],
            [-23.6940, -46.7050],
            [-23.7030, -46.7010]
        ],
        "sites": [
            {
                "id": "ERB-07",
                "name": "ERB-07 Interlagos",
                "lat": -23.7012,
                "lng": -46.6975,
                "is_event_site": True,
                "cells": [
                    { "id": "ERB-07-Y3500-1", "azimuth": 0 },
                    { "id": "ERB-07-L700-1",  "azimuth": 0 },
                    { "id": "ERB-07-L1800-1", "azimuth": 0 },
                    { "id": "ERB-07-Y3500-2", "azimuth": 120 },
                    { "id": "ERB-07-L700-2",  "azimuth": 120 },
                    { "id": "ERB-07-L1800-2", "azimuth": 120 },
                    { "id": "ERB-07-Y3500-3", "azimuth": 240 },
                    { "id": "ERB-07-L700-3",  "azimuth": 240 },
                    { "id": "ERB-07-L1800-3", "azimuth": 240 }
                ]
            },
            {
                "id": "ERB-03",
                "name": "ERB-03 Av. Interlagos",
                "lat": -23.6958,
                "lng": -46.6940,
                "is_event_site": True,
                "cells": [
                    { "id": "ERB-03-A1", "azimuth": 30 },
                    { "id": "ERB-03-A2", "azimuth": 150 },
                    { "id": "ERB-03-A3", "azimuth": 270 }
                ]
            }
        ],
        "vips": [
            { "id": "carlos-menezes", "task_id": None },
            { "id": "ana-rodrigues",  "task_id": None }
        ],
        "thresholds": {
            "rsrp_warning": -100,
            "rsrp_critical": -110,
            "rsrq_warning": -12,
            "rsrq_critical": -15,
            "utilization_warning": 80,
            "utilization_critical": 95
        }
    }
    
    db.save_event(event_config)
    
    # Salvar sites na tabela sites
    for site in event_config["sites"]:
        db.get_event_conn(event_id).execute("""
            INSERT OR REPLACE INTO sites (id, event_id, name, lat, lng, is_event_site, cells_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            site["id"],
            event_id,
            site["name"],
            site["lat"],
            site["lng"],
            1 if site["is_event_site"] else 0,
            json.dumps(site["cells"])
        ))
    db.get_event_conn(event_id).commit()
    
    # Gerar medições a cada 2 minutos
    current_time = start_dt
    kpis_to_insert = []
    vips_to_insert = []
    alerts_to_insert = []
    
    while current_time <= end_dt:
        ts_str = current_time.isoformat() + "Z"
        
        # Simula gaps de dados (pula alguns intervalos)
        if start_dt + timedelta(minutes=60) <= current_time <= start_dt + timedelta(minutes=70):
            current_time += timedelta(minutes=2)
            continue
            
        for site in event_config["sites"]:
            for cell in site["cells"]:
                # Medições de KPI
                util_dl = round(max(10, min(100, 60 + random.gauss(0, 15))), 1)
                vol_dl = round(max(10, 100 + random.gauss(0, 30)), 1)
                vol_ul = round(max(5, 40 + random.gauss(0, 15)), 1)
                tp_dl = round(max(0, 20 + random.gauss(0, 5)), 2)
                tp_ul = round(max(0, 5 + random.gauss(0, 1)), 2)
                users = int(max(1, 25 + random.gauss(0, 8)))
                accessibility = 100.0 if random.random() > 0.02 else 95.0
                
                metrics = {
                    "utilization_dl": util_dl,
                    "traffic_volume_dl": vol_dl,
                    "traffic_volume_ul": vol_ul,
                    "throughput_dl": tp_dl,
                    "throughput_ul": tp_ul,
                    "user_count": users,
                    "accessibility": accessibility
                }
                
                for metric, value in metrics.items():
                    kpis_to_insert.append({
                        "site_id": site["id"],
                        "cell_id": cell["id"],
                        "event_id": event_id,
                        "timestamp": ts_str,
                        "metric": metric,
                        "value": value
                    })
                    
        for vip in event_config["vips"]:
            rsrp = round(random.gauss(-85, 10), 1)
            rsrq = round(random.gauss(-10, 3), 1)
            serving = "ERB-07-L1800-1" if random.random() > 0.3 else "ERB-03-A2"
            
            vips_to_insert.append({
                "vip_name": vip["id"],
                "event_id": event_id,
                "timestamp": ts_str,
                "serving_cell": serving,
                "rsrp": rsrp,
                "rsrq": rsrq,
                "in_event": 1
            })
            
            # Alertas VIP
            if rsrp <= -110:
                alerts_to_insert.append({
                    "event_id": event_id,
                    "level": "EVENT",
                    "severity": "CRITICAL",
                    "site_id": serving.split("-")[0],
                    "cell_id": serving,
                    "message": f"RSRP crítico para {vip['id']}: {rsrp} dBm",
                    "timestamp": ts_str,
                    "acknowledged": 0
                })
                
        current_time += timedelta(minutes=2)
        
    db.insert_kpi_batch(kpis_to_insert)
    db.insert_vip_batch(vips_to_insert)
    
    # Inserir alertas
    for alert in alerts_to_insert:
        db.insert_alert(alert)
        
    logger.info(f"Histórico pré-populado com sucesso: {len(kpis_to_insert)} KPIs, {len(vips_to_insert)} VIPs, {len(alerts_to_insert)} Alertas.")


def main():
    import webview
    from api.api import Api

    mock_mode = "--mock" in sys.argv
    dev_mode = "--dev" in sys.argv

    logger.info(f"Iniciando Smart Events | mock={mock_mode} | dev={dev_mode}")

    # Garante que subprocessos (renovação de sessão Playwright) morram junto com o app.
    _setup_windows_job()

    db.init_db()

    # Sobe o servidor FastAPI embutido em localhost e fixa o server_url para o app apontar para ele.
    server_url = ""
    try:
        server_url = _start_embedded_server()
        port = int(server_url.rsplit(":", 1)[1])
        db.save_settings({**db.get_settings(), "server_url": server_url})

        # A 1ª execução do .exe onefile pode levar dezenas de segundos para extrair/iniciar o servidor.
        # Esperar a prontidão e sincronizar numa thread separada evita travar a abertura da janela;
        # o frontend (app.js) também re-tenta a sincronização no boot e ao recuperar o foco.
        def _warmup_sync(p: int):
            if _wait_server_ready(p, timeout=45.0):
                logger.info(f"Servidor embutido pronto em http://127.0.0.1:{p}")
            else:
                logger.warning(f"Servidor embutido não respondeu a tempo em http://127.0.0.1:{p}")
            try:
                logger.info(f"Sync inicial de eventos: {db.sync_events_from_server()}")
                logger.info(f"Sync inicial de VIPs: {db.sync_vips_from_server()}")
            except Exception as e:
                logger.error(f"Erro no sync inicial: {e}")

        threading.Thread(target=_warmup_sync, args=(port,), daemon=True).start()
    except Exception as e:
        logger.error(f"Falha ao iniciar servidor embutido: {e}")

    if mock_mode:
        _prepopulate_mock_history()

    api = Api()
    api._server_url = server_url
    if mock_mode:
        # Injeta flag para que o frontend saiba que está em modo mock
        api._mock_mode = True

    window = webview.create_window(
        title="Smart Events",
        url=str(FRONTEND),
        js_api=api,
        width=1440,
        height=900,
        min_size=(1024, 600),
        resizable=True,
        fullscreen=False,
        background_color="#0D1117",
    )

    def _on_loaded():
        logger.info("Interface carregada")
        window.maximize()
        if mock_mode:
            window.evaluate_js("window.__MOCK_MODE__ = true;")

    window.events.loaded += _on_loaded

    webview.start(
        debug=dev_mode,
        http_server=False,   # serve arquivos locais diretamente
        storage_path=str(Path(__file__).parent / "data"),
    )


if __name__ == "__main__":
    # Subprocessos sem janela: servidor embutido (--serve) ou renovação de sessão (--get-session).
    if "--serve" in sys.argv:
        _run_server_only()
    elif "--get-session" in sys.argv:
        sys.exit(_run_get_session())
    else:
        main()
