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


def _parse_raw(csv_text: str) -> dict:
    return asyncio.run(server.parse_sites(file=_upload(csv_text)))


def _parse(csv_text: str) -> dict:
    # Unrelated parser fixtures use an explicit EP declaration by default.
    lines = csv_text.splitlines()
    if not set(lines[0].split(',')) & {"tech", "technology", "tecnologia", "rat"}:
        lines = [lines[0] + ',tecnologia'] + [line + ',4G' for line in lines[1:] if line]
    return _parse_raw('\n'.join(lines))


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
    cell = result["sites"][0]["cells"][0]
    assert {key: cell[key] for key in ("id", "azimuth", "beamwidth")} == {
        "id": "SITE1-A", "azimuth": 0.0, "beamwidth": 120.0,
    }
    assert cell["ep"] == {
        "source_row": 2, "cellid": "SITE1-A", "cellname": "SITE1-A", "azimuth": 0.0, "technology": "4G",
    }


def test_ep_preserva_identificadores_source_row_e_ignora_coluna_desconhecida():
    csv_text = (
        f"{_BASE_COLUMNS},band,dlearfcn,coluna_secreta\n"
        "SITE ORIGINAL,CELL NAME,0007,-23.5,-46.6,120,00111,1800,01276,nao-copiar\n"
    )

    site = _parse(csv_text)["sites"][0]
    cell = site["cells"][0]

    assert site["id"] == "00111"
    assert site["ep"] == {
        "source_row": 2,
        "enodebid": "00111",
        "nename": "SITE ORIGINAL",
        "latitude": -23.5,
        "longitude": -46.6,
    }
    assert cell["id"] == "CELL NAME"
    assert cell["ep"] == {
        "source_row": 2,
        "cellid": "0007",
        "cellname": "CELL NAME",
        "azimuth": 120.0,
        "technology": "4G",
        "band": "1800",
        "dlearfcn": "01276",
    }
    assert "coluna_secreta" not in site and "coluna_secreta" not in cell


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
        f"{_BASE_COLUMNS},band,tecnologia\n"
        "VIVO,VIVO-4G,VIVO-4G,-23.5,-46.6,0,111,1800,4G\n"
        "VIVO,VIVO-5G,VIVO-5G,-23.5,-46.6,120,111,3500,5G\n"
    )

    cells = _parse(csv_text)["sites"][0]["cells"]

    assert [{key: cell[key] for key in ("id", "azimuth", "beamwidth", "tech", "frequency")}
            for cell in cells] == [
        {"id": "VIVO-4G", "azimuth": 0.0, "beamwidth": 120.0,
         "tech": "4G", "frequency": "1800"},
        {"id": "VIVO-5G", "azimuth": 120.0, "beamwidth": 120.0,
         "tech": "5G", "frequency": "3500"},
    ]
    assert cells[0]["ep"]["band"] == "1800"
    assert cells[1]["ep"]["technology"] == "5G"


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
])
def test_coluna_de_tecnologia_tem_precedencia_sobre_a_banda(raw, expected):
    csv_text = (
        f"{_BASE_COLUMNS},band,tecnologia\n"
        f"SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,2100,{raw}\n"
    )

    cell = _parse(csv_text)["sites"][0]["cells"][0]

    assert cell["frequency"] == "2100"
    assert cell["tech"] == expected


def test_tecnologia_invalida_rejeita_importacao():
    csv_text = (
        f"{_BASE_COLUMNS},band,tech\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,desconhecida,sem-tecnologia\n"
    )

    with pytest.raises(server.HTTPException) as exc:
        _parse(csv_text)
    assert "linha 2" in exc.value.detail
    assert "sem-tecnologia" in exc.value.detail


