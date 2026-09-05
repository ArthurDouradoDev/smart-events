"""Contrato único da família da célula (4G/5G).

A EP é a fonte da FAMÍLIA; a task continua sendo a fonte da FÓRMULA. Declarar
``5G`` aqui nunca escolhe entre NR Cell e NR DU Cell — essas duas têm contadores
disjuntos e seguem vindo de ``kpi_measurements.technology``.
"""
import re
from dataclasses import dataclass
from typing import Literal

Family = Literal["4G", "5G"]


def normalize_ep_technology(value: object) -> Family | None:
    """Converte o valor declarado na EP para a forma canônica ``4G``/``5G``."""
    token = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    if token in {"4G", "LTE"}:
        return "4G"
    if token in {"5G", "NR", "NRCELL", "NRDUCELL", "5GNRCELL", "5GNRDUCELL"}:
        return "5G"
    return None


def task_family(value: object) -> Family | None:
    """Família da tecnologia de uma task (``4G``, ``5G_NRCELL``, ``5G_NRDUCELL``)."""
    return normalize_ep_technology(value)


@dataclass(frozen=True)
class Resolution:
    family: Family | None
    source: Literal["ep", "measurement", "legacy_id", "legacy_frequency", "unknown"]


def resolve_cell_family(cell: dict | str | None, measurement: object = None,
                        *, allow_legacy: bool = True) -> Resolution:
    """Família da célula e a origem da decisão, nesta precedência.

    EP declarada vence sempre; depois a tecnologia persistida com a medição; só
    então as heurísticas legadas por nome e por frequência, para eventos salvos
    antes da coluna ``tecnologia`` existir. ``allow_legacy=False`` responde
    apenas o que a EP afirma, sem inventar família para o resto.
    """
    data = cell if isinstance(cell, dict) else {"id": cell}
    ep = data.get("ep") or {}
    declared = normalize_ep_technology(data.get("tech")) or normalize_ep_technology(ep.get("technology"))
    if declared:
        return Resolution(declared, "ep")
    if not allow_legacy:
        return Resolution(None, "unknown")
    measured = task_family(measurement)
    if measured:
        return Resolution(measured, "measurement")
    name = str(data.get("id") or "").upper()
    four = bool(re.search(r"(^|[^A-Z0-9])(?:4G|LTE)([^A-Z0-9]|$)", name))
    five = bool(re.search(r"(^|[^A-Z0-9])(?:5G|NR|NCI)([^A-Z0-9]|$)", name))
    if four != five:
        return Resolution("4G" if four else "5G", "legacy_id")
    frequency = str(data.get("frequency") or ep.get("band") or "")
    if frequency == "3500":
        return Resolution("5G", "legacy_frequency")
    if frequency in {"700", "850", "1800", "2100", "2300", "2600"}:
        return Resolution("4G", "legacy_frequency")
    return Resolution(None, "unknown")


def technology_conflict(cell: dict | str | None, technology: object) -> bool:
    """True quando a EP e a tecnologia da task/medição discordam da família."""
    declared = resolve_cell_family(cell, allow_legacy=False).family
    measured = task_family(technology)
    return bool(declared and measured and declared != measured)


@dataclass(frozen=True)
class FamilyIndex:
    """Inventário do evento já resolvido, para anotar vários lotes sem refazê-lo."""
    cells: dict
    site_families: dict


def build_family_index(config: dict) -> FamilyIndex:
    """Percorre o inventário uma vez; anotar N lotes não deve custar N varreduras."""
    cells = {}
    site_families = {}
    for site in (config or {}).get("sites", []):
        sid = str(site.get("id"))
        families = set()
        for cell in site.get("cells", []):
            cid = cell if isinstance(cell, str) else cell.get("id")
            cells[(sid, str(cid))] = cell
            family = resolve_cell_family(cell, allow_legacy=False).family
            if family:
                families.add(family)
        site_families[sid] = next(iter(families)) if len(families) == 1 else None
    return FamilyIndex(cells, site_families)


def annotate_rows(rows: list[dict], index: FamilyIndex) -> list[dict]:
    """Classifica um lote contra o inventário, sem alterar as linhas do banco.

    Devolve cópias com ``family``/``family_source``. Linha de escopo SITE não tem
    célula para consultar: herda a família do site quando ele só declara uma.
    """
    result = []
    for row in rows or []:
        sid, cid = str(row.get("site_id")), str(row.get("cell_id"))
        resolution = resolve_cell_family(index.cells.get((sid, cid), cid), row.get("technology"))
        family = resolution.family
        source = resolution.source
        if cid in {"None", "__site__", "__all__"} and index.site_families.get(sid):
            family, source = index.site_families[sid], "ep"
        result.append({**row, "family": family, "family_source": source})
    return result
