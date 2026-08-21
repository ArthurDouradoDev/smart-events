"""Confere os KPIs gravados contra o export CSV do próprio OSS.

O CSV de Monitoring é a única fonte independente disponível sem VPN: traz os
contadores brutos, o Granularity Period real (``Period(minute)``) e o instante
da amostra.  Este script recalcula os KPIs a partir dele com
``core.kpi_formulas`` e compara, célula a célula e minuto a minuto, com o que o
app gravou no banco do evento.

    python tools/kpi_crosscheck.py --event testesantoamaro --csv-dir csvs_reference

Razão ``APP/OSS`` de 1,000 em todas as métricas é o critério.  Uma razão
constante e redonda (0,2; 0,001) denuncia unidade ou período errados — foi
assim que a availability 5G em 20% e o throughput 5G 1000× menor apareceram.

Somente linhas de escopo CELL são comparadas: as de site dependem de o CSV
conter todas as células do site no mesmo minuto, o que o export não garante.
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.kpi_formulas import (  # noqa: E402
    CATALOG, InvalidKpi, calculate, check_throughput_floor, definitions_for,
)

_CELL_NAME = re.compile(r"Cell Name\s*=\s*([^,]+)", re.I)
_UNIT_SUFFIX = re.compile(r"\(.*\)$")
DEFAULT_TZ_OFFSET_MIN = -180  # o OSS de SP exporta em horário local


def _read_rows(path: Path) -> list[dict[str, str]]:
    """Lê o CSV do OSS, que traz um preâmbulo antes do cabeçalho real."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1")
    lines = text.splitlines()
    header = next((index for index, line in enumerate(lines)
                   if line.startswith('"Object"')), None)
    if header is None:
        return []
    reader = csv.DictReader(lines[header:])
    return [row for row in reader if row.get("Object")]


def _counter_name(column: str) -> str:
    return _UNIT_SUFFIX.sub("", column).strip()


def _technology_of(columns: list[str]) -> str | None:
    """A tecnologia é a que mais reconhece contadores do cabeçalho."""
    names = {_counter_name(column) for column in columns}
    scores = {}
    for item in CATALOG:
        scores[item.technology] = scores.get(item.technology, 0) + len(
            names & set(item.required))
    technology, score = max(scores.items(), key=lambda pair: pair[1], default=(None, 0))
    return technology if score else None


def _counters_of(row: dict[str, str]) -> dict[str, float]:
    counters = {}
    for column, raw in row.items():
        if column in (None, "Object", "Period(minute)", "Start Time"):
            continue
        text = (raw or "").strip()
        if not text:
            continue
        try:
            counters[_counter_name(column)] = float(text)
        except ValueError:
            continue
    return counters


def _timestamp_utc(local_text: str, offset_min: int) -> str:
    moment = datetime.strptime(local_text.strip(), "%Y-%m-%d %H:%M:%S")
    return (moment - timedelta(minutes=offset_min)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stored_values(db_path: Path, event_id: str) -> dict[tuple, float]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT cell_id, timestamp, metric, technology, value FROM kpi_measurements "
            "WHERE event_id = ? AND scope = 'CELL'", (event_id,)).fetchall()
    finally:
        conn.close()
    return {(cell, timestamp, metric, technology): value
            for cell, timestamp, metric, technology, value in rows}


def _compare(csv_dir: Path, stored: dict, offset_min: int) -> tuple[dict, list, list]:
    """Devolve (razões por métrica, exemplos, suspeitas de unidade)."""
    ratios: dict[tuple[str, str], list[float]] = defaultdict(list)
    samples, suspects = [], []
    for path in sorted(csv_dir.glob("*.csv")):
        rows = _read_rows(path)
        if not rows:
            print(f"[aviso] {path.name}: cabeçalho de contadores não encontrado.")
            continue
        technology = _technology_of(list(rows[0].keys()))
        if not technology:
            print(f"[aviso] {path.name}: nenhuma tecnologia do catálogo reconhecida.")
            continue
        for row in rows:
            match = _CELL_NAME.search(row["Object"])
            if not match:
                continue
            cell_id = match.group(1).strip()
            timestamp = _timestamp_utc(row["Start Time"], offset_min)
            period = float(row.get("Period(minute)") or 1)
            counters = _counters_of(row)
            for definition in definitions_for(technology):
                try:
                    value = calculate(definition, counters, period)
                except InvalidKpi:
                    continue
                reason = check_throughput_floor(definition, counters, period, value)
                if reason:
                    suspects.append((cell_id, timestamp, reason))
                app = stored.get((cell_id, timestamp, definition.id, technology))
                if app is None:
                    continue
                ratio = app / value if value else (1.0 if app == 0 else float("inf"))
                ratios[(technology, definition.id)].append(ratio)
                samples.append((technology, cell_id, timestamp, definition.id, value, app, ratio))
    return ratios, samples, suspects


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--event", required=True, help="id do evento (ex.: testesantoamaro)")
    parser.add_argument("--csv-dir", required=True, type=Path,
                        help="pasta com os ExportMonitoringResult_*.csv")
    parser.add_argument("--data-dir", type=Path, default=Path("data"),
                        help="pasta dos bancos por evento (padrão: data)")
    parser.add_argument("--tolerance", type=float, default=1e-6)
    parser.add_argument("--tz-offset-min", type=int, default=DEFAULT_TZ_OFFSET_MIN,
                        help="fuso do OSS em minutos (padrão: -180)")
    parser.add_argument("--examples", type=int, default=6,
                        help="quantas linhas detalhadas exibir (padrão: 6)")
    args = parser.parse_args()

    db_path = args.data_dir / f"smart_events_{args.event}.db"
    if not db_path.exists():
        print(f"Banco não encontrado: {db_path}")
        return 2
    if not args.csv_dir.is_dir():
        print(f"Pasta de CSVs não encontrada: {args.csv_dir}")
        return 2

    stored = _stored_values(db_path, args.event)
    ratios, samples, suspects = _compare(args.csv_dir, stored, args.tz_offset_min)
    if not ratios:
        print("Nenhuma medição do CSV encontrou linha correspondente no banco.")
        print("Confira o evento, o fuso (--tz-offset-min) e se os CSVs são do mesmo período.")
        return 2

    for technology, cell_id, timestamp, metric, oss, app, ratio in samples[:args.examples]:
        print(f"{technology:<12}{cell_id:<20}{timestamp}")
        print(f"  {metric:<18}OSS={oss:>12.3f}  APP={app:>12.3f}  "
              f"razão={ratio:.3f}  {'OK' if abs(ratio - 1) <= args.tolerance else 'DIVERGE'}")

    print()
    ok = 0
    for (technology, metric), values in sorted(ratios.items()):
        worst = max(values, key=lambda value: abs(value - 1))
        status = "OK" if abs(worst - 1) <= args.tolerance else "DIVERGE"
        if status == "OK":
            ok += 1
        print(f"{technology:<12}{metric:<24}n={len(values):<6}razão pior={worst:.6g}  {status}")

    if suspects:
        print(f"\n[trava de unidade] {len(suspects)} amostras abaixo do piso volume/período:")
        for cell_id, timestamp, reason in suspects[:3]:
            print(f"  {cell_id} {timestamp} — {reason}")

    print(f"\nRESULTADO: {ok}/{len(ratios)} métricas com razão 1,000 "
          f"(tolerância {args.tolerance:g})")
    return 0 if ok == len(ratios) else 1


if __name__ == "__main__":
    raise SystemExit(main())
