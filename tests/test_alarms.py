"""
Testes unitários para a coleta de alarmes (sem VPN).

Cobre: parse do catálogo, resolução de nomes → pares, montagem da condition,
achatamento, dedup por csn no banco, ordenação/timestamp de get_alarms,
MockCollector.collect_alarms e os métodos de alarme da Api.
Integração real (HttpCollector.collect_alarms) fica em test_http_vpn.py.
"""
import pytest

import core.database as db
from core.collector import (
    MockCollector,
    HttpCollector,
    _load_alarm_catalog,
    _resolve_alarm_pairs,
    _build_alarm_condition,
    _extract_alarm_model_id,
    _flatten_alarm,
    _DEFAULT_ALARM_NAMES,
)


# ── Catálogo / resolução / condition ──────────────────────────────────

class TestAlarmCatalog:
    def test_catalog_loads_defaults(self):
        catalog = _load_alarm_catalog()
        assert isinstance(catalog, dict)
        # Os dois tipos padrão devem existir no catálogo exportado.
        for name in _DEFAULT_ALARM_NAMES:
            assert name in catalog, f"{name!r} ausente no catálogo"
            assert catalog[name], f"{name!r} sem pares (alarmId, alarmGroupId)"

    def test_catalog_pairs_are_tuples(self):
        catalog = _load_alarm_catalog()
        pairs = catalog[_DEFAULT_ALARM_NAMES[0]]
        for aid, gid in pairs:
            assert aid and gid

    def test_resolve_defaults(self):
        catalog = _load_alarm_catalog()
        pairs, missing = _resolve_alarm_pairs(_DEFAULT_ALARM_NAMES, catalog)
        assert missing == []
        assert len(pairs) >= 1

    def test_resolve_lenient_on_missing(self):
        catalog = _load_alarm_catalog()
        pairs, missing = _resolve_alarm_pairs(
            [_DEFAULT_ALARM_NAMES[0], "Nome Inexistente XYZ"], catalog)
        assert "Nome Inexistente XYZ" in missing
        assert len(pairs) >= 1  # o nome válido ainda resolve

    def test_resolve_dedup(self):
        catalog = _load_alarm_catalog()
        name = _DEFAULT_ALARM_NAMES[0]
        pairs, _ = _resolve_alarm_pairs([name, name], catalog)
        assert len(pairs) == len(set(pairs))


class TestAlarmCondition:
    def test_build_condition_with_pairs(self):
        import json
        cond = json.loads(_build_alarm_condition([("6486", "8193")]))
        assert cond["alarmGroupId"]["operation"] == "in"
        assert cond["alarmGroupId"]["value"] == [{"alarmId": "6486", "alarmGroupId": "8193"}]
        # Chaves fixas que a tela "Current Alarms" sempre envia (paridade com o HAR).
        assert cond["alarmLevel"] == ["CRITICAL", "MAJOR", "MINOR", "WARNING"]
        assert cond["alarmStatus"] == [12, 10, 11, 13]

    def test_build_condition_empty_pairs(self):
        import json
        cond = json.loads(_build_alarm_condition([]))
        assert "alarmGroupId" not in cond  # sem restrição de grupo

    def test_extract_model_id_variants(self):
        assert _extract_alarm_model_id({"parameters": {"modelID": "m1"}}) == "m1"
        assert _extract_alarm_model_id({"modelId": "m2"}) == "m2"
        assert _extract_alarm_model_id({"parameters": {"result": {"modelID": "m3"}}}) == "m3"
        assert _extract_alarm_model_id({"parameters": {}}) is None


class TestFlattenAlarm:
    def test_flatten_maps_fields(self):
        raw = {
            "csn": 123, "alarmId": 6486, "alarmGroupId": 8193,
            "alarmName": "Cell Unavailable", "severity": 2,
            "meName": "SR-UWCTJ1", "address": "10.0.0.1", "subNet": "SP",
            "arriveUtc": "2026-06-30T12:00:00Z", "occurUtc": "2026-06-30T11:59:00Z",
            "additionalInformation": "info",
        }
        row = _flatten_alarm(raw, "evt-1", "2026-06-30T12:01:00Z")
        assert row["csn"] == 123
        assert row["event_id"] == "evt-1"
        assert row["alarm_name"] == "Cell Unavailable"
        assert row["severity"] == "Major"  # 2 → Major
        assert row["source"] == "SR-UWCTJ1"
        assert row["collected_at"] == "2026-06-30T12:01:00Z"


