"""Sincroniza tecnologia/frequencia de uma EP com um evento ja cadastrado."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from fastapi import UploadFile

from server import parse_sites


async def _parse_workbook(path: Path) -> dict:
    with path.open("rb") as handle:
        upload = UploadFile(file=handle, filename=path.name)
        return await parse_sites(upload)


def enrich(event_path: Path, workbook_path: Path) -> dict:
    event = json.loads(event_path.read_text(encoding="utf-8"))
    parsed = asyncio.run(_parse_workbook(workbook_path))
    if not parsed.get("ok"):
        raise RuntimeError("A EP nao foi interpretada com sucesso.")

    parsed_sites = {str(site["id"]): site for site in parsed.get("sites") or []}
    event_sites = {str(site["id"]): site for site in event.get("sites") or []}
    if set(parsed_sites) != set(event_sites):
        raise RuntimeError(
            "Sites da EP diferem do evento: "
            f"somente_ep={sorted(set(parsed_sites) - set(event_sites))}; "
            f"somente_evento={sorted(set(event_sites) - set(parsed_sites))}"
        )

    parsed_cells: dict[tuple[str, str], dict] = {}
    for site_id, site in parsed_sites.items():
        for cell in site.get("cells") or []:
            key = (site_id, str(cell.get("id") or ""))
            if key in parsed_cells:
                raise RuntimeError(f"Celula duplicada na EP: {key}")
            parsed_cells[key] = cell

    event_cells: dict[tuple[str, str], dict] = {}
    for site_id, site in event_sites.items():
        for cell in site.get("cells") or []:
            key = (site_id, str(cell.get("id") or ""))
            if key in event_cells:
                raise RuntimeError(f"Celula duplicada no evento: {key}")
            event_cells[key] = cell
    if set(parsed_cells) != set(event_cells):
        raise RuntimeError(
            "Celulas da EP diferem do evento: "
            f"somente_ep={sorted(set(parsed_cells) - set(event_cells))[:10]}; "
            f"somente_evento={sorted(set(event_cells) - set(parsed_cells))[:10]}"
        )

    counts: dict[str, int] = {}
    for key, cell in event_cells.items():
        source = parsed_cells[key]
        technology = str(source.get("tech") or "").strip()
        frequency = str(source.get("frequency") or "").strip()
        if not technology or not frequency:
            raise RuntimeError(f"Tecnologia/frequencia ausente na EP: {key}")
        cell["tech"] = technology
        cell["frequency"] = frequency
        counts[technology] = counts.get(technology, 0) + 1

    event_path.write_text(
        json.dumps(event, indent=4, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {
        "event": str(event.get("id") or event_path.stem),
        "sites": len(event_sites),
        "cells": len(event_cells),
        "technologies": dict(sorted(counts.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--workbook", type=Path, required=True)
    args = parser.parse_args()
    result = enrich(args.event.resolve(), args.workbook.resolve())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
