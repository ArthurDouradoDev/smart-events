"""Contrato offline do consumo incremental de Trace/VIP.

As fixtures vêm da captura real da task 2072 (`har-atualizado-filtrado.har`):
`query/result` aloca o msgId do ciclo e `filter-by-cols` devolve apenas as
mensagens `RRC_MEAS_RPRT`, cujo RSRP/RSRQ é decodificado localmente.
"""

import copy
import json
import re
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


def _time_key(row):
    """Chave de ordenação por ``Time``, como o servidor ordena.

    Os milissegundos do FARS não são zero-preenchidos, então comparar a string
    crua colocaria ``(98)`` depois de ``(179)``.
    """
    fields = {f["name"]: f["value"] for f in (row.get("payload") or [])}
    match = re.match(r"(.+?)\s*\((\d+)\)\s*$", str(fields.get("Time", "")))
    return (match.group(1), match.group(2).zfill(3)) if match else (str(fields.get("Time", "")), "")


def _page(rows, msg_id, total=None):
    """Monta uma resposta paginada com o mesmo envelope do FARS."""
    return {"recordCount": len(rows) if total is None else total,
            "data": {"lastSerialNo": None, "msgId": msg_id,
                     "tableData": list(rows), "serialNo": None}}


class _TraceSession:
    """Sessão falsa que reproduz o fluxo capturado no Network.

    A cadeia de handles espelha a captura: ``query/result`` aloca um ``msgId``,
    ``filter-by-cols`` materializa o conjunto filtrado e devolve **outro**, e
    ``sort`` devolve um terceiro. Só o último aceita ``result-paging`` — é isso
    que garante que o coletor pagine o snapshot que ele mesmo ordenou.
    """

    def __init__(self, rows=None, payload=None):
        self.rows = ALL_ROWS if rows is None else rows
        self.payload = payload          # sobrepõe a resposta de result-paging
        self.calls = []                 # (url, params) de todo GET
        self.posts = []                 # corpos enviados ao filter-by-cols
        self.sorts = []                 # corpos enviados ao query/sort
        self.msg_ids = iter(range(BOOTSTRAP_MSG_ID, BOOTSTRAP_MSG_ID + 100))
        self.sorted_ids = {}            # handle ordenado -> linhas naquela ordem

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
        if url.endswith("/result-paging"):
            if self.payload is not None:
                return _Response(self.payload)
            msg_id = int(params["msgId"])
            assert msg_id in self.sorted_ids, (
                f"result-paging pediu o handle {msg_id}, que não é o conjunto ordenado")
            ordered = self.sorted_ids[msg_id]
            start, size = int(params["startRow"]), int(params["pageSize"])
            return _Response(_page(ordered[start:start + size], msg_id,
                                   total=len(ordered)))
        raise AssertionError(f"GET inesperado: {url}")

    def post(self, url, params=None, json=None, **kwargs):
        if url.endswith("/filter-by-cols"):
            self.posts.append(json)
            # O conjunto filtrado é um snapshot novo, com handle próprio.
            return _Response(_page([], next(self.msg_ids), total=len(self.rows)))
        if url.endswith("/query/sort"):
            self.sorts.append(json)
            sorted_id = next(self.msg_ids)
            # O servidor ordena de verdade; o conjunto filtrado chega embaralhado.
            self.sorted_ids[sorted_id] = sorted(
                self.rows, key=_time_key, reverse=not json["isAscend"])
            return _Response(_page([], sorted_id, total=len(self.rows)))
        raise AssertionError(f"POST inesperado: {url}")


class _UnsortedTraceSession(_TraceSession):
    """Servidor que aceita o `sort` mas devolve o conjunto na ordem crua."""

    def post(self, url, params=None, json=None, **kwargs):
        if url.endswith("/query/sort"):
            self.sorts.append(json)
            sorted_id = next(self.msg_ids)
            self.sorted_ids[sorted_id] = list(self.rows)
            return _Response(_page([], sorted_id, total=len(self.rows)))
        return super().post(url, params=params, json=json, **kwargs)


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


class _EmptyBootstrapTraceSession(_TraceSession):
    def get(self, url, params=None, **kwargs):
        if url.endswith("/query/result"):
            self.calls.append((url, dict(params or {})))
            return _Response({
                "recordCount": 0,
                "data": {"msgId": next(self.msg_ids), "tableData": []},
            })
        if url.endswith("/fetch-field-values"):
            raise AssertionError("task vazia não deve consultar fetch-field-values")
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
    # Cada ciclo queima três handles: bootstrap, filtrado e ordenado.
    assert session.posts[0]["pageDto"]["msgId"] == BOOTSTRAP_MSG_ID
    assert session.posts[1]["pageDto"]["msgId"] == BOOTSTRAP_MSG_ID + 3


