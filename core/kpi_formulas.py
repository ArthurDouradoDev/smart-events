"""Catálogo e cálculos seguros dos KPIs de Performance Monitor.

As fórmulas ficam em código deliberadamente: os nomes dos contadores vindos do
OSS são dados externos e nunca devem ser avaliados como uma expressão Python.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable

logger = logging.getLogger(__name__)


class InvalidKpi(ValueError):
    """Um KPI não pode ser calculado de forma confiável."""


class NotApplicable(InvalidKpi):
    """O KPI não existe neste minuto — não é defeito.

    Contador fora da task ou denominador zero (nenhuma tentativa no período)
    são ausências legítimas.  Subclasse de ``InvalidKpi`` para que todo
    ``except InvalidKpi`` já escrito continue capturando os dois casos; quem
    precisa separar "sem dado" de "dado quebrado" captura esta primeiro.
    """


# Medido, não documentado: o OSS devolve N.ThpTime.* com unit vazio. Todos os
# valores são múltiplos de 500 (slot de 0,5 ms @30 kHz) e a hipótese de ms/s
# viola o piso volume/período em 79 de 83 amostras. Ver plano 2026-08-21-001.
# Se a documentação Huawei disser outra coisa, este é o único ponto a mudar.
THP_TIME_TO_SECONDS = 1e-6


@dataclass(frozen=True)
class KpiDefinition:
    id: str
    technology: str
    name: str
    unit: str
    required: tuple[str, ...]
    site_aggregation: str
    calculator: Callable[[dict[str, float], float | None], float]
    production_ready: bool = True
    monitoring_available: bool = True


def _need(c: dict[str, float], *names: str) -> tuple[float, ...]:
    missing = [name for name in names if name not in c]
    if missing:
        raise NotApplicable("contador ausente: " + ", ".join(missing))
    return tuple(c[name] for name in names)


def _ratio(n: float, d: float, factor: float = 1.0) -> float:
    if d == 0:
        raise NotApplicable("denominador zero")
    return factor * n / d


def _l_access(c, _period):
    a, b, d, e, g, h = _need(c, "L.RRC.ConnReq.Succ", "L.RRC.ConnReq.Att", "L.E-RAB.SuccEst", "L.E-RAB.AttEst", "L.S1Sig.ConnEst.Succ", "L.S1Sig.ConnEst.Att")
    return 100 * _ratio(a, b) * _ratio(d, e) * _ratio(g, h)


def _l_availability(c, period):
    sys, manual = _need(c, "L.Cell.Unavail.Dur.Sys", "L.Cell.Unavail.Dur.Manual")
    if not period or period <= 0:
        raise InvalidKpi("period ausente ou inválido")
    return 100 * (1 - _ratio(sys + manual, 60 * period))


def _l_drop(c, _period):
    ab, mme, normal, rel_mme = _need(c, "L.E-RAB.AbnormRel", "L.E-RAB.AbnormRel.MME", "L.E-RAB.NormRel", "L.E-RAB.Rel.MME")
    return _ratio(ab + mme, ab + mme + normal + rel_mme, 100)


def _l_prb_dl(c, _period):
    used, avail = _need(c, "L.ChMeas.PRB.DL.Used.Avg", "L.ChMeas.PRB.DL.Avail")
    return _ratio(used, avail, 100)


def _l_prb_ul(c, _period):
    used, avail = _need(c, "L.ChMeas.PRB.UL.Used.Avg", "L.ChMeas.PRB.UL.Avail")
    return _ratio(used, avail, 100)


def _l_thp_dl(c, _period):
    bits, last, duration = _need(c, "L.Thrp.bits.DL", "L.Thrp.bits.DL.LastTTI", "L.Thrp.Time.DL.RmvLastTTI")
    return _ratio(bits - last, duration, 1 / 1000)


def _l_thp_ul(c, _period):
    bits, last, duration = _need(c, "L.Thrp.bits.UL", "L.Thrp.bits.UE.UL.LastTTI", "L.Thrp.Time.UE.UL.RmvLastTTI")
    return _ratio(bits - last, duration, 1 / 1000)


def _single(name: str):
    def calculate(c, _period):
        return _need(c, name)[0]
    return calculate


def _ratio_formula(numerator: str, denominator: str, factor: float = 100.0):
    def calculate(c, _period):
        n, d = _need(c, numerator, denominator)
        return _ratio(n, d, factor)
    return calculate


def _n_access(c, _period):
    setup_s, resume_s, setup_a, resume_a, ng_s, ng_a, qos_s, eps, emc, conflict, resume_qos_s, qos_a, syntax, resume_qos_a = _need(
        c, "N.RRC.SetupReq.Succ", "N.RRC.ResumeReq.Succ", "N.RRC.SetupReq.Att", "N.RRC.ResumeReq.Att",
        "N.NGSig.ConnEst.Succ", "N.NGSig.ConnEst.Att", "N.QosFlow.Est.Succ", "N.QosFlow.Est.Att.EPSFB",
        "N.QosFlow.Est.Att.EmcFB", "N.QosFlow.FailEst.Conflict", "N.QosFlow.Resume.Succ", "N.QosFlow.Est.Att",
        "N.QosFlow.FailEst.AMF.SyntaxError", "N.QosFlow.Resume.Att")
    return 100 * _ratio(setup_s + resume_s, setup_a + resume_a) * _ratio(ng_s, ng_a) * _ratio(qos_s + eps + emc + conflict + resume_qos_s, qos_a - syntax + resume_qos_a)


def _n_drop(c, _period):
    abnormal, normal, inactive, suspended = _need(c, "N.QosFlow.AbnormRel", "N.QosFlow.NormRel", "N.QosFlow.RrcInactiveToIdle.Rel", "N.QosFlow.RrcConnToInactive.Suspend")
    return _ratio(abnormal, abnormal + normal - inactive + suspended, 100)


def _n_availability(c, period):
    available = _need(c, "N.Cell.Avail.Dur")[0]
    if not period or period <= 0:
        raise InvalidKpi("period ausente ou inválido")
    return _ratio(available, period * 60, 100)


def _n_thp_dl(c, _period):
    volume, last_slot, duration = _need(c, "N.ThpVol.DL", "N.ThpVol.DL.LastSlot", "N.ThpTime.DL.RmvLastSlot")
    # kbit / (N.ThpTime × 1e-6 s) = kbit/s; ÷1000 = Mbit/s.
    return _ratio(volume - last_slot, duration * THP_TIME_TO_SECONDS, 1 / 1000)


def _n_thp_ul(c, _period):
    volume, small_packet, duration = _need(c, "N.ThpVol.UL", "N.ThpVol.UE.UL.SmallPkt", "N.ThpTime.UE.UL.RmvSmallPkt")
    return _ratio(volume - small_packet, duration * THP_TIME_TO_SECONDS, 1 / 1000)


def _volume_sa(total: float, nsa: float, label: str) -> float:
    """SA = total − NSA, nunca negativo.

    O próprio export CSV do OSS traz amostras com ``N.NSA.ThpVol > N.ThpVol``;
    a inconsistência é da origem e não pode virar volume negativo no gráfico.
    """
    if nsa > total:
        logger.debug("[kpi] %s: NSA (%s) maior que o total (%s); volume SA fixado em 0.",
                     label, nsa, total)
        return 0.0
    return total - nsa


def _n_volume_dl_sa(c, _period):
    total, nsa = _need(c, "N.ThpVol.DL", "N.NSA.ThpVol.DL")
    return _volume_sa(total, nsa, "traffic_volume_dl_sa")


def _n_volume_ul_sa(c, _period):
    total, nsa = _need(c, "N.ThpVol.UL", "N.NSA.ThpVol.UL")
    return _volume_sa(total, nsa, "traffic_volume_ul_sa")


CATALOG: tuple[KpiDefinition, ...] = (
    KpiDefinition("accessibility", "4G", "Acessibilidade de Dados", "%", ("L.RRC.ConnReq.Succ", "L.RRC.ConnReq.Att", "L.E-RAB.SuccEst", "L.E-RAB.AttEst", "L.S1Sig.ConnEst.Succ", "L.S1Sig.ConnEst.Att"), "recalculate", _l_access),
    KpiDefinition("availability", "4G", "Availability", "%", ("L.Cell.Unavail.Dur.Sys", "L.Cell.Unavail.Dur.Manual"), "recalculate", _l_availability),
    KpiDefinition("drop_rate", "4G", "Drop Dados", "%", ("L.E-RAB.AbnormRel", "L.E-RAB.AbnormRel.MME", "L.E-RAB.NormRel", "L.E-RAB.Rel.MME"), "recalculate", _l_drop),
    KpiDefinition("utilization_dl", "4G", "DL PRB Utility", "%", ("L.ChMeas.PRB.DL.Used.Avg", "L.ChMeas.PRB.DL.Avail"), "recalculate", _l_prb_dl),
    KpiDefinition("utilization_ul", "4G", "UL PRB Utility", "%", ("L.ChMeas.PRB.UL.Used.Avg", "L.ChMeas.PRB.UL.Avail"), "recalculate", _l_prb_ul),
    KpiDefinition("interference_ul", "4G", "Interferência", "dBm", ("L.UL.Interference.Avg",), "mean", _single("L.UL.Interference.Avg")),
    KpiDefinition("throughput_dl", "4G", "Throughput DL", "Mbit/s", ("L.Thrp.bits.DL", "L.Thrp.bits.DL.LastTTI", "L.Thrp.Time.DL.RmvLastTTI"), "recalculate", _l_thp_dl),
    KpiDefinition("throughput_ul", "4G", "Throughput UL", "Mbit/s", ("L.Thrp.bits.UL", "L.Thrp.bits.UE.UL.LastTTI", "L.Thrp.Time.UE.UL.RmvLastTTI"), "recalculate", _l_thp_ul),
    KpiDefinition("user_count", "4G", "UE médio", "usuários", ("L.Traffic.User.Avg",), "sum", _single("L.Traffic.User.Avg")),
    # A4 — o OSS aceita no máximo 25 contadores por task PM e os quatro contadores de
    # RTT ficaram de fora das tasks do evento. Sem eles a fórmula nunca calcula: mantê-las
    # no seletor só produzia coluna vazia e 56 diagnósticos por ciclo. Reativar exige antes
    # criar uma task PM dedicada com esses contadores.
    KpiDefinition("ran_rtt", "4G", "Wireless RTT", "ms", ("L.PDCP.TCP.time.RANRtt.ConnSetup", "L.PDCP.TCP.RANRtt.ConnSetup"), "mean", _ratio_formula("L.PDCP.TCP.time.RANRtt.ConnSetup", "L.PDCP.TCP.RANRtt.ConnSetup", 1), monitoring_available=False),
    KpiDefinition("terrestrial_rtt", "4G", "Terrestrial RTT", "ms", ("L.PDCP.TCP.Time.TerrestrialRtt.ConnSetup", "L.PDCP.TCP.TerrestrialRtt.ConnSetup"), "mean", _ratio_formula("L.PDCP.TCP.Time.TerrestrialRtt.ConnSetup", "L.PDCP.TCP.TerrestrialRtt.ConnSetup", 1), monitoring_available=False),
    KpiDefinition("traffic_volume_dl", "4G", "Volume de Tráfego DL (legado)", "contador OSS", ("L.Thrp.bits.DL",), "sum", _single("L.Thrp.bits.DL")),
    KpiDefinition("traffic_volume_ul", "4G", "Volume de Tráfego UL (legado)", "contador OSS", ("L.Thrp.bits.UL",), "sum", _single("L.Thrp.bits.UL")),
    KpiDefinition("accessibility", "5G_NRCELL", "Acessibilidade considerando RRC Inactive", "%", ("N.RRC.SetupReq.Succ", "N.RRC.ResumeReq.Succ", "N.RRC.SetupReq.Att", "N.RRC.ResumeReq.Att", "N.NGSig.ConnEst.Succ", "N.NGSig.ConnEst.Att", "N.QosFlow.Est.Succ", "N.QosFlow.Est.Att.EPSFB", "N.QosFlow.Est.Att.EmcFB", "N.QosFlow.FailEst.Conflict", "N.QosFlow.Resume.Succ", "N.QosFlow.Est.Att", "N.QosFlow.FailEst.AMF.SyntaxError", "N.QosFlow.Resume.Att"), "recalculate", _n_access),
    KpiDefinition("drop_rate", "5G_NRCELL", "Drop considerando RRC Inactive", "%", ("N.QosFlow.AbnormRel", "N.QosFlow.NormRel", "N.QosFlow.RrcInactiveToIdle.Rel", "N.QosFlow.RrcConnToInactive.Suspend"), "recalculate", _n_drop),
    KpiDefinition("utilization_dl", "5G_NRDUCELL", "DL PRB Utility", "%", ("N.PRB.DL.Used.Avg", "N.PRB.DL.Avail.Avg"), "recalculate", _ratio_formula("N.PRB.DL.Used.Avg", "N.PRB.DL.Avail.Avg")),
    KpiDefinition("utilization_ul", "5G_NRDUCELL", "UL PRB Utility", "%", ("N.PRB.UL.Used.Avg", "N.PRB.UL.Avail.Avg"), "recalculate", _ratio_formula("N.PRB.UL.Used.Avg", "N.PRB.UL.Avail.Avg")),
    KpiDefinition("throughput_dl", "5G_NRDUCELL", "Throughput DL", "Mbit/s", ("N.ThpVol.DL", "N.ThpVol.DL.LastSlot", "N.ThpTime.DL.RmvLastSlot"), "recalculate", _n_thp_dl),
    KpiDefinition("throughput_ul", "5G_NRDUCELL", "Throughput UL", "Mbit/s", ("N.ThpVol.UL", "N.ThpVol.UE.UL.SmallPkt", "N.ThpTime.UE.UL.RmvSmallPkt"), "recalculate", _n_thp_ul),
    KpiDefinition("traffic_volume_dl_sa", "5G_NRDUCELL", "Downlink Traffic Volume 5G SA", "kbit", ("N.ThpVol.DL", "N.NSA.ThpVol.DL"), "sum", _n_volume_dl_sa),
    KpiDefinition("traffic_volume_dl_nsa", "5G_NRDUCELL", "Downlink Traffic Volume 5G NSA", "kbit", ("N.NSA.ThpVol.DL",), "sum", _single("N.NSA.ThpVol.DL")),
    KpiDefinition("traffic_volume_ul_sa", "5G_NRDUCELL", "Uplink Traffic Volume 5G SA", "kbit", ("N.ThpVol.UL", "N.NSA.ThpVol.UL"), "sum", _n_volume_ul_sa),
    KpiDefinition("traffic_volume_ul_nsa", "5G_NRDUCELL", "Uplink Traffic Volume 5G NSA", "kbit", ("N.NSA.ThpVol.UL",), "sum", _single("N.NSA.ThpVol.UL")),
    KpiDefinition("user_count", "5G_NRCELL", "User Médio", "usuários", ("N.User.RRCConn.Avg",), "sum", _single("N.User.RRCConn.Avg")),
    KpiDefinition("availability", "5G_NRCELL", "Availability", "%", ("N.Cell.Avail.Dur",), "recalculate", _n_availability),
    KpiDefinition("interference_ul", "5G_NRDUCELL", "UL Interference Médio", "dBm", ("N.UL.NI.Avg",), "mean", _single("N.UL.NI.Avg")),
)


def definitions_for(technology: str) -> tuple[KpiDefinition, ...]:
    return tuple(item for item in CATALOG if item.technology == technology)


def definition(metric: str, technology: str | None = None) -> KpiDefinition | None:
    return next((item for item in CATALOG if item.id == metric and (technology is None or item.technology == technology)), None)


def catalog_for_api() -> list[dict]:
    seen = set()
    rows = []
    for item in CATALOG:
        if not item.monitoring_available:
            continue
        key = (item.id, item.technology)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"id": item.id, "technology": item.technology, "name": item.name,
                     "unit": item.unit, "site_aggregation": item.site_aggregation,
                     "production_ready": item.production_ready})
    return rows


# B2 — threshold com unidade. Um threshold em `config["thresholds"]` pode estar
# no formato legado (número cru) ou no novo `{"value": ..., "unit": ...}`. Os
# dois helpers abaixo são o único ponto que decide qual formato está guardado,
# para que nenhum consumidor precise repetir esse `isinstance`.
def threshold_value(raw, default: float | None = None) -> float | None:
    """Extrai o número de um threshold, aceitando o formato legado e o novo (B2)."""
    if isinstance(raw, dict):
        return raw.get("value", default)
    return default if raw is None else raw


def threshold_object(raw, unit_default: str = "") -> dict | None:
    """Normaliza um threshold para ``{"value", "unit"}`` (B2).

    Devolve ``None`` quando não há valor configurado, nunca inventa um número.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        value = raw.get("value")
        return None if value is None else {"value": value, "unit": raw.get("unit", unit_default)}
    return {"value": raw, "unit": unit_default}


