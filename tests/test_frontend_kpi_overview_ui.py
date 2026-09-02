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


def _open_overview(page, url, query=""):
    page.goto(f"{url}/index.html{query}", wait_until="domcontentloaded")
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


def _show_all_sites(page):
    _open_picker(page, "kpi-overview-site-picker")
    checkbox = page.locator("#kpi-overview-site-picker .scope-picker-filter input")
    if checkbox.is_checked():
        checkbox.click()
    page.locator("#kpi-overview-site-picker .scope-picker-trigger").click()


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


def test_visao_herda_site_ou_cluster_sem_marcar_portadoras_por_padrao():
    source = OVERVIEW_JS.read_text(encoding="utf-8")

    assert 'siteIds.has(State.selectedSite)' in source
    assert 'clusterIds.has(State.clusterFilter)' in source
    assert '} else if (carriers.length) {' not in source


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
                assert any("ERB-03" in o for o in site_options)
                assert not any("Arquibancada Sul" in o for o in site_options)
    except Exception as exc:  # pragma: no cover - depende do browser instalado
        _skip_if_no_browser(exc)


def test_seletor_de_sites_filtra_pela_geometria_real_do_poligono():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url)
                _open_picker(page, "kpi-overview-site-picker")

                checkbox = page.locator(
                    "#kpi-overview-site-picker .scope-picker-filter input")
                assert checkbox.is_checked()
                assert page.locator(
                    "#kpi-overview-site-picker .scope-picker-filter"
                ).inner_text() == "Apenas sites do polígono"

                filtered = page.locator(
                    "#kpi-overview-site-picker .scope-picker-option"
                ).all_inner_texts()
                assert any("ERB-03" in item for item in filtered)
                assert any("ERB-15" in item for item in filtered)
                # Todos vêm marcados como `is_event_site` no mock. Estes três
                # ficam de fora exclusivamente pela geometria do polígono.
                assert not any("ERB-07" in item for item in filtered)
                assert not any("ERB-11" in item for item in filtered)
                assert not any("SPSMG7" in item for item in filtered)

                checkbox.click()
                all_sites = page.locator(
                    "#kpi-overview-site-picker .scope-picker-option"
                ).all_inner_texts()
                assert len(all_sites) == 5
                assert any("SPSMG7" in item for item in all_sites)

                page.locator(
                    "#kpi-overview-site-picker .scope-picker-option",
                    has_text="SPSMG7",
                ).first.locator("input").click()
                checkbox.click()
                assert checkbox.is_checked()
                assert page.locator(
                    "#kpi-overview-site-picker .scope-picker-option",
                    has_text="SPSMG7",
                ).count() == 0
                assert page.locator(
                    "#kpi-overview-context .kpi-overview-chip",
                    has_text="SPSMG7",
                ).count() == 0
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def _cell_option_texts(page):
    page.locator("#kpi-overview-cell-picker .scope-picker-trigger").click()
    texts = page.locator("#kpi-overview-cell-picker .scope-picker-option").all_inner_texts()
    page.locator("#kpi-overview-cell-picker .scope-picker-trigger").click()
    return texts


def test_celulas_sem_selecao_mostram_apenas_as_dos_clusters_do_evento():
    """Sem cluster/site selecionado, a lista cai para as células que já estão
    organizadas em algum cluster — não o evento inteiro."""
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url)
                _clear_selection(page)

                options = _cell_option_texts(page)

                # ERB-07 e ERB-03 estão no cluster "sul"; SPSMG7 está no "campo".
                assert any("ERB-07-A1" in o for o in options)
                assert any("ERB-03-A1" in o for o in options)
                assert any("4G-SPSMG7-1" in o for o in options)
                # ERB-11 e ERB-15 não pertencem a nenhum cluster.
                assert not any("ERB-11" in o for o in options)
                assert not any("ERB-15" in o for o in options)
                assert len(options) == 3 + 3 + 12
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_celulas_recorta_pelo_cluster_selecionado():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url)
                _clear_selection(page)
                _pick(page, "kpi-overview-cluster-picker", "Arquibancada Sul")

                options = _cell_option_texts(page)

                assert any("ERB-07-A1" in o for o in options)
                assert any("ERB-03-A1" in o for o in options)
                # Campo (5G) / SPSMG7 não foi selecionado — fica de fora.
                assert not any("SPSMG7" in o for o in options)
                assert len(options) == 6
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_celulas_recorta_pelo_site_selecionado():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url)
                _clear_selection(page)
                _show_all_sites(page)
                # ERB-11 não pertence a nenhum cluster — só a seleção direta do
                # site deve trazer as células dele para a lista.
                _pick(page, "kpi-overview-site-picker", "ERB-11 Autódromo Sul")

                options = _cell_option_texts(page)

                assert len(options) == 3
                assert all("ERB-11" in o for o in options)
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_celula_ja_selecionada_continua_visivel_fora_do_escopo():
    """Trocar o site selecionado não pode esconder uma célula já escolhida —
    senão o usuário perde o único jeito de desmarcá-la pelo dropdown."""
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url)
                _clear_selection(page)
                _show_all_sites(page)
                _pick(page, "kpi-overview-site-picker", "ERB-11 Autódromo Sul")
                _pick(page, "kpi-overview-cell-picker", "ERB-11-A1")

                # Troca o site selecionado: ERB-11 sai do recorte, ERB-15 entra.
                _pick(page, "kpi-overview-site-picker", "ERB-11 Autódromo Sul", checked=False)
                _pick(page, "kpi-overview-site-picker", "ERB-15 Buffer Norte")

                options = _cell_option_texts(page)

                assert any("ERB-11-A1" in o for o in options)
                assert not any("ERB-11-A2" in o for o in options)
                assert any("ERB-15-A1" in o for o in options)
                assert page.locator(
                    "#kpi-overview-cell-picker .scope-picker-option",
                    has_text="ERB-11-A1",
                ).first.locator("input").is_checked()
    except Exception as exc:  # pragma: no cover
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
                _pick(page, "kpi-overview-site-picker", "ERB-03")
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
                    "cluster:sul", "site:ERB-03"]
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
                assert "Arquibancada Sul" in context and "ERB-03" in context
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
                assert "Selecione ao menos um cluster, site ou célula" in error.inner_text()
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def _abrir_5g(page, url, query=""):
    """Visão geral no 5G comparando os dois clusters — o cenário dos pares."""
    _open_overview(page, url, query)
    _clear_selection(page)
    page.locator('#kpi-overview-family-tabs [data-family="5G"]').click()
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


