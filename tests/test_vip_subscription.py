"""Contrato offline do consumo incremental de Trace/VIP.

As fixtures vêm da captura real da task 2072 (`har-atualizado-filtrado.har`):
`query/result` aloca o msgId do ciclo e `filter-by-cols` devolve apenas as
mensagens `RRC_MEAS_RPRT`, cujo RSRP/RSRQ é decodificado localmente.
"""

import copy
import json
from pathlib import Path

import core.database as db
from core.collector import HttpCollector
from core.scheduler import Scheduler


FIXTURES = Path(__file__).parent / "fixtures"
FILTER_PAGE = json.loads((FIXTURES / "vip_filter_by_cols.json").read_text(encoding="utf-8"))
QUERY_RESULT = json.loads((FIXTURES / "vip_query_result.json").read_text(encoding="utf-8"))
ALL_ROWS = FILTER_PAGE["data"]["tableData"]
BOOTSTRAP_MSG_ID = QUERY_RESULT["data"]["msgId"]
# Janela devolvida por fetch-field-values para a task 2072.
WINDOW = ("2026-08-11 10:30:45", "2026-08-12 00:21:44")


class _Response:
    status_code = 200
    content = b"{}"
    headers = {}
    url = "https://oss.test/rest"
    text = "{}"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def _page(rows, total=None):
    """Monta uma resposta de filter-by-cols com o mesmo envelope do FARS."""
    return {"recordCount": len(rows) if total is None else total,
            "data": {"lastSerialNo": None, "msgId": BOOTSTRAP_MSG_ID,
                     "tableData": list(rows), "serialNo": None}}


class _TraceSession:
    """Sessão falsa que reproduz o fluxo capturado no Network."""

    def __init__(self, rows=None, payload=None):
        self.rows = ALL_ROWS if rows is None else rows
        self.payload = payload          # sobrepõe a resposta de filter-by-cols
        self.calls = []                 # (url, params) de todo GET
        self.posts = []                 # corpos enviados ao filter-by-cols
        self.msg_ids = iter(range(BOOTSTRAP_MSG_ID, BOOTSTRAP_MSG_ID + 100))

    def get(self, url, params=None, **kwargs):
        self.calls.append((url, dict(params or {})))
        if url.endswith("/pre-check"):
            return _Response({"checkState": True})
        if url.endswith("/query/result"):
            return _Response({"recordCount": QUERY_RESULT["recordCount"],
                              "data": {"msgId": next(self.msg_ids), "tableData": []}})
        if url.endswith("/fetch-field-values"):
            return _Response({"startTime": WINDOW[0], "endTime": WINDOW[1],
                              "filterMap": {}, "signalList": []})
        raise AssertionError(f"GET inesperado: {url}")

    def post(self, url, params=None, json=None, **kwargs):
        assert url.endswith("/filter-by-cols"), url
        self.posts.append(json)
        if self.payload is not None:
            return _Response(self.payload)
        page = json["pageDto"]
        start, size = page["startRow"], page["pageSize"]
        return _Response(_page(self.rows[start:start + size], total=len(self.rows)))