# A2 — trava aritmética da unidade de tempo. Por métrica: volume do período, a
# parcela que a fórmula desconta e o fator para Mbit. O piso é
# ``(volume − descontado) / período`` porque o tempo escalonado nunca é maior que o
# período de granularidade — é o mesmo numerador da fórmula, e por isso não acusa
# célula cuja transmissão se concentrou no último TTI/slot.
_THROUGHPUT_VOLUME: dict[tuple[str, str], tuple[str, str, float]] = {
    ("throughput_dl", "4G"): ("L.Thrp.bits.DL", "L.Thrp.bits.DL.LastTTI", 1e-6),
    ("throughput_ul", "4G"): ("L.Thrp.bits.UL", "L.Thrp.bits.UE.UL.LastTTI", 1e-6),
    ("throughput_dl", "5G_NRDUCELL"): ("N.ThpVol.DL", "N.ThpVol.DL.LastSlot", 1e-3),
    ("throughput_ul", "5G_NRDUCELL"): ("N.ThpVol.UL", "N.ThpVol.UE.UL.SmallPkt", 1e-3),
}


def check_throughput_floor(definition: KpiDefinition, counters: dict[str, float],
                           period: float | None, value: float) -> str | None:
    """Confere se um throughput calculado é compatível com o volume do período.

    Devolve ``None`` quando o valor é plausível e uma mensagem quando não é.  A
    linha **não** é descartada: durante evento ao vivo, dado suspeito sinalizado
    é melhor que dado ausente.  A violação aponta para a unidade de tempo de
    ``N.ThpTime.*``/``L.Thrp.Time.*`` (ver ``THP_TIME_TO_SECONDS``).
    """
    entry = _THROUGHPUT_VOLUME.get((definition.id, definition.technology))
    if entry is None or not period or period <= 0:
        return None
    counter, excluded, to_mbit = entry
    volume = counters.get(counter)
    if volume is None or excluded not in counters:
        return None
    volume -= counters[excluded]
    if volume <= 0:
        return None
    seconds = period * 60.0
    floor = volume * to_mbit / seconds
    if value >= floor * (1 - 1e-9):
        return None
    return (f"{definition.id}={value:.6g} Mbit/s abaixo do piso {floor:.6g} Mbit/s "
            f"({counter} líquido = {volume:.6g} em {seconds:.0f}s): "
            f"unidade de tempo suspeita")


