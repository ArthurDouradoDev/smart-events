"""Fase 4 — assinatura Authenticode (SmartEvents.exe, Setup.exe, desinstalador).

Sem certificado real disponivel neste ambiente (nem em produção — a chave fica
fora do repositório por decisão do plano), estes testes verificam o contrato
puro: resolução de configuração, montagem do comando do signtool e, acima de
tudo, que a senha do certificado nunca escapa para um log, exceção ou
manifesto. O processo real do signtool é substituído por um dublê
(`monkeypatch` em ``subprocess.run``); nenhum teste aqui depende de um
certificado ou do `signtool.exe` de verdade.
"""

import subprocess
from types import SimpleNamespace

import pytest

from core import authenticode


SECRET_PASSWORD = "s3nh4-de-teste-nao-e-real"


def _fake_completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def config(tmp_path):
    signtool = tmp_path / "signtool.exe"
    signtool.write_bytes(b"fake")
    pfx = tmp_path / "release.pfx"
    pfx.write_bytes(b"fake-pfx")
    return authenticode.AuthenticodeConfig(
        signtool_path=signtool, pfx_path=pfx, pfx_password=SECRET_PASSWORD,
        timestamp_url="http://timestamp.example.test",
    )


# ── Resolução de configuração ─────────────────────────────────────────

def test_load_config_is_none_without_environment(monkeypatch):
    for name in (
        authenticode.ENV_SIGNTOOL_PATH, authenticode.ENV_PFX_PATH,
        authenticode.ENV_PFX_PASSWORD, authenticode.ENV_TIMESTAMP_URL,
    ):
        monkeypatch.delenv(name, raising=False)

    assert authenticode.load_config() is None
    assert authenticode.is_configured() is False


def test_load_config_is_none_when_files_are_missing(monkeypatch, tmp_path):
    monkeypatch.setenv(authenticode.ENV_SIGNTOOL_PATH, str(tmp_path / "nao-existe.exe"))
    monkeypatch.setenv(authenticode.ENV_PFX_PATH, str(tmp_path / "nao-existe.pfx"))

    assert authenticode.load_config() is None


def test_load_config_resolves_from_environment_when_files_exist(monkeypatch, tmp_path):
    signtool = tmp_path / "signtool.exe"
    signtool.write_bytes(b"fake")
    pfx = tmp_path / "release.pfx"
    pfx.write_bytes(b"fake-pfx")
    monkeypatch.setenv(authenticode.ENV_SIGNTOOL_PATH, str(signtool))
    monkeypatch.setenv(authenticode.ENV_PFX_PATH, str(pfx))
    monkeypatch.setenv(authenticode.ENV_PFX_PASSWORD, SECRET_PASSWORD)
    monkeypatch.delenv(authenticode.ENV_TIMESTAMP_URL, raising=False)

    loaded = authenticode.load_config()

    assert loaded is not None
    assert loaded.signtool_path == signtool
    assert loaded.pfx_path == pfx
    assert loaded.pfx_password == SECRET_PASSWORD
    assert loaded.timestamp_url == authenticode.DEFAULT_TIMESTAMP_URL
    assert authenticode.is_configured() is True


def test_config_repr_never_shows_the_password(config):
    assert SECRET_PASSWORD not in repr(config)
    assert "<oculta>" in repr(config)


# ── Assinatura de SmartEvents.exe ─────────────────────────────────────

