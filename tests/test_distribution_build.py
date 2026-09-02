"""Fase 3 â€” build-base reutilizavel e instalador completo por selecao.

O ganho arquitetural da fase e um so: **selecionar eventos nunca pode disparar o
PyInstaller**. O programa e compilado uma vez por versao (`build.py base`), fica
num cache verificavel por manifesto e e reaproveitado por qualquer distribuicao.

Nenhum teste aqui compila de verdade (nem PyInstaller, nem ISCC): eles trabalham
sobre um build-base falso montado em `tmp_path` e sobre o texto do `.iss`, que e o
contrato que o Inno Setup executa na maquina do operador.
"""

import json
import re
from pathlib import Path

import pytest

from core import base_build
from core import distribution_service as ds
from core import event_package as ep


REPO_ROOT = Path(__file__).parents[1]
ISS_PATH = REPO_ROOT / "installer" / "SmartEvents.iss"
SPEC_PATH = REPO_ROOT / "main.spec"


def _iss() -> str:
    return ISS_PATH.read_text(encoding="utf-8")


# â”€â”€ Construtores â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _event(event_id, name, client, region="SP"):
    return {
        "id": event_id,
        "name": name,
        "status": "ACTIVE",
        "polygon": [[-20.0, -48.0], [-20.1, -48.0], [-20.0, -48.1]],
        "sites": [{"id": "SITE-1", "cells": [{"id": "CELL-1", "azimuth": 0, "beamwidth": 120}]}],
        "oss": {"cliente": client, "region": region, "base_url": ""},
        "integration": {"pm_tasks": [{"tech": "4G", "task_id": 2279}]},
    }


def _client(client_id, name, regions=(("SP", "https://10.0.0.1:31943"),)):
    return {
        "id": client_id,
        "name": name,
        "logo": "",
        "regionais": [{"region": region, "ip": ip} for region, ip in regions],
    }


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "server_data"
    for name in ("events", "clientes", "vips", "logos"):
        (root / name).mkdir(parents=True, exist_ok=True)
    _write_json(root / "events" / "barretos-2026.json", _event("barretos-2026", "Barretos", "Vivo"))
    _write_json(root / "events" / "rock-2026.json", _event("rock-2026", "Rock", "TIM", "RJ"))
    _write_json(root / "clientes" / "vivo.json", _client("vivo", "Vivo"))
    _write_json(root / "clientes" / "tim.json", _client("tim", "TIM", (("RJ", "https://10.0.0.2:31943"),)))
    return root


@pytest.fixture
def plenty_of_space(monkeypatch):
    """Neutraliza o espaco em disco real.

    Ele e uma condicao legitima para oferecer o Setup (o bundle instalado passa de
    880 MiB), mas nao pode decidir o resultado destes testes â€” a maquina de quem
    roda a suite varia. A recusa por disco cheio tem o seu proprio teste.
    """
    monkeypatch.setattr(
        ds.DistributionService, "_free_space", lambda self: ds.SETUP_FREE_SPACE_BYTES * 4
    )


@pytest.fixture
def fake_base(tmp_path):
    """Um build-base minimo, porem completo do ponto de vista do manifesto.

    O conteudo nao e um bundle real do PyInstaller: o que os testes precisam e do
    **contrato** do cache (manifesto, hashes, payload) â€” compilar de verdade levaria
    dezenas de minutos e nada acrescentaria a estas garantias.
    """
    dist = tmp_path / "dist"
    version = ep.app_version()
    root = base_build.base_dir(version, dist)
    app_dir = root / base_build.APP_DIR_NAME
    (app_dir / "_internal").mkdir(parents=True)
    (app_dir / base_build.EXECUTABLE_NAME).write_bytes(b"MZ" + b"programa-compilado" * 64)
    (app_dir / "_internal" / "base.dat").write_bytes(b"recurso")
    archive = base_build.make_base_archive(root)
    base_build.write_manifest(
        root, version,
        python_version="3.12.0", architecture="64bit",
        source_commit="0" * 40, source_dirty=False,
        browsers={"chromium": "1200"}, archive=archive,
    )
    return dist


