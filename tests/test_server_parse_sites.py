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


def test_valores_de_cluster_repetidos_nao_duplicam_o_site():
    csv_text = (
        f"{_BASE_COLUMNS},cluster\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,Sul\n"
        "SITE1,SITE1-B,SITE1-B,-23.5,-46.6,120,111,Sul\n"
    )
    result = _parse(csv_text)

    assert result["clusters"] == [{"id": "sul", "name": "Sul", "site_ids": ["111"]}]
