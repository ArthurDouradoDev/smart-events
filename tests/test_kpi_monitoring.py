import json
import logging
import re
import time
from functools import lru_cache
from pathlib import Path

import pytest
import requests

from core.collector import HttpCollector
from core import database as db


def _event():
    return {
        "id": "monitoring-phase2", "oss": {"region": "SP"},
        "integration": {"pm_tasks": [{"task_id": 10, "tech": "4G"}, {"task_id": 20, "tech": "NRCELL"}]},
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


@lru_cache(maxsize=1)
def _curitiba_task_objects():
    """Objetos reais e não sensíveis da abertura da task PM 2225."""
    har_path = Path(__file__).parents[1] / "har-oss-outros" / "har-monitoring-oss-tsl.har"
    har = json.loads(har_path.read_text(encoding="utf-8"))
    entry = next(
        item for item in har["log"]["entries"]
        if "/monitor/task/2225/start" in item["request"]["url"]
    )
    task = json.loads(entry["response"]["content"]["text"])["data"]["task"]
    return tuple(
        {"fdn": group["fdn"], **obj}
        for group in task["objectList"]
        for obj in group["objInstanceInfos"]
    )


def _curitiba_event_from_task():
    sites = {}
    for obj in _curitiba_task_objects():
        cell_match = re.search(r"Cell Name\s*=\s*([^,]+)", obj["objName"], re.I)
        assert cell_match, obj["objName"]
        cell_id = cell_match.group(1).strip()
        site_id = cell_id.split("-", 2)[1]
        sites.setdefault(site_id, []).append({"id": cell_id, "tech": "4G"})
    return {
        "id": "teste-curitiba-task-2225",
        "oss": {"region": "OUTRAS"},
        "integration": {"pm_task_id": 2225},
        "sites": [
            {"id": site_id, "name": site_id, "cells": cells}
            for site_id, cells in sites.items()
        ],
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


def test_os_116_objetos_reais_da_task_2225_mapeiam_116_de_116(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_curitiba_event_from_task(), "https://10.220.30.9:31943")
    objects = _curitiba_task_objects()

    mapped = [
        collector._resolve_monitoring_cell(2225, int(obj["objectNo"]), obj["objName"], "4G")
        for obj in objects
    ]

    assert len(objects) == 116
    assert sum(item is not None for item in mapped) == 116
    assert len({item["cell_id"] for item in mapped if item}) == 116


def test_parser_aceita_a_grafia_objectno_do_oss_de_curitiba(tmp_db, monkeypatch):
    """O resultado da PM em Curitiba identifica a célula em ``objectNo``/``objectName``
    e ainda devolve ``objName: null`` no mesmo dicionário. Lendo só ``objNo``, todos os
    objetos caíam em ``int(None)`` e eram contados como inválidos antes de ``received``:
    HTTP 200, 116 objetos por janela e zero medição."""
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_com_celula("4G-CTFZ01-18-I"), "https://oss.example")
    response = {"data": [{"taskId": 100, "execTime": 1_700_000_000_000, "results": [{
        "execTime": 1_700_000_000_000, "period": 5, "objRes": [{
            "obj": {
                "objectNo": "91162",
                "objectName": "SR-CTFZ01-eNodeB Function Name=4G-CTFZ01, "
                              "Local Cell ID=129, Cell Name=4G-CTFZ01-18-I",
                "objName": None,
            },
            "counterRes": [{"name": key, "value": value, "reliable": 1}
                           for key, value in _COUNTERS_4G.items()],
        }],
    }]}]}

    parsed = collector._parse_monitoring_response(response, {"100": "4G"})

    assert parsed["received"] == 1
    assert parsed["unmapped"] == 0
    assert any(row["cell_id"] == "4G-CTFZ01-18-I" for row in parsed["rows"])


def test_12_objetos_indisponiveis_nao_bloqueiam_as_outras_104_celulas(
        tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_curitiba_event_from_task(), "https://10.220.30.9:31943")
    objects = _curitiba_task_objects()
    unavailable = [obj for obj in objects if "CTFD60" in obj["objName"]]
    available = [obj for obj in objects if "CTFD60" not in obj["objName"]]
    response = {"data": [{
        "taskId": 2225,
        "execTime": 1_700_000_000_000,
        "objNoExecTimes": [
            {"objNo": int(obj["objectNo"]), "preExecTime": 1_700_000_000_000}
            for obj in available
        ],
        "results": [{
            "execTime": 1_700_000_000_000,
            "period": 5,
            "objRes": [
                _item(int(obj["objectNo"]), re.search(
                    r"Cell Name\s*=\s*([^,]+)", obj["objName"], re.I
                ).group(1), _COUNTERS_4G)
                for obj in available
            ],
        }],
    }]}

    parsed = collector._parse_monitoring_response(response, {"2225": "4G"})
    measured_cells = {
        row["cell_id"] for row in parsed["rows"] if row["scope"] == "CELL"
    }

    assert len(unavailable) == 12
    assert len(available) == 104
    assert parsed["received"] == 104
    assert parsed["unmapped"] == 0
    assert len(measured_cells) == 104


def test_celula_de_outra_tecnologia_nao_e_sequestrada_pela_task(tmp_db, monkeypatch):
    """O gate de tecnologia continua valendo quando ela é CONHECIDA: uma task 5G não
    pode adotar uma célula declaradamente 4G que tenha o mesmo nome."""
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_com_celula("4G-CELL"), "https://oss.example")
    parsed = collector._parse_monitoring_response(
        _resposta(1, "4G-CELL"), {"100": "5G_NRCELL"})
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

    # Contrato de descoberta capturado do navegador no OSS de Curitiba:
    # a chave objNoExecTimes não é enviada enquanto não há objetos conhecidos.
    assert payload == [{"taskId": 100, "preExecTime": 0}]