# â”€â”€ Semente e build-base genericos â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_base_build_has_no_event_or_client_specific_seed(tmp_path, source):
    """O cache so pode ser reusado por qualquer selecao porque nao carrega nenhuma."""
    from core.seed import validate_seed

    seed = base_build.prepare_generic_seed(tmp_path / "seed", "1.0.0")

    assert not list(seed.rglob("*.json")) == []  # o manifesto generico existe
    assert [item.name for item in seed.glob("*.json")] == [base_build.SEED_MANIFEST_NAME]
    for name in ("events", "clientes", "vips"):
        assert not list((seed / name).glob("*.json"))

    # Uma semente sem evento e um resultado VALIDO: o self-test generico do
    # build-base nao pode exigir RoadShow, TIM nem cadastro algum.
    result = validate_seed(seed)
    assert result["ok"] is True
    assert result["generic"] is True
    assert result["events"] == 0 and result["clientes"] == 0 and result["vips"] == 0
    assert result["profile"]["runtime_data_dir"] == base_build.RUNTIME_DATA_DIR
    assert result["profile"]["app_id"] == base_build.STABLE_APP_ID

    # E a guarda que impede um "base" nascer com semente de perfil por engano.
    _write_json(seed / "events" / "barretos-2026.json", _event("barretos-2026", "B", "Vivo"))
    with pytest.raises(base_build.BaseBuildError, match="cadastro especifico"):
        base_build.assert_generic_seed(seed)


def test_spec_refuses_client_catalog_and_profile_seed_in_base_mode():
    """A guarda vive tambem no `main.spec`: nenhum caminho produz base sujo."""
    spec = SPEC_PATH.read_text(encoding="utf-8")

    assert "SMARTEVENTS_BUILD_MODE" in spec
    assert "from core.base_build import assert_generic_seed" in spec
    assert "assert_generic_seed(_server_data_seed)" in spec
    # O fallback para `data/clientes.json` do repositorio so existe no modo legado.
    catalog = spec.split("_cred_seed = []")[1].split("_profile_datas")[0]
    assert "if _build_mode != 'base':" in catalog
    # O caminho legado por perfil continua disponivel para rollback.
    assert "'legacy'" in spec


def test_spec_embeds_version_used_by_event_package_validation():
    """O executavel nao pode cair em 0.0.0 e recusar pacotes da propria versao."""
    spec = SPEC_PATH.read_text(encoding="utf-8")

    assert "('VERSION', '.')" in spec


def test_seed_operator_data_creates_the_empty_layout_for_a_generic_install(tmp_path, monkeypatch):
    """Primeiro boot sem pacote: as pastas existem e o app abre num estado vazio."""
    from core import seed as seed_module

    bundled = base_build.prepare_generic_seed(tmp_path / "bundle" / "server_data", "1.0.0")
    operator = tmp_path / "operator" / "server_data"
    monkeypatch.setattr(seed_module, "bundled_seed_dir", lambda: bundled)
    monkeypatch.setattr(seed_module, "operator_seed_dir", lambda: operator)

    result = seed_module.seed_operator_data()

    assert result == operator
    for name in seed_module.SEED_DIRS:
        assert (operator / name).is_dir()
    assert not list((operator / "events").glob("*.json"))


# â”€â”€ Manifesto e integridade do cache â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_base_manifest_matches_every_cached_artifact(fake_base):
    build = base_build.load_base(root=fake_base)
    app_dir = build.app_dir

    declared = build.manifest["files"]
    on_disk = {
        item.relative_to(app_dir).as_posix() for item in app_dir.rglob("*") if item.is_file()
    }
    assert set(declared) == on_disk
    assert build.manifest["file_count"] == len(on_disk)
    assert declared[base_build.EXECUTABLE_NAME] == build.manifest["executable_sha256"]
    assert build.manifest["executable_sha256"] == base_build.sha256_of(build.executable)
    assert build.manifest["archive_sha256"] == base_build.sha256_of(build.archive)
    assert build.manifest["uncompressed_bytes"] == sum(
        (app_dir / name).stat().st_size for name in declared
    )
    assert build.manifest["app_id"] == base_build.STABLE_APP_ID
    assert build.manifest["runtime_data_dir"] == base_build.RUNTIME_DATA_DIR