def _sample_4g_access(c: dict[str, float]) -> float:
    return min(_need(c, "L.RRC.ConnReq.Att", "L.E-RAB.AttEst", "L.S1Sig.ConnEst.Att"))


def _sample_5g_access(c: dict[str, float]) -> float:
    setup_a, resume_a, ng_a, qos_a, syntax, resume_qos_a = _need(
        c, "N.RRC.SetupReq.Att", "N.RRC.ResumeReq.Att", "N.NGSig.ConnEst.Att",
        "N.QosFlow.Est.Att", "N.QosFlow.FailEst.AMF.SyntaxError", "N.QosFlow.Resume.Att")
    return min(setup_a + resume_a, ng_a, qos_a - syntax + resume_qos_a)


def _sample_4g_drop(c: dict[str, float]) -> float:
    ab, mme, normal, rel_mme = _need(
        c, "L.E-RAB.AbnormRel", "L.E-RAB.AbnormRel.MME", "L.E-RAB.NormRel", "L.E-RAB.Rel.MME")
    return ab + mme + normal + rel_mme


def _sample_5g_drop(c: dict[str, float]) -> float:
    abnormal, normal, inactive, suspended = _need(
        c, "N.QosFlow.AbnormRel", "N.QosFlow.NormRel", "N.QosFlow.RrcInactiveToIdle.Rel",
        "N.QosFlow.RrcConnToInactive.Suspend")
    return abnormal + normal - inactive + suspended


_SAMPLE_SIZE: dict[tuple[str, str], Callable[[dict[str, float]], float]] = {
    ("accessibility", "4G"): _sample_4g_access,
    ("accessibility", "5G_NRCELL"): _sample_5g_access,
    ("drop_rate", "4G"): _sample_4g_drop,
    ("drop_rate", "5G_NRCELL"): _sample_5g_drop,
}


def sample_size(definition: KpiDefinition, counters: dict[str, float]) -> float | None:
    """Menor denominador da fórmula — quantas tentativas sustentam o percentual.

    Só faz sentido para taxas contadas em tentativas (``accessibility`` e
    ``drop_rate``); as demais devolvem ``None``.  Não altera cálculo nenhum:
    existe para que um 50,0% vindo de 1 sucesso em 2 tentativas não vire alarme.
    """
    handler = _SAMPLE_SIZE.get((definition.id, definition.technology))
    if handler is None:
        return None
    try:
        return float(handler(counters))
    except InvalidKpi:
        return None


def calculate(definition: KpiDefinition, counters: dict[str, float], period: float | None) -> float:
    value = definition.calculator(counters, period)
    if not isinstance(value, (int, float)) or value != value:
        raise InvalidKpi("resultado não numérico")
    return float(value)
