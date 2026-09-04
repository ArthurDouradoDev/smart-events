"""
main.py — Ponto de entrada do Smart Events.
Inicializa o banco, a janela PyWebView e expõe a API Python ao JS.

Uso:
  python main.py           # produção (sem mock)
  python main.py --mock    # desenvolvimento com dados sintéticos
  python main.py --dev     # abre DevTools e habilita console

Modos sem janela (usados pelo instalador e pela associação do .sepack):
  main.py --inspect-event-package <arquivo> [--report <json>] [--require-signature]
  main.py --import-event-package <arquivo> [--conflict preserve] [--report <json>]
           [--show-dialog] [--require-signature]

--require-signature recusa pacote sem assinatura válida de uma chave ativa
(postura de produção). Sem a flag, pacote não assinado é aceito com aviso.
"""

import json
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

# Segundos apos o `loaded` para refazer a superficie do WebView2. Medido: em
# t+10s o dashboard ja esta montado e o ciclo devolve o quadro completo.
FIRST_PAINT_REPAINT_DELAY = 8.0
# Dá tempo para a bridge JS receber o retorno de ``window_close`` antes que o
# WinForms destrua o message loop usado pelo pythonnet para completar a chamada.
WINDOW_CLOSE_BRIDGE_DELAY = 0.10


class _ShutdownCoordinator:
    """Interrompe callbacks de fundo antes de destruir a janela WinForms."""

    def __init__(self, scheduler_obj, chrome_controller, timer_factory=threading.Timer,
                 purge_alarms=None):
        self._scheduler = scheduler_obj
        self._chrome = chrome_controller
        self._timer_factory = timer_factory
        self._purge_alarms = purge_alarms
        self._lock = threading.Lock()
        self._complete = False
        self._close_scheduled = False
        self._repaint_timer = None

    def schedule_repaint(self, delay: float) -> bool:
        with self._lock:
            if self._complete or self._repaint_timer is not None:
                return False
            timer = self._timer_factory(delay, self._chrome.force_repaint)
            timer.daemon = True
            self._repaint_timer = timer
            timer.start()
            return True

    def shutdown(self) -> None:
        with self._lock:
            if self._complete:
                return
            timer = self._repaint_timer
            if timer is not None:
                timer.cancel()
                if timer is not threading.current_thread():
                    timer.join(timeout=1.0)
            try:
                self._scheduler.set_update_callback(None)
            except Exception:
                logger.exception("Falha ao remover callback do scheduler")
            try:
                self._scheduler.stop()
            except Exception:
                logger.exception("Falha ao encerrar o scheduler")
            # Depois do stop(), nunca antes: um ciclo de coleta em voo regravaria
            # a tabela logo apos o DELETE. stop() sinaliza o stop_event dos
            # workers e os aguarda antes de retornar.
            if self._purge_alarms is not None:
                try:
                    removed = self._purge_alarms()
                    logger.info("Alarmes zerados no encerramento (%s linhas)", removed)
                except Exception:
                    logger.exception("Falha ao zerar os alarmes no encerramento")
            self._complete = True

    def request_close(self) -> bool:
        self.shutdown()
        with self._lock:
            if self._close_scheduled:
                return True
            self._close_scheduled = True
            timer = self._timer_factory(WINDOW_CLOSE_BRIDGE_DELAY, self._chrome.close)
            timer.daemon = True
            timer.start()
        return True


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


def _get_list_arg(flag: str) -> list[str]:
    """Lê `--flag a,b` ou `--flag a b c` (o instalador passa a lista separada por vírgula)."""
    raw = _get_arg(flag)
    if not raw or raw.startswith("--"):
        return []
    values = [raw]
    idx = sys.argv.index(flag) + 2
    while idx < len(sys.argv) and not sys.argv[idx].startswith("--"):
        values.append(sys.argv[idx])
        idx += 1
    return [part.strip() for value in values for part in value.split(",") if part.strip()]


def _show_message_box(title: str, message: str, failed: bool, warning: bool = False) -> None:
    """Diálogo curto de resultado; não abre a janela principal."""
    if os.name != "nt":
        return
    try:
        import ctypes
        icon = 0x10 if failed else (0x30 if warning else 0x40)
        ctypes.windll.user32.MessageBoxW(None, message, title, icon)
    except Exception as exc:  # pragma: no cover - ambiente sem user32
        logger.warning("Não foi possível exibir o diálogo: %s", exc)


