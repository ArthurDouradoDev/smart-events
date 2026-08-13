from core.collector import HttpCollector
from core import database as db


def _event():
    return {
        "id": "monitoring-phase2", "oss": {"region": "SP"},
        "integration": {"pm_tasks": [{"task_id": 10, "tech": "4G"}, {"task_id": 20, "tech": "5G"}]},
        "sites": [{"id": "SITE", "name": "SITE", "cells": [
            {"id": "4G-CELL", "tech": "4G", "obj_no": 1},
            {"id": "5G-CELL", "tech": "5G", "obj_no": 2},
        ]}],
    }


def _item(obj_no, name, counters):
    return {"obj": {"objNo": obj_no, "objName": f"Cell Name={name}"},
            "counterRes": [{"name": key, "value": value, "reliable": 1} for key, value in counters.items()]}


# Contadores suficientes para o cálculo de acessibilidade 4G.
_COUNTERS_4G = {
    "L.RRC.ConnReq.Succ": 10, "L.RRC.ConnReq.Att": 10, "L.E-RAB.SuccEst": 10, "L.E-RAB.AttEst": 10,
    "L.S1Sig.ConnEst.Succ": 10, "L.S1Sig.ConnEst.Att": 10,
}


def _event_com_celula(cell_id, tech=None):
    """Evento de uma célula só, com uma task PM 4G — o formato dos eventos de campo,
    em que a célula NÃO traz obj_no e o mapeamento depende do nome."""
    cell = {"id": cell_id}
    if tech:
        cell["tech"] = tech
    return {
        "id": "monitoring-regional", "oss": {"region": "OUTRAS"},
        "integration": {"pm_task_id": 100},
        "sites": [{"id": "SITE", "name": "SITE", "cells": [cell]}],
    }


def _resposta(obj_no, name, counters=None):
    return {"data": [{"taskId": 100, "execTime": 1_700_000_000_000,
                      "results": [{"execTime": 1_700_000_000_000, "period": 5,
                                   "objRes": [_item(obj_no, name, counters or _COUNTERS_4G)]}]}]}


def test_counter_res_is_calculated_and_site_rows_are_persisted(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event(), "https://oss.example")
    response = {"data": [
        {"taskId": 10, "execTime": 1_700_000_000_000, "objNoExecTimes": [{"objNo": 1, "preExecTime": 1_700_000_000_000}],
         "results": [{"execTime": 1_700_000_000_000, "period": 5, "objRes": [_item(1, "4G-CELL", {
            "L.RRC.ConnReq.Succ": 10, "L.RRC.ConnReq.Att": 10, "L.E-RAB.SuccEst": 10, "L.E-RAB.AttEst": 10,
            "L.S1Sig.ConnEst.Succ": 10, "L.S1Sig.ConnEst.Att": 10, "L.E-RAB.AbnormRel": 0,
            "L.E-RAB.AbnormRel.MME": 0, "L.E-RAB.NormRel": 10, "L.E-RAB.Rel.MME": 0,
            "L.Cell.Unavail.Dur.Sys": 0, "L.Cell.Unavail.Dur.Manual": 0,
            "L.ChMeas.PRB.DL.Used.Avg": 20, "L.ChMeas.PRB.DL.Avail": 100,
        })]}]},
    ]}
    parsed = collector._parse_monitoring_response(response, {"10": "4G"})
    assert any(row["metric"] == "accessibility" and row["value"] == 100 for row in parsed["rows"])
    assert any(row["scope"] == "SITE" and row["metric"] == "accessibility" for row in parsed["rows"])
    first = db.insert_kpi_batch(parsed["rows"])
    replay = db.insert_kpi_batch(parsed["rows"])
    assert first["inserted"] == len(parsed["rows"])
    assert replay["inserted"] == 0
    assert replay["duplicate"] == len(parsed["rows"])


def test_celula_sem_tecnologia_no_nome_e_mapeada_pela_task(tmp_db, monkeypatch):
    """Regional cujo OSS nomeia as células sem token de tecnologia (Curitiba:
    ``18NLCTAL01GI``). Antes, a tecnologia saía None, nunca era igual à da task e
    100% dos objetos eram descartados — o evento coletava zero."""
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_com_celula("18NLCTAL01GI"), "https://oss.example")
    parsed = collector._parse_monitoring_response(_resposta(11580, "18NLCTAL01GI"), {"100": "4G"})
    assert parsed["unmapped"] == 0
    linhas = [row for row in parsed["rows"] if row["scope"] == "CELL"]
    assert linhas, "a célula deveria ter sido mapeada e gerado medições"
    assert {row["cell_id"] for row in linhas} == {"18NLCTAL01GI"}
    # A tecnologia do dado vem da task consultada, não do palpite pelo nome.
    assert {row["technology"] for row in linhas} == {"4G"}


def test_celula_de_outra_tecnologia_nao_e_sequestrada_pela_task(tmp_db, monkeypatch):
    """O gate de tecnologia continua valendo quando ela é CONHECIDA: uma task 5G não
    pode adotar uma célula declaradamente 4G que tenha o mesmo nome."""
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_com_celula("4G-CELL"), "https://oss.example")
    parsed = collector._parse_monitoring_response(_resposta(1, "4G-CELL"), {"100": "5G"})
    assert parsed["unmapped"] == 1
    assert not [row for row in parsed["rows"] if row["scope"] == "CELL"]


