"""
core/session_renew.py — Renovação de sessão do iManager via Playwright.

Faz login no iManager (SPA), navega nos módulos de Performance Monitor e Signaling Trace e
captura os tokens de sessão (cookie `bspsession`, CSRF `roarand`) e metadados (task_id, obj_nos),
gravando tudo em `session.json`.

Usado em dois contextos:
  - Dev: `scratch/get_session.py` (CLI fino) chama `run(...)`.
  - .exe: `main.py --get-session` chama `run(...)`. Nesse caso o Chromium do Playwright é
    empacotado no bundle e localizado via PLAYWRIGHT_BROWSERS_PATH (ver _ensure_browsers_path).
"""

import json
import os
import sys
import time
import logging
import tempfile
from urllib.parse import urlparse, parse_qs
from pathlib import Path

logger = logging.getLogger(__name__)

# Credenciais e catálogo de clientes/regionais vivem em core/credentials.py (fonte única).
# Hierarquia: Cliente → Regional → base_url. Precedência das credenciais:
# data/credentials.json[CLIENTE][REGIÃO] → [CLIENTE]["_shared"] → vazio (sem fallback hardcoded).
from core import credentials as _credentials


def _data_dir() -> Path:
    """Diretório de dados PERSISTENTE (delega a core.credentials)."""
    return _credentials.data_dir()


def _seed_credentials():
    """1ª execução do .exe: semeia data/clientes.json (catálogo) e data/credentials.json (vazio)
    ao lado do executável. Delegado a core.credentials.seed_files()."""
    _credentials.seed_files()


def _resolve_credentials(cliente: str = "", region: str = "") -> tuple:
    """Resolve (username, password) de (cliente, regional). Delega a core.credentials.
    Faltando credencial, devolve vazio para preenchimento manual no navegador."""
    return _credentials.resolve_credentials(cliente, region)


def _browser_profile_path(session_path: Path, base_url: str) -> Path:
    """Perfil persistente exclusivo do host do OSS.

    O session file já é separado por host, mas todos ficavam no mesmo diretório e
    acabavam usando ``data/browser_profile``. O slug explícito impede cookies, SSO,
    cache e CAPTCHA de uma regional influenciarem outra.
    """
    host = (urlparse(base_url).hostname or "oss").lower()
    slug = "".join(ch if ch.isalnum() else "_" for ch in host).strip("_") or "oss"
    return session_path.parent / f"browser_profile_{slug}"

# Códigos de saída — o collector os usa para diferenciar a causa da falha.
EXIT_SUCCESS = 0           # sessão renovada E autenticada (sonda REST OK)
EXIT_GENERIC_FAIL = 1      # falha genérica (erro de execução, sem tokens, etc.)
EXIT_NEEDS_INTERACTIVE = 2 # login bloqueado por CAPTCHA/SSO — requer reauth interativa


def _ensure_browsers_path():
    """No .exe compilado, aponta o Playwright para os navegadores empacotados no bundle."""
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "ms-playwright"
        if bundled.exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)
            logger.info(f"PLAYWRIGHT_BROWSERS_PATH = {bundled}")
        else:
            logger.warning(f"Pasta de navegadores empacotada não encontrada: {bundled}")


def _extract_task_id_from_url(url: str):
    try:
        queries = parse_qs(urlparse(url).query)
        if "taskId" in queries:
            return int(queries["taskId"][0])
    except Exception:
        pass
    return None


def _still_on_login(page) -> bool:
    """True se a página ainda está no formulário de login do SSO (login não concluído)."""
    try:
        url = (page.url or "").lower()
        if "unisso" in url or "login.action" in url:
            return True
    except Exception:
        # Página possivelmente fechada/inacessível → considerar não-logado.
        return True
    try:
        if page.is_visible("#username"):
            return True
    except Exception:
        pass
    return False


def _is_auth_response(status: int, content_type: str, body: str, url: str = "") -> bool:
    """Heurística (espelha collector._check_session_valid): resposta JSON = sessão
    autenticada; redirecionamento HTML para o SSO/CAPTCHA = não-autenticada."""
    if status in (401, 403):
        return False
    
    url_low = (url or "").lower()
    if "unisso" in url_low or "login.action" in url_low:
        return False
        
    if "text/html" in (content_type or "").lower():
        low = (body or "")[:2000].lower()
        if any(k in low for k in ("login", "sso", "unisso", "authentication", "verifycode", "captcha")):
            return False
    return True


