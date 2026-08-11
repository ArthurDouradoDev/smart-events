"""Catálogo e cálculos seguros dos KPIs de Performance Monitor.

As fórmulas ficam em código deliberadamente: os nomes dos contadores vindos do
OSS são dados externos e nunca devem ser avaliados como uma expressão Python.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable


class InvalidKpi(ValueError):
    """Um KPI não pode ser calculado de forma confiável."""


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


def _need(c: dict[str, float], *names: str) -> tuple[float, ...]:
    missing = [name for name in names if name not in c]
    if missing:
        raise InvalidKpi("contador ausente: " + ", ".join(missing))
    return tuple(c[name] for name in names)


def _ratio(n: float, d: float, factor: float = 1.0) -> float:
    if d == 0:
        raise InvalidKpi("denominador zero")
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
    return _ratio(volume - last_slot, duration)


def _n_thp_ul(c, _period):
    volume, small_packet, duration = _need(c, "N.ThpVol.UL", "N.ThpVol.UE.UL.SmallPkt", "N.ThpTime.UE.UL.RmvSmallPkt")
    return _ratio(volume - small_packet, duration)


def _n_volume_dl_sa(c, _period):
    total, nsa = _need(c, "N.ThpVol.DL", "N.NSA.ThpVol.DL")
    return total - nsa


def _n_volume_ul_sa(c, _period):
    total, nsa = _need(c, "N.ThpVol.UL", "N.NSA.ThpVol.UL")
    return total - nsa


CATALOG: tuple[KpiDefinition, ...] = (
    KpiDefinition("accessibility", "4G", "Acessibilidade de Dados", "%", ("L.RRC.ConnReq.Succ", "L.RRC.ConnReq.Att", "L.E-RAB.SuccEst", "L.E-RAB.AttEst", "L.S1Sig.ConnEst.Succ", "L.S1Sig.ConnEst.Att"), "recalculate", _l_access),
    KpiDefinition("availability", "4G", "Availability", "%", ("L.Cell.Unavail.Dur.Sys", "L.Cell.Unavail.Dur.Manual"), "recalculate", _l_availability),
    KpiDefinition("drop_rate", "4G", "Drop Dados", "%", ("L.E-RAB.AbnormRel", "L.E-RAB.AbnormRel.MME", "L.E-RAB.NormRel", "L.E-RAB.Rel.MME"), "recalculate", _l_drop),
    KpiDefinition("utilization_dl", "4G", "DL PRB Utility", "%", ("L.ChMeas.PRB.DL.Used.Avg", "L.ChMeas.PRB.DL.Avail"), "recalculate", _l_prb_dl),
    KpiDefinition("utilization_ul", "4G", "UL PRB Utility", "%", ("L.ChMeas.PRB.UL.Used.Avg", "L.ChMeas.PRB.UL.Avail"), "recalculate", _l_prb_ul),
    KpiDefinition("interference_ul", "4G", "Interferência", "dBm", ("L.UL.Interference.Avg",), "mean", _single("L.UL.Interference.Avg")),
    KpiDefinition("throughput_dl", "4G", "Throughput DL", "Mbit/s", ("L.Thrp.bits.DL", "L.Thrp.bits.DL.LastTTI", "L.Thrp.Time.DL.RmvLastTTI"), "sum", _l_thp_dl),
    KpiDefinition("throughput_ul", "4G", "Throughput UL", "Mbit/s", ("L.Thrp.bits.UL", "L.Thrp.bits.UE.UL.LastTTI", "L.Thrp.Time.UE.UL.RmvLastTTI"), "sum", _l_thp_ul),
    KpiDefinition("user_count", "4G", "UE médio", "usuários", ("L.Traffic.User.Avg",), "sum", _single("L.Traffic.User.Avg")),
    KpiDefinition("ran_rtt", "4G", "Wireless RTT", "ms", ("L.PDCP.TCP.time.RANRtt.ConnSetup", "L.PDCP.TCP.RANRtt.ConnSetup"), "mean", _ratio_formula("L.PDCP.TCP.time.RANRtt.ConnSetup", "L.PDCP.TCP.RANRtt.ConnSetup", 1)),
    KpiDefinition("terrestrial_rtt", "4G", "Terrestrial RTT", "ms", ("L.PDCP.TCP.Time.TerrestrialRtt.ConnSetup", "L.PDCP.TCP.TerrestrialRtt.ConnSetup"), "mean", _ratio_formula("L.PDCP.TCP.Time.TerrestrialRtt.ConnSetup", "L.PDCP.TCP.TerrestrialRtt.ConnSetup", 1)),
    KpiDefinition("traffic_volume_dl", "4G", "Volume de Tráfego DL (legado)", "contador OSS", ("L.Thrp.bits.DL",), "sum", _single("L.Thrp.bits.DL")),
    KpiDefinition("traffic_volume_ul", "4G", "Volume de Tráfego UL (legado)", "contador OSS", ("L.Thrp.bits.UL",), "sum", _single("L.Thrp.bits.UL")),
    KpiDefinition("accessibility", "5G", "Acessibilidade considerando RRC Inactive", "%", ("N.RRC.SetupReq.Succ", "N.RRC.ResumeReq.Succ", "N.RRC.SetupReq.Att", "N.RRC.ResumeReq.Att", "N.NGSig.ConnEst.Succ", "N.NGSig.ConnEst.Att", "N.QosFlow.Est.Succ", "N.QosFlow.Est.Att.EPSFB", "N.QosFlow.Est.Att.EmcFB", "N.QosFlow.FailEst.Conflict", "N.QosFlow.Resume.Succ", "N.QosFlow.Est.Att", "N.QosFlow.FailEst.AMF.SyntaxError", "N.QosFlow.Resume.Att"), "recalculate", _n_access),
    KpiDefinition("drop_rate", "5G", "Drop considerando RRC Inactive", "%", ("N.QosFlow.AbnormRel", "N.QosFlow.NormRel", "N.QosFlow.RrcInactiveToIdle.Rel", "N.QosFlow.RrcConnToInactive.Suspend"), "recalculate", _n_drop),
    KpiDefinition("utilization_dl", "5G", "DL PRB Utility", "%", ("N.PRB.DL.Used.Avg", "N.PRB.DL.Avail.Avg"), "recalculate", _ratio_formula("N.PRB.DL.Used.Avg", "N.PRB.DL.Avail.Avg")),
    KpiDefinition("utilization_ul", "5G", "UL PRB Utility", "%", ("N.PRB.UL.Used.Avg", "N.PRB.UL.Avail.Avg"), "recalculate", _ratio_formula("N.PRB.UL.Used.Avg", "N.PRB.UL.Avail.Avg")),
    KpiDefinition("throughput_dl", "5G", "Throughput DL", "unidade OSS pendente", ("N.ThpVol.DL", "N.ThpVol.DL.LastSlot", "N.ThpTime.DL.RmvLastSlot"), "sum", _n_thp_dl, False),
    KpiDefinition("throughput_ul", "5G", "Throughput UL", "unidade OSS pendente", ("N.ThpVol.UL", "N.ThpVol.UE.UL.SmallPkt", "N.ThpTime.UE.UL.RmvSmallPkt"), "sum", _n_thp_ul, False),
    KpiDefinition("traffic_volume_dl_sa", "5G", "Downlink Traffic Volume 5G SA", "unidade OSS pendente", ("N.ThpVol.DL", "N.NSA.ThpVol.DL"), "sum", _n_volume_dl_sa, False),
    KpiDefinition("traffic_volume_dl_nsa", "5G", "Downlink Traffic Volume 5G NSA", "unidade OSS pendente", ("N.NSA.ThpVol.DL",), "sum", _single("N.NSA.ThpVol.DL"), False),
    KpiDefinition("traffic_volume_ul_sa", "5G", "Uplink Traffic Volume 5G SA", "unidade OSS pendente", ("N.ThpVol.UL", "N.NSA.ThpVol.UL"), "sum", _n_volume_ul_sa, False),
    KpiDefinition("traffic_volume_ul_nsa", "5G", "Uplink Traffic Volume 5G NSA", "unidade OSS pendente", ("N.NSA.ThpVol.UL",), "sum", _single("N.NSA.ThpVol.UL"), False),
    KpiDefinition("user_count", "5G", "User Médio", "usuários", ("N.User.RRCConn.Avg",), "sum", _single("N.User.RRCConn.Avg")),
    KpiDefinition("availability", "5G", "Availability", "%", ("N.Cell.Avail.Dur",), "recalculate", _n_availability),
    KpiDefinition("interference_ul", "5G", "UL Interference Médio", "dBm", ("N.UL.NI.Avg",), "mean", _single("N.UL.NI.Avg")),
)


def definitions_for(technology: str) -> tuple[KpiDefinition, ...]:
    return tuple(item for item in CATALOG if item.technology == technology)


def definition(metric: str, technology: str | None = None) -> KpiDefinition | None:
    return next((item for item in CATALOG if item.id == metric and (technology is None or item.technology == technology)), None)


def catalog_for_api() -> list[dict]:
    seen = set()
    rows = []
    for item in CATALOG:
        key = (item.id, item.technology)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"id": item.id, "technology": item.technology, "name": item.name,
                     "unit": item.unit, "site_aggregation": item.site_aggregation,
                     "production_ready": item.production_ready})
    return rows


def calculate(definition: KpiDefinition, counters: dict[str, float], period: float | None) -> float:
    value = definition.calculator(counters, period)
    if not isinstance(value, (int, float)) or value != value:
        raise InvalidKpi("resultado não numérico")
    return float(value)
