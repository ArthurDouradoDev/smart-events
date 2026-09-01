"""Distribution Studio — tela interna de geração de pacotes `.sepack`.

Esta é uma **ferramenta de repositório**, não um recurso do produto. O Smart Events
Central que o operador recebe não conhece nada disto: não expõe os endpoints, não
mostra a seleção de eventos e não tem link para esta tela. O que é distribuído são
os *resultados* desta tela (o `.sepack` e, na Fase 3, o `Setup.exe`).

Três consequências práticas do isolamento:

- roda em outro processo e em outra porta (``python -m tools.distribution_studio``),
  então nem o `server.py` nem o `main.spec` importam este módulo — ele nunca entra
  no bundle do PyInstaller;
- escuta apenas em ``127.0.0.1`` e não habilita CORS: a Central serve a rede local,
  o estúdio não sai da máquina do operador;
- fica sob o prefixo ``/distribution-studio``. ``/`` responde 404: sem o link, não
  há nada para achar.

Ele lê o **mesmo** ``server_data`` que a Central (``core.paths.server_data_dir()``),
então a seleção enxerga exatamente os eventos cadastrados. As regras de segurança da
Fase 2 continuam valendo: os endpoints recebem ids de evento e opções enumeradas,
nunca caminho de origem, saída ou compilador, e toda saída fica em
``data/distributions/<job-id>/``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Permite ``python tools/distribution_studio.py`` além de ``python -m tools....``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Body, FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse  # noqa: E402

from core.distribution_service import DistributionError, DistributionService  # noqa: E402

logger = logging.getLogger(__name__)

# Prefixo único da ferramenta. Não existe rota em ``/``: a tela só é alcançável por
# quem tem o link.
STUDIO_PATH = "/distribution-studio"
STUDIO_HTML = Path(__file__).resolve().parent / "distribution_studio.html"
DEFAULT_PORT = 8010

# Sem CORS e sem docs automáticos: nada aqui é uma API pública.
app = FastAPI(
    title="SmartEvents Distribution Studio",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Sem ``source_dir`` explícito o serviço usa ``core.paths.server_data_dir()`` — a
# mesma pasta lida pela Central.
DISTRIBUTION_SERVICE = DistributionService()

_PREVIEW_FIELDS = {"event_ids", "name", "vip_policy", "vip_ids"}
_JOB_FIELDS = _PREVIEW_FIELDS | {"format"}


def _distribution_body(payload, allowed: set) -> dict:
    """Aceita só os campos previstos — um `source`/`output` extra é recusado."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Corpo da requisição deve ser um objeto JSON.")
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise HTTPException(
            status_code=400,
            detail="Campos não aceitos nesta operação: " + ", ".join(unexpected),
        )
    name = payload.get("name")
    if name is not None and not isinstance(name, str):
        raise HTTPException(status_code=400, detail="O campo name deve ser texto.")
    vip_ids = payload.get("vip_ids")
    if vip_ids is not None and (
        not isinstance(vip_ids, list) or not all(isinstance(v, str) for v in vip_ids)
    ):
        raise HTTPException(status_code=400, detail="O campo vip_ids deve ser uma lista de texto.")
    return {
        "event_ids": payload.get("event_ids"),
        "name": name,
        "vip_policy": str(payload.get("vip_policy") or "auto"),
        "vip_ids": vip_ids,
    }


