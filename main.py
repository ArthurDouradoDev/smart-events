"""
main.py — Ponto de entrada do Smart Events.
Inicializa o banco, a janela PyWebView e expõe a API Python ao JS.

Uso:
  python main.py           # produção (sem mock)
  python main.py --mock    # desenvolvimento com dados sintéticos
  python main.py --dev     # abre DevTools e habilita console
"""

import logging
import sys
from pathlib import Path

# Adiciona o root ao path para imports absolutos
sys.path.insert(0, str(Path(__file__).parent))

import webview
from api.api import Api
from core import database as db

logging.basicConfig(
    level=logging.DEBUG if "--dev" in sys.argv else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

FRONTEND = Path(__file__).parent / "frontend" / "index.html"


def _prepopulate_mock_history():
    """Pré-popula o banco de dados com um evento finalizado e seu histórico para testes."""
    from datetime import datetime, timedelta
    import random
    import json
    
    event_id = "gp-sp-2025-ended"
    
    # Verifica se já existe para não reinserir repetidamente e inchar o banco
    if db.get_event(event_id):
        return
        
    logger.info("Pré-populando banco de dados com dados históricos de teste...")
    
    # Evento finalizado há 1 hora
    end_dt = datetime.utcnow() - timedelta(hours=1)
    start_dt = end_dt - timedelta(hours=4)
    
    event_config = {
        "id": event_id,
        "name": "GP São Paulo 2025 (Histórico)",
        "status": "ENDED",
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "polygon": [
            [-23.7030, -46.7010],
            [-23.6970, -46.6910],
            [-23.6910, -46.6960],
            [-23.6940, -46.7050],
            [-23.7030, -46.7010]
        ],
        "sites": [
            {
                "id": "ERB-07",
                "name": "ERB-07 Interlagos",
                "lat": -23.7012,
                "lng": -46.6975,
                "is_event_site": True,
                "cells": [
                    { "id": "ERB-07-Y3500-1", "azimuth": 0 },
                    { "id": "ERB-07-L700-1",  "azimuth": 0 },
                    { "id": "ERB-07-L1800-1", "azimuth": 0 },
                    { "id": "ERB-07-Y3500-2", "azimuth": 120 },
                    { "id": "ERB-07-L700-2",  "azimuth": 120 },
                    { "id": "ERB-07-L1800-2", "azimuth": 120 },
                    { "id": "ERB-07-Y3500-3", "azimuth": 240 },
                    { "id": "ERB-07-L700-3",  "azimuth": 240 },
                    { "id": "ERB-07-L1800-3", "azimuth": 240 }
                ]
            },
            {
                "id": "ERB-03",
                "name": "ERB-03 Av. Interlagos",
                "lat": -23.6958,
                "lng": -46.6940,
                "is_event_site": True,
                "cells": [
                    { "id": "ERB-03-A1", "azimuth": 30 },
                    { "id": "ERB-03-A2", "azimuth": 150 },
                    { "id": "ERB-03-A3", "azimuth": 270 }
                ]
            }
        ],
        "vips": [
            { "id": "carlos-menezes", "task_id": None },
            { "id": "ana-rodrigues",  "task_id": None }
        ],
        "thresholds": {
            "rsrp_warning": -100,
            "rsrp_critical": -110,
            "rsrq_warning": -12,
            "rsrq_critical": -15,
            "utilization_warning": 80,
            "utilization_critical": 95
        }
    }
    
    db.save_event(event_config)
    
    # Salvar sites na tabela sites
    for site in event_config["sites"]:
        db.get_event_conn(event_id).execute("""
            INSERT OR REPLACE INTO sites (id, event_id, name, lat, lng, is_event_site, cells_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            site["id"],
            event_id,
            site["name"],
            site["lat"],
            site["lng"],
            1 if site["is_event_site"] else 0,
            json.dumps(site["cells"])
        ))
    db.get_event_conn(event_id).commit()
    
    # Gerar medições a cada 2 minutos
    current_time = start_dt
    kpis_to_insert = []
    vips_to_insert = []
    alerts_to_insert = []
    
    while current_time <= end_dt:
        ts_str = current_time.isoformat() + "Z"
        
        # Simula gaps de dados (pula alguns intervalos)
        if start_dt + timedelta(minutes=60) <= current_time <= start_dt + timedelta(minutes=70):
            current_time += timedelta(minutes=2)
            continue
            
        for site in event_config["sites"]:
            for cell in site["cells"]:
                # Medições de KPI
                util_dl = round(max(10, min(100, 60 + random.gauss(0, 15))), 1)
                vol_dl = round(max(10, 100 + random.gauss(0, 30)), 1)
                vol_ul = round(max(5, 40 + random.gauss(0, 15)), 1)
                tp_dl = round(max(0, 20 + random.gauss(0, 5)), 2)
                tp_ul = round(max(0, 5 + random.gauss(0, 1)), 2)
                users = int(max(1, 25 + random.gauss(0, 8)))
                accessibility = 100.0 if random.random() > 0.02 else 95.0
                
                metrics = {
                    "utilization_dl": util_dl,
                    "traffic_volume_dl": vol_dl,
                    "traffic_volume_ul": vol_ul,
                    "throughput_dl": tp_dl,
                    "throughput_ul": tp_ul,
                    "user_count": users,
                    "accessibility": accessibility
                }
                
                for metric, value in metrics.items():
                    kpis_to_insert.append({
                        "site_id": site["id"],
                        "cell_id": cell["id"],
                        "event_id": event_id,
                        "timestamp": ts_str,
                        "metric": metric,
                        "value": value
                    })
                    
        for vip in event_config["vips"]:
            rsrp = round(random.gauss(-85, 10), 1)
            rsrq = round(random.gauss(-10, 3), 1)
            serving = "ERB-07-L1800-1" if random.random() > 0.3 else "ERB-03-A2"
            
            vips_to_insert.append({
                "vip_name": vip["id"],
                "event_id": event_id,
                "timestamp": ts_str,
                "serving_cell": serving,
                "rsrp": rsrp,
                "rsrq": rsrq,
                "in_event": 1
            })
            
            # Alertas VIP
            if rsrp <= -110:
                alerts_to_insert.append({
                    "event_id": event_id,
                    "level": "EVENT",
                    "severity": "CRITICAL",
                    "site_id": serving.split("-")[0],
                    "cell_id": serving,
                    "message": f"RSRP crítico para {vip['id']}: {rsrp} dBm",
                    "timestamp": ts_str,
                    "acknowledged": 0
                })
                
        current_time += timedelta(minutes=2)
        
    db.insert_kpi_batch(kpis_to_insert)
    db.insert_vip_batch(vips_to_insert)
    
    # Inserir alertas
    for alert in alerts_to_insert:
        db.insert_alert(alert)
        
    logger.info(f"Histórico pré-populado com sucesso: {len(kpis_to_insert)} KPIs, {len(vips_to_insert)} VIPs, {len(alerts_to_insert)} Alertas.")


def main():
    mock_mode = "--mock" in sys.argv
    dev_mode = "--dev" in sys.argv

    logger.info(f"Iniciando Smart Events | mock={mock_mode} | dev={dev_mode}")

    db.init_db()

    # Sincroniza VIPs e eventos a partir do servidor central no startup
    try:
        logger.info("Sincronizando VIPs do servidor central no startup...")
        vip_stats = db.sync_vips_from_server()
        logger.info(f"Sincronização de VIPs de startup concluída: {vip_stats}")
        logger.info("Sincronizando eventos do servidor central no startup...")
        event_stats = db.sync_events_from_server()
        logger.info(f"Sincronização de eventos de startup concluída: {event_stats}")
    except Exception as e:
        logger.error(f"Erro ao sincronizar dados no startup: {e}")

    if mock_mode:
        _prepopulate_mock_history()

    api = Api()
    if mock_mode:
        # Injeta flag para que o frontend saiba que está em modo mock
        api._mock_mode = True

    window = webview.create_window(
        title="Smart Events",
        url=str(FRONTEND),
        js_api=api,
        width=1440,
        height=900,
        min_size=(1024, 600),
        resizable=True,
        fullscreen=False,
        background_color="#0D1117",
    )

    def _on_loaded():
        logger.info("Interface carregada")
        window.maximize()
        if mock_mode:
            window.evaluate_js("window.__MOCK_MODE__ = true;")

    window.events.loaded += _on_loaded

    webview.start(
        debug=dev_mode,
        http_server=False,   # serve arquivos locais diretamente
        storage_path=str(Path(__file__).parent / "data"),
    )


if __name__ == "__main__":
    main()
