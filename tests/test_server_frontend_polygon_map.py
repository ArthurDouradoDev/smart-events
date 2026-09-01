"""
Mapa da página de cadastro (server_frontend/index.html):

- o zoom do operador não pode ser desfeito por um reenquadramento automático;
- só os marcadores dentro da viewport ficam no DOM (EP grande);
- o polígono automático é a envoltória convexa dos sites marcados como dentro,
  afastada pelo padding — os vizinhos não inflam mais a área.
"""
import json
import math
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


HTML_PATH = Path(__file__).parents[1] / "server_frontend" / "index.html"
SERVER_FRONTEND_ROOT = HTML_PATH.parent

# Evento compacto + vizinhos num anel bem afastado: é a EP que o polígono
# automático precisa separar.
CENTER_LAT, CENTER_LNG = -23.700, -46.690
INSIDE_COUNT = 120
OUTSIDE_COUNT = 40


def _fixture_sites():
    sites = []
    for i in range(INSIDE_COUNT):
        angle = 2 * math.pi * i / INSIDE_COUNT
        radius = 0.010 * (0.35 + 0.65 * ((i % 7) / 6))
        sites.append({
            "id": f"IN-{i}",
            "name": f"IN {i}",
            "lat": CENTER_LAT + radius * math.sin(angle),
            "lng": CENTER_LNG + radius * math.cos(angle),
            "is_event_site": True,
            "cells": [{"id": f"IN-{i}-A", "azimuth": 0, "tech": "4G", "frequency": "1800"}],
        })
    for i in range(OUTSIDE_COUNT):
        angle = 2 * math.pi * i / OUTSIDE_COUNT
        radius = 0.055 + 0.025 * ((i % 5) / 4)
        sites.append({
            "id": f"OUT-{i}",
            "name": f"OUT {i}",
            "lat": CENTER_LAT + radius * math.sin(angle),
            "lng": CENTER_LNG + radius * math.cos(angle),
            "is_event_site": False,
            "cells": [{"id": f"OUT-{i}-A", "azimuth": 0, "tech": "4G", "frequency": "1800"}],
        })
    return sites


class _MapHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/events") or self.path.startswith("/api/clientes"):
            self._json([])
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/parse-sites":
            self._json({"ok": True, "clusters": [], "sites": _fixture_sites()})
            return
        self._json({"detail": "not found"}, 404)

    def translate_path(self, path):
        clean = path.split("?", 1)[0]
        if clean == "/":
            relative = "index.html"
        elif clean.startswith("/static/"):
            relative = clean.removeprefix("/static/")
        else:
            relative = clean.lstrip("/")
        return str(SERVER_FRONTEND_ROOT / relative)


@contextmanager
def _map_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MapHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


@contextmanager
def _loaded_editor():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _map_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until="domcontentloaded")
            page.locator("#btn-novo-evento").click()
            page.locator("#file-input").set_input_files({
                "name": "sites.csv",
                "mimeType": "text/csv",
                "buffer": b"nename,cellname,latitude,longitude,azimuth,enodebid\n",
            })
            page.locator("#sites-info-msg").filter(has_text="Sucesso").wait_for(timeout=5000)
            page.wait_for_timeout(600)
            try:
                yield page
            finally:
                browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise


# ── Regressão do zoom travado ─────────────────────────────────────────────


def test_zoom_nao_dispara_reenquadramento_no_codigo_fonte():
    """fitBounds só pode acontecer numa carga explícita de dados, nunca no zoom."""
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "map.on('zoomend', () => plotSitesOnMap());" not in html
    assert "map.on('zoomend', scheduleViewportRefresh);" in html
    assert "map.on('moveend', scheduleViewportRefresh);" in html
    # O único fitBounds do mapa de sites fica atrás da opção `fit`.
    assert html.count("map.fitBounds(") == 1
    assert "if (options && options.fit) {" in html


def test_botao_de_zoom_do_mapa_mantem_o_nivel_escolhido():
    with _loaded_editor() as page:
        antes = page.evaluate("map.getZoom()")
        page.locator(".leaflet-control-zoom-in").click()
        page.wait_for_timeout(800)
        depois = page.evaluate("map.getZoom()")

        assert depois == antes + 1, "o mapa voltou sozinho ao enquadramento anterior"


