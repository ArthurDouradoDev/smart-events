"""Smoke tests da comparação multi-escopo na visão geral de KPIs.

Mesmo padrão de tests/test_frontend_cluster_filter_ui.py: serve frontend/ estático
e dirige com Playwright contra os mocks reproduzíveis de bridge.js.
"""
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


ROOT = Path(__file__).resolve().parents[1]
OVERVIEW_JS = ROOT / "frontend" / "js" / "kpi_overview.js"


@contextmanager
def _frontend_server():
    root = ROOT / "frontend"
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


_PANEL_DATASETS = """(panelId) => {
  const canvas = document.querySelector(
    `.kpi-overview-card[data-panel-id="${panelId}"] canvas`);
  const chart = Chart.getChart(canvas);
  if (!chart) return null;
  return {
    axes: Object.keys(chart.options.scales),
    legend: !!chart.options.plugins.legend.display,
    datasets: chart.data.datasets.map(d => ({
      label: d.label,
      color: d.borderColor,
      dash: (d.borderDash || []).length,
      scopeKey: d.scopeKey,
      type: d.type || chart.config.type,
    })),
  };
}"""


@contextmanager
def _overview(playwright):
    browser = playwright.chromium.launch(headless=True)
    try:
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        yield page, browser
    finally:
        browser.close()


def _open_overview(page, url):
    page.goto(f"{url}/index.html", wait_until="domcontentloaded")
    page.locator(".site-item").first.wait_for(state="visible", timeout=8000)
    page.locator("#open-kpi-overview").click()
    page.wait_for_function(
        """() => {
          const canvas = document.querySelector('.kpi-overview-card canvas');
          return !!canvas && !!Chart.getChart(canvas);
        }""",
        timeout=8000,
    )
    # Os escopos chegam por chamada assíncrona; só então os dropdowns têm opções.
    page.wait_for_function(
        """() => document.querySelectorAll(
             '#kpi-overview-cluster-picker .scope-picker-option').length >= 3
           && document.querySelectorAll(
             '#kpi-overview-site-picker .scope-picker-option').length > 0""",
        timeout=8000,
    )


def _open_picker(page, picker_id):
    if not page.locator(f"#{picker_id}.open").count():
        page.locator(f"#{picker_id} .scope-picker-trigger").click()


def _pick(page, picker_id, name, checked=True):
    """Abre o dropdown e deixa a opção com o rótulo informado no estado pedido."""
    _open_picker(page, picker_id)
    box = page.locator(
        f"#{picker_id} .scope-picker-option", has_text=name).first.locator("input")
    if box.is_checked() != checked:
        box.click()
    page.locator(f"#{picker_id} .scope-picker-trigger").click()


def _clear_selection(page):
    """Zera a comparação — o modal abre herdando o recorte do dashboard."""
    for picker_id in ("kpi-overview-cluster-picker", "kpi-overview-site-picker"):
        _open_picker(page, picker_id)
        while True:
            checked = page.locator(
                f"#{picker_id} .scope-picker-option input:checked")
            if not checked.count():
                break
            checked.first.click()
        page.locator(f"#{picker_id} .scope-picker-trigger").click()


def _skip_if_no_browser(exc):
    if "Executable doesn't exist" in str(exc):
        pytest.skip("Chromium do Playwright não está instalado neste ambiente")
    raise exc


def test_paleta_de_series_nao_e_reordenada_sem_revalidar():
    # A ordem é o mecanismo de segurança para daltonismo, não estética: se ela
    # mudar, o validador de paleta precisa rodar de novo (ver comentário no JS).
    source = OVERVIEW_JS.read_text(encoding="utf-8")

    assert '"#388BFD", "#F85149", "#ab7df6", "#3FB950",' in source
    assert '"#00d2ff", "#D29922", "#f692cc", "#FF7B00",' in source


def test_toolbar_tem_dropdowns_separados_de_clusters_e_sites():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url)

                assert page.locator("#kpi-overview-cluster-picker").is_visible()
                assert page.locator("#kpi-overview-site-picker").is_visible()
                assert page.locator("#kpi-overview-scope-selector").count() == 0

                page.locator("#kpi-overview-cluster-picker .scope-picker-trigger").click()
                cluster_options = page.locator(
                    "#kpi-overview-cluster-picker .scope-picker-option"
                ).all_inner_texts()
                assert any("Todos os clusters" in o for o in cluster_options)
                assert any("Arquibancada Sul" in o for o in cluster_options)
                assert any("Campo (5G)" in o for o in cluster_options)
                # O dropdown de clusters não lista sites — são seletores separados.
                assert not any("ERB-07" in o for o in cluster_options)

                page.locator("#kpi-overview-site-picker .scope-picker-trigger").click()
                site_options = page.locator(
                    "#kpi-overview-site-picker .scope-picker-option"
                ).all_inner_texts()
                assert any("ERB-07" in o for o in site_options)
                assert not any("Arquibancada Sul" in o for o in site_options)
    except Exception as exc:  # pragma: no cover - depende do browser instalado
        _skip_if_no_browser(exc)