def test_distribution_rejects_missing_or_tampered_base(fake_base, tmp_path):
    build = base_build.load_base(root=fake_base)

    # Conteudo alterado depois do manifesto.
    build.executable.write_bytes(b"MZ-outro-programa")
    with pytest.raises(base_build.BaseBuildError, match="adulterado"):
        base_build.load_base(root=fake_base)

    # Arquivo removido.
    (build.app_dir / "_internal" / "base.dat").unlink()
    build.executable.write_bytes(b"MZ" + b"programa-compilado" * 64)
    with pytest.raises(base_build.BaseBuildError, match="incompleto"):
        base_build.load_base(root=fake_base)

    # Arquivo a mais que o manifesto nao declara.
    (build.app_dir / "_internal" / "base.dat").write_bytes(b"recurso")
    (build.app_dir / "intruso.dll").write_bytes(b"nao declarado")
    with pytest.raises(base_build.BaseBuildError, match="nao declarado"):
        base_build.load_base(root=fake_base)

    # Payload pre-comprimido trocado.
    (build.app_dir / "intruso.dll").unlink()
    build.archive.write_bytes(b"PK\x03\x04-outro")
    with pytest.raises(base_build.BaseBuildError, match="pre-comprimido adulterado"):
        base_build.load_base(root=fake_base)

    # Cache inexistente.
    with pytest.raises(base_build.BaseBuildError, match="Nenhum build-base"):
        base_build.load_base(root=tmp_path / "vazio")


def test_base_version_never_becomes_an_arbitrary_path(fake_base):
    for hostile in ("../../server_data", "..", "1.0.0/../..", "", "latest"):
        with pytest.raises(base_build.BaseBuildError, match="Versao invalida"):
            base_build.base_dir(hostile, fake_base)


# â”€â”€ O Setup nao chama o PyInstaller â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_distribution_setup_never_invokes_pyinstaller(fake_base, source, tmp_path, monkeypatch):
    """O ganho da fase: gerar um Setup so recompila o instalador."""
    calls = []

    def _fake_run(command, **kwargs):
        calls.append(command)
        output = Path(kwargs.get("cwd") or ".")
        # Imita o ISCC: grava o .exe no OutputDir declarado pelo wrapper.
        wrapper = Path(command[-1]).read_text(encoding="utf-8")
        out_dir = re.search(r'#define MyOutputDir "([^"]+)"', wrapper).group(1)
        basename = re.search(r'#define MyOutputBaseFilename "([^"]+)"', wrapper).group(1)
        setup = Path(out_dir) / f"{basename}.exe"
        setup.parent.mkdir(parents=True, exist_ok=True)
        setup.write_bytes(b"MZ" + b"instalador" * 4096)
        del output

        class _Completed:
            returncode = 0
            stdout = "Successful compile (0 errors)"
            stderr = ""

        return _Completed()

    monkeypatch.setattr(base_build.subprocess, "run", _fake_run)
    monkeypatch.setattr(base_build, "iscc_path", lambda root=None: tmp_path / "ISCC.exe")
    (tmp_path / "ISCC.exe").write_bytes(b"MZ")

    build = base_build.load_base(root=fake_base)
    preview = ep.preview_package(source, ["barretos-2026"], name="Barretos")
    package = tmp_path / "pacote.sepack"
    ep.build_package(preview, package)

    result = base_build.compile_setup(
        build, package, tmp_path / "out", tmp_path / "work",
        setup_basename="Setup_SmartEvents_Barretos",
    )
    inspection = base_build.inspect_setup(result, build, package)

    assert len(calls) == 1
    executed = " ".join(str(part) for part in calls[0]).lower()
    assert "pyinstaller" not in executed
    assert "iscc.exe" in executed
    assert result.setup.is_file()
    assert inspection["event_ids"] == ["barretos-2026"]
    assert inspection["base_executable_sha256"] == build.manifest["executable_sha256"]