def test_zoom_programatico_tambem_permanece():
    with _loaded_editor() as page:
        antes = page.evaluate("map.getZoom()")
        page.evaluate("map.setZoom(map.getZoom() + 3)")
        page.wait_for_timeout(800)

        assert page.evaluate("map.getZoom()") == antes + 3


# ── Culling de viewport ───────────────────────────────────────────────────


def test_apenas_marcadores_da_viewport_ficam_no_dom():
    total = INSIDE_COUNT + OUTSIDE_COUNT
    with _loaded_editor() as page:
        page.evaluate(
            "([lat, lng]) => map.setView([lat, lng], 15)", [CENTER_LAT, CENTER_LNG]
        )
        page.wait_for_timeout(800)
        visiveis = page.locator(".leaflet-marker-icon").count()

        assert 0 < visiveis < total, (
            f"{visiveis} de {total} marcadores no DOM — o culling nao filtrou nada"
        )


def test_marcador_volta_ao_dom_quando_o_site_reentra_na_viewport():
    with _loaded_editor() as page:
        page.evaluate("map.setView([0, 0], 15)")
        page.wait_for_timeout(800)
        assert page.locator(".leaflet-marker-icon").count() == 0

        page.evaluate(
            "([lat, lng]) => map.setView([lat, lng], 13)", [CENTER_LAT, CENTER_LNG]
        )
        page.wait_for_timeout(800)
        assert page.locator(".leaflet-marker-icon").count() > 0


def test_zoom_reaproveita_o_marcador_em_vez_de_recriar_a_camada():
    """setIcon mantém o mesmo objeto de camada; recriar trocaria a referência."""
    with _loaded_editor() as page:
        page.evaluate(
            "window.__probeId = Object.keys(siteMarkersById)[0];"
            "window.__probe = siteMarkersById[window.__probeId];"
        )
        svg_antes = page.evaluate("siteMarkersById[window.__probeId]._icon.innerHTML")
        # Zoom para fora mantém todo site na viewport — isola a troca de ícone.
        page.evaluate("map.setZoom(map.getZoom() - 1)")
        page.wait_for_timeout(800)

        assert page.evaluate(
            "siteMarkersById[window.__probeId] === window.__probe"
        ) is True, "o marcador foi destruído e recriado no zoom"
        assert page.evaluate(
            "siteMarkersById[window.__probeId]._icon.innerHTML"
        ) != svg_antes, "o ícone não reescalou com o zoom"


# ── Polígono automático ───────────────────────────────────────────────────


def _point_in_polygon(point, ring):
    lat, lng = point
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        lat_i, lng_i = ring[i]
        lat_j, lng_j = ring[j]
        if (lng_i > lng) != (lng_j > lng):
            if lat < (lat_j - lat_i) * (lng - lng_i) / (lng_j - lng_i) + lat_i:
                inside = not inside
        j = i
    return inside


def test_poligono_automatico_cobre_os_sites_de_dentro_e_exclui_os_vizinhos():
    with _loaded_editor() as page:
        ring = page.evaluate("polygonCoordinates")
        sites = _fixture_sites()

        assert len(ring) > 4, "a envoltória deveria ter mais vértices que um retângulo"

        dentro = [s for s in sites if s["is_event_site"]]
        fora = [s for s in sites if not s["is_event_site"]]
        assert all(_point_in_polygon((s["lat"], s["lng"]), ring) for s in dentro)
        assert not any(_point_in_polygon((s["lat"], s["lng"]), ring) for s in fora)


def test_padding_maior_expande_o_poligono():
    with _loaded_editor() as page:
        def area():
            ring = page.evaluate("polygonCoordinates")
            total = 0.0
            for i in range(len(ring)):
                a, b = ring[i], ring[(i + 1) % len(ring)]
                total += a[1] * b[0] - b[1] * a[0]
            return abs(total / 2)

        pequena = area()
        page.evaluate(
            "paddingSlider.value = '2500'; paddingSlider.dispatchEvent(new Event('input'))"
        )
        page.wait_for_timeout(300)

        assert area() > pequena


def test_sem_coluna_na_ep_todos_os_sites_definem_o_poligono():
    """Planilha legada: nenhum site marcado como fora, todos entram no cálculo."""
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "const inside = parsedSites.filter(s => s.is_event_site !== false);" in html
    assert "return inside.length ? inside : parsedSites;" in html