def test_a_ordenacao_encadeia_o_handle_do_filtro(tmp_db, sample_event, monkeypatch):
    """O `sort` consome o conjunto filtrado, não o bootstrap."""
    db.save_event(sample_event)
    collector, session = _collector(sample_event, monkeypatch)

    collector.collect_vips()

    assert session.sorts[0]["msgId"] == session.posts[0]["pageDto"]["msgId"] + 1
    assert session.sorts[0]["sqlColumnName"] == "Time"


def test_a_ordenacao_e_ascendente_para_o_cursor_por_offset(tmp_db, sample_event, monkeypatch):
    """Descendente insere no início e desloca todo offset já consumido."""
    db.save_event(sample_event)
    collector, session = _collector(sample_event, monkeypatch)

    collector.collect_vips()

    assert session.sorts[0]["isAscend"] is True


def test_o_filtro_roda_uma_vez_e_a_paginacao_usa_o_conjunto_ordenado(
        tmp_db, sample_event, monkeypatch):
    """Regressão: re-POSTar filter-by-cols por página re-executa o filtro nos dados vivos.

    Duas páginas do mesmo ciclo podiam sair de snapshots diferentes. A sessão
    falsa recusa `result-paging` sobre qualquer handle que não seja o ordenado.
    """
    db.save_event(sample_event)
    collector, session = _collector(sample_event, monkeypatch)
    monkeypatch.setattr(type(collector), "_VIP_PAGE_SIZE", 5)
    monkeypatch.setattr(type(collector), "_VIP_MAX_PAGES_PER_CYCLE", 4)

    collector.collect_vips()

    assert len(session.posts) == 1          # um único filter-by-cols
    assert len(session.sorts) == 1          # uma única ordenação
    paginas = [p for url, p in session.calls if url.endswith("/result-paging")]
    assert [int(p["startRow"]) for p in paginas] == [0, 5, 10, 15]
    assert {int(p["msgId"]) for p in paginas} == {session.sorts[0]["msgId"] + 1}


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

    paginas = [p for url, p in session.calls if url.endswith("/result-paging")]
    assert int(paginas[-1]["startRow"]) == len(ALL_ROWS)
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

    # O total agora é conhecido antes de paginar: o offset estourado é descartado
    # sem gastar uma leitura, e a releitura começa direto do zero.
    paginas = [p for url, p in session.calls if url.endswith("/result-paging")]
    assert [int(p["startRow"]) for p in paginas] == [0]
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


def test_ciclo_parcial_nao_avanca_a_marca_dagua_de_serial(tmp_db, sample_event, monkeypatch):
    """Regressão: `max(serialNo)` de um lote parcial apaga o que ficou para trás.

    Enquanto houver backlog só o cursor `row` avança. A marca d'água de serial
    descartaria (`serial <= last_serial`) qualquer linha não lida de serial menor,
    e essas linhas nunca mais seriam buscadas.
    """
    db.save_event(sample_event)
    collector, _ = _collector(sample_event, monkeypatch)
    monkeypatch.setattr(type(collector), "_VIP_PAGE_SIZE", 5)
    monkeypatch.setattr(type(collector), "_VIP_MAX_PAGES_PER_CYCLE", 2)

    result = collector.collect_vips()

    assert result.coverage["backlog_tasks"] == 1
    assert result.cursors["2072:serial"]["cursor"] == 0     # não avançou
    assert result.cursors["2072:row"]["cursor"] == 10       # mas o offset sim


def test_ciclos_parciais_sucessivos_nao_perdem_nenhuma_linha(tmp_db, sample_event, monkeypatch):
    """O conjunto inteiro chega ao banco mesmo lido em pedaços."""
    db.save_event(sample_event)
    collector, _ = _collector(sample_event, monkeypatch)
    monkeypatch.setattr(type(collector), "_VIP_PAGE_SIZE", 5)
    monkeypatch.setattr(type(collector), "_VIP_MAX_PAGES_PER_CYCLE", 2)

    colhidos = []
    for _ in range(5):
        result = collector.collect_vips()
        colhidos.extend(result.measurements)
        db.insert_vip_batch(result.measurements)
        db.save_collection_checkpoints(
            sample_event["id"], result.cursors, collector="vip", oss="SP")
        if result.state != "partial":
            break

    assert {m["serial_no"] for m in colhidos} == {r["serialNo"] for r in ALL_ROWS}
    assert len(db.get_vip_series(sample_event["id"], "VIP Teste", minutes=0)) == len(ALL_ROWS)