def test_wrapper_uses_stable_app_id_and_data_dir(fake_base, source, tmp_path):
    """Instalar uma distribuicao nova ATUALIZA a mesma aplicacao, nao cria outra."""
    build = base_build.load_base(root=fake_base)
    preview = ep.preview_package(source, ["barretos-2026", "rock-2026"], name="Dois clientes")
    package = tmp_path / "pacote.sepack"
    ep.build_package(preview, package)

    definitions = base_build.wrapper_definitions(
        build, package, tmp_path / "out", setup_basename="Setup_SmartEvents_Teste"
    )

    assert definitions["MyAppId"] == base_build.STABLE_APP_ID
    assert definitions["MyDataDirName"] == "SmartEvents"
    assert definitions["MyInstallDirName"] == "SmartEvents"
    assert definitions["MyAppVersion"] == build.version
    assert definitions["MyEventPackageSha256"] == base_build.sha256_of(package)
    assert sorted(definitions["MyExpectedEventIds"].split(",")) == ["barretos-2026", "rock-2026"]
    # Nenhuma definicao carrega comando de shell nem argumento de compilador.
    for value in definitions.values():
        assert "&" not in str(value) and "|" not in str(value)

    wrapper = base_build.write_wrapper(tmp_path / "wrapper.iss", definitions)
    text = wrapper.read_text(encoding="utf-8")
    assert f'#define MyAppId "{base_build.STABLE_APP_ID}"' in text
    assert text.rstrip().endswith('SmartEvents.iss"')

    iss = _iss()
    assert f'#define MyAppId "{base_build.STABLE_APP_ID}"' in iss
    # Os dados do operador sao deliberadamente preservados na desinstalacao.
    assert "Dados do operador em LocalAppData sao deliberadamente preservados" in iss
    assert "{localappdata}\\{#MyDataDirName}" in iss
    assert 'Type: filesandordirs; Name: "{localappdata}' not in iss


def test_the_setup_is_self_contained_and_never_ships_the_base_archive(fake_base, source, tmp_path):
    """O payload pre-comprimido foi prototipado e reprovado â€” e isso fica travado.

    O Inno Setup exige a flag ``external`` junto de ``extractarchive``: o ZIP
    teria de viajar FORA do ``Setup.exe``, quebrando o criterio de aceite de um
    unico arquivo autocontido. O ZIP continua existindo como artefato do cache,
    mas nao entra no instalador.
    """
    build = base_build.load_base(root=fake_base)
    preview = ep.preview_package(source, ["barretos-2026"], name="Barretos")
    package = tmp_path / "pacote.sepack"
    ep.build_package(preview, package)

    definitions = base_build.wrapper_definitions(
        build, package, tmp_path / "out", setup_basename="Setup"
    )
    assert "MyBaseArchive" not in definitions
    assert definitions["MySourceDir"] == build.app_dir.as_posix()

    # SÃ³ as diretivas contam; os comentÃ¡rios explicam justamente por que a
    # otimizaÃ§Ã£o foi descartada e citam os nomes das flags.
    directives = "\n".join(
        line for line in _iss().splitlines() if not line.lstrip().startswith(";")
    )
    assert 'Source: "{#MySourceDir}\\*"; DestDir: "{app}"' in directives
    assert "extractarchive" not in directives
    assert "ArchiveExtraction" not in directives
    assert "MyBaseArchive" not in directives
    # O ZIP do cache continua sendo gerado: e ele que permite mover o build-base
    # para outra maquina de release sem recompilar.
    assert build.archive.is_file()
    assert build.manifest["archive"] == base_build.ARCHIVE_NAME


# â”€â”€ Contrato do instalador â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_wrapper_imports_package_before_self_test():
    """Validar -> importar -> diagnosticar. O self-test confere os eventos."""
    iss = _iss()
    # A associacao do `.sepack` no [Registry] tambem usa `--import-event-package`;
    # a ordem que interessa e a do codigo de pos-instalacao.
    code = iss[iss.index("[Code]"):]

    validate = code.index("--inspect-event-package")
    importing = code.index("--import-event-package")
    self_test = code.index("--self-test --report")
    assert validate < importing < self_test

    body = iss[iss.index("procedure CurStepChanged"):]
    assert body.index("ValidateEventPackage") < body.index("ImportEventPackage")
    assert body.index("ImportEventPackage") < body.index("RunSelfTest")

    # A politica e `preserve`: atualizar o programa nunca sobrescreve o evento local.
    assert "--conflict preserve" in iss
    # E o diagnostico confere justamente os eventos que o pacote deveria ter trazido.
    assert '--expect-package-report "' in iss
    assert '--expect-events "{#MyExpectedEventIds}"' in iss
    # Falha de importacao impede mensagem de sucesso falso.
    assert "RaiseException(Detail)" in iss
    assert "Result := SelfTestPassed and not PrerequisiteRestartRequired" in iss


