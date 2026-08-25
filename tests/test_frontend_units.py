"""Escala automática e formatação das unidades canônicas (Fase 4, B4/B5).

O módulo é ES puro e sem dependência de DOM, então roda dentro de uma página em
branco servida pelo mesmo servidor estático dos demais testes de frontend — é a
única forma de exercitar o código que realmente vai para a tela, em vez de
reimplementar a regra em Python e testar a reimplementação.
"""
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


UNITS_JS = Path(__file__).resolve().parents[1] / "frontend" / "js" / "units.js"


@contextmanager
def _frontend_server():
    root = Path(__file__).resolve().parents[1] / "frontend"
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _run_in_units_module(expression: str):
    """Avalia uma expressão com o módulo `units.js` importado como `U`."""
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(f"{url}/index.html", wait_until="domcontentloaded")
                return page.evaluate(
                    f"""async () => {{
                      const U = await import('{url}/js/units.js');
                      return ({expression});
                    }}"""
                )
            finally:
                browser.close()
    except Exception as exc:  # pragma: no cover - depende do browser instalado
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise


def test_escala_sobe_a_cada_mil():
    rotulos = _run_in_units_module(
        "[999, 1000, 1e6, 1e9, 1e12].map(v => U.escolherEscala([v], 'bit').rotulo)")

    assert rotulos == ["bit", "kbit", "Mbit", "Gbit", "Tbit"]


def test_escala_de_taxa_usa_a_familia_por_segundo():
    """Throughput não pode herdar o rótulo de volume: 4,31e8 bit/s é Mbit/s."""
    escala = _run_in_units_module("U.escolherEscala([4.31e8], 'bit/s')")

    assert escala["rotulo"] == "Mbit/s"
    assert escala["divisor"] == 1e6


def test_escala_e_decimal_nao_binaria():
    """kbit é 10³ por definição em telecom — 2^10 daria 2,4% de erro por degrau."""
    resultado = _run_in_units_module(
        "(() => { const e = U.escolherEscala([1024], 'bit');"
        " return [U.formatar(1024, e), e.rotulo]; })()")

    assert resultado == ["1,02", "kbit"]


def test_histerese_evita_troca_de_degrau():
    """Sobe em 1000, mas só desce abaixo de 900 — a faixa morta é a histerese."""
    rotulos = _run_in_units_module(
        """(() => {
          const passos = [];
          [[1000], [950], [899]].forEach(valores => {
            passos.push(U.escalaDaMetrica('teste', valores, 'bit').rotulo);
          });
          U.esquecerEscalas();
          return passos;
        })()"""
    )

    assert rotulos == ["kbit", "kbit", "bit"]


def test_painel_sem_pontos_nao_troca_o_degrau():
    """Buraco de coleta não pode fazer a unidade do cabeçalho piscar."""
    rotulos = _run_in_units_module(
        """(() => {
          const passos = [];
          passos.push(U.escalaDaMetrica('vazio', [2e9], 'bit').rotulo);
          passos.push(U.escalaDaMetrica('vazio', [null, undefined], 'bit').rotulo);
          U.esquecerEscalas();
          return passos;
        })()"""
    )

    assert rotulos == ["Gbit", "Gbit"]


def test_percentual_e_dbm_nao_escalam():
    escalas = _run_in_units_module(
        "['%', 'dBm', 'ms', 'usuários'].map(u => U.escolherEscala([1e9], u))")

    assert [escala["divisor"] for escala in escalas] == [1, 1, 1, 1]
    assert [escala["rotulo"] for escala in escalas] == ["%", "dBm", "ms", "usuários"]


def test_apenas_bit_e_bit_por_segundo_escalam():
    """B5: a lista de escaláveis é derivada dos degraus, não mantida à parte."""
    escalaveis = _run_in_units_module("[...U.ESCALAVEIS]")

    assert sorted(escalaveis) == ["bit", "bit/s"]


def test_esquecer_escalas_zera_a_memoria():
    """Troca de evento muda a ordem de grandeza: o degrau não pode sobreviver."""
    rotulos = _run_in_units_module(
        """(() => {
          const passos = [];
          passos.push(U.escalaDaMetrica('evento', [5e9], 'bit').rotulo);
          U.esquecerEscalas();
          passos.push(U.escalaDaMetrica('evento', [500], 'bit').rotulo);
          U.esquecerEscalas();
          return passos;
        })()"""
    )

    assert rotulos == ["Gbit", "bit"]


