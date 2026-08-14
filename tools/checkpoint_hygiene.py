"""CLI da auditoria segura de checkpoints da Fase 6."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import database as db
from core.checkpoint_hygiene import apply_report, audit_workspace, write_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Audita ou saneia checkpoints multi-regionais")
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit", help="gera relatório somente leitura")
    audit.add_argument("--output-dir", type=Path, default=db.BASE_DIR / "data" / "diagnostics")
    apply_cmd = sub.add_parser("apply", help="aplica um relatório confirmado")
    apply_cmd.add_argument("report", type=Path)
    apply_cmd.add_argument("--confirm", required=True, help="confirmation_hash do relatório")
    apply_cmd.add_argument("--backup-dir", type=Path, default=db.BASE_DIR / "data" / "backups")
    args = parser.parse_args()

    if args.command == "audit":
        report = audit_workspace(db.DB_PATH, db.get_event_db_path)
        path = write_report(report, args.output_dir)
        print(json.dumps({
            "report": str(path),
            "invalid": len(report["invalid_checkpoints"]),
            "manual_review": len(report["manual_review"]),
            "confirmation_hash": report["confirmation_hash"],
        }, ensure_ascii=False))
        return 0

    report = json.loads(args.report.read_text(encoding="utf-8"))
    result = apply_report(report, args.confirm, args.backup_dir)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