def test_conflicting_events_finish_with_a_warning_that_names_them():
    iss = _iss()

    assert "function PreservedEventsFromReport" in iss
    assert "'\"preserved\"'" in iss or '"preserved"' in iss
    assert "foram PRESERVADOS" in iss
    # Codigo 4 = concluido com conflito preservado: instalacao segue, com aviso.
    assert "if ResultCode = 4 then" in iss
    assert "ImportWarning" in iss


def test_restart_path_replays_import_then_self_test():
    """Reinicio por pre-requisito: a importacao ainda nao aconteceu."""
    iss = _iss()

    procedure = iss[
        iss.index("procedure SchedulePostInstallAfterRestart"):
        iss.index("procedure CurStepChanged")
    ]
    assert "--import-event-package" in procedure
    assert "--self-test" in procedure
    assert procedure.index("--import-event-package") < procedure.index("--self-test")
    # Importacao reprovada nao pode ser seguida de um diagnostico "aprovado".
    assert "if %IMPORT_CODE% NEQ 0 if %IMPORT_CODE% NEQ 4 exit /b %IMPORT_CODE%" in procedure
    assert "RunOnce" in procedure
    assert "SchedulePostInstallAfterRestart" in iss[iss.index("procedure CurStepChanged"):]


def test_sepack_file_association_is_scoped_and_uninstalled():
    iss = _iss()

    registry = iss[iss.index("[Registry]"):iss.index("[Run]")]
    assert "Root: HKA; Subkey: \"Software\\Classes\\.sepack\"" in registry
    assert "--import-event-package \"\"%1\"\" --show-dialog" in registry
    # Uninstall remove SOMENTE as chaves da associacao.
    assert registry.count("uninsdeletekey") >= 3
    assert "uninsdeletevalue" in registry
    for hive in ("HKLM", "HKCU", "HKCR"):
        assert f"Root: {hive};" not in registry
    assert "ChangesAssociations=yes" in iss


def test_the_legacy_profile_flow_keeps_working_without_a_package():
    """Sem `MyEventPackage`, o .iss volta a ser exatamente o instalador anterior."""
    iss = _iss()

    assert iss.count("#ifdef MyEventPackage") >= 4
    # A associacao, a importacao e o pacote nas [Files] estao todos condicionados.
    for guarded in ("[Registry]", "Source: \"{#MyEventPackage}\"", "function ImportEventPackage"):
        block_start = iss.index(guarded)
        assert "#ifdef MyEventPackage" in iss[:block_start]
    assert "ChangesAssociations=no" in iss