def _probe_authenticated(page, base_url: str, module: str, session_data: dict) -> bool:
    """Bate em um endpoint REST com os cookies VIVOS do navegador (page.request
    compartilha o cookie store do contexto) para confirmar que a sessão está de
    fato autenticada, e não presa no SSO/CAPTCHA. Retorna True só se autenticada."""
    roarand = session_data["trace"].get("roarand") or session_data["monitoring"].get("roarand")
    headers = {"roarand": roarand} if roarand else {}
    # Cookies são compartilhados entre os módulos (mesmo bspsession), então uma
    # única sonda confirma a autenticação para 'trace', 'monitoring' ou 'both'.
    task_id = session_data["trace"].get("task_id") or session_data["monitoring"].get("task_id") or 1
    nocache = int(time.time() * 1000)
    probe_url = (f"{base_url}/rest/oss/access/fars/v1/traceresult/pre-check"
                 f"?taskId={task_id}&queryType=0&nocache={nocache}")
    try:
        resp = page.request.get(probe_url, headers=headers, timeout=30000)
        url = resp.url
        try:
            ctype = resp.headers.get("content-type", "")
        except Exception:
            ctype = ""
        try:
            body = resp.text()
        except Exception:
            body = ""
        return _is_auth_response(resp.status, ctype, body, url)
    except Exception as e:
        logger.warning(f"Sonda de autenticação falhou: {e}")
        return False


