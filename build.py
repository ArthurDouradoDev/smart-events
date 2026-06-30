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
# clientes.json é o catálogo Cliente→Regional→IP (editável sem recompilar) — copiado para
# dist/main/data para já ficar disponível ao lado do .exe. As CREDENCIAIS (credentials.json) NÃO são copiadas:
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

    # ONEDIR: a saída é a pasta dist/main/ com o main.exe (e _internal/) dentro.
    app_dir = DIST / "main"
    exe = app_dir / "main.exe"
    if not exe.exists():
        print("\nERRO: executável não encontrado em", exe)
        sys.exit(1)

    # Copia os dados persistentes para data/ AO LADO do .exe (dentro de dist/main/), de onde o
    # app congelado lê/grava (core.database.BASE_DIR / credentials.data_dir() = pasta do .exe).
    dist_data = app_dir / "data"
    for fname in PERSIST_DATA_FILES:
        src = ROOT / "data" / fname
        if src.exists():
            dist_data.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dist_data / fname)
            print(f"      Copiado {fname} -> {dist_data / fname}")
        else:
            print(f"      AVISO: {src} não existe — o autologin por regional pode pedir "
                  f"login manual no .exe. Crie data/{fname} antes do build.")

    # Empacota a pasta inteira num .zip para envio a outras máquinas. Transferir a pasta como
    # ZIP é robusto: cada arquivo tem CRC próprio, então uma transferência corrompida falha
    # visível na extração — ao contrário do onefile, que corrompia silenciosamente e só
    # estourava "decompression error -1" ao abrir.
    print("\n[3/3] Gerando ZIP para distribuição...")
    zip_base = DIST / "SmartEvents"
    if (zip_base.with_suffix(".zip")).exists():
        (zip_base.with_suffix(".zip")).unlink()
    zip_path = shutil.make_archive(str(zip_base), "zip", root_dir=str(DIST), base_dir="main")
    zip_path = Path(zip_path)

    folder_mb = round(sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file()) / 1024 / 1024, 1)
    zip_mb = round(zip_path.stat().st_size / 1024 / 1024, 1)
    print(f"\n{'=' * 55}")
    print(f" Build concluído (onedir):")
    print(f"   Pasta: {app_dir}  ({folder_mb} MB)")
    print(f"   Abrir: {exe}")
    print(f"   Enviar: {zip_path}  ({zip_mb} MB)")
    print(f"{'=' * 55}\n")


if __name__ == "__main__":
    main()