# â”€â”€ Rastreabilidade â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_manifest_records_base_package_setup_and_exe_hashes(fake_base, source, tmp_path, monkeypatch, plenty_of_space):
    service = ds.DistributionService(
        root=tmp_path / "distributions", source_dir=source,
        dist_dir=fake_base, repo_dir=REPO_ROOT,
    )
    setup_path = tmp_path / "compiled" / "Setup_SmartEvents_Teste.exe"
    setup_path.parent.mkdir(parents=True)
    setup_path.write_bytes(b"MZ" + b"instalador" * 4096)

    def _fake_compile(build, package, output_dir, work_dir, **kwargs):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        destination = Path(output_dir) / setup_path.name
        destination.write_bytes(setup_path.read_bytes())
        return base_build.SetupResult(
            setup=destination, sha256=base_build.sha256_of(destination),
            bytes=destination.stat().st_size, wrapper=Path(work_dir) / "distribution.iss",
            definitions={"MyAppId": base_build.STABLE_APP_ID}, log="Successful compile",
        )

    monkeypatch.setattr(base_build, "compile_setup", _fake_compile)
    monkeypatch.setattr(base_build, "iscc_path", lambda root=None: tmp_path / "ISCC.exe")
    (tmp_path / "ISCC.exe").write_bytes(b"MZ")

    record = service.create_job(
        ["barretos-2026"], name="Barretos", fmt=ds.FORMAT_FULL_SETUP, background=False
    )

    assert record["state"] == "ready"
    assert [step["state"] for step in record["steps"]] == [
        "queued", "validating", "packaging", "compiling", "testing", "ready",
    ]
    package_entry = record["artifacts"]["package"]
    setup_entry = record["artifacts"]["setup"]
    manifest_entry = record["artifacts"]["manifest"]

    package_file, _ = service.artifact(record["job_id"], "package")
    setup_file, _ = service.artifact(record["job_id"], "setup")
    assert ep.sha256_of(package_file) == package_entry["sha256"]
    assert ep.sha256_of(setup_file) == setup_entry["sha256"]
    assert len(manifest_entry["sha256"]) == 64

    build = base_build.load_base(root=fake_base)
    assert record["base"]["version"] == build.version
    assert record["base"]["executable_sha256"] == build.manifest["executable_sha256"]
    assert record["inspection"]["event_ids"] == ["barretos-2026"]
    assert record["inspection"]["setup_sha256"] == setup_entry["sha256"]
    assert record["manifest"]["event_ids"] == ["barretos-2026"]


def test_setup_is_offered_only_when_the_host_can_actually_produce_it(fake_base, source, tmp_path, monkeypatch, plenty_of_space):
    empty = ds.DistributionService(
        root=tmp_path / "d1", source_dir=source, dist_dir=tmp_path / "sem-base", repo_dir=REPO_ROOT,
    )
    capabilities = empty.capabilities()

    assert capabilities["formats"] == ["event_package"]
    assert capabilities["base_ready"] is False
    formats = {item["id"]: item for item in capabilities["available_formats"]}
    assert formats["full_setup"]["enabled"] is False
    assert "build-base" in formats["full_setup"]["reason"].lower()
    assert formats["full_setup"]["action"] == ds.FULL_SETUP_ACTION

    monkeypatch.setattr(base_build, "iscc_path", lambda root=None: tmp_path / "ISCC.exe")
    (tmp_path / "ISCC.exe").write_bytes(b"MZ")
    ready = ds.DistributionService(
        root=tmp_path / "d2", source_dir=source, dist_dir=fake_base, repo_dir=REPO_ROOT,
    )
    capabilities = ready.capabilities()

    assert capabilities["formats"] == ["event_package", "full_setup"]
    assert capabilities["base_ready"] is True
    assert capabilities["iscc_ready"] is True
    assert capabilities["base_version"] == ep.app_version()
    formats = {item["id"]: item for item in capabilities["available_formats"]}
    assert formats["full_setup"]["enabled"] is True
    assert formats["full_setup"]["base_version"] == ep.app_version()
    # O aviso de tamanho e parte do contrato da tela.
    assert "grande" in formats["full_setup"]["description"]


def test_setup_is_refused_when_the_disk_cannot_hold_it(fake_base, source, tmp_path, monkeypatch):
    """O bundle instalado passa de 880 MiB; sem espaco, o formato nao e oferecido."""
    monkeypatch.setattr(base_build, "iscc_path", lambda root=None: tmp_path / "ISCC.exe")
    (tmp_path / "ISCC.exe").write_bytes(b"MZ")
    monkeypatch.setattr(
        ds.DistributionService, "_free_space", lambda self: 200 * 1024 * 1024
    )
    service = ds.DistributionService(
        root=tmp_path / "distributions", source_dir=source,
        dist_dir=fake_base, repo_dir=REPO_ROOT,
    )

    capabilities = service.capabilities()

    # O cache esta integro; o que falta e disco — e o motivo diz exatamente isso.
    assert capabilities["base_ready"] is True
    assert capabilities["formats"] == ["event_package"]
    assert "Espaco em disco insuficiente" in capabilities["setup_reason"]
    with pytest.raises(ds.DistributionError, match="Espaco em disco"):
        service.create_job(["rock-2026"], fmt=ds.FORMAT_FULL_SETUP, background=False)


