"""
Fixtures e infraestrutura compartilhada para todos os testes do SmartEvents.

Execução rápida (sem VPN):
    python -m pytest tests/ -v

Testes com VPN (requer iManager acessível):
    python -m pytest tests/ -v --vpn
    python -m pytest tests/ -v --vpn --vpn-prompt   # exibe diálogo antes de rodar
"""
import sys
import json
import pytest


# ── Infraestrutura do marcador vpn ───────────────────────────────────

def pytest_addoption(parser):
    parser.addoption(
        "--vpn", action="store_true", default=False,
        help="Executar testes marcados com @pytest.mark.vpn (requer VPN ativa)"
    )
    parser.addoption(
        "--vpn-prompt", action="store_true", default=False,
        help="Exibir diálogo de sistema pedindo VPN antes dos testes de VPN"
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "vpn: testes de integração que requerem conexão VPN ativa com o iManager OSS"
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--vpn"):
        skip_vpn = pytest.mark.skip(reason="VPN não ativa. Use --vpn para executar.")
        for item in items:
            if "vpn" in item.keywords:
                item.add_marker(skip_vpn)
    elif config.getoption("--vpn-prompt"):
        _prompt_vpn()


def _prompt_vpn():
    """Exibe diálogo de sistema pedindo ao operador que ative a VPN."""
    if sys.platform == "win32":
        try:
            import ctypes
            MB_ICONINFORMATION = 0x40
            MB_OKCANCEL        = 0x01
            IDOK               = 1
            result = ctypes.windll.user32.MessageBoxW(
                None,
                (
                    "Os testes de integração com o iManager requerem VPN ativa.\n\n"
                    "Conecte-se à VPN do cliente e clique em OK para continuar, "
                    "ou Cancelar para abortar os testes de VPN."
                ),
                "SmartEvents — VPN Necessária",
                MB_ICONINFORMATION | MB_OKCANCEL,
            )
            if result != IDOK:
                pytest.exit("Testes de VPN cancelados pelo operador.", returncode=0)
        except Exception:
            pass  # sem display disponível; continua silenciosamente
    else:
        print("\n[SmartEvents] Testes de VPN requerem conexão VPN ativa.")
        print("Pressione Enter para continuar ou Ctrl+C para cancelar...")
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            pytest.exit("Testes de VPN cancelados.", returncode=0)


# ── Fixtures compartilhadas ──────────────────────────────────────────

@pytest.fixture
def sample_event():
    """Evento de teste mínimo, sem dependência de dados reais."""
    return {
        "id": "test-gp-001",
        "name": "Teste GP SP",
        "status": "SCHEDULED",
        "start_time": "2026-06-01T10:00:00Z",
        "end_time":   "2026-06-01T22:00:00Z",
        "polygon": [],
        "sites": [
            {
                "id": "SR-SPPNB2",
                "name": "SR-SPPNB2",
                "lat": -23.58,
                "lng": -46.69,
                "is_event_site": True,
                "cells": [
                    {"id": "SR-SPPNB2_1", "azimuth": 0,   "beamwidth": 120, "obj_no": 211},
                    {"id": "SR-SPPNB2_2", "azimuth": 120, "beamwidth": 120, "obj_no": 216},
                    {"id": "SR-SPPNB2_3", "azimuth": 240, "beamwidth": 120, "obj_no": 221},
                ],
            }
        ],
        "vips": [],
        "thresholds": {
            "rsrp_warning": -100.0,
            "rsrp_critical": -110.0,
            "rsrq_warning": -12.0,
            "rsrq_critical": -15.0,
            "utilization_warning": 80.0,
            "utilization_critical": 95.0,
            "availability_critical": 95.0,
        },
        "oss": {"base_url": "", "region": "SP", "import_folder": ""},
    }


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """
    Redireciona todo o acesso ao SQLite para um diretório temporário e inicializa
    o schema. Garante isolamento total entre testes — cada teste começa com banco vazio.
    """
    import core.database as database

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setattr(database, "BASE_DIR",      tmp_path)
    monkeypatch.setattr(database, "DB_PATH",       data_dir / "smart_events.db")
    monkeypatch.setattr(database, "SETTINGS_PATH", data_dir / "settings.json")

    # Descarta conexões thread-local abertas por testes anteriores nesta thread
    database._local.__dict__.clear()
    database._event_local.__dict__.clear()
    database._migrated_events.clear()
    database._initialized_global_dbs.clear()
    database._initialized_event_dbs.clear()

    database.init_db()
    yield tmp_path

    database.close_conn()
    database._local.__dict__.clear()
    database._event_local.__dict__.clear()
    database._initialized_global_dbs.clear()
    database._initialized_event_dbs.clear()


@pytest.fixture
def event_in_db(tmp_db, sample_event):
    """Banco tmp já com o evento de teste salvo."""
    import core.database as database
    database.save_event(sample_event)
    return sample_event


@pytest.fixture
def csv_kpi_dir(tmp_path):
    """
    Diretório temporário com um arquivo KPI CSV no formato de exportação do iManager.
    Contém uma medição para o site SR-SPPNB2, célula SR-SPPNB2-Cell-1.
    """
    csv_dir = tmp_path / "imports"
    csv_dir.mkdir()
    content = (
        "Object,Period(minute),Start Time,"
        "{BRDC} Traffic Volume DL LTE,{BRDC} Traffic Volume UL LTE,"
        "{BRDC} DL User Throughput,{BRDC} UL User Throughput,"
        "DL PRB USAGE,{BRDC} Usuario,{BRDC} Acessibilidade\n"
        "SR-SPPNB2-Cell-1,15,2026-06-01 10:00:00,"
        "120.5,45.2,38.1,12.3,55.0,28,99.2\n"
    )
    (csv_dir / "kpi_test.csv").write_text(content, encoding="utf-8")
    return csv_dir
