"""Build reproduzivel do SmartEvents.

    python build.py base
    python build.py distribution --events evento-a evento-b --format package
    python build.py distribution --events evento-a evento-b --format setup
    python build.py legacy-profile --profile vivo-barretos-2026

`base` roda o PyInstaller **uma vez por versao** e produz um build-base generico,
sem evento e sem cliente. `distribution` combina esse cache com um `.sepack` e,
no formato `setup`, compila um instalador novo **sem chamar o PyInstaller** — o
ganho arquitetural da Fase 3. `legacy-profile` preserva o fluxo por perfil durante
a migracao.

Codigos de saida: 0 sucesso, 2 argumento invalido, 3 selecao/pacote invalido,
4 build-base ausente ou adulterado, 5 falha de compilacao ou de smoke test.
"""

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

from core import authenticode
from core import base_build
from core import event_package as ep
from tools.prepare_installer_seed import load_profile


ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "main.spec"
LOCK = ROOT / "requirements-build.lock"
VERSION_FILE = ROOT / "VERSION"
ISCC = base_build.iscc_path(ROOT)

EXIT_OK = 0
EXIT_ARGUMENTS = 2
EXIT_SELECTION = 3
EXIT_BASE = 4
EXIT_BUILD = 5


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


def _version() -> str:
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not version:
        raise SystemExit("ERRO: VERSION esta vazio.")
    return version


def _source_state() -> tuple[str, bool]:
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True, encoding="utf-8"
        ).strip()
    )
    return commit, dirty


def _browsers_path(env: dict[str, str]) -> Path:
    browsers = Path(env.get("PLAYWRIGHT_BROWSERS_PATH", ""))
    if not browsers.is_dir():
        raise SystemExit("ERRO: defina PLAYWRIGHT_BROWSERS_PATH para o cache homologado.")
    return browsers


def _browser_revisions(browsers: Path) -> dict:
    """Revisoes homologadas dos navegadores, para o manifesto do cache."""
    revisions = {}
    for item in sorted(browsers.iterdir()):
        if item.is_dir() and "-" in item.name:
            name, _, revision = item.name.rpartition("-")
            revisions[name] = revision
    return revisions


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
    """Limpa um diretorio de trabalho apos confirmar que ele esta dentro do esperado.

    Nenhum comando desta ferramenta pode apagar `dist/base/<versao>`: o cache do
    build-base e o que evita rodar o PyInstaller de novo.
    """
    resolved = path.resolve()
    parent_resolved = parent.resolve()
    resolved.relative_to(parent_resolved)
    if resolved == parent_resolved:
        raise RuntimeError(f"Recusa em apagar o diretorio raiz: {resolved}")
    base = base_build.base_root(DIST).resolve()
    if resolved == base or base in resolved.parents or resolved in base.parents:
        raise RuntimeError(f"Recusa em apagar o cache do build-base: {resolved}")
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


def _verify_base_bundle(app_dir: Path) -> None:
    """O build-base nao pode carregar evento, VIP nem catalogo de cliente."""
    internal = app_dir / "_internal"
    seed = internal / "server_data"
    intruders = sorted(
        f"{name}/{item.name}"
        for name in ("events", "clientes", "vips")
        for item in (seed / name).glob("*.json")
    )
    if intruders:
        raise SystemExit("ERRO: cadastro especifico no build-base: " + ", ".join(intruders))
    if (internal / "data" / "clientes.json").exists():
        raise SystemExit("ERRO: catalogo de clientes embutido no build-base.")
    if list(internal.rglob("credentials.json")):
        raise SystemExit("ERRO: arquivo de credenciais foi embutido no build-base.")
    profile = json.loads((internal / "build-profile.json").read_text(encoding="utf-8"))
    if not profile.get("generic"):
        raise SystemExit("ERRO: o perfil embutido no build-base nao e generico.")
    if profile.get("runtime_data_dir") != base_build.RUNTIME_DATA_DIR:
        raise SystemExit(
            f"ERRO: build-base com pasta de dados inesperada: {profile.get('runtime_data_dir')!r}."
        )