def test_comparar_todos_os_clusters_gera_uma_serie_por_cluster():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url)
                _clear_selection(page)
                _pick(page, "kpi-overview-cluster-picker", "Todos os clusters")
                page.wait_for_function(
                    """() => {
                      const canvas = document.querySelector(
                        '.kpi-overview-card[data-panel-id="drop_rate"] canvas');
                      const chart = Chart.getChart(canvas);
                      return !!chart && chart.data.datasets.length === 2;
                    }""",
                    timeout=8000,
                )

                panel = page.evaluate(_PANEL_DATASETS, "drop_rate")
                assert [d["label"] for d in panel["datasets"]] == [
                    "Arquibancada Sul", "Campo (5G)"]
                # Cor por entidade: cada cluster mantém a cor cadastrada.
                assert [d["color"] for d in panel["datasets"]] == ["#F85149", "#388BFD"]
                # Escopos consultados em chamadas separadas têm de cair na mesma
                # grade: um eixo por escopo fragmentaria as linhas em buracos.
                gaps = page.evaluate(
                    """() => {
                      const canvas = document.querySelector(
                        '.kpi-overview-card[data-panel-id="drop_rate"] canvas');
                      const chart = Chart.getChart(canvas);
                      return chart.data.datasets.map(
                        d => d.data.filter(v => v == null).length);
                    }"""
                )
                assert gaps == [0, 0]
                # Uma legenda só, no cabeçalho — não nove dentro dos cards.
                assert panel["legend"] is False
                assert page.locator(
                    "#kpi-overview-context .kpi-overview-chip").all_inner_texts() == [
                    "Arquibancada Sul", "Campo (5G)"]
                assert page.locator(
                    "#kpi-overview-cluster-picker .scope-picker-value"
                ).inner_text() == "Todos"
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_paineis_pareados_usam_linha_continua_no_dl_e_tracejada_no_ul():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url)
                _clear_selection(page)
                page.locator(
                    '#kpi-overview-family-tabs [data-family="5G"]').click()
                _pick(page, "kpi-overview-cluster-picker", "Todos os clusters")
                page.wait_for_function(
                    """() => {
                      const canvas = document.querySelector(
                        '.kpi-overview-card[data-panel-id="throughput"] canvas');
                      const chart = Chart.getChart(canvas);
                      return !!chart && chart.data.datasets.length === 4;
                    }""",
                    timeout=8000,
                )

                panel = page.evaluate(_PANEL_DATASETS, "throughput")
                assert [d["label"] for d in panel["datasets"]] == [
                    "Arquibancada Sul · DL", "Arquibancada Sul · UL",
                    "Campo (5G) · DL", "Campo (5G) · UL",
                ]
                # Nenhuma barra sobrou: os quatro traços são linhas.
                assert {d["type"] for d in panel["datasets"]} == {"line"}
                # DL contínuo, UL tracejado, na mesma cor do cluster.
                assert [d["dash"] for d in panel["datasets"]] == [0, 2, 0, 2]
                assert panel["datasets"][0]["color"] == panel["datasets"][1]["color"]
                assert panel["datasets"][2]["color"] == panel["datasets"][3]["color"]
                assert panel["datasets"][0]["color"] != panel["datasets"][2]["color"]
                # Eixo único — DL e UL do painel compartilham a unidade.
                assert panel["axes"] == ["x", "yLeft"]
                assert page.locator(
                    '.kpi-overview-card[data-panel-id="throughput"] '
                    '.kpi-overview-dash-hint').is_visible()
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_cluster_e_site_podem_ser_comparados_juntos():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url)
                _clear_selection(page)
                _pick(page, "kpi-overview-cluster-picker", "Arquibancada Sul")
                _pick(page, "kpi-overview-site-picker", "ERB-07")
                page.wait_for_function(
                    """() => {
                      const canvas = document.querySelector(
                        '.kpi-overview-card[data-panel-id="drop_rate"] canvas');
                      const chart = Chart.getChart(canvas);
                      return !!chart && chart.data.datasets.length === 2;
                    }""",
                    timeout=8000,
                )

                panel = page.evaluate(_PANEL_DATASETS, "drop_rate")
                assert [d["scopeKey"] for d in panel["datasets"]] == [
                    "cluster:sul", "site:ERB-07"]
                # Séries distintas: escopos diferentes não podem virar a mesma curva.
                values = page.evaluate(
                    """() => {
                      const canvas = document.querySelector(
                        '.kpi-overview-card[data-panel-id="drop_rate"] canvas');
                      return Chart.getChart(canvas).data.datasets.map(d => d.data[0]);
                    }"""
                )
                assert values[0] != values[1]
                context = page.locator("#kpi-overview-context").inner_text()
                assert "Arquibancada Sul" in context and "ERB-07" in context
                # Cluster e site nunca podem sair com a mesma cor no mesmo painel.
                assert panel["datasets"][0]["color"] != panel["datasets"][1]["color"]
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_legenda_do_cabecalho_oculta_o_escopo_nos_nove_paineis():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url)
                _clear_selection(page)
                _pick(page, "kpi-overview-cluster-picker", "Todos os clusters")
                page.wait_for_function(
                    """() => document.querySelectorAll(
                         '#kpi-overview-context .kpi-overview-chip').length === 2""",
                    timeout=8000,
                )

                page.locator(
                    '#kpi-overview-context .kpi-overview-chip',
                    has_text="Campo (5G)").click()
                page.wait_for_function(
                    """() => {
                      const cards = document.querySelectorAll('.kpi-overview-card canvas');
                      return [...cards].every(canvas => {
                        const chart = Chart.getChart(canvas);
                        return chart && !chart.isDatasetVisible(1);
                      });
                    }""",
                    timeout=5000,
                )
                assert page.locator(
                    '#kpi-overview-context .kpi-overview-chip.is-off').count() == 1

                # O último escopo visível não pode ser desligado.
                page.locator(
                    '#kpi-overview-context .kpi-overview-chip',
                    has_text="Arquibancada Sul").click()
                assert page.locator(
                    '#kpi-overview-context .kpi-overview-chip.is-off').count() == 1
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_sem_escopo_selecionado_a_visao_pede_uma_selecao():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url)
                _clear_selection(page)

                error = page.locator("#kpi-overview-error")
                error.wait_for(state="visible", timeout=5000)
                assert "Selecione ao menos um cluster ou site" in error.inner_text()
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)
