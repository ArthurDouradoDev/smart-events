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
from logging.handlers import RotatingFileHandler

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
from core import credentials
from core.paths import resource_dir

_log_handlers = [logging.StreamHandler()]
try:
    # ``data_dir`` é persistente também no executável onefile; nunca use _MEIPASS
    # para logs que precisam sobreviver ao fechamento do aplicativo.
    _log_path = credentials.data_dir() / "logs" / "smart_events.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _log_handlers.append(RotatingFileHandler(
        _log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    ))
except Exception as exc:
    # A interface continua abrindo mesmo se, por exemplo, a pasta ao lado do .exe
    # estiver momentaneamente sem permissão de escrita.
    logging.getLogger(__name__).warning("Não foi possível configurar o log em disco: %s", exc)

logging.basicConfig(
    level=logging.DEBUG if "--dev" in sys.argv else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger("main")

# Captura os logs de coleta num buffer em memória (painel </> do frontend + download).
from core.log_buffer import install as _install_log_buffer
_install_log_buffer()

FRONTEND = resource_dir() / "frontend" / "index.html"


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
        region=_get_arg("--region", ""),
        cliente=_get_arg("--cliente", ""),
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


def main():
    import webview
    from api.api import Api

    mock_mode = "--mock" in sys.argv
    dev_mode = "--dev" in sys.argv

    logger.info(f"Iniciando Smart Events | mock={mock_mode} | dev={dev_mode}")

    # Garante que subprocessos (renovação de sessão Playwright) morram junto com o app.
    _setup_windows_job()

    db.init_db()

    # 1ª execução do .exe: semeia data/clientes.json (catálogo) e data/credentials.json (vazio).
    from core import credentials
    credentials.seed_files()

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
                logger.info(f"Sync inicial de clientes: {db.sync_clientes_from_server()}")
                logger.info(f"Sync inicial de eventos: {db.sync_events_from_server()}")
                logger.info(f"Sync inicial de VIPs: {db.sync_vips_from_server()}")
            except Exception as e:
                logger.error(f"Erro no sync inicial: {e}")

        threading.Thread(target=_warmup_sync, args=(port,), daemon=True).start()
    except Exception as e:
        logger.error(f"Falha ao iniciar servidor embutido: {e}")

    api = Api()
    api._server_url = server_url
    if mock_mode:
        # Injeta flag para que o frontend saiba que está em modo mock
        api._mock_mode = True

    # O WebView2 (via pywebview) tenta desabilitar o cache HTTP do servidor local
    # (Cache-Control: no-cache), mas o bottle.static_file() descarta esse header —
    # e como storage_path é persistente entre execuções (ver private_mode=False
    # abaixo), o cache em disco do WebView2 pode servir HTML/CSS/JS desatualizados
    # por dias, mesmo após reiniciar o app. Reduzir o disk-cache a praticamente
    # zero força o WebView2 a sempre buscar os arquivos do disco; cookies/sessão
    # (Cookies, Local Storage) ficam em outro lugar do profile e não são afetados.
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--disk-cache-size=1")

    window = webview.create_window(
        title="Smart Events",
        url=str(FRONTEND) + "#desktop",
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
        storage_path=str(credentials.data_dir() / "webview"),
        # private_mode=False: o pywebview, em private_mode (default=True), APAGA a
        # storage_path ao fechar o app. Como a storage_path é a pasta data/, isso
        # destruía o session.json (cookies/roarand) e o browser_profile a cada
        # fechamento — forçando novo login a CADA início. Persistindo a sessão, o
        # login só é refeito quando os cookies realmente expiram no servidor.
        private_mode=False,
    )


if __name__ == "__main__":
    # Subprocessos sem janela: servidor embutido (--serve) ou renovação de sessão (--get-session).
    if "--self-test-webview" in sys.argv:
        from core.self_test import run_webview_probe
        sys.exit(run_webview_probe())
    elif "--self-test" in sys.argv:
        from core.self_test import run_self_test
        report_arg = _get_arg("--report", None)
        exit_code, report_path, report = run_self_test(report_arg)
        logger.info("Diagnostico %s: %s", "aprovado" if exit_code == 0 else "reprovado", report_path)
        if "--show-dialog" in sys.argv:
            import ctypes
            failed = [item["name"] for item in report["checks"] if not item["ok"]]
            message = (
                "Todos os testes foram aprovados."
                if not failed
                else "Falha em: " + ", ".join(failed) + f"\n\nRelatorio: {report_path}"
            )
            ctypes.windll.user32.MessageBoxW(None, message, "SmartEvents - Diagnostico", 0x40 if not failed else 0x10)
        sys.exit(exit_code)
    elif "--serve" in sys.argv:
        _run_server_only()
    elif "--get-session" in sys.argv:
        sys.exit(_run_get_session())
    else:
        main()