_PAIRED_PANELS = ("prb_utility", "throughput", "traffic_volume_sa",
                  "traffic_volume_nsa")


def test_paired_panel_uses_single_axis_when_units_match():
    """B3: com a unidade igual nos dois lados, um segundo eixo só engana.

    O painel PRB é o caso caro: a linha do threshold é desenhada em `yLeft`
    fixo, então com dois eixos ela cruzava as barras de UL de uma escala que
    não era a dela.
    """
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _abrir_5g(page, url)

                for panel_id in _PAIRED_PANELS:
                    panel = page.evaluate(_PANEL_DATASETS, panel_id)
                    assert panel["axes"] == ["x", "yLeft"], panel_id
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_panel_header_shows_unit_not_dl_ul():
    """B4/B5: o cabeçalho traz a unidade escalada, não o rótulo de fallback."""
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _abrir_5g(page, url)

                unidades = {
                    panel_id: page.locator(
                        f'.kpi-overview-card[data-panel-id="{panel_id}"] '
                        '.kpi-overview-card-unit').inner_text()
                    for panel_id in _PAIRED_PANELS
                }

                assert "DL / UL" not in unidades.values()
                assert unidades["prb_utility"] == "%"
                # Throughput em bit/s na base, ~1e7 no mock: sobe dois degraus.
                assert unidades["throughput"] == "Mbit/s"
                # Volume em bit na base, ~1e9 no mock: sobe três.
                assert unidades["traffic_volume_nsa"] == "Gbit"
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_eixo_e_tooltip_dividem_o_mesmo_degrau_de_escala():
    """O tick e o tooltip não podem divergir: é o mesmo número na mesma tela."""
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _abrir_5g(page, url)

                rotulos = page.evaluate(
                    """() => {
                      const canvas = document.querySelector(
                        '.kpi-overview-card[data-panel-id="traffic_volume_nsa"] canvas');
                      const chart = Chart.getChart(canvas);
                      const callback = chart.options.scales.yLeft.ticks.callback;
                      return [callback(2e9), callback(2.5e9),
                              chart.data.datasets[0].unitLabel];
                    }"""
                )

                # 2e9 bit lidos em Gbit: "2", não "2.000.000.000".
                assert rotulos == ["2", "2,5", "Gbit"]
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_empty_panel_distinguishes_no_traffic_from_no_data():
    """B6: métrica indefinida por falta de tentativa não é falha de coleta."""
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                # O SA do mock não tem tráfego nenhum, como no evento real.
                _abrir_5g(page, url)
                vazio = page.locator(
                    '.kpi-overview-card[data-panel-id="traffic_volume_sa"] '
                    '.kpi-overview-no-data')
                vazio.wait_for(state="visible", timeout=5000)
                assert vazio.inner_text() == "Sem tráfego no período"
                assert page.locator(
                    '.kpi-overview-card[data-panel-id="throughput"] '
                    '.kpi-overview-no-data').is_hidden()

            with _overview(playwright) as (page, _browser):
                # Nenhuma coleta na janela: nenhum painel pode falar de tráfego.
                _abrir_5g(page, url, query="?kpiOverview=empty")
                textos = page.locator(
                    ".kpi-overview-card.is-empty .kpi-overview-no-data"
                ).all_inner_texts()
                assert len(textos) == 9
                assert set(textos) == {"Sem dados no período"}
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_visao_4g_abre_com_site_do_dashboard_sem_portadoras_marcadas():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url, "?kpiOverview=earfcn")
                page.wait_for_function(
                    """() => {
                      const chips = [...document.querySelectorAll(
                        '#kpi-overview-context .kpi-overview-chip')]
                        .map(el => el.textContent.trim());
                      return chips.length === 1
                        && chips.includes('ERB-03 Av. Interlagos');
                    }""",
                    timeout=8000,
                )
                chips = page.locator(
                    "#kpi-overview-context .kpi-overview-chip").all_inner_texts()
                assert chips == ["ERB-03 Av. Interlagos"]
                _open_picker(page, "kpi-overview-cluster-picker")
                assert page.locator(
                    "#kpi-overview-cluster-picker .scope-picker-option",
                    has_text="Portadora 1276",
                ).first.locator("input").is_checked() is False
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def _expand_carriers(page, site_name):
    """Abre o "separar por portadora" do site — marca as portadoras da aba ativa."""
    _open_picker(page, "kpi-overview-site-picker")
    page.locator("#kpi-overview-site-picker .scope-picker-option",
                 has_text=site_name).first.locator(".scope-picker-chevron").click()
    page.locator("#kpi-overview-site-picker .scope-picker-trigger").click()