def test_dlearfcn_da_ep_e_gravado_na_celula():
    csv_text = (
        f"{_BASE_COLUMNS},dlearfcn\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,1276\n"
        "SITE1,SITE1-B,SITE1-B,-23.5,-46.6,120,111,1700.0\n"
    )

    cells = _parse(csv_text)["sites"][0]["cells"]

    assert cells[0]["earfcn"] == "1276"
    assert cells[1]["earfcn"] == "1700"


@pytest.mark.parametrize("header", ["dlearfcn", "earfcn", "dl_earfcn"])
def test_aliases_de_earfcn_sao_aceitos(header):
    csv_text = (
        f"{_BASE_COLUMNS},{header}\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,12345\n"
    )

    cell = _parse(csv_text)["sites"][0]["cells"][0]

    assert cell["earfcn"] == "12345"


def test_coluna_dlearfcn_ausente_nao_quebra_importacao():
    csv_text = (
        f"{_BASE_COLUMNS}\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111\n"
    )

    cell = _parse(csv_text)["sites"][0]["cells"][0]

    assert "earfcn" not in cell


def test_dlearfcn_invalido_e_ignorado():
    csv_text = (
        f"{_BASE_COLUMNS},dlearfcn\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,nao-e-numero\n"
    )

    cell = _parse(csv_text)["sites"][0]["cells"][0]

    assert "earfcn" not in cell


# ── Coluna opcional que marca o site dentro/fora do polígono ──────────────


@pytest.mark.parametrize("header", [
    "is_event_site", "site_evento", "no_evento", "dentro",
    "dentro_poligono", "in_event", "in_polygon",
])
def test_coluna_dentro_do_poligono_aceita_os_apelidos_da_ep(header):
    csv_text = (
        f"{_BASE_COLUMNS},{header}\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,sim\n"
        "SITE2,SITE2-A,SITE2-A,-23.6,-46.7,0,222,nao\n"
    )

    sites = {s["id"]: s for s in _parse(csv_text)["sites"]}

    assert sites["111"]["is_event_site"] is True
    assert sites["222"]["is_event_site"] is False


@pytest.mark.parametrize("valor,esperado", [
    ("sim", True), ("SIM", True), ("s", True), ("1", True), ("true", True),
    ("dentro", True), ("in", True), ("x", True),
    ("nao", False), ("não", False), ("n", False), ("0", False),
    ("false", False), ("fora", False), ("out", False), ("vizinho", False),
    ("borda", False), ("buffer", False),
])
def test_valores_dentro_e_fora_reconhecidos(valor, esperado):
    csv_text = (
        f"{_BASE_COLUMNS},dentro\n"
        f"SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,{valor}\n"
    )

    assert _parse(csv_text)["sites"][0]["is_event_site"] is esperado


def test_coluna_ausente_mantem_todo_site_dentro_do_evento():
    """Planilha legada, sem a coluna, não pode mudar de comportamento."""
    csv_text = (
        f"{_BASE_COLUMNS}\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111\n"
    )

    assert _parse(csv_text)["sites"][0]["is_event_site"] is True


def test_celula_vazia_nao_vota_e_o_site_fica_dentro():
    csv_text = (
        f"{_BASE_COLUMNS},dentro\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,\n"
        "SITE2,SITE2-A,SITE2-A,-23.6,-46.7,0,222,fora\n"
    )

    sites = {s["id"]: s for s in _parse(csv_text)["sites"]}

    assert sites["111"]["is_event_site"] is True
    assert sites["222"]["is_event_site"] is False


def test_valor_irreconhecivel_nao_derruba_o_site_do_evento():
    csv_text = (
        f"{_BASE_COLUMNS},dentro\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,talvez\n"
    )

    assert _parse(csv_text)["sites"][0]["is_event_site"] is True


