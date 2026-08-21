import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
from core.paths import data_dir, resource_dir
from core.seed import seed_operator_data

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("SmartEventsServer")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "cluster"

app = FastAPI(title="SmartEvents Central Server")

# Configure CORS so any client on the network can call it
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_event():
    import socket
    logger.info("====================================================")
    logger.info("SmartEvents Server is active! Access URLs:")
    logger.info("  - Local:   http://localhost:8000")
    try:
        # Create a dummy connection to get the actual local network interface IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        logger.info(f"  - Network: http://{local_ip}:8000")
    except Exception:
        logger.info("  - Network: (Could not detect local network IP automatically)")
    logger.info("====================================================")

# Diretórios cientes do modo "frozen" (PyInstaller):
#   RESOURCE_DIR → recursos read-only embutidos no bundle (server_frontend + semente de server_data)
#   DATA_DIR     → diretório gravável ao lado do .exe (mesmo critério de core/database.py),
#                  garantindo que app desktop e servidor compartilhem o MESMO server_data.
RESOURCE_DIR = resource_dir()
DATA_DIR = data_dir().parent if not getattr(sys, "frozen", False) else data_dir()

SERVER_DATA_DIR = DATA_DIR / "server_data"

# Seed na 1ª execução: se server_data ainda não existe ao lado do .exe, copia a cópia embutida no bundle.
def _seed_server_data():
    try:
        target = seed_operator_data()
        logger.info("server_data disponivel em %s", target)
    except Exception as e:
        logger.error(f"Falha ao semear server_data: {e}")

_seed_server_data()

EVENTS_DIR = SERVER_DATA_DIR / "events"
EVENTS_DIR.mkdir(parents=True, exist_ok=True)
VIPS_DIR = SERVER_DATA_DIR / "vips"
VIPS_DIR.mkdir(parents=True, exist_ok=True)
CLIENTES_DIR = SERVER_DATA_DIR / "clientes"
CLIENTES_DIR.mkdir(parents=True, exist_ok=True)
LOGOS_DIR = SERVER_DATA_DIR / "logos"
LOGOS_DIR.mkdir(parents=True, exist_ok=True)
FRONTEND_FILE = RESOURCE_DIR / "server_frontend" / "index.html"

# Mount server_frontend folder to serve local scripts/assets
app.mount("/static", StaticFiles(directory=str(RESOURCE_DIR / "server_frontend")), name="static")
# Logos das empresas (upload por cliente) — servidas como estáticos.
app.mount("/logos", StaticFiles(directory=str(LOGOS_DIR)), name="logos")