class _ExpiredOnceTraceSession(_TraceSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.expired = False

    def get(self, url, params=None, **kwargs):
        if url.endswith("/pre-check") and not self.expired:
            self.expired = True
            response = _Response({})
            response.status_code = 401
            response.content = b""
            return response
        return super().get(url, params, **kwargs)


def _collector(sample_event, monkeypatch, session=None):
    session = session or _TraceSession()
    monkeypatch.setattr(db, "get_event_vips", lambda *args: [
        {"id": "vip-1", "name": "VIP Teste", "task_id": 2072}
    ])
    collector = HttpCollector(sample_event, "https://oss.test")
    monkeypatch.setattr(collector, "_get_session", lambda module: session)
    return collector, session


# ── Bootstrap e contrato da requisição ──────────────────────────────

def test_bootstrap_decodifica_a_captura_real(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    collector, _ = _collector(sample_event, monkeypatch)

    result = collector.collect_vips()

    assert result.state == "data"
    assert len(result.measurements) == len(ALL_ROWS)
    first = result.measurements[0]
    assert first["serial_no"] == 64454
    assert first["rsrp"] == -85.0 and first["rsrq"] == -4.0
    assert first["serving_cell"] == "SR-SPSMI4_22"
    assert first["vip_name"] == "VIP Teste"


def test_milissegundos_de_um_a_tres_digitos_sao_preservados(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    collector, _ = _collector(sample_event, monkeypatch)

    result = collector.collect_vips()

    # "(849)" → .849; o FARS não zero-preenche, então "(98)" precisa virar .098.
    assert result.measurements[0]["timestamp"].endswith(".849Z")


def test_o_filtro_de_tipo_e_resolvido_no_servidor(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    collector, session = _collector(sample_event, monkeypatch)

    collector.collect_vips()

    assert session.posts[0]["colFilterDto"]["colFltExpSeq"] == [
        {"fieldId": "Message Type", "value": "RRC_MEAS_RPRT", "operator": {"op": 0}}
    ]


def test_a_janela_da_task_e_lida_antes_de_filtrar(tmp_db, sample_event, monkeypatch):
    """Regressão: filter-by-cols devolve HTTP 500 com startTime/endTime vazios."""
    db.save_event(sample_event)
    collector, session = _collector(sample_event, monkeypatch)

    collector.collect_vips()

    assert any(url.endswith("/fetch-field-values") for url, _ in session.calls)
    filtro = session.posts[0]["colFilterDto"]
    assert (filtro["startTime"], filtro["endTime"]) == WINDOW
    assert filtro["hasStartTime"] is False and filtro["hasEndTime"] is False


def test_task_sem_janela_nao_vira_ciclo_saudavel(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    session = _TraceSession()
    original = session.get
    monkeypatch.setattr(session, "get", lambda url, params=None, **k: _Response({})
                        if url.endswith("/fetch-field-values") else original(url, params, **k))
    collector, _ = _collector(sample_event, monkeypatch, session)

    result = collector.collect_vips()

    assert result.state == "partial"
    assert result.coverage["tasks_valid"] == 0


def test_o_msgid_usado_no_filtro_e_o_alocado_no_mesmo_ciclo(tmp_db, sample_event, monkeypatch):
    """Regressão: reaproveitar um msgId de outro ciclo fez o FARS responder vazio."""
    db.save_event(sample_event)
    collector, session = _collector(sample_event, monkeypatch)

    first = collector.collect_vips()
    db.insert_vip_batch(first.measurements)
    db.save_collection_checkpoints(sample_event["id"], first.cursors, collector="vip", oss="SP")
    collector.collect_vips()

    alocados = [params for url, params in session.calls if url.endswith("/query/result")]
    assert [p["msgId"] for p in alocados] == [1, 1]      # sempre pede alocação nova
    assert session.posts[0]["pageDto"]["msgId"] == BOOTSTRAP_MSG_ID
    assert session.posts[1]["pageDto"]["msgId"] == BOOTSTRAP_MSG_ID + 1


# ── Cursor incremental ──────────────────────────────────────────────

def test_cursores_de_serial_e_offset_sao_confirmados_juntos(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    collector, _ = _collector(sample_event, monkeypatch)

    result = collector.collect_vips()

    assert result.cursors["2072:serial"]["cursor"] == 335034
    assert result.cursors["2072:row"]["cursor"] == len(ALL_ROWS)


def test_segundo_ciclo_continua_do_offset_persistido(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    collector, session = _collector(sample_event, monkeypatch)
    first = collector.collect_vips()
    db.insert_vip_batch(first.measurements)
    db.save_collection_checkpoints(sample_event["id"], first.cursors, collector="vip", oss="SP")

    result = collector.collect_vips()

    assert session.posts[1]["pageDto"]["startRow"] == len(ALL_ROWS)
    assert result.state == "empty"
    assert result.measurements == []


def test_replay_do_mesmo_lote_nao_duplica(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    collector, _ = _collector(sample_event, monkeypatch)
    result = collector.collect_vips()

    assert db.insert_vip_batch(result.measurements)["inserted"] == len(ALL_ROWS)
    assert db.insert_vip_batch(result.measurements) == {"inserted": 0, "duplicate": len(ALL_ROWS)}


def test_dois_relatorios_no_mesmo_milissegundo_sao_preservados(tmp_db, sample_event, monkeypatch):
    """Serials 64476/64477 têm timestamp idêntico na captura real."""
    db.save_event(sample_event)
    collector, _ = _collector(sample_event, monkeypatch)
    result = collector.collect_vips()
    db.insert_vip_batch(result.measurements)

    gemeos = [m for m in result.measurements if m["serial_no"] in (64476, 64477)]
    assert len(gemeos) == 2
    assert gemeos[0]["timestamp"] == gemeos[1]["timestamp"]
    assert len(db.get_vip_series(sample_event["id"], "VIP Teste", minutes=0)) == len(ALL_ROWS)


def test_task_reiniciada_rele_do_inicio_em_vez_de_travar(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    collector, session = _collector(sample_event, monkeypatch)
    db.save_collection_checkpoints(sample_event["id"], {
        "2072:serial": {"task_id": 2072, "object_key": "serial", "cursor": 999999},
        "2072:row": {"task_id": 2072, "object_key": "row", "cursor": 5000},
    }, collector="vip", oss="SP")

    result = collector.collect_vips()

    assert [p["pageDto"]["startRow"] for p in session.posts] == [5000, 0]
    assert len(result.measurements) == len(ALL_ROWS)


# ── Backlog, decode e falhas ────────────────────────────────────────

def test_backlog_mantem_o_ciclo_parcial_e_avanca_o_offset(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    collector, session = _collector(sample_event, monkeypatch)
    monkeypatch.setattr(type(collector), "_VIP_PAGE_SIZE", 5)
    monkeypatch.setattr(type(collector), "_VIP_MAX_PAGES_PER_CYCLE", 2)

    result = collector.collect_vips()

    assert result.state == "partial"
    assert len(result.measurements) == 10
    assert result.cursors["2072:row"]["cursor"] == 10
    assert result.coverage["backlog_messages"] == len(ALL_ROWS) - 10


def test_mensagem_indecifravel_vira_diagnostico_sem_travar_o_cursor(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    rows = copy.deepcopy(ALL_ROWS)
    rows[1]["messageBody"] = "00 00 00 00"       # não é um MeasurementReport
    collector, _ = _collector(sample_event, monkeypatch, _TraceSession(rows=rows))

    result = collector.collect_vips()

    assert len(result.measurements) == len(ALL_ROWS) - 1
    assert result.coverage["undecoded_messages"] == 1
    # O decode é determinístico: parar no serial problemático travaria a coleta.
    assert result.cursors["2072:serial"]["cursor"] == 335034
    assert any(d.code == "decode" for d in result.diagnostics)


def test_sessao_expirada_so_e_superada_pelo_retry_real(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    collector, _ = _collector(sample_event, monkeypatch, _ExpiredOnceTraceSession())
    renovadas = []
    monkeypatch.setattr(collector, "_renew_session", lambda module: renovadas.append(module) or True)

    result = collector.collect_vips()

    assert renovadas == ["trace"]
    assert result.state == "data"


def test_renovacao_que_nao_resolve_permanece_auth_required(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    session = _TraceSession()
    expirada = _Response({})
    expirada.status_code = 401
    expirada.content = b""
    monkeypatch.setattr(session, "get", lambda *a, **k: expirada)
    collector, _ = _collector(sample_event, monkeypatch, session)
    monkeypatch.setattr(collector, "_renew_session", lambda module: True)

    result = collector.collect_vips()

    assert result.state == "auth_required"
    assert result.measurements == []


def test_contrato_invalido_do_filtro_fica_visivel_como_parcial(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    session = _TraceSession(payload={"data": {"msgId": 1, "tableData": "inválido"}})
    collector, _ = _collector(sample_event, monkeypatch, session)

    result = collector.collect_vips()

    assert result.state == "partial"
    assert result.coverage["tasks_invalid"] == 1
    assert result.measurements == []


def test_task_recusada_no_pre_check_nao_vira_ciclo_saudavel(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    session = _TraceSession()
    monkeypatch.setattr(session, "get", lambda url, params=None, **k: _Response(
        {"checkState": False} if url.endswith("/pre-check") else {"data": {"msgId": 1}}))
    collector, _ = _collector(sample_event, monkeypatch, session)

    result = collector.collect_vips()

    assert result.state == "partial"
    assert result.coverage["tasks_valid"] == 0


def test_sem_vip_configurado_e_vazio_explicito(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    monkeypatch.setattr(db, "get_event_vips", lambda *args: [])
    collector = HttpCollector(sample_event, "https://oss.test")

    result = collector.collect_vips()

    assert result.state == "empty"
    assert result.coverage["vips_configured"] == 0


# ── Integração com o agendador ──────────────────────────────────────

def test_scheduler_persiste_os_cursores_depois_do_lote(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    collector, _ = _collector(sample_event, monkeypatch)
    scheduler = Scheduler()
    scheduler._collector = collector
    scheduler._event_config = sample_event

    scheduler._collect_vips(mode="incremental")

    salvos = db.get_collection_checkpoints(sample_event["id"], "vip", 2072, "SP")
    assert salvos["serial"] == "335034"
    assert salvos["row"] == str(len(ALL_ROWS))
