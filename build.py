"""Build reproduzivel do pacote ONEDIR do SmartEvents.

Requer CPython 3.12 x64 e as versoes de ``requirements-build.lock``.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "main.spec"
LOCK = ROOT / "requirements-build.lock"
VERSION_FILE = ROOT / "VERSION"
APP_DIR = DIST / "SmartEvents"


def run(command: list[str], *, env: dict[str, str] | None = None, log=None) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        stdout=log or None,
        stderr=subprocess.STDOUT if log else None,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        raise SystemExit(completed.returncode)


def require_build_python() -> None:
    if sys.version_info[:2] != (3, 12):
        raise SystemExit(
            f"ERRO: use CPython 3.12 x64; encontrado {platform.python_version()}."
        )
    if platform.architecture()[0] != "64bit":
        raise SystemExit("ERRO: o build requer Python x64.")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    require_build_python()
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not version:
        raise SystemExit("ERRO: VERSION esta vazio.")

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    artifacts = DIST / "artifacts"
    artifacts.mkdir()
    seed = BUILD / "installer_seed"
    build_log = artifacts / "build.log"

    env = os.environ.copy()
    env["SMARTEVENTS_SERVER_DATA_SEED"] = str(seed)
    browsers = Path(env.get("PLAYWRIGHT_BROWSERS_PATH", ""))
    if not browsers.is_dir():
        raise SystemExit("ERRO: defina PLAYWRIGHT_BROWSERS_PATH para o cache homologado.")

    with build_log.open("w", encoding="utf-8") as log:
        run(
            [sys.executable, "-m", "tools.prepare_installer_seed", "--source", "server_data", "--output", str(seed)],
            env=env,
            log=log,
        )
        run(
            [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)],
            env=env,
            log=log,
        )

    exe = APP_DIR / "SmartEvents.exe"
    if not exe.is_file():
        raise SystemExit(f"ERRO: executavel ausente: {exe}")

    shutil.copy2(ROOT / "installer" / "README-operador.txt", APP_DIR / "LEIA-ME.txt")
    freeze = subprocess.check_output(
        [sys.executable, "-m", "pip", "freeze", "--all"], text=True, encoding="utf-8"
    )
    (artifacts / "dependencies.txt").write_text(freeze, encoding="utf-8")
    shutil.copy2(LOCK, artifacts / LOCK.name)

    smoke_data = BUILD / "self-test-data"
    if smoke_data.exists():
        shutil.rmtree(smoke_data)
    smoke_env = env.copy()
    smoke_env["SMARTEVENTS_DATA_DIR"] = str(smoke_data)
    run(
        [str(exe), "--self-test", "--report", str(artifacts / "self-test-build-machine.json")],
        env=smoke_env,
    )

    archive = shutil.make_archive(str(DIST / "SmartEvents_RoadShow"), "zip", DIST, "SmartEvents")
    metadata = {
        "application": "SmartEvents",
        "version": version,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "architecture": platform.architecture()[0],
        "pyinstaller_package_sha256": sha256(Path(archive)),
        "executable_sha256": sha256(exe),
        "playwright_browsers_path": str(browsers),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, encoding="utf-8"
        ).strip(),
        "source_dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True, encoding="utf-8"
            ).strip()
        ),
    }
    (artifacts / "build-manifest.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Build aprovado: {exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