def test_sign_file_uses_sha256_digest_and_timestamp(monkeypatch, config, tmp_path):
    target = tmp_path / "SmartEvents.exe"
    target.write_bytes(b"fake-exe")
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return _fake_completed(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = authenticode.sign_file(target, config)

    assert result.ok
    args = captured["args"]
    assert args[0] == str(config.signtool_path)
    assert args[1] == "sign"
    assert "/fd" in args and args[args.index("/fd") + 1] == "sha256"
    assert "/tr" in args and args[args.index("/tr") + 1] == config.timestamp_url
    assert "/td" in args and args[args.index("/td") + 1] == "sha256"
    assert args[-1] == str(target)


def test_sign_file_rejects_missing_target(config, tmp_path):
    with pytest.raises(authenticode.AuthenticodeError):
        authenticode.sign_file(tmp_path / "nao-existe.exe", config)


def test_sign_file_failure_redacts_password_in_message(monkeypatch, config, tmp_path):
    target = tmp_path / "SmartEvents.exe"
    target.write_bytes(b"fake-exe")

    def fake_run(args, **kwargs):
        # signtool as vezes ecoa os proprios argumentos no erro; simulamos o
        # pior caso, a senha aparecendo na saida.
        return _fake_completed(
            returncode=1, stderr=f"SignTool Error: bad password {SECRET_PASSWORD}",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = authenticode.sign_file(target, config)

    assert not result.ok
    assert SECRET_PASSWORD not in result.message
    assert "<oculta>" in result.message


def test_private_key_password_never_enters_artifacts_or_logs(monkeypatch, config, tmp_path, caplog):
    """Nenhuma excecao ou log deste modulo pode conter a senha do certificado."""
    target = tmp_path / "SmartEvents.exe"
    target.write_bytes(b"fake-exe")

    def raising_run(args, **kwargs):
        raise subprocess.SubprocessError("boom")

    monkeypatch.setattr(subprocess, "run", raising_run)

    with caplog.at_level("DEBUG"):
        with pytest.raises(authenticode.AuthenticodeError) as excinfo:
            authenticode.sign_file(target, config)

    assert SECRET_PASSWORD not in str(excinfo.value)
    assert all(SECRET_PASSWORD not in record.getMessage() for record in caplog.records)


# ── Verificacao ────────────────────────────────────────────────────────

def test_verify_file_reports_valid_signature(monkeypatch, config, tmp_path):
    target = tmp_path / "Setup.exe"
    target.write_bytes(b"fake-setup")

    monkeypatch.setattr(subprocess, "run", lambda args, **kw: _fake_completed(returncode=0))

    result = authenticode.verify_file(target, signtool_path=config.signtool_path)

    assert result.ok


def test_verify_file_reports_invalid_signature(monkeypatch, config, tmp_path):
    target = tmp_path / "Setup.exe"
    target.write_bytes(b"fake-setup")

    monkeypatch.setattr(
        subprocess, "run",
        lambda args, **kw: _fake_completed(returncode=1, stderr="No signature found."),
    )

    result = authenticode.verify_file(target, signtool_path=config.signtool_path)

    assert not result.ok


def test_verify_file_without_signtool_fails_closed(tmp_path):
    target = tmp_path / "Setup.exe"
    target.write_bytes(b"fake-setup")

    result = authenticode.verify_file(target, signtool_path=tmp_path / "nao-existe.exe")

    assert not result.ok


# ── Diretiva SignTool do Inno Setup ───────────────────────────────────

def test_iss_sign_tool_definition_uses_name_and_placeholder():
    value = authenticode.iss_sign_tool_definition(_config_for_definition())

    assert value.startswith(f"/S{authenticode.ISS_SIGN_TOOL_NAME}=")
    assert "$f" in value  # placeholder do Inno Setup para "o arquivo a assinar"


def _config_for_definition():
    return authenticode.AuthenticodeConfig(
        signtool_path="C:/tools/signtool.exe", pfx_path="C:/secrets/release.pfx",
        pfx_password=SECRET_PASSWORD, timestamp_url=authenticode.DEFAULT_TIMESTAMP_URL,
    )


def test_wrapper_definitions_expose_only_the_sign_tool_name_not_the_secret(tmp_path):
    """``core.base_build.wrapper_definitions`` só grava o NOME no `.iss`."""
    from core import base_build
    from core import event_package as ep

    # Pacote real (mínimo): `wrapper_definitions` lê o hash do arquivo e os
    # ids de evento do próprio `.sepack` validado.
    server_data = tmp_path / "server_data"
    (server_data / "events").mkdir(parents=True)
    (server_data / "clientes").mkdir(parents=True)
    (server_data / "events" / "evento-a.json").write_text(
        '{"id": "evento-a", "name": "Evento A", "status": "ACTIVE", '
        '"polygon": [[-20.0, -48.0], [-20.1, -48.0], [-20.0, -48.1]], '
        '"sites": [{"id": "SITE-1", "cells": [{"id": "CELL-1", "azimuth": 0, "beamwidth": 120}]}], '
        '"oss": {"cliente": "Vivo", "region": "SP", "base_url": "", "import_folder": ""}, '
        '"integration": {"pm_tasks": [{"tech": "4G", "task_id": 2279}]}}',
        encoding="utf-8",
    )
    (server_data / "clientes" / "vivo.json").write_text(
        '{"id": "vivo", "name": "Vivo", "logo": "", '
        '"regionais": [{"region": "SP", "ip": "https://10.0.0.1:31943"}]}',
        encoding="utf-8",
    )
    preview = ep.preview_package(server_data, ["evento-a"], name="Teste")
    assert preview.ok, [item.message for item in preview.errors]
    package_path = tmp_path / "pacote.sepack"
    ep.build_package(preview, package_path)

    config = _config_for_definition()
    build = SimpleNamespace(
        version="1.0.0",
        app_dir=SimpleNamespace(as_posix=lambda: "C:/dist/base/1.0.0/SmartEvents"),
        uncompressed_bytes=123,
    )

    definitions = base_build.wrapper_definitions(
        build, package_path, tmp_path / "out",
        setup_basename="Setup_Teste", repo=tmp_path, authenticode_config=config,
    )

    assert definitions["MySignTool"] == authenticode.ISS_SIGN_TOOL_NAME
    serialized = str(definitions)
    assert SECRET_PASSWORD not in serialized
    assert "release.pfx" not in serialized