def _distribution_failure(exc: DistributionError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


@app.get(STUDIO_PATH, response_class=HTMLResponse)
def get_studio_page():
    if not STUDIO_HTML.is_file():
        raise HTTPException(status_code=500, detail="Página do estúdio ausente do repositório.")
    return FileResponse(STUDIO_HTML)


@app.get(STUDIO_PATH + "/api/events")
def get_studio_events():
    """Resumo dos eventos para a lista de seleção.

    Devolve só o que a tela desenha. O evento inteiro (polígono, células, tasks)
    não precisa atravessar a rede para o operador marcar uma caixa.
    """
    rows = []
    directory = DISTRIBUTION_SERVICE.source_dir / "events"
    if not directory.is_dir():
        return rows
    for file_path in sorted(directory.glob("*.json")):
        try:
            event = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.error("Evento ilegível em %s: %s", file_path.name, exc)
            continue
        if not isinstance(event, dict) or not event.get("id"):
            continue
        sites = event.get("sites") if isinstance(event.get("sites"), list) else []
        oss = event.get("oss") if isinstance(event.get("oss"), dict) else {}
        rows.append({
            "id": str(event["id"]),
            "name": str(event.get("name") or event["id"]),
            "status": str(event.get("status") or ""),
            "start_time": str(event.get("start_time") or ""),
            "end_time": str(event.get("end_time") or ""),
            "client": str(oss.get("cliente") or ""),
            "region": str(oss.get("region") or ""),
            "sites": len(sites),
            "cells": sum(
                len(site.get("cells") or []) for site in sites if isinstance(site, dict)
            ),
        })
    rows.sort(key=lambda row: (row["start_time"], row["name"]))
    return rows


@app.get(STUDIO_PATH + "/api/capabilities")
def get_distribution_capabilities():
    return DISTRIBUTION_SERVICE.capabilities()


@app.post(STUDIO_PATH + "/api/preview")
def post_distribution_preview(payload: dict = Body(...)):
    body = _distribution_body(payload, _PREVIEW_FIELDS)
    try:
        return DISTRIBUTION_SERVICE.preview(
            body["event_ids"],
            name=body["name"],
            vip_policy=body["vip_policy"],
            vip_ids=body["vip_ids"],
        )
    except DistributionError as exc:
        raise _distribution_failure(exc) from exc


@app.post(STUDIO_PATH + "/api/jobs")
def post_distribution(payload: dict = Body(...)):
    body = _distribution_body(payload, _JOB_FIELDS)
    fmt = payload.get("format") or "event_package"
    if not isinstance(fmt, str):
        raise HTTPException(status_code=400, detail="O campo format deve ser texto.")
    try:
        return DISTRIBUTION_SERVICE.create_job(
            body["event_ids"],
            name=body["name"],
            fmt=fmt,
            vip_policy=body["vip_policy"],
            vip_ids=body["vip_ids"],
        )
    except DistributionError as exc:
        raise _distribution_failure(exc) from exc


@app.get(STUDIO_PATH + "/api/jobs/{job_id}")
def get_distribution(job_id: str):
    try:
        return DISTRIBUTION_SERVICE.get_job(job_id)
    except DistributionError as exc:
        raise _distribution_failure(exc) from exc


def _distribution_artifact(job_id: str, kind: str) -> FileResponse:
    try:
        path, entry = DISTRIBUTION_SERVICE.artifact(job_id, kind)
    except DistributionError as exc:
        raise _distribution_failure(exc) from exc
    return FileResponse(path, filename=entry["name"], media_type=entry["media_type"])


@app.get(STUDIO_PATH + "/api/jobs/{job_id}/download")
def download_distribution(job_id: str):
    return _distribution_artifact(job_id, "package")


@app.get(STUDIO_PATH + "/api/jobs/{job_id}/manifest")
def download_distribution_manifest(job_id: str):
    return _distribution_artifact(job_id, "manifest")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.distribution_studio",
        description="Sobe a tela interna de geração de distribuições (uso no repositório).",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"Porta local do estúdio (padrão: {DEFAULT_PORT}).",
    )
    args = parser.parse_args(argv)

    import uvicorn

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    logger.info("====================================================")
    logger.info("Distribution Studio (ferramenta interna, nao distribuida)")
    logger.info("  Abra: http://127.0.0.1:%s%s", args.port, STUDIO_PATH)
    logger.info("  Origem: %s", DISTRIBUTION_SERVICE.source_dir)
    logger.info("  Saidas: %s", DISTRIBUTION_SERVICE.root)
    logger.info("====================================================")
    # Sempre loopback: a Central serve a rede local, esta ferramenta nao.
    uvicorn.run(app, host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