def _sync_imported_events(event_ids: list[str]) -> None:
    """Leva ao banco local só os eventos que o pacote tocou. Nunca remove nada."""
    ids = [value for value in event_ids if value]
    if not ids:
        return
    try:
        from core import database as db

        db.init_db()
        stats = db.sync_events_from_local_files(ids)
        logger.info("Eventos sincronizados apos a importacao: %s", stats)
    except Exception as exc:  # noqa: BLE001 - o pacote já foi gravado com sucesso
        logger.error("Falha ao sincronizar os eventos importados no banco local: %s", exc)


def _run_event_package_mode() -> int:
    """Inspeciona ou importa um pacote `.sepack` sem subir a interface.

    Códigos de saída: 0 concluído (ou idempotente), 2 argumento inválido,
    3 pacote inválido, 4 concluído com conflito preservado.
    """
    from core import event_package

    inspecting = "--inspect-event-package" in sys.argv
    flag = "--inspect-event-package" if inspecting else "--import-event-package"
    package = _get_arg(flag)
    if not package or package.startswith("--"):
        logger.error("Informe o caminho do pacote: %s <arquivo.sepack>", flag)
        return 2

    signature_policy = (
        event_package.SIGNATURE_POLICY_PRODUCTION if "--require-signature" in sys.argv
        else event_package.SIGNATURE_POLICY_DEVELOPMENT
    )

    if inspecting:
        payload = event_package.inspect_package(package, signature_policy=signature_policy)
        exit_code = 0 if payload["ok"] else 3
        lines = (
            ["Pacote válido: " + str((payload.get("manifest") or {}).get("name", ""))]
            if payload["ok"]
            else ["Pacote inválido: " + item["message"] for item in payload["errors"][:3]]
        )
    else:
        conflict = _get_arg("--conflict", "preserve")
        result = event_package.import_package(
            package, conflict_policy=conflict, signature_policy=signature_policy,
        )
        payload = result.to_dict()
        lines = event_package.summary_lines(result)
        if not result.ok:
            exit_code = 3
        else:
            exit_code = 4 if result.conflicts else 0
            # O app que já esteja aberto lê os eventos do banco, não do
            # `server_data`. Sincronizamos SOMENTE os ids que o pacote tocou: o
            # caminho autoritativo (`sync_events_from_server`) apagaria o evento
            # local ausente na origem, e importação de pacote é incremental.
            _sync_imported_events([
                str(item.get("record_id") or "")
                for item in payload.get("actions") or []
                if item.get("kind") == "event" and item.get("action") != "skip"
            ])

    report_arg = _get_arg("--report", None)
    if report_arg:
        try:
            report_path = Path(report_arg)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            logger.error("Não foi possível gravar o relatório: %s", exc)

    for line in lines:
        logger.info(line)
    if "--show-dialog" in sys.argv:
        _show_message_box(
            "SmartEvents - Pacote de eventos",
            "\n".join(lines) or "Nenhuma alteração.",
            failed=exit_code == 3,
            warning=exit_code == 4,
        )
    return exit_code


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


def _setup_per_monitor_dpi():
    """
    Declara o processo Per-Monitor-DPI-Aware V2 antes de o pywebview subir a janela.

    O pywebview chama ``SetProcessDPIAware()`` (System DPI aware) ao iniciar. Num setup com
    monitores de escalas diferentes — o caso real aqui, notebook a 150% e monitor externo a
    100% — isso faz o Windows *virtualizar* a janela no monitor secundário: o Win32 responde
    coordenadas de um espaço (o DPI do sistema) enquanto o WebView2, que é per-monitor aware
    no processo dele, desenha em outro. É essa divergência que deixa a janela maximizada
    "torta", com parte do app para fora da tela.

    Com PMv2 não há virtualização: ``GetDpiForWindow``, ``GetMonitorInfoW`` e o WM_NCHITTEST
    da barra passam a falar o mesmo idioma do WebView2, e ``WM_DPICHANGED`` chega ao trocar
    de monitor. Chamado só no modo customizado — ``--native-titlebar`` continua com o
    comportamento anterior, preservando o rollback.
    """
    if os.name != "nt":
        return "nao-windows"
    import ctypes

    try:
        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except (AttributeError, OSError) as exc:
        logger.debug("SetProcessDpiAwarenessContext indisponível: %s", exc)
    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE (Windows 8.1+)
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except (AttributeError, OSError) as exc:
        logger.debug("SetProcessDpiAwareness indisponível: %s", exc)
    return "system (fallback)"


