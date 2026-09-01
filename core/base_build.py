"""Build-base reutilizavel do SmartEvents e geracao do Setup a partir dele.

A Fase 3 separa dois ciclos que estavam colados:

- o **build-base** roda o PyInstaller **uma vez por versao**. Ele e generico: nao
  carrega evento, VIP nem catalogo de cliente. O manifesto (``base-manifest.json``)
  e a chave do cache e descreve cada arquivo do bundle;
- a **distribuicao** combina esse base ja pronto com um ``.sepack`` e compila um
  ``Setup.exe`` novo. Selecionar eventos **nunca** dispara o PyInstaller.

Este modulo e a fronteira entre os dois. Ele e importado por ``build.py`` e por
``core/distribution_service.py`` — nenhum dos dois entra no bundle do PyInstaller,
entao o gerador continua fora do executavel distribuido (decisao 8 do plano).

Todo texto e ASCII: as mesmas mensagens vao para o console do operador (cp1252 no
Windows), para o log de build e para a interface do estudio.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core import authenticode

logger = logging.getLogger(__name__)


# ── Contrato do build-base ───────────────────────────────────────────

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_NAME = "base-manifest.json"
ARCHIVE_NAME = "SmartEvents-base.zip"
APP_DIR_NAME = "SmartEvents"
EXECUTABLE_NAME = "SmartEvents.exe"
SEED_MANIFEST_NAME = "seed-manifest.json"

# ``AppId`` estavel do SmartEvents generico: instalar uma distribuicao nova
# **atualiza** a mesma aplicacao em vez de criar uma instalacao paralela. E o
# mesmo valor default do ``installer/SmartEvents.iss``; um ``AppId`` exclusivo
# continua possivel apenas pelo caminho legado por perfil.
STABLE_APP_ID = "72D1D22D-A46F-42C4-8D5C-F82435BDD0AE"
RUNTIME_DATA_DIR = "SmartEvents"
INSTALL_DIR_NAME = "SmartEvents"
APP_NAME = "SmartEvents"

# Pastas que a instalacao generica precisa ter mesmo sem nenhum pacote aplicado.
SEED_DIRS = ("events", "clientes", "vips", "logos")

BUILD_MODE_ENV = "SMARTEVENTS_BUILD_MODE"
BUILD_MODE_BASE = "base"
BUILD_MODE_LEGACY = "legacy"

_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}$")


class BaseBuildError(Exception):
    """Cache de build-base ausente, incompleto ou divergente do manifesto."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def sha256_of(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checked_version(value: str) -> str:
    """Recusa uma versao que nao possa virar componente de caminho com seguranca."""
    text = str(value or "").strip()
    if not _VERSION.fullmatch(text):
        raise BaseBuildError(f"Versao invalida para o build-base: {value!r}.")
    return text


def base_root(root: Path | str | None = None) -> Path:
    """Raiz do cache de build-base: ``dist/base``."""
    return (Path(root) if root is not None else repo_root() / "dist") / "base"


def base_dir(version: str, root: Path | str | None = None) -> Path:
    return base_root(root) / checked_version(version)


def iscc_path(root: Path | str | None = None) -> Path:
    base = Path(root) if root is not None else repo_root()
    return base / ".build-tools" / "InnoSetup7" / "ISCC.exe"


# ── Semente generica ─────────────────────────────────────────────────

def generic_profile(version: str) -> dict:
    """Perfil de runtime do build-base: identidade estavel e nenhum evento."""
    return {
        "schema_version": 1,
        "id": "smartevents-base",
        "profile_name": "SmartEvents",
        "app_name": APP_NAME,
        "client": "",
        "event_ids": [],
        "generic": True,
        "artifact_basename": "SmartEvents",
        "setup_basename": "Setup_SmartEvents",
        "runtime_data_dir": RUNTIME_DATA_DIR,
        "install_dir_name": INSTALL_DIR_NAME,
        "app_id": STABLE_APP_ID,
        "version": checked_version(version),
    }


