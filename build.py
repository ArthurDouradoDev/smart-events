"""
build.py — Gera o executável do SmartEvents.

Uso:
    python build.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
SPEC = ROOT / "main.spec"


def main():
    if not SPEC.exists():
        print(f"ERRO: {SPEC} não encontrado. Execute na raiz do projeto.")
        sys.exit(1)

    print("=" * 55)
    print(" SmartEvents — Build")
    print("=" * 55)

    # 1. Limpa dist/
    if DIST.exists():
        print(f"\n[1/2] Removendo {DIST} ...")
        shutil.rmtree(DIST)
        print("      OK")
    else:
        print(f"\n[1/2] {DIST} não existe, nada a limpar.")

    # 2. PyInstaller
    print("\n[2/2] Gerando executável (pode levar alguns minutos)...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)],
        cwd=str(ROOT),
    )

    if result.returncode != 0:
        print("\nERRO: PyInstaller encerrou com código", result.returncode)
        sys.exit(result.returncode)

    exe = DIST / "main.exe"
    if exe.exists():
        mb = round(exe.stat().st_size / 1024 / 1024, 1)
        print(f"\n{'=' * 55}")
        print(f" Build concluído: {exe}  ({mb} MB)")
        print(f"{'=' * 55}\n")
    else:
        print("\nERRO: executável não encontrado em", exe)
        sys.exit(1)


if __name__ == "__main__":
    main()