# ── build-base ───────────────────────────────────────────────────────

def command_base(args) -> int:
    """PyInstaller uma vez por versao; nenhuma selecao de eventos dispara isto."""
    require_build_python()
    require_locked_dependencies()
    version = _version()

    destination = base_build.base_dir(version, DIST)
    if destination.exists() and not args.force:
        try:
            existing = base_build.load_base(version, DIST)
        except base_build.BaseBuildError as exc:
            raise SystemExit(
                f"ERRO: cache do build-base {version} invalido ({exc}). "
                "Use --force para reconstrui-lo."
            )
        print(f"Build-base {existing.version} ja existe e confere: {existing.root}")
        return EXIT_OK

    work = BUILD / "base" / version
    _replace_directory(work, BUILD)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    seed = base_build.prepare_generic_seed(work / "server_data", version)
    base_build.assert_generic_seed(seed)
    runtime_profile = work / "build-profile.json"
    runtime_profile.write_text(
        json.dumps(base_build.generic_profile(version), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    browsers = _browsers_path(env)
    env[base_build.BUILD_MODE_ENV] = base_build.BUILD_MODE_BASE
    env["SMARTEVENTS_SERVER_DATA_SEED"] = str(seed)
    env["SMARTEVENTS_BUILD_PROFILE_FILE"] = str(runtime_profile)
    env.pop("SMARTEVENTS_CLIENT_CATALOG_SEED", None)

    build_log = destination / "build.log"
    with build_log.open("w", encoding="utf-8") as log:
        run(
            [
                sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                "--distpath", str(destination),
                "--workpath", str(work / "pyinstaller"),
                str(SPEC),
            ],
            env=env,
            log=log,
        )

    app_dir = destination / base_build.APP_DIR_NAME
    exe = app_dir / base_build.EXECUTABLE_NAME
    if not exe.is_file():
        raise SystemExit(f"ERRO: executavel ausente: {exe}")
    _verify_base_bundle(app_dir)

    # Assinatura Authenticode do executavel (Fase 4): so acontece quando
    # signtool + certificado estao configurados nesta maquina. Sem isso, o
    # manifesto declara `signed: false` -- a build continua valida para
    # desenvolvimento, so nao pode ser chamada de "producao".
    signed = False
    auth_config = authenticode.load_config()
    if auth_config is not None:
        result = authenticode.sign_file(exe, auth_config)
        if not result.ok:
            raise SystemExit(f"ERRO: falha ao assinar {exe.name}: {result.message}")
        signed = True
        print(f"+ {exe.name} assinado (Authenticode)")

    # Diagnostico GENERICO: sem evento embutido, o self-test nao pode exigir
    # RoadShow, TIM nem qualquer cadastro de cliente.
    smoke_env = env.copy()
    smoke_env["SMARTEVENTS_DATA_DIR"] = str(work / "self-test-data")
    run([str(exe), "--self-test", "--report", str(destination / "self-test-base.json")],
        env=smoke_env)

    archive = base_build.make_base_archive(destination)
    commit, dirty = _source_state()
    manifest = base_build.write_manifest(
        destination,
        version,
        python_version=platform.python_version(),
        architecture=platform.architecture()[0],
        source_commit=commit,
        source_dirty=dirty,
        browsers=_browser_revisions(browsers),
        package_schema_version=ep.SCHEMA_VERSION,
        archive=archive,
        signed=signed,
    )
    freeze = subprocess.check_output(
        [sys.executable, "-m", "pip", "freeze", "--all"], text=True, encoding="utf-8"
    )
    (destination / "dependencies.txt").write_text(freeze, encoding="utf-8")

    print(f"Build-base pronto: {destination}")
    print(
        f"  {manifest['file_count']} arquivo(s) | "
        f"{manifest['uncompressed_bytes'] / (1024 * 1024):.1f} MiB descompactados | "
        f"payload {manifest['archive_bytes'] / (1024 * 1024):.1f} MiB"
    )
    return EXIT_OK


# ── distribuicao ─────────────────────────────────────────────────────

def _distribution_dir(name: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "eventos"
    directory = DIST / "distributions" / f"{slug}-{stamp}"
    _replace_directory(directory, DIST / "distributions")
    return directory


def _build_sepack(args, destination_dir: Path) -> tuple[Path, ep.PackagePreview]:
    source = args.source or None
    preview = ep.preview_package(
        source, args.events, name=args.name, vip_policy=args.vip_policy
    )
    if not preview.ok:
        for item in preview.errors:
            print(f"ERRO [{item.code}] {item.path}: {item.message}")
        raise SystemExit(EXIT_SELECTION)
    for message in preview.warnings:
        print(f"AVISO {message}")
    package = destination_dir / ep.suggested_filename(preview)
    ep.build_package(preview, package, force=True)
    return package, preview


def command_distribution(args) -> int:
    version = _version()
    try:
        base = base_build.load_base(args.base_version or version, DIST)
    except base_build.BaseBuildError as exc:
        if args.format == "setup":
            print(f"ERRO: {exc}")
            return EXIT_BASE
        base = None

    directory = _distribution_dir(args.name or "-".join(args.events))
    package, preview = _build_sepack(args, directory)
    metadata = {
        "schema_version": 1,
        "format": args.format,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": version,
        "events": list(preview_event_ids(preview)),
        "clients": list(preview.clients),
        "package": package.name,
        "package_sha256": sha256(package),
        "package_bytes": package.stat().st_size,
        "warnings": list(preview.warnings),
    }

    if args.format == "setup":
        # O PyInstaller NAO roda aqui: o programa vem do cache do build-base.
        installer_dir = directory / "installer"
        work = directory / "work"
        work.mkdir(parents=True, exist_ok=True)
        basename = f"Setup_SmartEvents_{re.sub(r'[^A-Za-z0-9]+', '_', preview.name).strip('_') or 'Eventos'}"
        auth_config = authenticode.load_config()
        try:
            result = base_build.compile_setup(
                base, package, installer_dir, work, setup_basename=basename, repo=ROOT,
                authenticode_config=auth_config,
            )
            inspection = base_build.inspect_setup(result, base, package, authenticode_config=auth_config)
        except base_build.BaseBuildError as exc:
            print(f"ERRO: {exc}")
            return EXIT_BUILD
        (directory / "iscc.log").write_text(result.log + "\n", encoding="utf-8")
        metadata.update({
            "base": base.summary(),
            "setup": result.setup.name,
            "setup_sha256": result.sha256,
            "setup_bytes": result.bytes,
            "executable_sha256": str(base.manifest.get("executable_sha256") or ""),
            "inspection": inspection,
        })
        if args.smoke:
            try:
                metadata["smoke"] = smoke_test_setup(result.setup, inspection["event_ids"], work)
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001 - o smoke reporta, nao derruba
                print(f"ERRO no smoke test: {exc}")
                return EXIT_BUILD

    (directory / "build-manifest.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    lines = [f"{metadata['package_sha256']}  {package.name}"]
    if metadata.get("setup"):
        lines.append(f"{metadata['setup_sha256']}  installer/{metadata['setup']}")
        lines.append(f"{metadata['executable_sha256']}  base/SmartEvents.exe")
    (directory / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Distribuicao pronta: {directory}")
    for line in lines:
        print("  " + line)
    return EXIT_OK


def preview_event_ids(preview: ep.PackagePreview) -> list[str]:
    return [str(event.get("id") or "") for event in preview.events]


def smoke_test_setup(setup: Path, expected_events: list[str], work: Path) -> dict:
    """Instalacao silenciosa isolada, conferencia dos relatorios e desinstalacao.

    Roda somente com `--smoke`, e apenas pela linha de comando: instalar de verdade
    e operacao de quem distribui, nunca efeito de um clique na interface.
    """
    target = work / "smoke-install"
    data = work / "smoke-data"
    for directory in (target, data):
        if directory.exists():
            shutil.rmtree(directory)
    log_path = work / "smoke-install.log"

    env = os.environ.copy()
    env["SMARTEVENTS_DATA_DIR"] = str(data)
    print(f"+ smoke: instalando em {target}")
    completed = subprocess.run(
        [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
         f"/DIR={target}", f"/LOG={log_path}"],
        env=env, text=True, encoding="utf-8", errors="replace", timeout=3600,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"A instalacao silenciosa falhou com o codigo {completed.returncode}.")

    executable = target / base_build.EXECUTABLE_NAME
    if not executable.is_file():
        raise RuntimeError(f"Executavel ausente apos a instalacao: {executable}")
    report = data / "diagnostics" / "installer-self-test.json"
    if not report.is_file():
        raise RuntimeError(f"Relatorio do diagnostico ausente: {report}")
    diagnostic = json.loads(report.read_text(encoding="utf-8"))
    if not diagnostic.get("ok"):
        failed = [item["name"] for item in diagnostic.get("checks", []) if not item.get("ok")]
        raise RuntimeError("O diagnostico reprovou em: " + ", ".join(failed))
    installed_events = {
        json.loads(item.read_text(encoding="utf-8")).get("id")
        for item in (data / "server_data" / "events").glob("*.json")
    }
    missing = sorted(set(expected_events) - installed_events)
    if missing:
        raise RuntimeError("Eventos ausentes apos a instalacao: " + ", ".join(missing))

    uninstaller = next(target.glob("unins*.exe"), None)
    if uninstaller is None:
        raise RuntimeError("Desinstalador ausente na instalacao de smoke test.")
    print("+ smoke: desinstalando")
    subprocess.run(
        [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
        env=env, text=True, timeout=1800,
    )
    if not data.is_dir():
        raise RuntimeError("A desinstalacao apagou os dados do operador.")
    return {
        "installed_events": sorted(installed_events),
        "self_test_ok": True,
        "data_preserved": True,
        "log": log_path.name,
    }


# ── fluxo legado por perfil ──────────────────────────────────────────

def command_legacy_profile(args) -> int:
    require_build_python()
    require_locked_dependencies()
    profile_file = _profile_path(args.profile)
    profile = load_profile(profile_file)
    version = _version()
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
    browsers = _browsers_path(env)

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
        env[base_build.BUILD_MODE_ENV] = base_build.BUILD_MODE_LEGACY
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

    commit, dirty = _source_state()
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
    return EXIT_OK


# ── linha de comando ─────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)

    base = commands.add_parser("base", help="Gera o build-base generico (roda o PyInstaller).")
    base.add_argument(
        "--force", action="store_true",
        help="Reconstroi o cache mesmo que ja exista e confira.",
    )
    base.set_defaults(handler=command_base)

    distribution = commands.add_parser(
        "distribution", help="Gera .sepack e, opcionalmente, o Setup a partir do build-base."
    )
    distribution.add_argument("--events", nargs="+", required=True, metavar="ID")
    distribution.add_argument("--name", default=None, help="Nome da distribuicao.")
    distribution.add_argument(
        "--format", choices=("package", "setup"), default="package",
    )
    distribution.add_argument(
        "--source", type=Path, default=None,
        help="Pasta server_data de origem (padrao: a do aplicativo).",
    )
    distribution.add_argument(
        "--vip-policy", choices=ep.VIP_POLICIES, default="auto", dest="vip_policy",
    )
    distribution.add_argument(
        "--base-version", default=None, dest="base_version",
        help="Versao do build-base a reutilizar (padrao: a do VERSION).",
    )
    distribution.add_argument(
        "--smoke", action="store_true",
        help="Instala silenciosamente num diretorio isolado, valida e desinstala.",
    )
    distribution.set_defaults(handler=command_distribution)

    legacy = commands.add_parser(
        "legacy-profile", help="Fluxo historico por perfil (roda o PyInstaller)."
    )
    legacy.add_argument("--profile", default="roadshow-tim")
    legacy.set_defaults(handler=command_legacy_profile)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # Compatibilidade: `build.py --profile <id>` continua funcionando durante a
    # migracao, redirecionado para o subcomando legado.
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0].startswith("-") and "--profile" in arguments:
        arguments = ["legacy-profile"] + arguments
    args = parser.parse_args(arguments)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
