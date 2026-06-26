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

# Dados persistentes que precisam viver AO LADO do .exe (não dentro dele): o app, quando
# congelado, lê/grava em <pasta do .exe>/data (ver core/database.BASE_DIR e _data_dir()).
# clientes.json é o catálogo Cliente→Regional→IP (editável sem recompilar) — copiado para dist/
# para já ficar disponível ao lado do .exe. As CREDENCIAIS (credentials.json) NÃO são copiadas:
# o .exe circula entre clientes e não pode carregar segredos; core.credentials.seed_files() cria
# um credentials.json VAZIO na 1ª execução e o operador digita suas contas no app.
PERSIST_DATA_FILES = ["clientes.json"]


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
    if not exe.exists():
        print("\nERRO: executável não encontrado em", exe)
        sys.exit(1)

    # Copia os dados persistentes (credenciais por regional) para dist/data, ao lado do .exe.
    dist_data = DIST / "data"
    for fname in PERSIST_DATA_FILES:
        src = ROOT / "data" / fname
        if src.exists():
            dist_data.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dist_data / fname)
            print(f"      Copiado {fname} -> {dist_data / fname}")
        else:
            print(f"      AVISO: {src} não existe — o autologin por regional pode pedir "
                  f"login manual no .exe. Crie data/{fname} antes do build.")

    mb = round(exe.stat().st_size / 1024 / 1024, 1)
    print(f"\n{'=' * 55}")
    print(f" Build concluído: {exe}  ({mb} MB)")
    print(f"{'=' * 55}\n")


if __name__ == "__main__":
    main()
