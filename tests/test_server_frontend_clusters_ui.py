import json
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


HTML_PATH = Path(__file__).parents[1] / "server_frontend" / "index.html"
SERVER_FRONTEND_ROOT = HTML_PATH.parent


class _ClusterEditorHandler(SimpleHTTPRequestHandler):
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
            self._json({
                "ok": True,
                "clusters": [],
                "sites": [{
                    "id": "SITE-A",
                    "name": "SITE A",
                    "lat": -23.7,
                    "lng": -46.69,
                    "cells": [
                        {"id": "SITE-A-1", "azimuth": 0, "tech": "4G", "frequency": "1800"},
                        {"id": "SITE-A-2", "azimuth": 120, "tech": "4G", "frequency": "1800"},
                    ],
                }],
            })
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
def _cluster_editor_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ClusterEditorHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_cluster_editor_is_a_section_inside_event_form_not_a_map_mode():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert 'id="event-clusters-section"' in html
    assert 'id="cluster-panel"' in html
    assert 'id="cluster-list"' in html
    assert 'id="btn-new-cluster"' in html
    assert 'id="cluster-selection-tree"' in html
    assert 'id="cluster-name-input"' in html
    assert 'id="btn-cluster-map-pick"' in html
    assert 'id="btn-cluster-lasso"' in html
    assert 'id="btn-cluster-lasso-apply"' in html
    assert 'id="cluster-site-search"' in html
    assert 'id="tool-cluster-poly"' not in html


def test_cluster_membership_is_n_to_n_never_single_owner_field():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "function setCellSelected" in html
    assert "function setSiteSelected" in html
    assert "function applyClusterLasso" in html
    assert "function pointInPolygon" in html
    # A trava do N:N: nenhum código de cluster escreve `site.cluster =`.
    assert "site.cluster =" not in html
    assert ".cluster = " not in html


def test_lasso_never_reevaluates_membership_after_apply():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "cluster.polygon = clusterLassoCoords" in html
    # applyClusterLasso só soma sites inteiros dentro do laço; nunca remove os demais.
    assert "setSiteSelected(cluster, site, true)" in html
    assert "setSiteSelected(cluster, site, false)" not in html


def test_submit_payload_includes_clusters():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "clusters: eventClusters.map(c => ({" in html
    assert "members: (c.members || []).map(member => ({" in html
    assert "cell_ids: [...(member.cell_ids || [])]" in html
    assert "site_ids: [...new Set((c.members || [])" in html


def test_edit_event_repopulates_clusters_from_saved_event():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "eventClusters = (event.clusters || []).map(" in html
    assert "eventClusters.forEach(normalizeClusterMembers);" in html
    assert "renderClusterList();" in html


def test_hierarchy_supports_merged_physical_sites_and_indeterminate_checkboxes():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "function physicalSiteGroups()" in html
    assert "distanceMeters(item.sites[0], site) <= 50" in html
    assert "data-role=\"group\"" in html
    assert "data-role=\"cell\"" in html
    assert "checkbox.indeterminate" in html


def test_selecting_cell_preserves_the_expanded_site_group():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "const expandedGroupKeys = new Set(" in html
    assert "details[open][data-group-key]" in html
    assert "expandedGroupKeys.has(groupKey) ? 'open' : ''" in html


def test_selecting_multiple_cells_keeps_site_open_in_real_editor():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with _cluster_editor_server() as url, sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.locator("#btn-novo-evento").click()
            page.locator("#file-input").set_input_files({
                "name": "sites.csv",
                "mimeType": "text/csv",
                "buffer": b"site_id,site_name,lat,lng\nSITE-A,SITE A,-23.7,-46.69\n",
            })
            page.locator("#sites-info-msg").filter(has_text="Sucesso").wait_for(timeout=5000)
            page.locator("#btn-new-cluster").click()

            details = page.locator("#cluster-selection-tree details[data-group-key]")
            details.locator("summary").click()
            assert details.evaluate("el => el.open") is True

            details.locator('[data-role="cell"]').nth(0).check()
            details = page.locator("#cluster-selection-tree details[data-group-key]")
            assert details.evaluate("el => el.open") is True

            details.locator('[data-role="cell"]').nth(1).check()
            details = page.locator("#cluster-selection-tree details[data-group-key]")
            assert details.evaluate("el => el.open") is True
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium do Playwright não está instalado neste ambiente")
        raise


def test_cluster_card_layout_rule_stays_in_stylesheet_not_inline_script():
    html = HTML_PATH.read_text(encoding="utf-8")
    style_source, remainder = html.split("</style>", 1)

    assert ".cluster-delete-btn { grid-column: 3; grid-row: 1 / 3; }" in style_source
    assert ".cluster-delete-btn { grid-column: 3; grid-row: 1 / 3; }" not in remainder


def test_cancel_edit_resets_cluster_state():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "function resetClusterState()" in html
    assert "resetClusterState();" in html


def test_parse_sites_response_can_seed_clusters_on_new_event_only():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "result.clusters" in html
    assert "editingEventId === null && eventClusters.length === 0" in html