def main():
    mock_mode = "--mock" in sys.argv
    dev_mode = "--dev" in sys.argv

    from core.window_chrome import host_diagnostic, resolve_window_chrome_mode

    # A barra personalizada depende de o runtime WebView2 assumir a região não-cliente.
    # A leitura do Registro é best-effort: sem ela o padrão continua sendo o modo
    # personalizado, apenas sem essa garantia adicional.
    webview2_version = None
    try:
        from core.self_test import webview2_version as _read_webview2_version
        webview2_version = _read_webview2_version()
    except Exception as exc:
        logger.debug("Versão do WebView2 indisponível no Registro: %s", exc)

    chrome_decision = resolve_window_chrome_mode(
        sys.argv[1:], webview2_version=webview2_version, log=logger
    )
    custom_titlebar = chrome_decision.mode == "custom"

    # Precisa acontecer antes de qualquer import/janela do pywebview: a consciência de DPI
    # de um processo só pode ser definida uma vez, e quem chamar primeiro vence.
    if custom_titlebar:
        logger.info("Consciência de DPI do processo: %s", _setup_per_monitor_dpi())

    import webview
    from api.api import Api
    from core.scheduler import scheduler
    from core.window_chrome import WindowChromeController

    host = host_diagnostic()
    requested_mode = (
        "native" if chrome_decision.native_requested
        else "custom" if chrome_decision.custom_requested
        else "padrao"
    )
    logger.info(
        "Iniciando Smart Events | mock=%s | dev=%s | titlebar solicitada=%s | "
        "titlebar efetiva=%s | motivo=%s | sistema=%s | dpi=%s | webview2=%s",
        mock_mode,
        dev_mode,
        requested_mode,
        chrome_decision.mode,
        chrome_decision.reason,
        host.get("windows") or host.get("platform"),
        host.get("dpi"),
        webview2_version or "desconhecido",
    )

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

    chrome_controller = WindowChromeController(log=logger)
    shutdown = _ShutdownCoordinator(
        scheduler, chrome_controller, purge_alarms=db.clear_all_alarms
    )
    # A query informa o modo já na primeira pintura. Sem ela a barra só apareceria
    # depois do primeiro ``window_get_state``, empurrando todo o layout 36 px para
    # baixo com o WebView2 ainda carregando. O fragmento continua sendo exatamente
    # ``#desktop`` — é o que a bridge compara para não cair em modo mock.
    frontend_url = str(FRONTEND) + ("?chrome=custom" if custom_titlebar else "") + "#desktop"
    window = webview.create_window(
        title="Smart Events",
        url=frontend_url,
        js_api=api,
        width=1440,
        height=900,
        min_size=(1024, 600),
        resizable=True,
        frameless=custom_titlebar,
        easy_drag=False,
        fullscreen=False,
        maximized=True,
        background_color="#0D1117",
    )

    # Controles do shell ficam separados da API de dominio. O pywebview os
    # publica em ``window.pywebview.api`` pelos nomes abaixo, enquanto o
    # controlador continua sendo a unica unidade com acesso ao Win32.
    def window_minimize():
        return chrome_controller.minimize()

    def window_close():
        return shutdown.request_close()

    def window_get_state():
        return chrome_controller.get_state()

    def window_set_chrome_regions(payload):
        return chrome_controller.set_regions(payload)

    def window_begin_drag():
        return chrome_controller.begin_drag()

    def window_toggle_maximize():
        return chrome_controller.toggle_maximize()

    def window_toggle_fullscreen():
        return chrome_controller.toggle_fullscreen()

    window.expose(
        window_minimize,
        window_close,
        window_get_state,
        window_set_chrome_regions,
        window_begin_drag,
        window_toggle_maximize,
        window_toggle_fullscreen,
    )

    def _on_before_show():
        if not custom_titlebar:
            logger.info("Window chrome nao instalado: moldura nativa ativa")
            return
        if chrome_controller.attach(window):
            # A janela abre maximizada; isto define apenas para onde ela volta ao
            # restaurar, para o app nunca cair num retângulo menor que o dashboard.
            chrome_controller.apply_default_geometry(min_size=(1024, 600))
            # O CoreWebView2 ainda não existe aqui, e ligar o ``app-region``
            # depois do ``loaded`` não teria efeito algum sobre o documento já
            # carregado. O controlador agenda a ativação para o instante certo.
            chrome_controller.arm_nonclient_regions(window)
            logger.info("Window chrome instalado (attach=ok): %s", chrome_controller.get_state())
        else:
            # ``attach`` restaura WS_CAPTION e os estilos nativos antes de
            # retornar. A falha nunca deve impedir que a janela seja exibida.
            logger.error(
                "Window chrome em fallback nativo (attach=falhou) | motivo=%s | estado=%s",
                chrome_controller.last_error or "sem detalhes",
                chrome_controller.get_state(),
            )

    def _on_loaded():
        logger.info("Interface carregada")
        if custom_titlebar and not chrome_controller.get_state().get("nonclient"):
            # A ativação foi agendada em ``before_show``; se não valeu, o arraste
            # da barra não funciona — o caminho alternativo por mensagem esbarra
            # na captura do mouse, que pertence ao processo do WebView2.
            logger.warning(
                "WebView2 sem regiao nao-cliente: o arraste da barra de titulo "
                "nao estara disponivel."
            )
        if mock_mode:
            window.evaluate_js("window.__MOCK_MODE__ = true;")
        if custom_titlebar:
            # O primeiro quadro do WebView2 e composto enquanto a janela ainda
            # esta sendo maximizada e o WM_NCCALCSIZE da moldura customizada
            # ainda mexe na area cliente; as camadas perdidas nessa corrida
            # nunca voltam sozinhas (ver ERRORS.md). O atraso existe porque o
            # dashboard so termina de ser montado alguns segundos depois do
            # `loaded` — refazer a superficie antes disso nao adianta, a
            # corrida ainda estaria em curso.
            shutdown.schedule_repaint(FIRST_PAINT_REPAINT_DELAY)

    def _on_closing():
        # ``closing`` é bloqueante no pywebview: os workers param enquanto a
        # thread WinForms ainda possui message loop. Cobre Alt+F4 e fechamento
        # pela barra nativa; o botão customizado já chama o mesmo coordenador.
        shutdown.shutdown()

    window.events.before_show += _on_before_show
    window.events.loaded += _on_loaded
    window.events.closing += _on_closing

    try:
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
    finally:
        # Defesa para falha de startup e para qualquer caminho que não tenha
        # disparado ``closing``. Aqui estamos novamente na thread principal.
        shutdown.shutdown()
        db.close_conn()
        # Em uma destruicao Win32 normal o WNDPROC ja foi restaurado por
        # WM_NCDESTROY. O finally cobre falhas de startup e encerramentos atipicos.
        chrome_controller.detach()


if __name__ == "__main__":
    # Subprocessos sem janela: servidor embutido (--serve) ou renovação de sessão (--get-session).
    # Os modos de pacote são resolvidos antes de qualquer coisa do pywebview:
    # nem servidor, nem scheduler, nem navegador são inicializados aqui.
    if "--inspect-event-package" in sys.argv or "--import-event-package" in sys.argv:
        sys.exit(_run_event_package_mode())
    elif "--self-test-webview" in sys.argv:
        from core.self_test import run_webview_probe
        sys.exit(run_webview_probe())
    elif "--self-test-window-chrome" in sys.argv:
        from core.self_test import run_window_chrome_probe
        sys.exit(run_window_chrome_probe(_get_arg("--report", None)))
    elif "--self-test" in sys.argv:
        from core.self_test import run_self_test
        report_arg = _get_arg("--report", None)
        exit_code, report_path, report = run_self_test(
            report_arg,
            expect_events=_get_list_arg("--expect-events"),
            expect_package_report=_get_arg("--expect-package-report", None),
        )
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