class TestAlarmTimezone:
    """arriveUtc/occurUtc vêm no fuso do cliente (UTC-3) apesar do nome 'Utc'.
    _flatten_alarms deve convertê-los para UTC real (soma +3h com offset -180)."""

    def test_flatten_alarms_converts_local_to_utc(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = HttpCollector(sample_event, "")  # offset default -180 (America/Sao_Paulo)
        raw = [{
            "csn": 1, "alarmId": "26529", "alarmGroupId": "8193",
            "alarmName": "RF Unit VSWR Threshold Crossed", "severity": 3,
            "meName": "SR-UWCTJ1",
            "arriveUtc": "2026-06-24 15:45:03", "occurUtc": "2026-06-24 15:45:00",
        }]
        rows = c._flatten_alarms(raw)
        assert rows[0]["arrive_time"] == "2026-06-24T18:45:03Z"  # 15:45 local → 18:45 UTC
        assert rows[0]["occur_time"] == "2026-06-24T18:45:00Z"


# ── MockCollector.collect_alarms ──────────────────────────────────────

class TestMockCollectAlarms:
    def test_returns_flattened_rows(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = MockCollector(sample_event)
        rows = c.collect_alarms().measurements
        assert len(rows) > 0
        first = rows[0]
        assert isinstance(first, dict)
        for key in ("csn", "event_id", "alarm_name", "severity", "collected_at"):
            assert key in first
        assert first["event_id"] == sample_event["id"]

    def test_unique_csn(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = MockCollector(sample_event)
        rows = c.collect_alarms().measurements
        csns = [r["csn"] for r in rows]
        assert len(csns) == len(set(csns))


# ── Banco: insert dedup + get_alarms ──────────────────────────────────

class TestAlarmDatabase:
    def _row(self, event_id, csn, arrive, name="Cell Unavailable", collected="2026-06-30T12:00:00Z"):
        return {
            "csn": csn, "event_id": event_id, "alarm_id": "6486",
            "alarm_group_id": "8193", "alarm_name": name, "severity": "Major",
            "source": "SR-X", "ip": "10.0.0.1", "location": "SP",
            "occur_time": arrive, "arrive_time": arrive, "additional_info": "",
            "collected_at": collected,
        }

    def test_insert_dedup_by_csn(self, event_in_db, sample_event):
        eid = sample_event["id"]
        row = self._row(eid, 555, "2026-06-30T12:00:00Z")
        db.insert_alarms_batch([row, row])  # csn repetido
        alarms = db.get_alarms(eid)
        assert len([a for a in alarms if a["csn"] == 555]) == 1

    def test_get_alarms_order_desc(self, event_in_db, sample_event):
        eid = sample_event["id"]
        db.insert_alarms_batch([
            self._row(eid, 1, "2026-06-30T10:00:00Z"),
            self._row(eid, 2, "2026-06-30T12:00:00Z"),
            self._row(eid, 3, "2026-06-30T11:00:00Z"),
        ])
        alarms = db.get_alarms(eid)
        arrive = [a["arrive_time"] for a in alarms]
        assert arrive == sorted(arrive, reverse=True)

    def test_get_alarms_timestamp_filter(self, event_in_db, sample_event):
        eid = sample_event["id"]
        db.insert_alarms_batch([
            self._row(eid, 1, "2026-06-30T10:00:00Z", collected="2026-06-30T10:00:00Z"),
            self._row(eid, 2, "2026-06-30T11:00:00Z", collected="2026-06-30T13:00:00Z"),
        ])
        alarms = db.get_alarms(eid, timestamp="2026-06-30T12:00:00Z")
        csns = {a["csn"] for a in alarms}
        assert csns == {1}  # csn 2 foi coletado depois do corte

    def test_clear_event_history_removes_alarms(self, event_in_db, sample_event):
        eid = sample_event["id"]
        db.insert_alarms_batch([self._row(eid, 9, "2026-06-30T10:00:00Z")])
        db.clear_event_history(eid)
        assert db.get_alarms(eid) == []


# ── Estado de limpeza: upsert, reconciliação e leitura ────────────────

def _raw_alarm(csn, **extra):
    """Linha crua do cmd 1103, no formato em que o iManager a devolve."""
    row = {
        "csn": csn, "alarmId": "6486", "alarmGroupId": "8193",
        "alarmName": "Cell Unavailable", "severity": 2, "meName": "SR-X",
        "arriveUtc": "2026-06-30 09:00:00", "occurUtc": "2026-06-30 09:00:00",
    }
    row.update(extra)
    return row


class TestAlarmClearState:
    def _row(self, event_id, csn, arrive="2026-06-30T12:00:00Z", name="Cell Unavailable",
             collected="2026-06-30T12:00:00Z", **extra):
        row = {
            "csn": csn, "event_id": event_id, "alarm_id": "6486",
            "alarm_group_id": "8193", "alarm_name": name, "severity": "Major",
            "source": "SR-X", "ip": "10.0.0.1", "location": "SP",
            "occur_time": arrive, "arrive_time": arrive, "additional_info": "",
            "collected_at": collected,
        }
        row.update(extra)
        return row

    @staticmethod
    def _stored(event_id, csn):
        conn = db.get_event_conn(event_id)
        return dict(conn.execute("SELECT * FROM alarms WHERE csn = ?", (csn,)).fetchone())

    def test_flatten_captura_cleared_e_clear_time(self):
        row = _flatten_alarm(
            _raw_alarm(1, cleared=1, clearUtc="2026-06-30 09:30:00", acked=True),
            "evt-1", "2026-06-30T12:00:00Z",
        )
        assert row["cleared"] == 1
        assert row["clear_time"] == "2026-06-30 09:30:00"
        assert row["acked"] == 1

    def test_flatten_alarme_ativo_nao_inventa_clear(self):
        row = _flatten_alarm(_raw_alarm(1), "evt-1", "2026-06-30T12:00:00Z")
        assert row["cleared"] == 0
        assert row["clear_time"] is None
        assert row["acked"] == 0

    def test_flatten_alarms_converte_clear_time_para_utc(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = HttpCollector(sample_event, "")  # offset default -180 (America/Sao_Paulo)
        rows = c._flatten_alarms([
            _raw_alarm(1, cleared=1, clearUtc="2026-06-24 15:45:03"),
        ])
        assert rows[0]["clear_time"] == "2026-06-24T18:45:03Z"

    def test_upsert_atualiza_alarme_ja_conhecido(self, event_in_db, sample_event):
        eid = sample_event["id"]
        db.insert_alarms_batch([self._row(eid, 77)])
        assert len(db.get_alarms(eid)) == 1

        db.insert_alarms_batch([self._row(
            eid, 77, cleared=1, clear_time="2026-06-30T13:00:00Z", acked=1,
        )])

        stored = self._stored(eid, 77)
        assert stored["cleared"] == 1
        assert stored["clear_time"] == "2026-06-30T13:00:00Z"
        assert stored["acked"] == 1
        assert db.get_alarms(eid) == []  # some do painel

    def test_upsert_preserva_o_primeiro_collected_at(self, event_in_db, sample_event):
        """A timeline usa collected_at como 'primeira vez que apareceu'. Se cada
        recoleta o empurrasse para frente, o alarme sumiria do próprio passado."""
        eid = sample_event["id"]
        db.insert_alarms_batch([self._row(eid, 78, collected="2026-06-30T10:00:00Z")])
        db.insert_alarms_batch([self._row(eid, 78, collected="2026-06-30T14:00:00Z")])
        assert self._stored(eid, 78)["collected_at"] == "2026-06-30T10:00:00Z"

    def test_reconcile_marca_ausentes_como_limpos(self, event_in_db, sample_event):
        eid = sample_event["id"]
        db.insert_alarms_batch([self._row(eid, 1), self._row(eid, 2)])

        marked = db.reconcile_alarms(eid, {1}, "2026-06-30T13:00:00Z")

        assert marked == 1
        assert self._stored(eid, 2)["cleared"] == 1
        assert self._stored(eid, 2)["clear_time"] == "2026-06-30T13:00:00Z"
        assert {a["csn"] for a in db.get_alarms(eid)} == {1}

    def test_reconcile_preserva_os_presentes(self, event_in_db, sample_event):
        eid = sample_event["id"]
        db.insert_alarms_batch([self._row(eid, 1), self._row(eid, 2)])

        assert db.reconcile_alarms(eid, {1, 2}, "2026-06-30T13:00:00Z") == 0

        assert self._stored(eid, 1)["cleared"] == 0
        assert self._stored(eid, 1)["clear_time"] is None
        assert len(db.get_alarms(eid)) == 2

    def test_reconcile_com_conjunto_vazio_limpa_tudo(self, event_in_db, sample_event):
        """Zerar o último alarme é o caso em que a reconciliação mais importa."""
        eid = sample_event["id"]
        db.insert_alarms_batch([self._row(eid, 1), self._row(eid, 2)])

        assert db.reconcile_alarms(eid, set(), "2026-06-30T13:00:00Z") == 2
        assert db.get_alarms(eid) == []

    def test_reconcile_com_lote_grande(self, event_in_db, sample_event):
        """5.000 csn ativos não podem esbarrar no limite de variáveis do SQLite."""
        eid = sample_event["id"]
        db.insert_alarms_batch([self._row(eid, csn) for csn in range(1, 5001)])
        db.insert_alarms_batch([self._row(eid, 99999)])

        marked = db.reconcile_alarms(eid, set(range(1, 5001)), "2026-06-30T13:00:00Z")

        assert marked == 1
        assert len(db.get_alarms(eid, limit=6000)) == 5000

    def test_reconcile_nao_remexe_no_ja_limpo(self, event_in_db, sample_event):
        eid = sample_event["id"]
        db.insert_alarms_batch([self._row(
            eid, 5, cleared=1, clear_time="2026-06-30T11:00:00Z",
        )])

        assert db.reconcile_alarms(eid, set(), "2026-06-30T13:00:00Z") == 0
        assert self._stored(eid, 5)["clear_time"] == "2026-06-30T11:00:00Z"

    def test_get_alarms_devolve_so_ativos(self, event_in_db, sample_event):
        eid = sample_event["id"]
        db.insert_alarms_batch([
            self._row(eid, 1),
            self._row(eid, 2, cleared=1, clear_time="2026-06-30T13:00:00Z"),
        ])
        assert {a["csn"] for a in db.get_alarms(eid)} == {1}

    def test_get_alarms_historico_mostra_o_que_estava_ativo(self, event_in_db, sample_event):
        eid = sample_event["id"]
        db.insert_alarms_batch([self._row(
            eid, 1, collected="2026-06-30T10:00:00Z",
            cleared=1, clear_time="2026-06-30T12:00:00Z",
        )])

        antes  = db.get_alarms(eid, timestamp="2026-06-30T11:00:00Z")
        depois = db.get_alarms(eid, timestamp="2026-06-30T13:00:00Z")

        assert {a["csn"] for a in antes} == {1}   # estava ativo naquele instante
        assert depois == []                       # já tinha sido limpo

    def test_get_alarms_historico_ignora_o_que_ainda_nao_existia(self, event_in_db, sample_event):
        eid = sample_event["id"]
        db.insert_alarms_batch([self._row(eid, 1, collected="2026-06-30T13:00:00Z")])
        assert db.get_alarms(eid, timestamp="2026-06-30T12:00:00Z") == []

    def test_delete_alarms_by_names_apaga_so_os_tipos_pedidos(self, event_in_db, sample_event):
        eid = sample_event["id"]
        db.insert_alarms_batch([
            self._row(eid, 1, name="Cell Unavailable"),
            self._row(eid, 2, name="RF Unit VSWR Threshold Crossed"),
        ])

        assert db.delete_alarms_by_names(eid, ["Cell Unavailable"]) == 1

        assert {a["csn"] for a in db.get_alarms(eid)} == {2}

    def test_delete_alarms_by_names_com_lista_vazia_nao_apaga_nada(self, event_in_db, sample_event):
        eid = sample_event["id"]
        db.insert_alarms_batch([self._row(eid, 1)])
        assert db.delete_alarms_by_names(eid, []) == 0
        assert len(db.get_alarms(eid)) == 1


# ── Zerar a tabela entre sessões ──────────────────────────────────────

class TestClearAlarms:
    def _row(self, event_id, csn, name="Cell Unavailable", **extra):
        row = {
            "csn": csn, "event_id": event_id, "alarm_id": "6486",
            "alarm_group_id": "8193", "alarm_name": name, "severity": "Major",
            "source": "SR-X", "ip": "10.0.0.1", "location": "SP",
            "occur_time": "2026-06-30T12:00:00Z", "arrive_time": "2026-06-30T12:00:00Z",
            "additional_info": "", "collected_at": "2026-06-30T12:00:00Z",
        }
        row.update(extra)
        return row

    @staticmethod
    def _total(event_id):
        conn = db.get_event_conn(event_id)
        return conn.execute("SELECT COUNT(*) FROM alarms").fetchone()[0]

    def test_clear_alarms_esvazia_so_o_evento_pedido(self, tmp_db, sample_event):
        a = {**sample_event, "id": "evento-a"}
        b = {**sample_event, "id": "evento-b"}
        db.save_event(a)
        db.save_event(b)
        db.insert_alarms_batch([self._row("evento-a", 1), self._row("evento-a", 2)])
        db.insert_alarms_batch([self._row("evento-b", 3)])

        removed = db.clear_alarms("evento-a")

        assert removed == 2
        assert self._total("evento-a") == 0
        assert self._total("evento-b") == 1

    def test_clear_alarms_apaga_tambem_os_ja_limpos(self, event_in_db, sample_event):
        """A tabela fica vazia de verdade — não só o que o painel mostrava."""
        eid = sample_event["id"]
        db.insert_alarms_batch([
            self._row(eid, 1),
            self._row(eid, 2, cleared=1, clear_time="2026-06-30T13:00:00Z"),
        ])

        assert db.clear_alarms(eid) == 2
        assert self._total(eid) == 0

    def test_clear_alarms_em_tabela_vazia_devolve_zero(self, event_in_db, sample_event):
        assert db.clear_alarms(sample_event["id"]) == 0

    def test_clear_all_alarms_percorre_os_eventos_locais(self, tmp_db, sample_event):
        """Inclui o evento antigo que ficou para trás, não só o ativo."""
        a = {**sample_event, "id": "evento-a"}
        b = {**sample_event, "id": "evento-b"}
        db.save_event(a)
        db.save_event(b)
        db.update_event_status("evento-a", "ACTIVE")
        db.insert_alarms_batch([self._row("evento-a", 1), self._row("evento-a", 2)])
        db.insert_alarms_batch([self._row("evento-b", 3)])

        total = db.clear_all_alarms()

        assert total == 3
        assert self._total("evento-a") == 0
        assert self._total("evento-b") == 0

    def test_clear_all_alarms_ignora_evento_sem_banco(self, tmp_db, sample_event):
        """Linha em `events` sem arquivo de banco (apagado da pasta data/) não
        ganha um banco novo só para ter zero linhas removidas."""
        conn = db.get_conn()
        conn.execute(
            "INSERT INTO events (id, name, status) VALUES ('evento-fantasma', 'Fantasma', 'ENDED')"
        )
        conn.commit()
        path = db.get_event_db_path("evento-fantasma")
        assert not path.exists()

        assert db.clear_all_alarms() == 0
        assert not path.exists()

    def test_clear_alarms_preserva_kpis_e_alertas(self, event_in_db, sample_event):
        """Não é um clear_event_history disfarçado: só a tabela de alarmes sai."""
        eid = sample_event["id"]
        db.insert_alarms_batch([self._row(eid, 1)])
        db.insert_kpi_batch([{
            "site_id": "SR-SPPNB2", "cell_id": "SR-SPPNB2_1", "event_id": eid,
            "timestamp": "2026-06-30T12:00:00Z", "metric": "rsrp", "value": -95.0,
        }])
        db.insert_alert({
            "event_id": eid, "level": "WARNING", "severity": "warning",
            "site_id": "SR-SPPNB2", "cell_id": "SR-SPPNB2_1",
            "message": "RSRP baixo", "timestamp": "2026-06-30T12:00:00Z",
        })

        db.clear_alarms(eid)

        conn = db.get_event_conn(eid)
        assert conn.execute("SELECT COUNT(*) FROM alarms").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM kpi_measurements").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1


class TestAlarmSchemaMigration:
    def test_migracao_aditiva_em_banco_legado(self, tmp_db, sample_event):
        """Banco de evento anterior às colunas de limpeza abre, ganha as três e
        mantém as linhas que já tinha."""
        import sqlite3
        eid = "evento-legado"
        path = db.get_event_db_path(eid)
        path.parent.mkdir(parents=True, exist_ok=True)
        legacy = sqlite3.connect(str(path))
        legacy.executescript("""
            CREATE TABLE alarms (
                csn             INTEGER PRIMARY KEY,
                event_id        TEXT NOT NULL,
                alarm_id        TEXT,
                alarm_group_id  TEXT,
                alarm_name      TEXT,
                severity        TEXT,
                source          TEXT,
                ip              TEXT,
                location        TEXT,
                occur_time      TEXT,
                arrive_time     TEXT,
                additional_info TEXT,
                collected_at    TEXT NOT NULL
            );
        """)
        legacy.execute(
            "INSERT INTO alarms (csn, event_id, alarm_name, arrive_time, collected_at) "
            "VALUES (1, ?, 'Cell Unavailable', '2026-06-30T10:00:00Z', '2026-06-30T10:00:00Z')",
            (eid,),
        )
        legacy.commit()
        legacy.close()

        conn = db.get_event_conn(eid)  # dispara init_event_db → migração

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(alarms)")}
        assert {"cleared", "clear_time", "acked"} <= columns
        row = conn.execute("SELECT * FROM alarms WHERE csn = 1").fetchone()
        assert row["cleared"] == 0          # linha antiga entra como ativa
        assert row["clear_time"] is None
        assert db.get_alarms(eid)[0]["csn"] == 1

    def test_migracao_e_idempotente(self, tmp_db, sample_event):
        eid = sample_event["id"]
        db.save_event(sample_event)
        conn = db.get_event_conn(eid)
        db.init_event_db(conn)  # segunda passagem não pode falhar
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(alarms)")}
        assert {"cleared", "clear_time", "acked"} <= columns


class TestAlarmSeverityCatalog:
    def test_severidade_5_e_6_seguem_o_catalogo_do_fm(self):
        """alarmSeverityInt do FM: {1:CRITICAL, 2:MAJOR, 3:MINOR, 4:WARNING,
        5:EVENT, 6:ALARM_NAME}. Não existe severidade 'Cleared' — limpeza é o
        campo `cleared`, e mapear 6 assim dava a impressão falsa de já estar tratada."""
        from core.collector import _ALARM_SEVERITY
        assert _ALARM_SEVERITY[5] == "Event"
        assert _ALARM_SEVERITY[6] == "Alarm Name"
        assert "Cleared" not in _ALARM_SEVERITY.values()
        assert "Indeterminate" not in _ALARM_SEVERITY.values()
        # Os quatro níveis que o coletor realmente pede seguem intactos.
        assert [_ALARM_SEVERITY[i] for i in (1, 2, 3, 4)] == [
            "Critical", "Major", "Minor", "Warning"]


class TestCollectAlarmPagesIntegrity:
    """`complete` distingue o lote inteiro do que parou numa página vazia."""

    @staticmethod
    def _collector(sample_event, monkeypatch, pages):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = HttpCollector(sample_event, "")
        monkeypatch.setattr(c, "_fetch_alarm_page", lambda mid, frm, to: pages.pop(0))
        return c

    def test_lote_inteiro_e_completo(self, sample_event, monkeypatch):
        c = self._collector(sample_event, monkeypatch,
                            [{"total": 2, "data": [_raw_alarm(1), _raw_alarm(2)]}])
        rows, complete = c._collect_alarm_pages("m1")
        assert len(rows) == 2 and complete is True

    def test_pagina_vazia_no_meio_marca_parcial(self, sample_event, monkeypatch):
        c = self._collector(sample_event, monkeypatch, [
            {"total": 300, "data": [_raw_alarm(i) for i in range(148)]},
            {"total": 300, "data": []},
        ])
        rows, complete = c._collect_alarm_pages("m1")
        assert len(rows) == 148 and complete is False

    def test_rede_sem_alarmes_e_um_lote_completo(self, sample_event, monkeypatch):
        c = self._collector(sample_event, monkeypatch, [{"total": 0, "data": []}])
        rows, complete = c._collect_alarm_pages("m1")
        assert rows == [] and complete is True

    def test_collect_alarms_propaga_a_integridade(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = HttpCollector(sample_event, "")
        monkeypatch.setattr(c, "_create_alarm_model", lambda cond: "m1")
        monkeypatch.setattr(c, "_collect_alarm_pages",
                            lambda mid: ([_raw_alarm(1)], False))
        assert c.collect_alarms().coverage["complete"] is False

    def test_collect_alarms_vazio_tambem_propaga(self, sample_event, monkeypatch):
        monkeypatch.setattr(db, "get_event_vips", lambda *a, **k: [])
        c = HttpCollector(sample_event, "")
        monkeypatch.setattr(c, "_create_alarm_model", lambda cond: "m1")
        monkeypatch.setattr(c, "_collect_alarm_pages", lambda mid: ([], True))
        result = c.collect_alarms()
        assert result.state == "empty"
        assert result.coverage["complete"] is True


# ── Api ───────────────────────────────────────────────────────────────

@pytest.fixture
def api(tmp_db, monkeypatch):
    from core import scheduler as sched_module
    from api.api import Api
    monkeypatch.setattr(sched_module.scheduler, "_recording", False)
    return Api()


class TestApiAlarms:
    def test_get_alarm_catalog(self, api):
        res = api.get_alarm_catalog()
        assert res["ok"] is True
        assert isinstance(res["names"], list)
        assert _DEFAULT_ALARM_NAMES[0] in res["names"]

    def test_get_alarm_filter_default(self, api, sample_event):
        db.save_event(sample_event)
        res = api.get_alarm_filter(sample_event["id"])
        assert res["ok"] is True
        assert res["names"]  # default aplicado quando não definido

    def test_set_and_get_alarm_filter(self, api, sample_event):
        db.save_event(sample_event)
        names = ["Cell Unavailable"]
        res = api.set_alarm_filter(sample_event["id"], names)
        assert res["ok"] is True
        got = api.get_alarm_filter(sample_event["id"])
        assert got["names"] == names

    def test_set_alarm_filter_rejects_non_list(self, api, sample_event):
        db.save_event(sample_event)
        res = api.set_alarm_filter(sample_event["id"], "nao-e-lista")
        assert res["ok"] is False

    def test_get_alarms_returns_list(self, api, sample_event):
        db.save_event(sample_event)
        assert isinstance(api.get_alarms(sample_event["id"]), list)

    def test_get_alarms_marks_in_event(self, api, sample_event):
        db.save_event(sample_event)
        eid = sample_event["id"]
        # sample_event tem o site SR-SPPNB2; source igual ao meName deve casar.
        db.insert_alarms_batch([
            {"csn": 1, "event_id": eid, "alarm_id": "1", "alarm_group_id": "1",
             "alarm_name": "Cell Unavailable", "severity": "Major", "source": "SR-SPPNB2",
             "ip": "", "location": "", "occur_time": "2026-06-30T10:00:00Z",
             "arrive_time": "2026-06-30T10:00:00Z", "additional_info": "",
             "collected_at": "2026-06-30T10:00:00Z"},
            {"csn": 2, "event_id": eid, "alarm_id": "1", "alarm_group_id": "1",
             "alarm_name": "Cell Unavailable", "severity": "Major", "source": "SR-OUTRO99",
             "ip": "", "location": "", "occur_time": "2026-06-30T10:00:00Z",
             "arrive_time": "2026-06-30T10:00:00Z", "additional_info": "",
             "collected_at": "2026-06-30T10:00:00Z"},
        ])
        by_csn = {a["csn"]: a for a in api.get_alarms(eid)}
        assert by_csn[1]["in_event"] is True
        assert by_csn[1]["serving_site"] == "SR-SPPNB2"
        assert by_csn[2]["in_event"] is False

    def test_resolve_site_for_source(self, api, sample_event):
        sites = sample_event["sites"]
        assert api._resolve_site_for_source(sites, "SR-SPPNB2")[0] == "SR-SPPNB2"
        assert api._resolve_site_for_source(sites, "SR-SPPNB2-3")[0] == "SR-SPPNB2"  # prefixo
        assert api._resolve_site_for_source(sites, "SR-NOPE")[0] is None
        assert api._resolve_site_for_source(sites, None)[0] is None

    def test_refresh_alarms_when_not_recording(self, api, sample_event):
        db.save_event(sample_event)
        res = api.refresh_alarms(sample_event["id"])
        assert res["ok"] is False  # coleta inativa

    def test_set_alarm_filter_remove_tipos_desmarcados(self, api, sample_event):
        """Desmarcar um tipo tira os alarmes daquele tipo da lista. Apagar, e não
        marcar como limpo: eles não foram limpos na rede, foram desselecionados."""
        eid = sample_event["id"]
        sample_event.setdefault("oss", {})["alarm_filter"] = list(_DEFAULT_ALARM_NAMES)
        db.save_event(sample_event)
        db.insert_alarms_batch([
            {"csn": 1, "event_id": eid, "alarm_id": "1", "alarm_group_id": "1",
             "alarm_name": _DEFAULT_ALARM_NAMES[0], "severity": "Major", "source": "SR-X",
             "ip": "", "location": "", "occur_time": "2026-06-30T10:00:00Z",
             "arrive_time": "2026-06-30T10:00:00Z", "additional_info": "",
             "collected_at": "2026-06-30T10:00:00Z"},
            {"csn": 2, "event_id": eid, "alarm_id": "2", "alarm_group_id": "1",
             "alarm_name": _DEFAULT_ALARM_NAMES[1], "severity": "Major", "source": "SR-X",
             "ip": "", "location": "", "occur_time": "2026-06-30T10:00:00Z",
             "arrive_time": "2026-06-30T10:00:00Z", "additional_info": "",
             "collected_at": "2026-06-30T10:00:00Z"},
        ])

        res = api.set_alarm_filter(eid, [_DEFAULT_ALARM_NAMES[1]])

        assert res["ok"] is True
        remaining = db.get_alarms(eid)
        assert {a["csn"] for a in remaining} == {2}
        # Apagado de verdade — não sobra linha marcada como limpa.
        conn = db.get_event_conn(eid)
        assert conn.execute("SELECT COUNT(*) FROM alarms").fetchone()[0] == 1

    def test_set_alarm_filter_mantendo_tudo_nao_apaga_nada(self, api, sample_event):
        eid = sample_event["id"]
        sample_event.setdefault("oss", {})["alarm_filter"] = list(_DEFAULT_ALARM_NAMES)
        db.save_event(sample_event)
        db.insert_alarms_batch([
            {"csn": 1, "event_id": eid, "alarm_id": "1", "alarm_group_id": "1",
             "alarm_name": _DEFAULT_ALARM_NAMES[0], "severity": "Major", "source": "SR-X",
             "ip": "", "location": "", "occur_time": "2026-06-30T10:00:00Z",
             "arrive_time": "2026-06-30T10:00:00Z", "additional_info": "",
             "collected_at": "2026-06-30T10:00:00Z"},
        ])

        api.set_alarm_filter(eid, list(_DEFAULT_ALARM_NAMES))

        assert len(db.get_alarms(eid)) == 1
