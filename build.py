"""Build reproduzivel e isolado dos perfis instalaveis do SmartEvents."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.prepare_installer_seed import load_profile


ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "main.spec"
LOCK = ROOT / "requirements-build.lock"
VERSION_FILE = ROOT / "VERSION"
ISCC = ROOT / ".build-tools" / "InnoSetup7" / "ISCC.exe"


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


def _canonical_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def require_locked_dependencies() -> None:
    mismatches = []
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, expected = line.split("==", 1)
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = "ausente"
        if actual != expected:
            mismatches.append(f"{_canonical_package(name)}: esperado {expected}, atual {actual}")
    if mismatches:
        raise SystemExit("ERRO: ambiente de build fora do lock:\n- " + "\n- ".join(mismatches))
    run([sys.executable, "-m", "pip", "check"])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _profile_path(value: str) -> Path:
    candidate = Path(value)
    if candidate.suffix.casefold() != ".json":
        candidate = ROOT / "build_profiles" / f"{value}.json"
    elif not candidate.is_absolute():
        candidate = ROOT / candidate
    if not candidate.is_file():
        raise SystemExit(f"ERRO: perfil de build nao encontrado: {candidate}")
    return candidate.resolve()


def _replace_directory(path: Path, parent: Path) -> None:
    resolved = path.resolve()
    resolved.relative_to(parent.resolve())
    if resolved == parent.resolve():
        raise RuntimeError(f"Recusa em apagar o diretorio raiz: {resolved}")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _client_catalog(seed: Path, destination: Path) -> None:
    client_files = sorted((seed / "clientes").glob("*.json"))
    if len(client_files) != 1:
        raise SystemExit("ERRO: a semente deve conter exatamente um cliente.")
    client = json.loads(client_files[0].read_text(encoding="utf-8"))
    name = str(client.get("name") or client.get("id") or "").strip()
    raw_regions = client.get("regionais") or {}
    regions: dict[str, str] = {}
    if isinstance(raw_regions, list):
        for item in raw_regions:
            if not isinstance(item, dict):
                continue
            region = str(item.get("region") or "").strip().upper()
            url = str(item.get("ip") or item.get("base_url") or "").strip()
            if region and url:
                regions[region] = url
    elif isinstance(raw_regions, dict):
        regions = {
            str(region).strip().upper(): str(url).strip()
            for region, url in raw_regions.items()
            if str(region).strip() and str(url).strip()
        }
    if not name or not regions:
        raise SystemExit("ERRO: cliente/regional sem URL na semente.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                name: {
                    "name": name,
                    "logo": str(client.get("logo") or ""),
                    "regionais": regions,
                }
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _iss_string(value: object) -> str:
    return str(value).replace('"', '""')


def _installer_wrapper(
    destination: Path,
    profile: dict,
    version: str,
    app_dir: Path,
    installer_dir: Path,
) -> None:
    definitions = {
        "MyAppVersion": version,
        "MyAppName": profile["app_name"],
        "MyAppId": profile["app_id"],
        "MyInstallDirName": profile["install_dir_name"],
        "MyDataDirName": profile["runtime_data_dir"],
        "MyOutputBaseFilename": profile["setup_basename"],
        "MySourceDir": app_dir.as_posix(),
        "MyOutputDir": installer_dir.as_posix(),
        "MyPrerequisitesDir": (ROOT / "installer" / "prerequisites").as_posix(),
        "MyIconFile": (ROOT / "assets" / "logoSmartEvents.ico").as_posix(),
    }
    lines = [f'#define {name} "{_iss_string(value)}"' for name, value in definitions.items()]
    lines.append(f'#include "{_iss_string((ROOT / "installer" / "SmartEvents.iss").as_posix())}"')
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _verify_bundle(app_dir: Path, profile: dict) -> None:
    internal = app_dir / "_internal"
    embedded_profile = json.loads(
        (internal / "build-profile.json").read_text(encoding="utf-8")
    )
    if embedded_profile.get("id") != profile["id"]:
        raise SystemExit("ERRO: perfil incorreto no bundle.")
    catalog = json.loads((internal / "data" / "clientes.json").read_text(encoding="utf-8"))
    if set(catalog) != {profile["client"]}:
        raise SystemExit(f"ERRO: catalogo indevido no bundle: {sorted(catalog)}")
    seed_manifest = json.loads(
        (internal / "server_data" / "seed-manifest.json").read_text(encoding="utf-8")
    )
    if set(seed_manifest.get("profile", {}).get("event_ids") or []) != set(profile["event_ids"]):
        raise SystemExit("ERRO: eventos incorretos no bundle.")
    if list(internal.rglob("credentials.json")):
        raise SystemExit("ERRO: arquivo de credenciais foi embutido no pacote.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="roadshow-tim")
    args = parser.parse_args()

    require_build_python()
    require_locked_dependencies()
    profile_file = _profile_path(args.profile)
    profile = load_profile(profile_file)
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not version:
        raise SystemExit("ERRO: VERSION esta vazio.")
    if not ISCC.is_file():
        raise SystemExit(f"ERRO: compilador Inno Setup ausente: {ISCC}")

    profile_id = str(profile["id"])
    profile_dist = DIST / profile_id
    profile_build = BUILD / profile_id
    _replace_directory(profile_dist, DIST)
    _replace_directory(profile_build, BUILD)

    app_dir = profile_dist / "SmartEvents"
    artifacts = profile_dist / "artifacts"
    installer_dir = profile_dist / "installer"
    artifacts.mkdir()
    installer_dir.mkdir()
    seed = profile_build / "installer_seed"
    catalog = profile_build / "catalog" / "clientes.json"
    runtime_profile = profile_build / "build-profile.json"
    runtime_profile.write_text(
        json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    env = os.environ.copy()
    browsers = Path(env.get("PLAYWRIGHT_BROWSERS_PATH", ""))
    if not browsers.is_dir():
        raise SystemExit("ERRO: defina PLAYWRIGHT_BROWSERS_PATH para o cache homologado.")

    build_log = artifacts / "build.log"
    with build_log.open("w", encoding="utf-8") as log:
        run(
            [
                sys.executable,
                "-m",
                "tools.prepare_installer_seed",
                "--source",
                "server_data",
                "--output",
                str(seed),
                "--profile-file",
                str(profile_file),
            ],
            env=env,
            log=log,
        )
        _client_catalog(seed, catalog)
        env["SMARTEVENTS_SERVER_DATA_SEED"] = str(seed)
        env["SMARTEVENTS_CLIENT_CATALOG_SEED"] = str(catalog)
        env["SMARTEVENTS_BUILD_PROFILE_FILE"] = str(runtime_profile)
        run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--distpath",
                str(profile_dist),
                "--workpath",
                str(profile_build / "pyinstaller"),
                str(SPEC),
            ],
            env=env,
            log=log,
        )

    exe = app_dir / "SmartEvents.exe"
    if not exe.is_file():
        raise SystemExit(f"ERRO: executavel ausente: {exe}")
    _verify_bundle(app_dir, profile)

    readme = ROOT / str(profile["operator_readme"])
    if not readme.is_file():
        raise SystemExit(f"ERRO: README do perfil ausente: {readme}")
    shutil.copy2(readme, app_dir / "LEIA-ME.txt")
    shutil.copy2(profile_file, artifacts / profile_file.name)
    shutil.copy2(seed / "seed-manifest.json", artifacts / "seed-manifest.json")
    freeze = subprocess.check_output(
        [sys.executable, "-m", "pip", "freeze", "--all"], text=True, encoding="utf-8"
    )
    (artifacts / "dependencies.txt").write_text(freeze, encoding="utf-8")
    shutil.copy2(LOCK, artifacts / LOCK.name)

    smoke_data = profile_build / "self-test-data"
    smoke_env = env.copy()
    smoke_env["SMARTEVENTS_DATA_DIR"] = str(smoke_data)
    run(
        [
            str(exe),
            "--self-test",
            "--report",
            str(artifacts / "self-test-build-machine.json"),
        ],
        env=smoke_env,
    )

    archive = Path(
        shutil.make_archive(
            str(profile_dist / profile["artifact_basename"]),
            "zip",
            profile_dist,
            "SmartEvents",
        )
    )
    wrapper = profile_build / "installer-profile.iss"
    _installer_wrapper(wrapper, profile, version, app_dir, installer_dir)
    with build_log.open("a", encoding="utf-8") as log:
        run([str(ISCC), "/Qp", str(wrapper)], env=env, log=log)

    setup = installer_dir / f"{profile['setup_basename']}.exe"
    if not setup.is_file():
        raise SystemExit(f"ERRO: instalador ausente: {setup}")

    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True, encoding="utf-8"
        ).strip()
    )
    metadata = {
        "application": profile["app_name"],
        "profile": profile_id,
        "client": profile["client"],
        "events": profile["event_ids"],
        "version": version,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "architecture": platform.architecture()[0],
        "setup": setup.name,
        "setup_sha256": sha256(setup),
        "zip": archive.name,
        "zip_sha256": sha256(archive),
        "executable_sha256": sha256(exe),
        "runtime_data_dir": profile["runtime_data_dir"],
        "app_id": profile["app_id"],
        "playwright_browsers_path": str(browsers),
        "source_commit": commit,
        "source_dirty": dirty,
    }
    manifest_path = artifacts / "build-manifest.json"
    manifest_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (profile_dist / "SHA256SUMS.txt").write_text(
        f"{metadata['setup_sha256']}  installer/{setup.name}\n"
        f"{metadata['zip_sha256']}  {archive.name}\n"
        f"{metadata['executable_sha256']}  SmartEvents/SmartEvents.exe\n",
        encoding="utf-8",
    )
    print(f"Build aprovado: {setup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