def _wait_chips(page, expected):
    page.wait_for_function(
        """expected => {
          const chips = [...document.querySelectorAll(
            '#kpi-overview-context .kpi-overview-chip')].map(el => el.textContent.trim());
          return chips.length === expected.length
            && chips.every((chip, i) => chip === expected[i]);
        }""",
        arg=expected,
        timeout=8000,
    )


def test_troca_de_tecnologia_carrega_so_cluster_sem_familia_e_site():
    """Atravessam a troca 4G↔5G só os escopos que existem nas duas famílias.

    Portadora — cluster EARFCN e site×portadora — é de uma tecnologia só. Sem a
    purga ela sobrevive como escopo invisível: vira chip e entra na consulta,
    mas o seletor da aba nova não tem linha para desmarcá-la.
    """
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url, "?kpiOverview=earfcn")
                _clear_selection(page)
                _show_all_sites(page)
                _pick(page, "kpi-overview-cluster-picker", "Arquibancada Sul")
                _pick(page, "kpi-overview-cluster-picker", "Portadora 1276")
                _pick(page, "kpi-overview-site-picker", "SPSMG7")
                _expand_carriers(page, "SPSMG7")
                _wait_chips(page, ["Arquibancada Sul", "Portadora 1276",
                                   "SPSMG7", "SPSMG7 · 1276"])

                page.locator('#kpi-overview-family-tabs [data-family="5G"]').click()

                _wait_chips(page, ["Arquibancada Sul", "SPSMG7"])
                _open_picker(page, "kpi-overview-cluster-picker")
                assert page.locator(
                    "#kpi-overview-cluster-picker .scope-picker-option",
                    has_text="Portadora 1276").count() == 0
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_troca_de_familia_preserva_site_herdado_do_dashboard():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url, "?kpiOverview=earfcn")
                _wait_chips(page, ["ERB-03 Av. Interlagos"])

                page.locator('#kpi-overview-family-tabs [data-family="5G"]').click()

                _wait_chips(page, ["ERB-03 Av. Interlagos"])
                assert page.locator("#kpi-overview-error").is_hidden()
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_comparacao_esvaziada_de_proposito_continua_vazia_ao_trocar():
    """O vazio escolhido pelo usuário não é desfeito pela troca de aba."""
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url, "?kpiOverview=earfcn")
                _clear_selection(page)

                page.locator('#kpi-overview-family-tabs [data-family="5G"]').click()

                error = page.locator("#kpi-overview-error")
                error.wait_for(state="visible", timeout=5000)
                assert page.locator(
                    "#kpi-overview-context .kpi-overview-chip").count() == 0
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)


def test_interface_permite_selecionar_mais_de_oito_escopos():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            with _overview(playwright) as (page, _browser):
                _open_overview(page, url)
                _clear_selection(page)
                _show_all_sites(page)
                _pick(page, "kpi-overview-cluster-picker", "Todos os clusters")
                for site in ("ERB-07 Interlagos", "ERB-03 Av. Interlagos",
                             "ERB-11 Autódromo Sul", "ERB-15 Buffer Norte", "SPSMG7"):
                    _pick(page, "kpi-overview-site-picker", site)
                for cell in ("ERB-07-A1", "ERB-07-A2", "ERB-07-A3"):
                    _pick(page, "kpi-overview-cell-picker", cell)

                page.wait_for_function(
                    """() => document.querySelectorAll(
                      '#kpi-overview-context .kpi-overview-chip').length === 10""",
                    timeout=8000,
                )
                assert page.locator(".scope-picker-option input:disabled").count() == 0
                assert page.locator("#kpi-overview-error").is_hidden()
    except Exception as exc:  # pragma: no cover
        _skip_if_no_browser(exc)