def test_setup_job_is_refused_when_the_base_disappears(fake_base, source, tmp_path, monkeypatch, plenty_of_space):
    monkeypatch.setattr(base_build, "iscc_path", lambda root=None: tmp_path / "ISCC.exe")
    (tmp_path / "ISCC.exe").write_bytes(b"MZ")
    service = ds.DistributionService(
        root=tmp_path / "distributions", source_dir=source,
        dist_dir=fake_base, repo_dir=REPO_ROOT,
    )
    assert ds.FORMAT_FULL_SETUP in service.supported_formats()
    build = base_build.load_base(root=fake_base)
    intact = build.executable.read_bytes()

    monkeypatch.setattr(
        base_build, "compile_setup",
        lambda *a, **k: pytest.fail("o Setup nao pode ser compilado sem base integro"),
    )

    # 1) Cache adulterado antes da selecao: o formato deixa de existir.
    build.executable.write_bytes(b"MZ-adulterado")
    with pytest.raises(ds.DistributionError) as refused:
        service.create_job(["rock-2026"], fmt=ds.FORMAT_FULL_SETUP, background=False)
    assert refused.value.code == "format.unsupported"
    assert "adulterado" in refused.value.message

    # 2) Cache adulterado DEPOIS da selecao, com o job ja em andamento: o job
    # falha na etapa em que estava, sem produzir instalador.
    build.executable.write_bytes(intact)
    real_build_package = ds.ep.build_package

    def _tamper_then_build(*args, **kwargs):
        result = real_build_package(*args, **kwargs)
        build.executable.write_bytes(b"MZ-adulterado-no-meio")
        return result

    monkeypatch.setattr(ds.ep, "build_package", _tamper_then_build)
    failed = service.create_job(["rock-2026"], fmt=ds.FORMAT_FULL_SETUP, background=False)

    assert failed["state"] == "failed"
    assert failed["errors"][0]["code"] == "base.invalid"
    assert "adulterado" in failed["diagnostic"]
    assert failed["failed_step"] in ("packaging", "compiling")
    assert "setup" not in failed["artifacts"]


def test_generating_a_distribution_never_touches_the_base_cache(fake_base, tmp_path):
    """Nenhum comando pode apagar `dist/base/<versao>` ao gerar uma distribuicao."""
    import build as build_module

    base = base_build.base_dir(ep.app_version(), fake_base)
    with pytest.raises(RuntimeError, match="cache do build-base"):
        build_module._replace_directory(
            build_module.DIST / "base" / ep.app_version(), build_module.DIST
        )
    with pytest.raises(RuntimeError, match="cache do build-base"):
        build_module._replace_directory(build_module.DIST / "base", build_module.DIST)
    assert base.is_dir()


def test_build_exposes_the_three_commands_and_keeps_the_legacy_flag():
    import build as build_module

    parser = build_module.build_parser()
    names = set()
    for action in parser._subparsers._group_actions:  # noqa: SLF001 - contrato do argparse
        names.update(action.choices or {})
    assert {"base", "distribution", "legacy-profile"} <= names

    # `--format setup` existe e `distribution` nunca recebe caminho de .iss/saida.
    args = parser.parse_args(["distribution", "--events", "a", "b", "--format", "setup"])
    assert args.events == ["a", "b"] and args.format == "setup"
    assert args.handler is build_module.command_distribution
    for forbidden in ("iss", "output", "app_id"):
        assert not hasattr(args, forbidden)

    parsed = parser.parse_args(["legacy-profile", "--profile", "vivo-barretos-2026"])
    assert parsed.profile == "vivo-barretos-2026"
    assert parsed.handler is build_module.command_legacy_profile

    # Compatibilidade durante a migracao: `build.py --profile <id>` ainda funciona.
    assert build_module.build_parser().parse_args(
        ["legacy-profile", "--profile", "roadshow-tim"]
    ).handler is build_module.command_legacy_profile
