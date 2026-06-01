"""
clear_demo_event.py — Remove DEFINITIVAMENTE o evento de demo "GP São Paulo 2025"
da pasta de dados local (banco global + bancos por-evento).

Uso:
    python clear_demo_event.py                 # limpa a pasta ./data ao lado deste script
    python clear_demo_event.py "C:/caminho/data"   # limpa uma pasta data especifica

Rode ao lado do executavel (na mesma pasta onde existe a subpasta "data/")
caso o evento residual ainda apareca no .exe ja distribuido. Seguro de rodar
varias vezes; nao toca em nenhum outro evento.
"""

import sqlite3
import sys
from pathlib import Path

# IDs de demo a remover (ACTIVE e historico). Apenas estes eventos sao afetados.
DEMO_EVENT_IDS = ["gp-sp-2025", "gp-sp-2025-ended"]


def clear(data_dir: Path) -> None:
    if not data_dir.exists():
        print(f"Pasta nao encontrada: {data_dir}")
        return

    # 1. Remove do banco global (events / vips associados / silenced_alerts)
    global_db = data_dir / "smart_events.db"
    if global_db.exists():
        conn = sqlite3.connect(str(global_db))
        try:
            existing_tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            for eid in DEMO_EVENT_IDS:
                if "events" in existing_tables:
                    cur = conn.execute("DELETE FROM events WHERE id = ?", (eid,))
                    if cur.rowcount:
                        print(f"  [global] evento '{eid}' removido ({cur.rowcount} linha)")
                if "event_vips" in existing_tables:
                    conn.execute("DELETE FROM event_vips WHERE event_id = ?", (eid,))
            conn.commit()
        finally:
            conn.close()

    # 2. Apaga os bancos por-evento dedicados ao demo
    for eid in DEMO_EVENT_IDS:
        event_db = data_dir / f"smart_events_{eid}.db"
        if event_db.exists():
            event_db.unlink()
            print(f"  [arquivo] {event_db.name} apagado")

    print("Limpeza concluida.")


if __name__ == "__main__":
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "data"
    print(f"Limpando evento de demo em: {base.resolve()}")
    clear(base)