def test_reinicio_reutiliza_objetos_confirmados_para_restaurar_mapeamento(
        tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    monkeypatch.setattr(
        db, "get_collection_checkpoints",
        lambda *_: {"": "1700000000000", "91162": "1700000000100", "91163": "1700000000200"},
    )
    collector = HttpCollector(_event_com_celula("18NLCTAL01GI"), "https://oss.example")

    payload = collector._monitoring_payload(
        [{"task_id": 100, "technology": "4G"}], "OUTRAS")

    assert payload == [{
        "taskId": 100,
        "preExecTime": 1700000000000,
        "objNoExecTimes": [
            {"objNo": 91162, "preExecTime": 1700000000100},
            {"objNo": 91163, "preExecTime": 1700000000200},
        ],
    }]


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


def _sessao_que_responde(monkeypatch, collector, corpo):
    class Response:
        status_code = 200
        url = "https://oss.example/monitoring"
        text = ""

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return corpo

    class Session:
        def post(self, _url, json, **_kwargs):
            return Response()

    monkeypatch.setattr(collector, "_get_session", lambda *_: Session())
    monkeypatch.setattr(collector, "_check_session_valid", lambda *_: True)


def test_task_parada_no_oss_vira_causa_com_a_ultima_execucao(tmp_db, monkeypatch, caplog):
    """Reproduz o campo de 20/08: a task 2225 de OUTRAS respondia 200 com
    ``state=-1``, ``results`` vazio e ``execTime`` de quatro dias antes. O painel
    dizia só "Parcial", sem dizer que o OSS é que não executava a task."""
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    monkeypatch.setattr(db, "get_collection_checkpoints", lambda *_: {})
    collector = HttpCollector(_event_com_celula("18NLCTAL01GI"), "https://oss.example")
    parado_ms = int((time.time() - 4 * 24 * 3600) * 1000)
    _sessao_que_responde(monkeypatch, collector, {"data": [{
        "taskId": 100, "state": -1, "execTime": parado_ms, "results": [],
        "objNoExecTimes": [{"objNo": 91162, "preExecTime": parado_ms}],
    }]})

    with caplog.at_level(logging.WARNING, logger="core.collector"):
        result = collector.collect_kpis()

    assert "task 100" in result.cause
    assert "há 4 d" in result.cause
    assert "não registra execução nova" in result.cause
    assert any("task 100" in registro.getMessage() for registro in caplog.records)


def test_descoberta_sem_execTime_nao_acusa_task_parada(tmp_db, monkeypatch):
    """Primeiro ciclo de um evento novo: o OSS ainda não devolve ``execTime``.
    Sem essa evidência, a coleta não pode acusar task parada."""
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    monkeypatch.setattr(db, "get_collection_checkpoints", lambda *_: {})
    collector = HttpCollector(_event_com_celula("18NLCTAL01GI"), "https://oss.example")
    _sessao_que_responde(monkeypatch, collector, {"data": [{
        "taskId": 100, "state": -1, "results": [], "objNoExecTimes": [],
    }]})

    result = collector.collect_kpis()

    assert "não registra execução nova" not in (result.cause or "")


def test_ciclo_com_medicao_nao_acusa_task_parada(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    monkeypatch.setattr(db, "get_collection_checkpoints", lambda *_: {})
    collector = HttpCollector(_event_com_celula("18NLCTAL01GI"), "https://oss.example")
    _sessao_que_responde(monkeypatch, collector, _resposta(91162, "18NLCTAL01GI"))

    result = collector.collect_kpis()

    assert "não registra execução nova" not in (result.cause or "")


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


@lru_cache(maxsize=1)
def _sp_nrducell_response():
    har_path = Path(__file__).parents[1] / "har-5g-oss" / "har-monitoring-oss-tsp-5g-ducell.har"
    har = json.loads(har_path.read_text(encoding="utf-8"))
    entry = next(
        item for item in har["log"]["entries"]
        if "/monitor/task/result" in item["request"]["url"]
    )
    return json.loads(entry["response"]["content"]["text"])


def _sp_nrducell_event():
    sites = {}
    task = _sp_nrducell_response()["data"][0]
    for result in task["results"]:
        for item in result["objRes"]:
            match = re.search(r"Cell Name\s*=\s*([^,]+)", item["obj"]["objName"], re.I)
            assert match
            cell_id = match.group(1).strip()
            site_id = cell_id.split("-", 2)[1]
            sites.setdefault(site_id, []).append({"id": cell_id, "tech": "5G"})
    return {
        "id": "santo-amaro-nrducell",
        "oss": {"region": "SP"},
        "integration": {"pm_tasks": [{"task_id": 748, "tech": "NRDUCELL"}]},
        "sites": [
            {"id": site_id, "name": site_id, "cells": cells}
            for site_id, cells in sites.items()
        ],
    }


def test_task_748_real_mapeia_celulas_e_calcula_so_kpis_nrducell(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_sp_nrducell_event(), "https://10.220.50.9:31943")

    parsed = collector._parse_monitoring_response(
        _sp_nrducell_response(), {"748": "5G_NRDUCELL"})

    cell_rows = [row for row in parsed["rows"] if row["scope"] == "CELL"]
    assert parsed["received"] == 5
    assert parsed["unmapped"] == 0
    assert {row["technology"] for row in cell_rows} == {"5G_NRDUCELL"}
    assert {row["metric"] for row in cell_rows} == {
        "utilization_dl", "utilization_ul", "throughput_ul", "interference_ul",
        "traffic_volume_dl_sa", "traffic_volume_dl_nsa",
        "traffic_volume_ul_sa", "traffic_volume_ul_nsa",
    }
    # Os contadores de DL não vêm nesta task: é ausência legítima, não defeito.
    absent = [item for item in parsed["diagnostics"] if item.code == "not_applicable"]
    assert absent
    assert {item.details["metric"] for item in absent} == {"throughput_dl"}
    assert not [item for item in parsed["diagnostics"] if item.code == "invalid_formula"]


def test_configured_pm_tasks_preserva_os_tipos_de_objeto_5g(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    event = _sp_nrducell_event()
    event["integration"]["pm_tasks"] = [
        {"task_id": 747, "tech": "LTE"},
        {"task_id": 749, "tech": "NR CELL"},
        {"task_id": 748, "tech": "NR DU CELL"},
    ]
    collector = HttpCollector(event, "https://10.220.50.9:31943")

    assert collector._configured_pm_tasks({}) == [
        {"task_id": 747, "technology": "4G", "period_seconds": 60},
        {"task_id": 749, "technology": "5G_NRCELL", "period_seconds": 60},
        {"task_id": 748, "technology": "5G_NRDUCELL", "period_seconds": 60},
    ]


def test_objno_igual_em_tasks_5g_nao_reaproveita_mapeamento(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    event = {
        "id": "objno-por-task", "oss": {"region": "OUTRAS"},
        "sites": [{"id": "SITE", "cells": [
            {"id": "5G-CELL-A", "tech": "5G"},
            {"id": "5G-CELL-B", "tech": "5G"},
        ]}],
    }
    collector = HttpCollector(event, "https://oss.example")

    nr_cell = collector._resolve_monitoring_cell(
        2241, 705, "NR Cell Name=5G-CELL-A", "5G_NRCELL")
    nr_du_cell = collector._resolve_monitoring_cell(
        2242, 705, "NR DU Cell Name=5G-CELL-B", "5G_NRDUCELL")

    assert nr_cell["cell_id"] == "5G-CELL-A"
    assert nr_du_cell["cell_id"] == "5G-CELL-B"
    assert (2241, 705) in collector._obj_to_cell
    assert (2242, 705) in collector._obj_to_cell


def test_cobertura_de_uma_task_5g_nao_cobra_celulas_4g(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    event = {
        "id": "coverage-5g", "oss": {"region": "SP"},
        "integration": {"pm_tasks": [{"task_id": 748, "tech": "NRDUCELL"}]},
        "sites": [{"id": "SITE", "cells": [
            {"id": "4G-CELL", "tech": "4G"},
            {"id": "5G-CELL", "tech": "5G"},
            {"id": "CELL-SEM-TECH"},
        ]}],
    }
    collector = HttpCollector(event, "https://oss.example")
    tasks = collector._configured_pm_tasks({})

    assert collector._expected_pm_cell_ids(tasks) == {"5G-CELL", "CELL-SEM-TECH"}


def _event_multi_4g():
    return {
        "id": "multi-pm-4g", "oss": {"region": "SP"},
        "integration": {"pm_tasks": [
            {"task_id": 747, "tech": "4G"},
            {"task_id": 752, "tech": "4G"},
            {"task_id": 749, "tech": "NRCELL"},
        ]},
        "sites": [{"id": "SITE", "name": "SITE", "cells": [
            {"id": "4G-CELL-A", "tech": "4G"},
            {"id": "4G-CELL-B", "tech": "4G"},
            {"id": "5G-CELL", "tech": "5G"},
        ]}],
    }


def _task_body(task_id, cell_name, counters=None, obj_no=1, exec_time=1_700_000_000_000):
    return {"data": [{
        "taskId": task_id, "execTime": exec_time,
        "results": [{"execTime": exec_time, "period": 5,
                     "objRes": [_item(obj_no, cell_name, counters or _COUNTERS_4G)]}],
        "objNoExecTimes": [{"objNo": obj_no, "preExecTime": exec_time}],
    }]}


def _http_response(body, status=200):
    class Response:
        status_code = status
        url = "https://oss.example/monitoring"
        text = ""
        headers = {}

        def raise_for_status(self):
            if self.status_code >= 400:
                error = requests.exceptions.HTTPError(f"{self.status_code} Server Error")
                error.response = self
                raise error

        def json(self):
            return body

    return Response()


def _sessao_por_task(monkeypatch, collector, bodies_by_task):
    posts = []

    class Session:
        def post(self, _url, json, **_kwargs):
            posts.append(json)
            task_id = json[0]["taskId"]
            spec = bodies_by_task[task_id]
            if isinstance(spec, int):
                return _http_response({}, status=spec)
            return _http_response(spec)

    monkeypatch.setattr(collector, "_get_session", lambda *_: Session())
    monkeypatch.setattr(collector, "_check_session_valid", lambda *_: True)
    return posts


def test_duas_tasks_da_mesma_tecnologia_sao_aceitas(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_multi_4g(), "https://oss.example")

    assert collector._configured_pm_tasks({}) == [
        {"task_id": 747, "technology": "4G", "period_seconds": 60},
        {"task_id": 752, "technology": "4G", "period_seconds": 60},
        {"task_id": 749, "technology": "5G_NRCELL", "period_seconds": 60},
    ]


def test_task_id_repetido_e_rejeitado(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    event = _event_multi_4g()
    event["integration"]["pm_tasks"] = [
        {"task_id": 747, "tech": "4G"},
        {"task_id": 747, "tech": "NRCELL"},
    ]
    collector = HttpCollector(event, "https://oss.example")

    result = collector.collect_kpis()

    assert result.state == "error"
    assert "747" in (result.cause or "")


def test_cada_task_vira_uma_requisicao(tmp_db, monkeypatch, caplog):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    monkeypatch.setattr(db, "get_collection_checkpoints", lambda *_: {})
    collector = HttpCollector(_event_multi_4g(), "https://oss.example")
    posts = _sessao_por_task(monkeypatch, collector, {
        747: _task_body(747, "4G-CELL-A", obj_no=11),
        752: _task_body(752, "4G-CELL-B", obj_no=22),
        749: _task_body(749, "5G-CELL", {"N.User.RRCConn.Avg": 4}, obj_no=33),
    })

    with caplog.at_level(logging.INFO, logger="core.collector"):
        collector.collect_kpis()

    assert len(posts) == 3
    assert all(len(payload) == 1 for payload in posts)
    assert [payload[0]["taskId"] for payload in posts] == [747, 752, 749]
    messages = [record.getMessage() for record in caplog.records]
    assert any("task=747" in message and "HTTP=200" in message for message in messages)
    assert any("task=752" in message and "HTTP=200" in message for message in messages)
    assert any("task=749" in message and "HTTP=200" in message for message in messages)
    assert any("ciclo tasks=3" in message for message in messages)


def test_falha_de_uma_task_nao_derruba_as_outras(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    monkeypatch.setattr(db, "get_collection_checkpoints", lambda *_: {})
    collector = HttpCollector(_event_multi_4g(), "https://oss.example")
    _sessao_por_task(monkeypatch, collector, {
        747: _task_body(747, "4G-CELL-A", obj_no=11),
        752: 500,
        749: _task_body(749, "5G-CELL", {"N.User.RRCConn.Avg": 4}, obj_no=33),
    })

    result = collector.collect_kpis()

    assert result.state == "partial"
    cell_ids = {row["cell_id"] for row in result.measurements if row["scope"] == "CELL"}
    assert "4G-CELL-A" in cell_ids
    assert "5G-CELL" in cell_ids
    assert "4G-CELL-B" not in cell_ids
    assert any(
        item.code == "http" and item.details.get("task_id") == 752
        for item in result.diagnostics
    )
    assert "task 752" in (result.cause or "")


def test_sessao_expirada_renova_uma_vez_por_ciclo(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    monkeypatch.setattr(db, "get_collection_checkpoints", lambda *_: {})
    collector = HttpCollector(_event_multi_4g(), "https://oss.example")
    session_ok = {"value": False}
    renews = []
    posts = []
    bodies = {
        747: _task_body(747, "4G-CELL-A", obj_no=11),
        752: _task_body(752, "4G-CELL-B", obj_no=22),
        749: _task_body(749, "5G-CELL", {"N.User.RRCConn.Avg": 4}, obj_no=33),
    }

    class Session:
        def post(self, _url, json, **_kwargs):
            posts.append(json)
            return _http_response(bodies[json[0]["taskId"]])

    monkeypatch.setattr(collector, "_get_session", lambda *_: Session())
    monkeypatch.setattr(
        collector, "_check_session_valid", lambda *_: session_ok["value"])

    def _renew(_module):
        renews.append(_module)
        session_ok["value"] = True
        return True

    monkeypatch.setattr(collector, "_renew_session", _renew)

    result = collector.collect_kpis()

    assert len(renews) == 1
    assert [payload[0]["taskId"] for payload in posts] == [747, 747, 752, 749]
    assert result.state in {"data", "partial"}
    cell_ids = {row["cell_id"] for row in result.measurements if row["scope"] == "CELL"}
    assert "4G-CELL-A" in cell_ids
    assert "4G-CELL-B" in cell_ids


def test_diagnosticos_sao_deduplicados_por_metrica_e_codigo(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_com_celula("18NLCTAL01GI"), "https://oss.example")
    objects = [_item(index, "18NLCTAL01GI", _COUNTERS_4G) for index in range(100)]
    parsed = collector._parse_monitoring_response(
        {"data": [{"taskId": 100, "results": [{"period": 5, "objRes": objects}]}]},
        {"100": "4G"},
    )

    formula = [item for item in parsed["diagnostics"] if item.code == "not_applicable"]
    by_metric = [item.details["metric"] for item in formula]
    assert by_metric
    assert len(by_metric) == len(set(by_metric))
    assert by_metric.count("availability") == 1


def test_cursores_de_tasks_distintas_nao_se_misturam(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    monkeypatch.setattr(db, "get_collection_checkpoints", lambda *_: {})
    event = {
        "id": "cursors-same-objno", "oss": {"region": "SP"},
        "integration": {"pm_tasks": [
            {"task_id": 747, "tech": "4G"},
            {"task_id": 752, "tech": "4G"},
        ]},
        "sites": [{"id": "SITE", "name": "SITE", "cells": [
            {"id": "4G-CELL-A", "tech": "4G"},
            {"id": "4G-CELL-B", "tech": "4G"},
        ]}],
    }
    collector = HttpCollector(event, "https://oss.example")
    _sessao_por_task(monkeypatch, collector, {
        747: _task_body(747, "4G-CELL-A", obj_no=70, exec_time=111),
        752: _task_body(752, "4G-CELL-B", obj_no=70, exec_time=222),
    })

    result = collector.collect_kpis()

    assert result.cursors["747:70"]["cursor"] == 111
    assert result.cursors["752:70"]["cursor"] == 222
    assert result.cursors["747:70"]["task_id"] == 747
    assert result.cursors["752:70"]["task_id"] == 752


# ── Fase 1: período configurado, "não aplicável" e aviso de PRB ──────────

def _availability_response(task_id, cell_name, avail_dur, period_na_resposta=5):
    return {"data": [{"taskId": task_id, "execTime": 1_700_000_000_000, "results": [{
        "execTime": 1_700_000_000_000, "period": period_na_resposta,
        "objRes": [_item(1, cell_name, {"N.Cell.Avail.Dur": avail_dur})],
    }]}]}


def _event_5g_nrcell(task_id=20, period_seconds=None):
    task = {"task_id": task_id, "tech": "NRCELL"}
    if period_seconds is not None:
        task["period_seconds"] = period_seconds
    return {
        "id": "monitoring-period", "oss": {"region": "SP"},
        "integration": {"pm_tasks": [task]},
        "sites": [{"id": "SITE", "name": "SITE",
                   "cells": [{"id": "5G-CELL", "tech": "5G", "obj_no": 1}]}],
    }


def test_period_comes_from_task_config_not_response(tmp_db, monkeypatch):
    """O ``period=5`` da resposta não é o Granularity Period: com ele, uma célula
    disponível o minuto inteiro (``N.Cell.Avail.Dur=60``) saía com 20%."""
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_5g_nrcell(period_seconds=60), "https://oss.example")

    parsed = collector._parse_monitoring_response(
        _availability_response(20, "5G-CELL", 60), {"20": "5G_NRCELL"}, {"20": 60})

    availability = [row for row in parsed["rows"]
                    if row["metric"] == "availability" and row["scope"] == "CELL"]
    assert availability and availability[0]["value"] == pytest.approx(100.0)


def test_task_period_of_15_minutes_changes_availability(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_5g_nrcell(period_seconds=900), "https://oss.example")

    assert collector._configured_pm_tasks({}) == [
        {"task_id": 20, "technology": "5G_NRCELL", "period_seconds": 900}]

    parsed = collector._parse_monitoring_response(
        _availability_response(20, "5G-CELL", 900), {"20": "5G_NRCELL"}, {"20": 900})
    availability = [row for row in parsed["rows"]
                    if row["metric"] == "availability" and row["scope"] == "CELL"]
    assert availability[0]["value"] == pytest.approx(100.0)


def test_task_without_period_defaults_to_60_seconds(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_5g_nrcell(), "https://oss.example")

    assert collector._configured_pm_tasks({}) == [
        {"task_id": 20, "technology": "5G_NRCELL", "period_seconds": 60}]

    # Sem o mapa de períodos, o parser assume o mesmo default.
    parsed = collector._parse_monitoring_response(
        _availability_response(20, "5G-CELL", 60), {"20": "5G_NRCELL"})
    availability = [row for row in parsed["rows"] if row["metric"] == "availability"]
    assert availability[0]["value"] == pytest.approx(100.0)


def test_response_period_divergence_only_warns(tmp_db, monkeypatch, caplog):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_5g_nrcell(), "https://oss.example")

    with caplog.at_level(logging.WARNING):
        collector._parse_monitoring_response(
            _availability_response(20, "5G-CELL", 60, period_na_resposta=5),
            {"20": "5G_NRCELL"}, {"20": 60})

    avisos = [record for record in caplog.records if "period=5" in record.getMessage()]
    assert len(avisos) == 1


def test_missing_counter_is_not_applicable_not_invalid(tmp_db, monkeypatch):
    """Contador fora da task não é defeito: o ciclo 4G saudável deixava de fechar
    "Com dados" só porque availability e drop não estavam na task."""
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_com_celula("4G-CELL-A", tech="4G"), "https://oss.example")

    parsed = collector._parse_monitoring_response(_resposta(1, "4G-CELL-A"), {"100": "4G"})

    assert parsed["invalid"] == 0
    assert parsed["not_applicable"] > 0
    assert all(item.code != "invalid_formula" for item in parsed["diagnostics"])


def test_zero_denominator_is_not_applicable(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_com_celula("4G-CELL-A", tech="4G"), "https://oss.example")
    sem_tentativas = {name: 0 for name in _COUNTERS_4G}

    parsed = collector._parse_monitoring_response(
        _resposta(1, "4G-CELL-A", sem_tentativas), {"100": "4G"})

    assert parsed["invalid"] == 0
    accessibility = [item for item in parsed["diagnostics"]
                     if item.details.get("metric") == "accessibility"]
    assert accessibility and accessibility[0].code == "not_applicable"


def test_non_standard_prb_avail_logs_warning(tmp_db, monkeypatch, caplog):
    """A3 — 150/225 PRBs não existem no LTE. Nenhuma fórmula muda; o aviso
    existe para a próxima ocorrência não passar nove dias despercebida."""
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_com_celula("4G-SPSMH1-18-DA", tech="4G"),
                              "https://oss.example")
    counters = {**_COUNTERS_4G, "L.ChMeas.PRB.DL.Avail": 225,
                "L.ChMeas.PRB.UL.Avail": 225, "L.ChMeas.PRB.DL.Used.Avg": 10,
                "L.ChMeas.PRB.UL.Used.Avg": 10}

    with caplog.at_level(logging.WARNING):
        collector._parse_monitoring_response(
            _resposta(1, "4G-SPSMH1-18-DA", counters), {"100": "4G"})
        # Segunda passagem: o aviso é uma vez por célula por sessão.
        collector._parse_monitoring_response(
            _resposta(1, "4G-SPSMH1-18-DA", counters), {"100": "4G"})

    avisos = [record for record in caplog.records if "PRB.Avail" in record.getMessage()]
    assert len(avisos) == 1
    assert "4G-SPSMH1-18-DA" in avisos[0].getMessage()


def test_standard_prb_avail_is_silent(tmp_db, monkeypatch, caplog):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_com_celula("4G-CELL-A", tech="4G"), "https://oss.example")
    counters = {**_COUNTERS_4G, "L.ChMeas.PRB.DL.Avail": 75, "L.ChMeas.PRB.UL.Avail": 75}

    with caplog.at_level(logging.WARNING):
        collector._parse_monitoring_response(
            _resposta(1, "4G-CELL-A", counters), {"100": "4G"})

    assert not [record for record in caplog.records if "PRB.Avail" in record.getMessage()]


def test_measurements_carry_the_sample_size_for_the_alert_floor(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_com_celula("4G-CELL-A", tech="4G"), "https://oss.example")
    poucas_tentativas = {**_COUNTERS_4G, "L.RRC.ConnReq.Att": 2, "L.RRC.ConnReq.Succ": 1}

    parsed = collector._parse_monitoring_response(
        _resposta(1, "4G-CELL-A", poucas_tentativas), {"100": "4G"})

    accessibility = [row for row in parsed["rows"]
                     if row["metric"] == "accessibility" and row["scope"] == "CELL"]
    assert accessibility[0]["sample_size"] == 2.0


def test_sample_size_is_not_persisted(tmp_db, monkeypatch):
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    collector = HttpCollector(_event_com_celula("4G-CELL-A", tech="4G"), "https://oss.example")
    parsed = collector._parse_monitoring_response(_resposta(1, "4G-CELL-A"), {"100": "4G"})

    inserted = db.insert_kpi_batch(parsed["rows"])

    assert inserted["inserted"] == len(parsed["rows"])


def test_site_throughput_is_recalculated_not_summed(tmp_db, monkeypatch):
    """B9 — duas células com a mesma taxa dão a taxa, não o dobro dela."""
    monkeypatch.setattr(db, "get_event_vips", lambda *_: [])
    event = {
        "id": "site-throughput", "oss": {"region": "SP"},
        "integration": {"pm_tasks": [{"task_id": 100, "tech": "4G"}]},
        "sites": [{"id": "SITE", "name": "SITE", "cells": [
            {"id": "4G-CELL-A", "tech": "4G", "obj_no": 1},
            {"id": "4G-CELL-B", "tech": "4G", "obj_no": 2},
        ]}],
    }
    collector = HttpCollector(event, "https://oss.example")
    thrp = {"L.Thrp.bits.DL": 4_000_000, "L.Thrp.bits.DL.LastTTI": 1_000_000,
            "L.Thrp.Time.DL.RmvLastTTI": 100}
    response = {"data": [{"taskId": 100, "execTime": 1_700_000_000_000, "results": [{
        "execTime": 1_700_000_000_000, "period": 5, "objRes": [
            _item(1, "4G-CELL-A", thrp), _item(2, "4G-CELL-B", thrp)]}]}]}

    parsed = collector._parse_monitoring_response(response, {"100": "4G"}, {"100": 60})

    cell = [row for row in parsed["rows"]
            if row["metric"] == "throughput_dl" and row["scope"] == "CELL"]
    site = [row for row in parsed["rows"]
            if row["metric"] == "throughput_dl" and row["scope"] == "SITE"]
    assert len(cell) == 2 and len(site) == 1
    assert site[0]["value"] == pytest.approx(cell[0]["value"])
