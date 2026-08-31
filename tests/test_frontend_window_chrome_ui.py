"""Testes da title bar HTML e da adaptacao dos layouts ao chrome da janela."""

from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _frontend_server():
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT / "frontend"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _skip_if_no_browser(exc):
    if "Executable doesn't exist" in str(exc):
        pytest.skip("Chromium do Playwright nao esta instalado neste ambiente")
    raise exc


@contextmanager
def _page(playwright, *, width=1440, height=900, reduced_motion=None):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": width, "height": height},
        reduced_motion=reduced_motion,
    )
    try:
        yield context.new_page()
    finally:
        context.close()
        browser.close()


def _open_preview(page, url):
    page.goto(f"{url}/index.html?chromePreview=1", wait_until="domcontentloaded")
    page.locator("#window-titlebar").wait_for(state="visible", timeout=5000)
    page.wait_for_function(
        "() => window.__windowChromeMock?.regions?.buttons?.close",
        timeout=5000,
    )


def test_barra_fica_oculta_no_navegador_comum():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _page(playwright) as page:
                page.goto(f"{url}/index.html", wait_until="domcontentloaded")
                page.wait_for_timeout(1200)
                assert page.locator("#window-titlebar").is_hidden()
                state = page.evaluate(
                    """() => ({
                      custom: document.documentElement.classList.contains('chrome-custom'),
                      titlebar: getComputedStyle(document.documentElement)
                        .getPropertyValue('--titlebar-h').trim(),
                    })"""
                )
                assert state == {"custom": False, "titlebar": "0px"}
    except Exception as exc:  # pragma: no cover - depende do browser instalado
        _skip_if_no_browser(exc)


