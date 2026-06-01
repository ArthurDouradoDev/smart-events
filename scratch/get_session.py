"""
scratch/get_session.py — CLI fino para renovação de sessão do iManager.

A lógica de fato vive em core/session_renew.py (compartilhada com o modo `main.py --get-session`
do executável). Este wrapper apenas parseia argumentos e delega.

Uso:
  python scratch/get_session.py --headless --module both --base-url <url> --session-file <path>
"""

import argparse
import sys
from pathlib import Path

# Permite rodar como script solto a partir de qualquer cwd.
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.session_renew import run


def main():
    parser = argparse.ArgumentParser(description="SmartEvents - Automação de Sessão")
    parser.add_argument("--headless", action="store_true", help="Executa o navegador em modo headless")
    parser.add_argument("--module", choices=["trace", "monitoring", "both"], default="both",
                        help="Módulo a renovar (trace, monitoring ou both)")
    parser.add_argument("--base-url", default="https://10.220.50.9:31943", help="URL base do iManager")
    parser.add_argument("--session-file", default=None, help="Caminho do arquivo de sessão de saída")
    parser.add_argument("--region", default="", help="Regional do OSS (SP, RJ, …) para escolher as credenciais")
    args = parser.parse_args()

    return run(
        headless=args.headless,
        module=args.module,
        base_url=args.base_url,
        session_file=args.session_file,
        region=args.region,
    )


if __name__ == "__main__":
    sys.exit(main())