def run(headless: bool = True, module: str = "both",
        base_url: str = "https://10.220.50.9:31943",
        session_file: str = None, region: str = "", cliente: str = "") -> int:
    """
    Renova a sessão do iManager. Retorna EXIT_SUCCESS/EXIT_GENERIC_FAIL/EXIT_NEEDS_INTERACTIVE.
    `module`: 'trace' | 'monitoring' | 'both'.
    `cliente`/`region`: cliente (TIM, Vivo, …) e regional do OSS (SP, RJ, …) — definem quais
    credenciais usar (Cliente → Regional).
    """
    _ensure_browsers_path()
    _seed_credentials()  # garante clientes.json + credentials.json ao lado do .exe na 1ª execução

    # Credenciais do par (cliente, regional). Conta dedicada por OSS.
    username, password = _resolve_credentials(cliente, region)

    # Import tardio: só carrega o Playwright quando a renovação é de fato acionada.
    from playwright.sync_api import sync_playwright, Request

    base_url = base_url.rstrip("/")
    login_url = f"{base_url}/unisso/login.action"
    pm_url = f"{base_url}/ossfacewebsite/index.html#Access/Access_Performance_Monitor"
    trace_url = f"{base_url}/ossfacewebsite/index.html#Access/Access_FARS_MENU_TASK_BROWSE"

    if session_file:
        session_path = Path(session_file)
    else:
        session_path = _data_dir() / "session.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)

    # Preserva seções não renovadas por esta execução.
    existing = {}
    try:
        if session_path.exists():
            existing = json.loads(session_path.read_text(encoding="utf-8"))
    except Exception:
        pass

    # Metadados de task/objeto são uma observação da SPA, não uma configuração.
    # Ao renovar um módulo, nunca os reapresentamos como se tivessem sido
    # recapturados: a nova execução precisa observá-los novamente no Network.
    session_data = {
        "trace": dict(existing.get("trace") or {}),
        "monitoring": dict(existing.get("monitoring") or {}),
    }
    for section in ("trace", "monitoring"):
        session_data[section].setdefault("bspsession", None)
        session_data[section].setdefault("roarand", None)
        session_data[section].setdefault("task_id", None)
        session_data[section].setdefault("cookies", [])
    session_data["monitoring"].setdefault("obj_nos", [])
    session_data["monitoring"].setdefault("tasks", [])
    reset_sections = ("trace", "monitoring") if module == "both" else (module,)
    for section in reset_sections:
        session_data[section]["task_id"] = None
        session_data[section]["roarand"] = None
        session_data[section]["cookies"] = []
        if section == "monitoring":
            session_data[section]["obj_nos"] = []
            session_data[section]["tasks"] = []

    def monitor_requests(request: "Request"):
        url = request.url
        headers = request.headers
        if "/rest/oss/access/fars/" in url:
            roarand = headers.get("roarand") or headers.get("Roarand")
            if roarand:
                session_data["trace"]["roarand"] = roarand
            task_id = _extract_task_id_from_url(url)
            if task_id:
                session_data["trace"]["task_id"] = task_id
            if request.method == "POST":
                try:
                    payload = json.loads(request.post_data)
                    if isinstance(payload, dict) and "taskId" in payload:
                        session_data["trace"]["task_id"] = payload["taskId"]
                except Exception:
                    pass
        elif "/rest/oss/access/pm/" in url:
            roarand = headers.get("roarand") or headers.get("Roarand")
            if roarand:
                session_data["monitoring"]["roarand"] = roarand
            if request.method == "POST":
                try:
                    payload = json.loads(request.post_data)
                    items = payload if isinstance(payload, list) else [payload]
                    for item in items:
                        if isinstance(item, dict):
                            if "taskId" in item:
                                session_data["monitoring"]["task_id"] = item["taskId"]
                                if item["taskId"] not in session_data["monitoring"]["tasks"]:
                                    session_data["monitoring"]["tasks"].append(item["taskId"])
                            if "objNoExecTimes" in item:
                                obj_nos = [x["objNo"] for x in item["objNoExecTimes"] if "objNo" in x]
                                current = set(session_data["monitoring"]["obj_nos"])
                                for obj in obj_nos:
                                    if obj not in current:
                                        session_data["monitoring"]["obj_nos"].append(obj)
                except Exception:
                    pass

    with sync_playwright() as p:
        # Perfil PERSISTENTE (não descartável): mantém o cache de disco do Chromium QUENTE
        # entre execuções. O iManager é uma SPA pesada — com perfil novo a cada vez, todos
        # os assets eram rebaixados pela VPN lenta (~3x mais lento que um navegador normal),
        # o que fazia os waits de PM/Trace expirarem antes da SPA disparar as chamadas REST
        # que carregam o roarand → "Nenhuma sessão ou token pôde ser capturado". O perfil
        # persistente também guarda os cookies, então muitas renovações dispensam novo login.
        user_data_dir = _browser_profile_path(session_path, base_url)
        user_data_dir.mkdir(parents=True, exist_ok=True)
        context = p.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=headless,
            ignore_https_errors=True,
            viewport={"width": 1366, "height": 768},
            args=["--ignore-certificate-errors"],
        )
        browser = context.browser  # usado só para detectar o operador fechando a janela

        def _alive() -> bool:
            """True enquanto a janela do operador continua aberta. Robusto a
            context.browser ser None em alguns builds do Playwright."""
            try:
                return browser.is_connected() if browser is not None else bool(context.pages)
            except Exception:
                return False

        page = context.pages[0] if context.pages else context.new_page()
        page.on("request", monitor_requests)

        # 1. Login — preenche as credenciais. O CAPTCHA (código exibido em imagem)
        #    NÃO pode ser resolvido automaticamente: em headless o login não conclui
        #    e retornamos EXIT_NEEDS_INTERACTIVE; em modo visível o operador digita o
        #    CAPTCHA (e ajusta usuário/senha por regional, se preciso) e conclui o login.
        already_auth = False
        try:
            # 'domcontentloaded' (não 'load'): a página de login do iManager é uma SPA
            # pesada que frequentemente não dispara o evento 'load' dentro de 30s
            # (sub-recursos/long-polling pendentes), causando timeout antes mesmo de
            # exibir o formulário. O formulário (#username) já existe no DOM inicial.
            page.goto(login_url, timeout=60000, wait_until="domcontentloaded")
            # Com perfil persistente a sessão pode já estar autenticada (SSO redireciona
            # para o app e o formulário nem aparece). Nesse caso, pula o login.
            if not _still_on_login(page) and _probe_authenticated(page, base_url, module, session_data):
                logger.info("Sessão do perfil persistente já autenticada — pulando login.")
                already_auth = True
            else:
                page.wait_for_selector("#username", timeout=15000)
                page.fill("#username", username)
                page.wait_for_selector("#value", timeout=10000)
                page.fill("#value", password)

                if headless:
                    # Em headless submetemos direto (só conclui quando NÃO há CAPTCHA).
                    # No modo interativo NÃO auto-submetemos: o operador digita o CAPTCHA
                    # e clica em entrar — auto-submeter com CAPTCHA vazio só atrapalha.
                    # NÃO usar heurística de "input visível extra = CAPTCHA": a página de
                    # login do iManager tem campos visíveis que não são CAPTCHA, gerando
                    # falso positivo que aborta TODA renovação headless. A detecção honesta
                    # de CAPTCHA é o bloco pós-submit abaixo (_still_on_login após o submit),
                    # que só dispara EXIT_NEEDS_INTERACTIVE quando o login realmente não passa.
                    page.click("#submitDataverify")
        except Exception as e:
            logger.error(f"Falha no login: {e}")
            try:
                context.close()
            except Exception:
                pass
            return EXIT_GENERIC_FAIL

        if already_auth:
            pass
        elif headless:
            page.wait_for_timeout(7000)
            if _still_on_login(page):
                logger.error(
                    "Login bloqueado (CAPTCHA/credencial) e não resolvível em modo headless "
                    "— requer reautenticação interativa (navegador visível)."
                )
                try:
                    context.close()
                except Exception:
                    pass
                return EXIT_NEEDS_INTERACTIVE
        else:
            print("\n" + "*" * 60)
            print(" REAUTENTICAÇÃO INTERATIVA DO iManager")
            print(" -> Confira usuário/senha da regional, digite o CÓDIGO DE")
            print("    VERIFICAÇÃO (CAPTCHA) exibido na imagem e conclua o login.")
            print("    Aguardando o login ser concluído (até 240s)...")
            print("*" * 60 + "\n")
            # Detecção de login concluído pela VERDADE-BASE: uma chamada REST autenticada
            # (sonda). O heurístico de URL/DOM falha porque o iManager pode fazer transição
            # in-page mantendo 'login.action' na URL mesmo após autenticar — o que travava
            # a detecção e gerava "login não concluído" mesmo com o operador logado.
            start = time.time()
            completed = False
            while time.time() - start < 240:
                if not _alive():
                    break
                # Garante um roarand para a sonda (CSRF), via sessionStorage, se ainda não houver.
                try:
                    sr = page.evaluate("sessionStorage.getItem('u2020Showedrand')")
                    if sr:
                        for _m in ("trace", "monitoring"):
                            session_data[_m]["roarand"] = session_data[_m].get("roarand") or sr
                except Exception:
                    pass
                # Concluído quando a sonda REST autentica (verdade-base, independente
                # de heurística de URL/DOM — que é justamente o que falhava antes).
                if _probe_authenticated(page, base_url, module, session_data):
                    completed = True
                    break
                time.sleep(2)
            if not _alive():
                logger.error("Janela de reautenticação fechada antes de concluir o login.")
                return EXIT_NEEDS_INTERACTIVE
            if not completed:
                logger.error("Login interativo não concluído no tempo limite (sonda REST não autenticou).")
                try:
                    context.close()
                except Exception:
                    pass
                return EXIT_NEEDS_INTERACTIVE
            page.wait_for_timeout(2000)

        # Caminho RÁPIDO: na reauth do operador (janela visível) ou quando o perfil
        # persistente já abriu autenticado, a sessão já foi CONFIRMADA pela sonda REST e
        # o roarand já veio do sessionStorage. Só pulamos a navegação por PM/Trace se
        # já tivermos capturado o roarand (anti-CSRF) com sucesso. Caso contrário,
        # precisamos ir para os módulos PM/Trace para que os requests de inicialização
        # do iManager disparem o cabeçalho 'roarand' e nós possamos capturá-lo.
        has_roarand = (
            session_data["trace"].get("roarand") is not None
            and session_data["monitoring"].get("roarand") is not None
        )
        fast_capture = (already_auth or not headless) and has_roarand

        # 2-3. Navegação por Performance Monitor / Signaling Trace — apenas quando
        # não pudermos fazer a captura rápida (por exemplo, no login headless limpo ou
        # quando o roarand ainda precisa ser capturado nos módulos).
        if not fast_capture:
            if module in ("monitoring", "both"):
                try:
                    page.goto(pm_url, timeout=60000, wait_until="domcontentloaded")
                    page.wait_for_timeout(10000)
                except Exception as e:
                    logger.warning(f"Falha ao ir para PM: {e}")
            if module in ("trace", "both"):
                try:
                    page.goto(trace_url, timeout=60000, wait_until="domcontentloaded")
                    page.wait_for_timeout(10000)
                except Exception as e:
                    logger.warning(f"Falha ao ir para Trace: {e}")

        # 4. Cookies e tokens globais — filtrados pelo HOST da regional ativa.
        # O perfil já é separado por host. Mantemos também o filtro defensivo por HOST
        # (e não por context.cookies(urls=...), que casa o PATH e descartava o bspsession
        # quando ele não está no path "/"). Preservamos o 'path' de cada cookie:
        # o iManager usa cookies homônimos (ex.: JSESSIONID) em paths distintos (/unisso vs /);
        # sem path, o requests assume "/" e um sobrescreve o outro.
        import urllib.parse
        host = (urllib.parse.urlparse(base_url).hostname or "").lower()
        all_cookies = context.cookies()
        cookies = [
            c for c in all_cookies
            if not host or c.get("domain", "").lstrip(".").lower() == host
        ]
        cookie_list = [
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", ""),
                "path": c.get("path", "/"),
            }
            for c in cookies
        ]
        session_data["trace"]["cookies"] = cookie_list
        session_data["monitoring"]["cookies"] = cookie_list

        bspsessions = [c for c in cookies if c["name"] == "bspsession"]
        if bspsessions:
            global_bsp = bspsessions[0]["value"]
            session_data["trace"]["bspsession"] = global_bsp
            session_data["monitoring"]["bspsession"] = global_bsp

        # roarand: prioriza o token capturado de uma requisição REST autenticada
        # (monitor_requests). Só usa o sessionStorage como fallback quando o header
        # não foi observado — evita enviar um CSRF que o backend REST rejeita.
        try:
            showedrand = page.evaluate("sessionStorage.getItem('u2020Showedrand')")
            if showedrand:
                for _m in ("trace", "monitoring"):
                    if not session_data[_m].get("roarand"):
                        session_data[_m]["roarand"] = showedrand
        except Exception as e:
            logger.warning(f"Não foi possível ler token do sessionStorage: {e}")

        # 5. Captura interativa de metadados — RELÍQUIA DE DEV. Só roda quando explicitamente
        # pedido (SMARTEVENTS_CAPTURE_METADATA=1). Na reauth do operador NÃO deve rodar:
        # adicionava até 60s de janela aberta e prompts confusos. task_id/obj_nos são apenas
        # fallbacks (o coletor usa o pm_task_id do evento e os task_ids dos VIPs).
        if (os.environ.get("SMARTEVENTS_CAPTURE_METADATA") == "1" and not headless
                and (not session_data["trace"]["task_id"] or not session_data["monitoring"]["task_id"])):
            print("\n" + "*" * 60)
            print(" CAPTURA INTERATIVA DE METADADOS (60s):")
            print(" -> No Performance Monitor, clique em Consultar KPIs.")
            print(" -> No Signaling Trace, abra os resultados da tarefa ativa.")
            print("*" * 60 + "\n")
            start = time.time()
            try:
                while time.time() - start < 60:
                    if not _alive():
                        break
                    if (session_data["trace"]["task_id"] and session_data["monitoring"]["task_id"]
                            and len(session_data["monitoring"]["obj_nos"]) > 0):
                        break
                    time.sleep(1)
            except Exception:
                pass

        # ── Verificação de autenticação real ────────────────────────────
        # Sem tokens não há nem o que validar.
        tokens_present = (
            session_data["trace"]["bspsession"] is not None
            and (session_data["trace"]["roarand"] is not None
                 or session_data["monitoring"]["roarand"] is not None)
        )
        if not tokens_present:
            logger.error("Nenhuma sessão ou token pôde ser capturado.")
            try:
                context.close()
            except Exception:
                pass
            return EXIT_GENERIC_FAIL

        # Confirma com uma sonda REST que a sessão capturada está autenticada.
        # Sem isto, uma sessão presa no SSO/CAPTCHA seria gravada como "sucesso" e
        # o coletor entraria em loop infinito de renovação (bug original).
        if not _probe_authenticated(page, base_url, module, session_data):
            logger.error(
                "Sessão capturada NÃO está autenticada (sonda REST devolveu SSO/HTML). "
                "Provável CAPTCHA/login incompleto — requer reautenticação interativa."
            )
            try:
                context.close()
            except Exception:
                pass
            return EXIT_NEEDS_INTERACTIVE

        # Só publica uma sessão depois de cookies, token CSRF e sonda REST terem
        # sido confirmados. ``replace`` é atômico no mesmo volume, evitando que
        # outro worker leia JSON parcial durante a renovação.
        fd, temp_name = tempfile.mkstemp(prefix="session-", suffix=".tmp", dir=session_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                json.dump(session_data, temp_file, indent=4)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_name, session_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        logger.info(f"Sessão renovada e autenticada com sucesso em {session_path}")
        if not headless:
            print(f"[SUCESSO] Sessão salva em: {session_path.absolute()}")

        try:
            context.close()
        except Exception:
            pass
        return EXIT_SUCCESS