def test_valor_ausente_vira_travessao_e_nao_zero():
    formatados = _run_in_units_module(
        "[null, undefined, 'x', NaN].map(v => U.formatar(v))")

    assert formatados == ["—", "—", "—", "—"]


def test_formatacao_e_pt_br():
    """Um formatador só para o app inteiro — e o app é pt-BR."""
    formatados = _run_in_units_module(
        "[U.formatar(1234.5), U.formatar(85.3, null, { minimoDeCasas: 2 })]")

    assert formatados == ["1.234,5", "85,30"]


def test_lista_e_grafico_do_dashboard_usam_a_mesma_escala():
    """A lista de sites e o eixo do gráfico não podem discordar de unidade.

    São a mesma métrica na mesma tela: se a lista dissesse "Mbit/s" e o eixo
    mostrasse o valor cru, o operador leria 51 e 51.985.476 como coisas
    diferentes. Percentual, no mesmo caminho, tem de continuar sem escala.
    """
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _frontend_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1500, "height": 950})
                page.goto(f"{url}/index.html", wait_until="domcontentloaded")
                page.locator(".site-item").first.wait_for(state="visible", timeout=8000)
                page.locator(".site-item").first.click()
                page.locator("#metric-selector").select_option("throughput_dl")
                page.wait_for_function(
                    """() => [...document.querySelectorAll('#site-list .site-util')]
                             .some(el => el.textContent.includes('Mbit/s'))""",
                    timeout=8000,
                )

                leitura = page.evaluate(
                    """() => {
                      const chart = Chart.getChart(document.getElementById('kpi-chart'));
                      const dados = (chart.data.datasets[0].data || [])
                        .filter(valor => valor != null);
                      const maximo = Math.max(...dados);
                      return {
                        maximo,
                        tick: chart.options.scales.y.ticks.callback(maximo),
                        lista: document.querySelector('#site-list .site-util').textContent,
                      };
                    }"""
                )

                # Gravado em bit/s (ordem de 1e7), exibido em Mbit/s nos dois lugares.
                assert leitura["maximo"] > 1e6
                assert float(leitura["tick"].replace(".", "").replace(",", ".")) < 1000
                assert leitura["lista"].endswith(" Mbit/s")

                page.locator("#metric-selector").select_option("utilization_dl")
                page.wait_for_function(
                    """() => [...document.querySelectorAll('#site-list .site-util')]
                             .every(el => el.textContent.endsWith('%'))""",
                    timeout=8000,
                )
            finally:
                browser.close()
    except Exception as exc:  # pragma: no cover - depende do browser instalado
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise


def test_units_e_o_unico_formatador_das_telas_de_kpi():
    """Fase 4, item 6: quatro implementações divergentes viraram uma."""
    js = Path(__file__).resolve().parents[1] / "frontend" / "js"
    for arquivo in ("kpi.js", "kpi_overview.js", "vip.js"):
        fonte = (js / arquivo).read_text(encoding="utf-8")
        assert 'from "./units.js"' in fonte, arquivo
        assert "toFixed(2)" not in fonte, arquivo


def test_importadores_usam_o_mesmo_especificador_de_units():
    """A memória de escala é do módulo: um `?v=` diferente criaria outra cópia.

    O app versiona os imports para escapar do cache do WebView; se `units.js`
    entrasse nesse esquema com sufixos distintos, cada tela teria a sua própria
    memória e o degrau deixaria de ser comum — silenciosamente.
    """
    js = Path(__file__).resolve().parents[1] / "frontend" / "js"
    especificadores = set()
    for arquivo in ("kpi.js", "kpi_overview.js", "vip.js"):
        fonte = (js / arquivo).read_text(encoding="utf-8")
        for linha in fonte.splitlines():
            if "units.js" in linha and linha.startswith("import"):
                especificadores.add(linha.split("from")[-1].strip().strip(';"\''))

    assert especificadores == {"./units.js"}
    assert UNITS_JS.exists()
