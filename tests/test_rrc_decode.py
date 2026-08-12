"""Contrato do decodificador local de RRC_MEAS_RPRT.

Os vetores vêm da captura real de `filter-by-cols` da task 2072 (386 mensagens),
o que permite provar regressões de decodificação sem VPN.
"""

import json
from pathlib import Path

import pytest

from core.rrc_decode import decode_meas_report, parse_message_body


VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "rrc_meas_report_vectors.json").read_text(encoding="utf-8")
)


def test_todas_as_mensagens_capturadas_sao_decodificadas():
    falhas = [v["serialNo"] for v in VECTORS if decode_meas_report(v["messageBody"]) is None]
    assert falhas == []


@pytest.mark.parametrize("vector", VECTORS[:25], ids=lambda v: str(v["serialNo"]))
def test_valores_conferem_com_o_vetor_de_referencia(vector):
    decoded = decode_meas_report(vector["messageBody"])
    assert decoded["rsrp"] == vector["expected"]["rsrp"]
    assert decoded["rsrq"] == vector["expected"]["rsrq"]
    assert decoded["meas_id"] == vector["expected"]["meas_id"]


def test_valores_ficam_nas_faixas_fisicas_de_lte():
    for vector in VECTORS:
        decoded = decode_meas_report(vector["messageBody"])
        # TS 36.133: RSRP-Range 0..97 → -140..-43 dBm; RSRQ-Range 0..34 → -19,5..-2,5 dB.
        assert -140.0 <= decoded["rsrp"] <= -43.0
        assert -19.5 <= decoded["rsrq"] <= -2.5
        assert 1 <= decoded["meas_id"] <= 32


def test_o_prefixo_proprietario_e_estavel_na_captura():
    offsets = {decode_meas_report(v["messageBody"])["header_offset"] for v in VECTORS}
    assert offsets == {1}


def test_parse_message_body_le_hexadecimal_separado_por_espaco():
    assert parse_message_body("02 08 36 AD") == b"\x02\x08\x36\xad"


@pytest.mark.parametrize("corpo", ["", None, "ZZ ZZ", "02", "00 00 00 00 00 00"])
def test_corpo_invalido_devolve_none_em_vez_de_zero(corpo):
    # Nunca inventar 0 dBm: uma mensagem não reconhecida precisa virar lacuna.
    assert decode_meas_report(corpo) is None


def test_mensagem_de_outro_tipo_nao_e_confundida_com_measurement_report():
    # RRC_CONN_REQ capturado no mesmo trace (novo-curl-vip.md).
    assert decode_meas_report("04 4B 8D AC 11 F7 44") is None
