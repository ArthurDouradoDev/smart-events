"""
Testes para POST /api/parse-sites (server.py), coluna opcional de cluster (Fase 4).

Chama a função do endpoint diretamente (sem subir um servidor HTTP) — o projeto
não tem httpx instalado e o TestClient do FastAPI depende dele.
"""
import asyncio
import io

import pytest
from fastapi import UploadFile

import server


def _upload(csv_text: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(csv_text.encode("utf-8")), filename="sites.csv")


def _parse(csv_text: str) -> dict:
    return asyncio.run(server.parse_sites(file=_upload(csv_text)))


_BASE_COLUMNS = "nename,cellname,cellid,latitude,longitude,azimuth,enodebid"


def test_coluna_cluster_da_ep_semeia_varios_grupos():
    csv_text = (
        f"{_BASE_COLUMNS},cluster\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,Sul;Oeste\n"
        "SITE1,SITE1-B,SITE1-B,-23.5,-46.6,120,111,Sul\n"
        "SITE2,SITE2-A,SITE2-A,-23.6,-46.7,0,222,Oeste\n"
    )
    result = _parse(csv_text)

    assert result["ok"] is True
    clusters = {c["name"]: c for c in result["clusters"]}
    assert set(clusters) == {"Sul", "Oeste"}
    assert clusters["Sul"]["site_ids"] == ["111"]
    assert clusters["Oeste"]["site_ids"] == ["111", "222"]


def test_coluna_cluster_ausente_nao_quebra_importacao():
    csv_text = (
        f"{_BASE_COLUMNS}\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111\n"
    )
    result = _parse(csv_text)

    assert result["ok"] is True
    assert len(result["sites"]) == 1
    assert result["clusters"] == []
    assert result["sites"][0]["cells"] == [{
        "id": "SITE1-A", "azimuth": 0.0, "beamwidth": 120.0,
    }]


def test_valores_de_cluster_repetidos_nao_duplicam_o_site():
    csv_text = (
        f"{_BASE_COLUMNS},cluster\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,Sul\n"
        "SITE1,SITE1-B,SITE1-B,-23.5,-46.6,120,111,Sul\n"
    )
    result = _parse(csv_text)

    assert result["clusters"] == [{"id": "sul", "name": "Sul", "site_ids": ["111"]}]


def test_band_da_ep_vivo_vira_frequencia_e_tecnologia_sem_ler_coluna_auxiliar():
    csv_text = (
        f"{_BASE_COLUMNS},band,\n"
        "VIVO,VIVO-4G,VIVO-4G,-23.5,-46.6,0,111,1800,(L1)\n"
        "VIVO,VIVO-5G,VIVO-5G,-23.5,-46.6,120,111,3500,(NR1)\n"
    )

    cells = _parse(csv_text)["sites"][0]["cells"]

    assert cells == [
        {"id": "VIVO-4G", "azimuth": 0.0, "beamwidth": 120.0,
         "tech": "4G", "frequency": "1800"},
        {"id": "VIVO-5G", "azimuth": 120.0, "beamwidth": 120.0,
         "tech": "5G", "frequency": "3500"},
    ]


@pytest.mark.parametrize("header", ["band", "banda", "frequency", "frequencia", "frequência", "freq"])
def test_aliases_de_frequencia_sao_aceitos(header):
    csv_text = (
        f"{_BASE_COLUMNS},{header}\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,1800 MHz\n"
    )

    cell = _parse(csv_text)["sites"][0]["cells"][0]

    assert cell["frequency"] == "1800"
    assert cell["tech"] == "4G"


@pytest.mark.parametrize("raw,expected", [
    ("LTE", "4G"), ("4G", "4G"), ("NR", "5G"), ("5G_NRDUCELL", "5G"),
    ("WCDMA", "3G"), ("GSM", "2G"),
])
def test_coluna_de_tecnologia_opcional_tem_precedencia_sobre_a_banda(raw, expected):
    csv_text = (
        f"{_BASE_COLUMNS},band,tecnologia\n"
        f"SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,2100,{raw}\n"
    )

    cell = _parse(csv_text)["sites"][0]["cells"][0]

    assert cell["frequency"] == "2100"
    assert cell["tech"] == expected


def test_valores_opcionais_invalidos_sao_ignorados():
    csv_text = (
        f"{_BASE_COLUMNS},band,tech\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,desconhecida,sem-tecnologia\n"
    )

    cell = _parse(csv_text)["sites"][0]["cells"][0]

    assert "frequency" not in cell
    assert "tech" not in cell