def test_conjunto_fora_de_ordem_nao_perde_linhas_em_ciclo_parcial(
        tmp_db, sample_event, monkeypatch):
    """Defesa em profundidade: se a ordenação do servidor falhar, nada se perde.

    A ordenação é o que hoje mantém `row` e `serial` coerentes. Este teste tira
    essa garantia — a sessão devolve o conjunto embaralhado, como o `filter-by-cols`
    cru faz — e exige que o ciclo parcial ainda entregue todas as linhas.
    """
    db.save_event(sample_event)
    embaralhado = list(ALL_ROWS[10:]) + list(ALL_ROWS[:10])
    session = _UnsortedTraceSession(rows=embaralhado)
    collector, _ = _collector(sample_event, monkeypatch, session)
    monkeypatch.setattr(type(collector), "_VIP_PAGE_SIZE", 5)
    monkeypatch.setattr(type(collector), "_VIP_MAX_PAGES_PER_CYCLE", 2)

    colhidos = []
    for _ in range(5):
        result = collector.collect_vips()
        colhidos.extend(result.measurements)
        db.insert_vip_batch(result.measurements)
        db.save_collection_checkpoints(
            sample_event["id"], result.cursors, collector="vip", oss="SP")
        if result.state != "partial":
            break

    assert {m["serial_no"] for m in colhidos} == {r["serialNo"] for r in ALL_ROWS}


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


def test_contrato_invalido_da_paginacao_fica_visivel_como_parcial(tmp_db, sample_event, monkeypatch):
    db.save_event(sample_event)
    session = _TraceSession(payload={"data": {"msgId": 1, "tableData": "inválido"}})
    collector, _ = _collector(sample_event, monkeypatch, session)

    result = collector.collect_vips()

    assert result.state == "partial"
    assert result.coverage["tasks_invalid"] == 1
    assert result.measurements == []


def test_task_recusada_no_pre_check_nao_vira_ciclo_saudavel(tmp_db, sample_event, monkeypatch, caplog):
    db.save_event(sample_event)
    session = _TraceSession()
    monkeypatch.setattr(session, "get", lambda url, params=None, **k: _Response(
        {"checkState": False} if url.endswith("/pre-check") else {"data": {"msgId": 1}}))
    collector, _ = _collector(sample_event, monkeypatch, session)

    result = collector.collect_vips()

    assert result.state == "partial"
    assert result.coverage["tasks_valid"] == 0
    assert any("task 2072" in record.message and "motivo=" in record.message
               for record in caplog.records)


def test_task_vazia_registra_diagnostico_por_task(tmp_db, sample_event, monkeypatch, caplog):
    db.save_event(sample_event)
    collector, _ = _collector(sample_event, monkeypatch, _TraceSession(rows=[]))

    with caplog.at_level("INFO"):
        result = collector.collect_vips()

    assert result.state == "empty"
    assert any("task 2072: recordCount=0 linhas_lidas=0 linhas_decodificadas=0" in record.message
               for record in caplog.records)


def test_task_sem_mensagens_nao_chama_endpoint_que_responde_500(
        tmp_db, sample_event, monkeypatch, caplog):
    db.save_event(sample_event)
    collector, session = _collector(
        sample_event, monkeypatch, _EmptyBootstrapTraceSession(rows=[]))

    with caplog.at_level("INFO"):
        result = collector.collect_vips()

    assert result.state == "empty"
    assert result.coverage["tasks_valid"] == 1
    assert not any(url.endswith("/fetch-field-values") for url, _ in session.calls)


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
    from core.scheduler import CollectionContext
    import threading
    context = CollectionContext(
        generation=1, event_id=sample_event["id"], oss="SP", collector=collector,
        event_config=sample_event, stop_event=threading.Event(),
    )
    scheduler._generation = 1
    scheduler._active_context = context
    scheduler._collector = collector
    scheduler._event_config = sample_event
    scheduler._recording = True

    scheduler._collect_vips(context, mode="incremental")

    salvos = db.get_collection_checkpoints(sample_event["id"], "vip", 2072, "SP")
    assert salvos["serial"] == "335034"
    assert salvos["row"] == str(len(ALL_ROWS))
    scheduler.stop()