def prepare_generic_seed(destination: Path | str, version: str) -> Path:
    """Semente embutida no build-base: manifesto generico e nenhum cadastro.

    O bundle nao carrega evento, cliente nem VIP; o que ele leva e a marca de que
    a semente e generica. As pastas vazias sao criadas no primeiro boot por
    ``core.seed.ensure_data_layout`` — o PyInstaller nao empacota diretorio vazio.
    """
    path = Path(destination)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    (path / SEED_MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema_version": 3,
                "generic": True,
                "created_at_utc": _now(),
                "profile": generic_profile(version),
                "events": [],
                "clientes": [],
                "vips": [],
                "logos": [],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def assert_generic_seed(path: Path | str) -> Path:
    """Trava o ganho da fase: nenhum evento/cliente/VIP viaja dentro do build-base.

    Chamado tanto por ``build.py base`` quanto pelo proprio ``main.spec``, para que
    um build-base nunca possa ser produzido com semente de perfil por engano.
    """
    root = Path(path)
    if not root.is_dir():
        raise BaseBuildError(f"Semente generica ausente: {root}.")
    manifest_path = root / SEED_MANIFEST_NAME
    if not manifest_path.is_file():
        raise BaseBuildError(f"Semente sem {SEED_MANIFEST_NAME}: {root}.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BaseBuildError(f"Semente com manifesto ilegivel: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("generic") is not True:
        raise BaseBuildError(f"A semente do build-base nao esta marcada como generica: {root}.")
    intruders = sorted(
        f"{name}/{item.name}"
        for name in ("events", "clientes", "vips")
        for item in (root / name).glob("*.json")
    )
    if intruders:
        raise BaseBuildError(
            "O build-base nao pode embutir cadastro especifico: " + ", ".join(intruders)
        )
    return root


# ── Manifesto do cache ───────────────────────────────────────────────

@dataclass(frozen=True)
class BaseBuild:
    """Um build-base ja validado contra o proprio manifesto."""

    root: Path
    manifest: dict

    @property
    def version(self) -> str:
        return str(self.manifest.get("version") or "")

    @property
    def app_dir(self) -> Path:
        return self.root / APP_DIR_NAME

    @property
    def executable(self) -> Path:
        return self.app_dir / EXECUTABLE_NAME

    @property
    def archive(self) -> Path | None:
        name = str(self.manifest.get("archive") or "")
        return (self.root / name) if name else None

    @property
    def built_at_utc(self) -> str:
        return str(self.manifest.get("built_at_utc") or "")

    @property
    def uncompressed_bytes(self) -> int:
        return int(self.manifest.get("uncompressed_bytes") or 0)

    def summary(self) -> dict:
        """O que a interface do estudio mostra sobre o base usado."""
        return {
            "version": self.version,
            "built_at_utc": self.built_at_utc,
            "source_commit": str(self.manifest.get("source_commit") or ""),
            "source_dirty": bool(self.manifest.get("source_dirty")),
            "signed": bool(self.manifest.get("signed")),
            "executable_sha256": str(self.manifest.get("executable_sha256") or ""),
            "uncompressed_bytes": self.uncompressed_bytes,
            "file_count": int(self.manifest.get("file_count") or 0),
        }


def _bundle_files(app_dir: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for item in sorted(app_dir.rglob("*")):
        if item.is_file():
            files[item.relative_to(app_dir).as_posix()] = sha256_of(item)
    return files


def write_manifest(
    root: Path | str,
    version: str,
    *,
    python_version: str,
    architecture: str,
    source_commit: str,
    source_dirty: bool,
    browsers: dict | None = None,
    package_schema_version: int = 1,
    archive: Path | str | None = None,
    signed: bool = False,
) -> dict:
    """Grava o ``base-manifest.json`` com o hash de cada arquivo do bundle."""
    base = Path(root)
    app_dir = base / APP_DIR_NAME
    if not (app_dir / EXECUTABLE_NAME).is_file():
        raise BaseBuildError(f"Executavel ausente no build-base: {app_dir / EXECUTABLE_NAME}")

    files = _bundle_files(app_dir)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": "smartevents-base",
        "version": checked_version(version),
        "built_at_utc": _now(),
        "python": python_version,
        "architecture": architecture,
        "source_commit": source_commit,
        "source_dirty": bool(source_dirty),
        "playwright_browsers": dict(browsers or {}),
        "package_schema_version": int(package_schema_version),
        # Fase 4: builds sem certificado configurado saem `signed: false`, sem
        # fingir confianca -- a ausencia de certificado nao bloqueia o build.
        "signed": bool(signed),
        "app_id": STABLE_APP_ID,
        "runtime_data_dir": RUNTIME_DATA_DIR,
        "app_dir": APP_DIR_NAME,
        "executable_sha256": files[EXECUTABLE_NAME],
        "file_count": len(files),
        "uncompressed_bytes": sum(
            (app_dir / name).stat().st_size for name in files
        ),
        "files": files,
    }
    if archive is not None:
        archive_path = Path(archive)
        manifest["archive"] = archive_path.name
        manifest["archive_sha256"] = sha256_of(archive_path)
        manifest["archive_bytes"] = archive_path.stat().st_size

    (base / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def make_base_archive(root: Path | str) -> Path:
    """ZIP do bundle, gerado uma unica vez no comando ``base``.

    Ele NAO entra no Setup: o Inno Setup exige a flag ``external`` junto de
    ``extractarchive``, o que faria o ZIP viajar fora do ``Setup.exe`` e quebraria
    o requisito de um unico arquivo autocontido (ver ``installer/SmartEvents.iss``).
    O que ele continua sendo e o artefato compartilhavel do build-base — para
    copiar o cache para outra maquina de release sem recompilar — e a entrada do
    manifesto que prova que o cache nao foi trocado.
    """
    base = Path(root)
    app_dir = base / APP_DIR_NAME
    destination = base / ARCHIVE_NAME
    staging = base / f".{ARCHIVE_NAME}.tmp"
    staging.unlink(missing_ok=True)
    with zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for item in sorted(app_dir.rglob("*")):
            if item.is_file():
                archive.write(item, item.relative_to(app_dir).as_posix())
    os.replace(staging, destination)
    return destination


def load_base(
    version: str | None = None,
    root: Path | str | None = None,
    *,
    verify: bool = True,
) -> BaseBuild:
    """Le e valida o cache. Um manifesto que nao bate com o disco e recusado.

    ``verify=False`` troca a conferencia arquivo a arquivo por ``verify_quick``:
    a interface consulta as capacidades a cada abertura de modal e nao pode
    reprocessar centenas de MiB por isso. Qualquer geracao de Setup usa a
    verificacao completa, imediatamente antes de compilar.
    """
    if version is None:
        available = list_bases(root)
        if not available:
            raise BaseBuildError(
                "Nenhum build-base encontrado. Execute: python build.py base"
            )
        version = available[-1]
    directory = base_dir(version, root)
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        raise BaseBuildError(
            f"Build-base {version} sem {MANIFEST_NAME}. Execute: python build.py base"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BaseBuildError(f"Manifesto do build-base ilegivel: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("kind") != "smartevents-base":
        raise BaseBuildError(f"Manifesto do build-base invalido: {manifest_path.name}")
    if str(manifest.get("version") or "") != str(version):
        raise BaseBuildError(
            f"Manifesto declara a versao {manifest.get('version')!r}, mas esta em {version!r}."
        )

    build = BaseBuild(directory, manifest)
    if verify:
        verify_base(build)
    else:
        verify_quick(build)
    return build


def verify_quick(build: BaseBuild) -> BaseBuild:
    """Conferencia barata: o bundle existe e o executavel e o do manifesto."""
    if not build.app_dir.is_dir():
        raise BaseBuildError(f"Bundle ausente no build-base: {build.app_dir}")
    executable = build.executable
    if not executable.is_file():
        raise BaseBuildError(f"Executavel ausente no build-base: {executable.name}")
    declared = str(build.manifest.get("executable_sha256") or "")
    if not declared:
        raise BaseBuildError("Manifesto do build-base sem o hash do executavel.")
    if sha256_of(executable) != declared:
        raise BaseBuildError(f"Build-base adulterado: {executable.name} tem hash divergente.")
    return build


def verify_base(build: BaseBuild) -> BaseBuild:
    """Confere cada arquivo do bundle contra o manifesto, sem tolerar sobra."""
    app_dir = build.app_dir
    if not app_dir.is_dir():
        raise BaseBuildError(f"Bundle ausente no build-base: {app_dir}")
    declared = build.manifest.get("files")
    if not isinstance(declared, dict) or not declared:
        raise BaseBuildError("Manifesto do build-base sem a lista de arquivos.")

    actual = _bundle_files(app_dir)
    missing = sorted(set(declared) - set(actual))
    if missing:
        raise BaseBuildError(
            f"Build-base incompleto ({len(missing)} arquivo(s) ausente(s)): "
            + ", ".join(missing[:5])
        )
    extra = sorted(set(actual) - set(declared))
    if extra:
        raise BaseBuildError(
            f"Build-base com arquivo nao declarado ({len(extra)}): " + ", ".join(extra[:5])
        )
    changed = sorted(name for name, digest in declared.items() if actual[name] != digest)
    if changed:
        raise BaseBuildError(
            f"Build-base adulterado ({len(changed)} arquivo(s) com hash divergente): "
            + ", ".join(changed[:5])
        )

    archive = build.archive
    if archive is not None:
        if not archive.is_file():
            raise BaseBuildError(f"Payload pre-comprimido ausente: {archive.name}")
        if sha256_of(archive) != str(build.manifest.get("archive_sha256") or ""):
            raise BaseBuildError(f"Payload pre-comprimido adulterado: {archive.name}")
    return build


def list_bases(root: Path | str | None = None) -> list[str]:
    """Versoes com manifesto presente, em ordem crescente."""
    directory = base_root(root)
    if not directory.is_dir():
        return []
    versions = []
    for item in sorted(directory.iterdir()):
        if item.is_dir() and (item / MANIFEST_NAME).is_file() and _VERSION.fullmatch(item.name):
            versions.append(item.name)
    versions.sort(key=lambda value: tuple(int(part) for part in value.split(".")))
    return versions


def describe(
    version: str | None = None,
    root: Path | str | None = None,
    *,
    repo: Path | str | None = None,
) -> dict:
    """Estado do host para as ``capabilities`` do estudio.

    Nunca levanta: a interface precisa mostrar o motivo, e nao um erro 500.
    """
    compiler = iscc_path(repo)
    iscc_ready = compiler.is_file()
    missing_iscc = "" if iscc_ready else f"Compilador Inno Setup ausente: {compiler.name}."
    try:
        # Conferencia barata: a tela consulta as capacidades a cada abertura, e a
        # verificacao completa (hash de cada arquivo do bundle) roda no momento
        # que importa — imediatamente antes de compilar o Setup.
        build = load_base(version, root, verify=False)
    except BaseBuildError as exc:
        return {
            "base_ready": False,
            "base_version": "",
            "base_built_at_utc": "",
            "base": None,
            "iscc_ready": iscc_ready,
            "reason": " ".join(filter(None, (str(exc), missing_iscc))),
        }
    return {
        "base_ready": True,
        "base_version": build.version,
        "base_built_at_utc": build.built_at_utc,
        "base": build.summary(),
        "iscc_ready": iscc_ready,
        "reason": missing_iscc,
    }


# ── Setup completo a partir do build-base ────────────────────────────

def _iss_string(value: object) -> str:
    return str(value).replace('"', '""')


def wrapper_definitions(
    build: BaseBuild,
    package: Path | str,
    output_dir: Path | str,
    *,
    setup_basename: str,
    repo: Path | str | None = None,
    authenticode_config: authenticode.AuthenticodeConfig | None = None,
) -> dict:
    """Definicoes do wrapper ``.iss``. Nada aqui vem do navegador (decisao 6)."""
    root = Path(repo) if repo is not None else repo_root()
    package_path = Path(package)
    definitions = {
        "MyAppVersion": build.version,
        "MyAppName": APP_NAME,
        "MyAppId": STABLE_APP_ID,
        "MyInstallDirName": INSTALL_DIR_NAME,
        "MyDataDirName": RUNTIME_DATA_DIR,
        "MyOutputBaseFilename": setup_basename,
        "MySourceDir": build.app_dir.as_posix(),
        "MyOutputDir": Path(output_dir).as_posix(),
        "MyPrerequisitesDir": (root / "installer" / "prerequisites").as_posix(),
        "MyIconFile": (root / "assets" / "logoSmartEvents.ico").as_posix(),
        "MyEventPackage": package_path.as_posix(),
        "MyEventPackageName": package_path.name,
        "MyEventPackageSha256": sha256_of(package_path),
        "MyExpectedEventIds": ",".join(_package_event_ids(package_path)),
        "MyBaseUncompressedSize": str(build.uncompressed_bytes),
    }
    if authenticode_config is not None:
        # So o NOME entra no `.iss` (via `#define`); o comando com a senha vira
        # argumento `/S<nome>=...` do ISCC, nunca texto gravado em disco.
        definitions["MySignTool"] = authenticode.ISS_SIGN_TOOL_NAME
    return definitions


def _package_event_ids(package: Path | str) -> list[str]:
    """Ids do ``.sepack``, lidos do proprio manifesto validado do pacote."""
    from core import event_package as ep

    inspection = ep.validate_package(package)
    if not inspection.ok or inspection.manifest is None:
        messages = "; ".join(item.message for item in inspection.errors[:3])
        raise BaseBuildError(f"Pacote de eventos invalido: {messages or 'sem detalhes'}")
    return list(inspection.manifest.event_ids)


def write_wrapper(
    destination: Path | str,
    definitions: dict,
    *,
    repo: Path | str | None = None,
) -> Path:
    root = Path(repo) if repo is not None else repo_root()
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'#define {name} "{_iss_string(value)}"' for name, value in definitions.items()]
    lines.append(f'#include "{_iss_string((root / "installer" / "SmartEvents.iss").as_posix())}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@dataclass(frozen=True)
class SetupResult:
    setup: Path
    sha256: str
    bytes: int
    wrapper: Path
    definitions: dict
    log: str


def compile_setup(
    build: BaseBuild,
    package: Path | str,
    output_dir: Path | str,
    work_dir: Path | str,
    *,
    setup_basename: str,
    repo: Path | str | None = None,
    timeout: float = 3600.0,
    authenticode_config: authenticode.AuthenticodeConfig | None = None,
) -> SetupResult:
    """Compila o Setup **sem** chamar o PyInstaller: o programa ja existe no cache.

    O build-base e reverificado imediatamente antes da compilacao — um cache que
    mudou entre a selecao e a geracao nunca vira instalador. Quando
    ``authenticode_config`` e informado, o proprio Inno Setup assina o
    ``Setup.exe`` e o desinstalador durante a compilacao (``SignTool=``); sem
    ele, o Setup sai sem assinatura Authenticode.
    """
    verify_base(build)
    compiler = iscc_path(repo)
    if not compiler.is_file():
        raise BaseBuildError(f"Compilador Inno Setup ausente: {compiler}")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    definitions = wrapper_definitions(
        build, package, output, setup_basename=setup_basename, repo=repo,
        authenticode_config=authenticode_config,
    )
    wrapper = write_wrapper(Path(work_dir) / "distribution.iss", definitions, repo=repo)

    command = [str(compiler)]
    if authenticode_config is not None:
        # Argumento de processo do ISCC, nunca escrito no `.iss` nem em log
        # (`run()` do build.py NAO e usado aqui de proposito).
        command.append(authenticode.iss_sign_tool_definition(authenticode_config))
    command.extend(["/Qp", str(wrapper)])

    completed = subprocess.run(
        command,
        cwd=str(Path(repo) if repo is not None else repo_root()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    log = ((completed.stdout or "") + (completed.stderr or "")).strip()
    if completed.returncode != 0:
        raise BaseBuildError(
            f"ISCC falhou com o codigo {completed.returncode}: {log[-2000:] or 'sem saida'}"
        )

    setup = output / f"{setup_basename}.exe"
    if not setup.is_file():
        raise BaseBuildError(f"Instalador ausente apos a compilacao: {setup.name}")
    size = setup.stat().st_size
    if size <= 0:
        raise BaseBuildError(f"Instalador vazio: {setup.name}")
    return SetupResult(
        setup=setup,
        sha256=sha256_of(setup),
        bytes=size,
        wrapper=wrapper,
        definitions=definitions,
        log=log,
    )


def inspect_setup(
    result: SetupResult,
    build: BaseBuild,
    package: Path | str,
    *,
    authenticode_config: authenticode.AuthenticodeConfig | None = None,
) -> dict:
    """Etapa ``testing``: confere o que foi produzido, sem instalar nada.

    A instalacao silenciosa de verdade e um smoke test de quem distribui
    (``build.py distribution --format setup --smoke``); ela nunca pode acontecer
    por um clique comum na interface, porque instalaria na maquina do operador.
    """
    from core import event_package as ep

    inspection = ep.validate_package(package)
    if not inspection.ok:
        messages = "; ".join(item.message for item in inspection.errors[:3])
        raise BaseBuildError(f"Pacote de eventos invalido apos a compilacao: {messages}")
    verify_base(build)

    # O Setup carrega o bundle inteiro; um artefato menor que o executavel
    # denuncia uma compilacao que nao incluiu o programa.
    minimum = build.executable.stat().st_size
    if result.bytes < minimum:
        raise BaseBuildError(
            f"Instalador menor que o proprio executavel ({result.bytes} < {minimum} bytes)."
        )
    lowered = result.log.lower()
    if "error" in lowered and "0 error" not in lowered:
        raise BaseBuildError(f"O log do ISCC reporta erro: {result.log[-500:]}")

    setup_signed = False
    if authenticode_config is not None:
        setup_signed = authenticode.verify_file(
            result.setup, signtool_path=authenticode_config.signtool_path
        ).ok

    return {
        "setup": result.setup.name,
        "setup_bytes": result.bytes,
        "setup_sha256": result.sha256,
        "setup_signed": setup_signed,
        "package": Path(package).name,
        "package_sha256": sha256_of(package),
        "event_ids": list(inspection.manifest.event_ids),
        "base_version": build.version,
        "base_executable_sha256": str(build.manifest.get("executable_sha256") or ""),
    }