def test_objeto_nao_mapeado_e_diagnosticado_pelo_nome(tmp_db, monkeypatch):
    """O nome não casado precisa aparecer no diagnóstico — é o dado que revela a
    diferença de convenção entre o inventário do evento e o OSS da regional."""
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_com_celula("4G-CTJA02-18-A", tech="4G"), "https://oss.example")
    resposta = _resposta(11580, "18NLCTAL01GI")
    # objName no item (e não dentro de obj), como o OSS também responde.
    item = resposta["data"][0]["results"][0]["objRes"][0]
    item["objName"] = item.pop("obj")["objName"]
    item["objNo"] = 11580
    parsed = collector._parse_monitoring_response(resposta, {"100": "4G"})
    assert parsed["unmapped"] == 1
    assert parsed["unmapped_cells"] == ["Cell Name=18NLCTAL01GI"]


def test_ciclo_parcial_confirma_so_o_cursor_do_objeto_mapeado(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_com_celula("18NLCTAL01GI"), "https://oss.example")
    resposta = _resposta(1, "18NLCTAL01GI")
    task = resposta["data"][0]
    task["objNoExecTimes"] = [
        {"objNo": 1, "preExecTime": 101},
        {"objNo": 2, "preExecTime": 102},
    ]
    task["results"][0]["objRes"].append(_item(2, "NAO-CASA", _COUNTERS_4G))

    parsed = collector._parse_monitoring_response(resposta, {"100": "4G"})

    assert parsed["unmapped"] == 1
    assert parsed["cursors"] == {
        "100:1": {"task_id": 100, "object_key": "1", "cursor": 101},
    }


def test_resposta_vazia_legitima_mantem_cursor_da_task(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_com_celula("18NLCTAL01GI"), "https://oss.example")

    parsed = collector._parse_monitoring_response(
        {"data": [{"taskId": 100, "execTime": 123, "results": []}]}, {"100": "4G"})

    assert parsed["received"] == 0
    assert parsed["unmapped"] == 0
    assert parsed["cursors"] == {
        "100:": {"task_id": 100, "object_key": "", "cursor": 123},
    }


def test_descoberta_ignora_cursor_geral_antigo(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    monkeypatch.setattr(
        db, "get_collection_checkpoints", lambda *_: {"": "9999999999999"})
    collector = HttpCollector(_event_com_celula("18NLCTAL01GI"), "https://oss.example")

    payload = collector._monitoring_payload(
        [{"task_id": 100, "technology": "4G"}], "OUTRAS")

    assert payload == [{"taskId": 100, "preExecTime": 0, "objNoExecTimes": []}]


def test_resposta_vazia_na_descoberta_nao_confirma_cursor(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    monkeypatch.setattr(
        db, "get_collection_checkpoints", lambda *_: {"": "9999999999999"})
    collector = HttpCollector(_event_com_celula("18NLCTAL01GI"), "https://oss.example")

    class Response:
        status_code = 200
        url = "https://oss.example/monitoring"
        text = ""

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"data": [{"taskId": 100, "execTime": 123, "results": []}]}

    class Session:
        def __init__(self):
            self.payload = None

        def post(self, _url, json, **_kwargs):
            self.payload = json
            return Response()

    session = Session()
    monkeypatch.setattr(collector, "_get_session", lambda *_: session)
    monkeypatch.setattr(collector, "_check_session_valid", lambda *_: True)

    result = collector.collect_kpis()

    assert session.payload[0]["preExecTime"] == 0
    assert result.cursors == {}


def test_log_unmapped_emite_warning_so_quando_ha_descarte(tmp_db, monkeypatch, caplog):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_com_celula("4G-CTJA02-18-A", tech="4G"), "https://oss.example")
    parsed = collector._parse_monitoring_response(_resposta(11580, "18NLCTAL01GI"), {"100": "4G"})

    with caplog.at_level("WARNING"):
        collector._log_unmapped(parsed)
    assert "18NLCTAL01GI" in caplog.text
    assert "DESCARTADOS" in caplog.text

    caplog.clear()
    parsed["unmapped"] = 0
    with caplog.at_level("WARNING"):
        collector._log_unmapped(parsed)
    assert not caplog.records


def test_unreliable_and_non_numeric_counters_create_diagnostics(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event(), "https://oss.example")
    response = {"data": [{"taskId": 10, "results": [{"period": 5, "objRes": [_item(1, "4G-CELL", {
        "L.RRC.ConnReq.Succ": "not-number", "L.RRC.ConnReq.Att": 0,
    })]}]}]}
    response["data"][0]["results"][0]["objRes"][0]["counterRes"][1]["reliable"] = 0
    parsed = collector._parse_monitoring_response(response, {"10": "4G"})
    assert parsed["invalid"] > 0
    assert any(item.code == "invalid_counter" for item in parsed["diagnostics"])