@pytest.mark.parametrize("width", [1024, 1440, 1920])
def test_preview_renderiza_barra_e_regioes_sem_sobreposicao(width):
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _page(playwright, width=width, height=700) as page:
                _open_preview(page, url)
                assert page.locator(".window-titlebar-title").inner_text() == "Smart Events"
                assert page.locator(".window-titlebar-logo").get_attribute("src").endswith(
                    "logoSmartEvents-32.png"
                )
                assert page.locator("#window-minimize").get_attribute("aria-label") == "Minimizar"
                assert page.locator("#window-close").get_attribute("aria-label") == "Fechar"
                # O mock abre maximizado: o botao precisa oferecer "restaurar".
                assert page.locator("#window-maximize").get_attribute("aria-label") == "Restaurar"
                assert page.locator("#window-fullscreen").get_attribute("aria-pressed") == "false"
                glyphs = page.locator("#window-maximize svg:visible")
                assert glyphs.count() == 1
                assert "restore" in glyphs.get_attribute("class")

                regions = page.evaluate("() => window.__windowChromeMock.regions")
                assert regions["titlebar"]["height"] == pytest.approx(36, abs=0.1)
                assert regions["titlebar"]["width"] == pytest.approx(width, abs=0.1)
                drag = regions["draggable"][0]
                buttons = list(regions["buttons"].values())
                assert drag["x"] + drag["width"] <= buttons[0]["x"]
                for left, right in zip(buttons, buttons[1:]):
                    assert left["x"] + left["width"] <= right["x"]
                assert buttons[-1]["x"] + buttons[-1]["width"] <= width
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_controles_tem_ordem_de_tab_foco_arraste_e_mocks_inofensivos():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _page(playwright) as page:
                _open_preview(page, url)
                page.evaluate(
                    """() => {
                      document.body.tabIndex = -1;
                      document.body.focus();
                    }"""
                )
                focused = []
                for _ in range(4):
                    page.keyboard.press("Tab")
                    focused.append(page.evaluate("document.activeElement.id"))
                assert focused == [
                    "window-fullscreen",
                    "window-minimize",
                    "window-maximize",
                    "window-close",
                ]
                assert page.locator("#window-close").evaluate(
                    "el => getComputedStyle(el).outlineStyle"
                ) != "none"

                page.locator("#window-titlebar-drag").dispatch_event(
                    "pointerdown", {"button": 0, "detail": 0, "isPrimary": True}
                )
                page.locator("#window-minimize").click()
                page.wait_for_function(
                    "() => window.__windowChromeMock.calls.some(call => call.method === 'window_begin_drag')"
                )
                page.locator("#window-close").click()
                methods = page.evaluate(
                    "() => window.__windowChromeMock.calls.map(call => call.method)"
                )
                assert "window_minimize" in methods
                assert "window_begin_drag" in methods
                assert "window_close" in methods
                assert not page.is_closed()
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_maximizar_restaurar_e_tela_cheia_seguem_o_estado_da_janela():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _page(playwright) as page:
                _open_preview(page, url)
                assert page.evaluate("document.documentElement.dataset.windowState") == "maximized"

                page.locator("#window-maximize").click()
                page.wait_for_function(
                    "() => document.documentElement.dataset.windowState === 'normal'"
                )
                assert page.locator("#window-maximize").get_attribute("aria-label") == "Maximizar"

                # Duplo clique na barra equivale ao botao maximizar.
                page.wait_for_timeout(450)
                page.locator("#window-titlebar-drag").dblclick()
                page.wait_for_function(
                    "() => document.documentElement.dataset.windowState === 'maximized'"
                )

                page.wait_for_timeout(450)
                page.locator("#window-fullscreen").click()
                page.wait_for_function(
                    "() => document.documentElement.dataset.windowState === 'fullscreen'"
                )
                fullscreen = page.locator("#window-fullscreen")
                assert fullscreen.get_attribute("aria-pressed") == "true"
                assert fullscreen.get_attribute("aria-label") == "Sair da tela cheia"
                # Em tela cheia o glifo tambem oferece a volta.
                assert page.locator("#window-maximize").get_attribute("aria-label") == "Restaurar"

                toggles = page.evaluate(
                    """() => window.__windowChromeMock.calls
                         .filter(c => c.method.startsWith('window_toggle')).map(c => c.method)"""
                )
                assert toggles == [
                    "window_toggle_maximize",
                    "window_toggle_maximize",
                    "window_toggle_fullscreen",
                ]
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_duplo_clique_nao_alterna_duas_vezes():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _page(playwright) as page:
                _open_preview(page, url)
                # Reproduz o caminho nativo chegando junto com o handler do DOM:
                # dois pedidos quase simultaneos devem valer por um so.
                page.evaluate(
                    """() => {
                      const bar = document.getElementById('window-titlebar-drag');
                      bar.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
                      bar.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
                    }"""
                )
                page.wait_for_function(
                    "() => document.documentElement.dataset.windowState === 'normal'"
                )
                page.wait_for_timeout(300)
                toggles = page.evaluate(
                    """() => window.__windowChromeMock.calls
                         .filter(c => c.method === 'window_toggle_maximize').length"""
                )
                assert toggles == 1
                assert page.evaluate("document.documentElement.dataset.windowState") == "normal"
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_resize_reenvia_regioes_e_preserva_a_altura_logica():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _page(playwright, width=1440, height=900) as page:
                _open_preview(page, url)
                before = page.evaluate(
                    "() => window.__windowChromeMock.calls.filter(c => c.method === 'window_set_chrome_regions').length"
                )
                page.set_viewport_size({"width": 1024, "height": 600})
                page.wait_for_function(
                    """before => window.__windowChromeMock.calls
                      .filter(c => c.method === 'window_set_chrome_regions').length > before""",
                    arg=before,
                    timeout=5000,
                )
                regions = page.evaluate("() => window.__windowChromeMock.regions")
                assert regions["titlebar"]["width"] == pytest.approx(1024, abs=0.1)
                assert regions["titlebar"]["height"] == pytest.approx(36, abs=0.1)
                assert page.evaluate("document.documentElement.scrollHeight") == 600
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_drawers_modais_e_visao_geral_respeitam_o_chrome():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _page(playwright, width=1024, height=600) as page:
                _open_preview(page, url)
                layout = page.evaluate(
                    """() => {
                      for (const id of ['alert-drawer', 'alarms-drawer', 'logs-drawer']) {
                        document.getElementById(id).classList.remove('hidden');
                      }
                      const modal = document.getElementById('clear-history-modal');
                      modal.classList.remove('hidden');
                      const overview = document.getElementById('kpi-overview-modal');
                      overview.classList.remove('hidden');
                      const drawer = id => {
                        const style = getComputedStyle(document.getElementById(id));
                        return { top: parseFloat(style.top), height: parseFloat(style.height) };
                      };
                      const bar = document.getElementById('window-titlebar');
                      return {
                        alert: drawer('alert-drawer'),
                        alarms: drawer('alarms-drawer'),
                        logs: drawer('logs-drawer'),
                        modalTop: parseFloat(getComputedStyle(modal).top),
                        overviewHeight: document.querySelector('.kpi-overview-shell').getBoundingClientRect().height,
                        barZ: Number(getComputedStyle(bar).zIndex),
                        overviewZ: Number(getComputedStyle(overview).zIndex),
                        bodyOverflow: getComputedStyle(document.body).overflow,
                      };
                    }"""
                )
                for drawer in (layout["alert"], layout["alarms"], layout["logs"]):
                    assert drawer == {"top": 86, "height": 514}
                assert layout["modalTop"] == 36
                assert layout["overviewHeight"] == 564
                assert layout["barZ"] > layout["overviewZ"]
                assert layout["bodyOverflow"] == "hidden"

                # Mesmo com overlays abertos, o chrome continua recebendo clique.
                page.locator("#window-close").click()
                assert page.evaluate(
                    "() => window.__windowChromeMock.calls.some(c => c.method === 'window_close')"
                )
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_modo_nativo_preserva_layout_anterior():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _page(playwright, width=1280, height=720) as page:
                page.goto(f"{url}/index.html", wait_until="domcontentloaded")
                page.wait_for_timeout(1200)
                layout = page.evaluate(
                    """() => {
                      const drawer = document.getElementById('alert-drawer');
                      drawer.classList.remove('hidden');
                      const modal = document.getElementById('clear-history-modal');
                      modal.classList.remove('hidden');
                      return {
                        drawerTop: parseFloat(getComputedStyle(drawer).top),
                        drawerHeight: parseFloat(getComputedStyle(drawer).height),
                        modalTop: parseFloat(getComputedStyle(modal).top),
                      };
                    }"""
                )
                assert layout == {"drawerTop": 50, "drawerHeight": 670, "modalTop": 0}
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_prefers_reduced_motion_remove_transicoes_visiveis():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _page(playwright, reduced_motion="reduce") as page:
                _open_preview(page, url)
                durations = page.locator("#window-close").evaluate(
                    """el => ({
                      transition: getComputedStyle(el).transitionDuration,
                      animation: getComputedStyle(el).animationDuration,
                    })"""
                )
                assert durations["transition"] in {"0s", "1e-06s"}
                assert durations["animation"] in {"0s", "1e-06s"}
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)