@app.post("/api/parse-sites")
async def parse_sites(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        filename = file.filename.lower()

        import io
        import pandas as pd

        if filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(io.BytesIO(contents))
        else:
            # CSV/TSV
            decoded = None
            for enc in ('utf-8', 'latin-1', 'utf-16', 'cp1252'):
                try:
                    decoded = contents.decode(enc)
                    break
                except Exception:
                    continue
            if decoded is None:
                raise ValueError("Não foi possível decodificar o arquivo. Use UTF-8 ou Latin-1.")

            # Detect separator
            first_line = decoded.split('\n')[0]
            sep = ','
            if '\t' in first_line:
                sep = '\t'
            elif ';' in first_line:
                sep = ';'

            df = pd.read_csv(io.StringIO(decoded), sep=sep)

        # Normalize column names by removing spaces and lowercasing
        df.columns = [c.strip().lower() for c in df.columns]

        # Possible column name mappings
        col_mappings = {
            'latitude': ['lat', 'latitude', 'latitud', 'coordenada y', 'y'],
            'longitude': ['lng', 'lon', 'longitude', 'longitud', 'coordenada x', 'x'],
            'enodebid': ['enodebid', 'enodeb_id', 'site_id', 'siteid', 'id_site', 'id site', 'codigo site', 'código site'],
            'cellid': ['cellid', 'cell_id', 'cell', 'id_celula', 'id celula', 'célula', 'celula'],
            'nename': ['nename', 'ne_name', 'sitename', 'site_name', 'nome site', 'nome_site', 'estacao', 'estação'],
            'cellname': ['cellname', 'cell_name', 'nome celula', 'nome_celula', 'nome da celula'],
            'azimuth': ['azimut', 'azimuth', 'azimute', 'direcao', 'direção'],
            'cluster': ['cluster', 'grupo', 'agrupamento', 'setor', 'area', 'área'],
        }

        # Rename columns if candidates match
        for req, candidates in col_mappings.items():
            if req not in df.columns:
                for cand in candidates:
                    if cand in df.columns:
                        df.rename(columns={cand: req}, inplace=True)
                        break

        # Verify required columns are present
        required = ['enodebid', 'cellid', 'nename', 'cellname', 'longitude', 'latitude', 'azimuth']
        missing = [r for r in required if r not in df.columns]
        if missing:
            raise ValueError(f"Colunas obrigatórias ausentes no arquivo: {', '.join(missing)}")

        # Group by site (enodebid)
        sites_dict = {}
        clusters_dict = {}  # nome do cluster -> set de site_ids (coluna opcional; §1 do plano)
        for _, row in df.iterrows():
            # Skip rows with missing required columns
            if pd.isna(row['enodebid']) or pd.isna(row['latitude']) or pd.isna(row['longitude']):
                continue

            site_id = str(int(float(row['enodebid']))) if isinstance(row['enodebid'], (int, float)) else str(row['enodebid']).strip()

            try:
                lat = float(row['latitude'])
                lng = float(row['longitude'])
                import math
                if math.isnan(lat) or math.isnan(lng) or math.isinf(lat) or math.isinf(lng):
                    continue

                azimuth_val = row.get('azimuth')
                if pd.isna(azimuth_val):
                    azimuth = 0.0
                else:
                    azimuth = float(azimuth_val)
                    if math.isnan(azimuth) or math.isinf(azimuth):
                        azimuth = 0.0
            except Exception:
                continue

            cell_id = str(row['cellname']).strip() if not pd.isna(row.get('cellname')) else ""
            if not cell_id:
                cell_id = str(row.get('cellid')).strip() if not pd.isna(row.get('cellid')) else ""
            if not cell_id:
                continue

            site_name = str(row['nename']).strip() if not pd.isna(row.get('nename')) else site_id

            if site_id not in sites_dict:
                sites_dict[site_id] = {
                    "id": site_id,
                    "name": site_name,
                    "lat": lat,
                    "lng": lng,
                    "is_event_site": True,
                    "cells": []
                }

            # Avoid duplicate cells
            existing_cells = [c["id"] for c in sites_dict[site_id]["cells"]]
            if cell_id not in existing_cells:
                sites_dict[site_id]["cells"].append({
                    "id": cell_id,
                    "azimuth": azimuth,
                    "beamwidth": 120.0
                })

            # Coluna opcional de cluster: valores separados por ";" atribuem o site
            # a vários clusters de uma vez (N:N), preservado pela importação como semente.
            cluster_val = row.get('cluster')
            if not pd.isna(cluster_val):
                for cluster_name in str(cluster_val).split(';'):
                    cluster_name = cluster_name.strip()
                    if not cluster_name:
                        continue
                    clusters_dict.setdefault(cluster_name, set()).add(site_id)

        sites_list = list(sites_dict.values())
        clusters_list = [
            {"id": _slugify(name), "name": name, "site_ids": sorted(site_ids)}
            for name, site_ids in clusters_dict.items()
        ]
        return {"ok": True, "sites": sites_list, "clusters": clusters_list}
    except Exception as e:
        logger.error(f"Erro ao parsear arquivo de sites: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/", response_class=HTMLResponse)
def get_home():
    if not FRONTEND_FILE.exists():
        return "<h3>SmartEvents Server: Frontend file not found. Place index.html inside server_frontend/</h3>"
    return FileResponse(FRONTEND_FILE)

@app.get("/api/events")
def get_events():
    events = []
    for file_path in EVENTS_DIR.glob("*.json"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                event_data = json.load(f)
                if isinstance(event_data, dict) and "id" in event_data:
                    events.append(event_data)
        except Exception as e:
            logger.error(f"Error loading event file {file_path.name}: {e}")
    return events

@app.post("/api/events")
async def post_event(request: Request):
    try:
        event_data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if not isinstance(event_data, dict) or "id" not in event_data or "name" not in event_data:
        raise HTTPException(status_code=400, detail="Missing required event fields: id or name")

    event_id = event_data["id"]
    file_path = EVENTS_DIR / f"{event_id}.json"

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(event_data, f, indent=4, ensure_ascii=False)
        logger.info(f"Registered event: {event_data['name']} ({event_id})")
        return {"ok": True, "message": f"Event '{event_data['name']}' synchronized/saved successfully"}
    except Exception as e:
        logger.error(f"Failed to save event {event_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/events/{event_id}")
def delete_event(event_id: str):
    file_path = EVENTS_DIR / f"{event_id}.json"
    if file_path.exists():
        try:
            file_path.unlink()
            logger.info(f"Deleted event: {event_id}")
            return {"ok": True, "message": f"Event '{event_id}' deleted successfully"}
        except Exception as e:
            logger.error(f"Failed to delete event {event_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    else:
        raise HTTPException(status_code=404, detail="Event not found")


# ── VIPs (cadastro global) ──────────────────────────────────────────

@app.get("/api/vips")
def get_vips(oss: Optional[str] = None, cliente: Optional[str] = None):
    vips = []
    for file_path in VIPS_DIR.glob("*.json"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "id" in data and "name" in data:
                    if oss is not None and data.get("oss") != oss:
                        continue
                    # VIPs sem cliente (legado) sempre passam; só filtra os de cliente diferente.
                    if cliente is not None and data.get("cliente") and data.get("cliente") != cliente:
                        continue
                    vips.append(data)
        except Exception as e:
            logger.error(f"Error loading vip file {file_path.name}: {e}")
    return vips


@app.post("/api/vips")
async def post_vip(request: Request):
    try:
        vip = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if not isinstance(vip, dict) or "id" not in vip or "name" not in vip:
        raise HTTPException(status_code=400, detail="Missing required vip fields: id or name")

    file_path = VIPS_DIR / f"{vip['id']}.json"
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(vip, f, indent=4, ensure_ascii=False)
        logger.info(f"Registered VIP: {vip['name']} ({vip['id']})")
        return {"ok": True}
    except Exception as e:
        logger.error(f"Failed to save vip {vip['id']}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/vips/{vip_id}")
def delete_vip(vip_id: str):
    file_path = VIPS_DIR / f"{vip_id}.json"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="VIP not found")
    try:
        file_path.unlink()
        logger.info(f"Deleted VIP: {vip_id}")
        return {"ok": True}
    except Exception as e:
        logger.error(f"Failed to delete vip {vip_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Clientes (catálogo Cliente → Regional → IP, com logo) ───────────
# Cada cliente é um JSON em server_data/clientes/<id>.json:
#   {"id","name","logo": <arquivo em /logos ou "">, "regionais":[{"region","ip"}]}

def _slugify(text: str) -> str:
    import re, unicodedata
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "cliente"


@app.get("/api/clientes")
def get_clientes():
    clientes = []
    for file_path in CLIENTES_DIR.glob("*.json"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "id" in data and "name" in data:
                    clientes.append(data)
        except Exception as e:
            logger.error(f"Error loading cliente file {file_path.name}: {e}")
    clientes.sort(key=lambda c: c.get("name", ""))
    return clientes


@app.post("/api/clientes")
async def post_cliente(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    if not isinstance(data, dict) or not data.get("name"):
        raise HTTPException(status_code=400, detail="Missing required field: name")

    cid = data.get("id") or _slugify(data["name"])
    data["id"] = cid
    # Normaliza regionais: lista de {region, ip}
    regionais = []
    for r in data.get("regionais", []) or []:
        region = (r.get("region") or "").strip().upper()
        ip = (r.get("ip") or "").strip()
        if region:
            regionais.append({"region": region, "ip": ip})
    data["regionais"] = regionais
    data.setdefault("logo", "")

    file_path = CLIENTES_DIR / f"{cid}.json"
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logger.info(f"Registered cliente: {data['name']} ({cid})")
        return {"ok": True, "id": cid}
    except Exception as e:
        logger.error(f"Failed to save cliente {cid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/clientes/{cliente_id}")
def delete_cliente(cliente_id: str):
    file_path = CLIENTES_DIR / f"{cliente_id}.json"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Cliente not found")
    try:
        file_path.unlink()
        logger.info(f"Deleted cliente: {cliente_id}")
        return {"ok": True}
    except Exception as e:
        logger.error(f"Failed to delete cliente {cliente_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/clientes/{cliente_id}/logo")
async def upload_cliente_logo(cliente_id: str, file: UploadFile = File(...)):
    file_path = CLIENTES_DIR / f"{cliente_id}.json"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Cliente not found")
    ext = Path(file.filename or "").suffix.lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"):
        raise HTTPException(status_code=400, detail="Formato de imagem não suportado")
    logo_name = f"{cliente_id}{ext}"
    try:
        # Remove logos antigas do cliente (extensão pode mudar)
        for old in LOGOS_DIR.glob(f"{cliente_id}.*"):
            old.unlink()
        with open(LOGOS_DIR / logo_name, "wb") as out:
            shutil.copyfileobj(file.file, out)
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["logo"] = logo_name
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logger.info(f"Logo atualizada para cliente {cliente_id}: {logo_name}")
        return {"ok": True, "logo": logo_name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to save logo for cliente {cliente_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    # Host on all interfaces (0.0.0.0) so it is accessible from the local network
    uvicorn.run(app, host="0.0.0.0", port=8000)