def test_linhas_divergentes_do_mesmo_site_resolvem_por_or():
    """Mesma semântica da fusão 4G/5G em Api._as_merged_site: um "dentro" basta."""
    csv_text = (
        f"{_BASE_COLUMNS},dentro\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,fora\n"
        "SITE1,SITE1-B,SITE1-B,-23.5,-46.6,120,111,dentro\n"
        "SITE2,SITE2-A,SITE2-A,-23.6,-46.7,0,222,fora\n"
        "SITE2,SITE2-B,SITE2-B,-23.6,-46.7,120,222,fora\n"
    )

    sites = {s["id"]: s for s in _parse(csv_text)["sites"]}

    assert sites["111"]["is_event_site"] is True
    assert sites["222"]["is_event_site"] is False


def test_coluna_dentro_convive_com_a_coluna_de_cluster():
    csv_text = (
        f"{_BASE_COLUMNS},cluster,dentro\n"
        "SITE1,SITE1-A,SITE1-A,-23.5,-46.6,0,111,Sul,sim\n"
        "SITE2,SITE2-A,SITE2-A,-23.6,-46.7,0,222,Sul,nao\n"
    )
    result = _parse(csv_text)

    sites = {s["id"]: s for s in result["sites"]}
    assert sites["111"]["is_event_site"] is True
    assert sites["222"]["is_event_site"] is False
    assert result["clusters"][0]["site_ids"] == ["111", "222"]


@pytest.mark.parametrize("suffix,value", [("", ""), (",tecnologia", ","), (",tecnologia", ",GSM")])
def test_nova_ep_rejeita_tecnologia_ausente_ou_invalida(suffix, value):
    with pytest.raises(server.HTTPException) as exc:
        _parse_raw(f"{_BASE_COLUMNS}{suffix}\nS,5G-X,001,-23,-46,0,001{value}\n")
    assert exc.value.status_code == 400
    assert "4G, LTE, 5G, NR" in exc.value.detail


def test_ep_rejeita_duplicidade_contraditoria_com_amostra_limitada():
    rows = ["S,C,001,-23,-46,0,001,4G"] + ["S,C,001,-23,-46,0,001,5G"] * 25
    with pytest.raises(server.HTTPException) as exc:
        _parse_raw(f"{_BASE_COLUMNS},tecnologia\n" + "\n".join(rows))
    assert "25 erro(s)" in exc.value.detail
    assert "linha 3" in exc.value.detail and "linha 27" not in exc.value.detail


@pytest.mark.parametrize("header", ["tech", "technology", "tecnologia", "tecnologia móvel", "rat"])
def test_ep_declarada_vence_nome_e_banda(header):
    cell = _parse_raw(f"{_BASE_COLUMNS},band,{header}\nS,5G-X,001,-23,-46,0,001,3500,LTE\n")["sites"][0]["cells"][0]
    assert cell["tech"] == cell["ep"]["technology"] == "4G"


@pytest.mark.parametrize("junk", [
    "S,SEM-COORD,002,,,0,002,",     # sem latitude/longitude
    ",,,,,,,",                      # linha em branco no meio da planilha
])
def test_linha_descartada_pelo_parser_nao_exige_tecnologia(junk):
    """Só as linhas que viram célula precisam declarar tecnologia.

    Exigir da linha que o parser já descarta faria uma EP válida com sobras no
    fim da planilha parar de importar, sem nenhum ganho de contrato.
    """
    result = _parse_raw(
        f"{_BASE_COLUMNS},tecnologia\n"
        "S,CELULA-A,001,-23,-46,0,001,4G\n"
        f"{junk}\n"
    )

    assert result["ok"] is True
    assert [cell["id"] for cell in result["sites"][0]["cells"]] == ["CELULA-A"]


def test_template_xlsx_importa_com_tecnologia_canonica():
    from pathlib import Path
    upload = UploadFile(file=io.BytesIO((Path(__file__).parents[1] / 'ep_default.xlsx').read_bytes()), filename='ep_default.xlsx')
    result = asyncio.run(server.parse_sites(file=upload))
    assert result['ok'] and len(result['sites']) == 1
    assert result['sites'][0]['cells'][0]['tech'] == '4G'
    assert result['sites'][0]['cells'][0]['ep']['technology'] == '4G'
