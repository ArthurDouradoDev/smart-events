"""Testes para POST /api/clientes/{id}/logo (server.py).

O endpoint ficou quebrado por um `NameError`: usava `shutil.copyfileobj` sem que
`shutil` estivesse importado. Nenhum teste o exercitava, então a falha só
aparecia para o operador que tentasse trocar a logo de um cliente.

Chama a função do endpoint diretamente (sem subir HTTP) — o projeto não tem
`httpx` e o TestClient do FastAPI depende dele.
"""
import asyncio
import io
import json
from pathlib import Path

import pytest
from fastapi import UploadFile

import server


PNG = b"\x89PNG\r\n\x1a\n" + b"conteudo-de-imagem"


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """Aponta o cadastro de clientes e a pasta de logos para um tmp vazio."""
    clientes = tmp_path / "clientes"
    logos = tmp_path / "logos"
    clientes.mkdir()
    logos.mkdir()
    monkeypatch.setattr(server, "CLIENTES_DIR", clientes)
    monkeypatch.setattr(server, "LOGOS_DIR", logos)
    return clientes, logos


def _cliente(clientes: Path, cliente_id: str = "tim", **extra) -> Path:
    path = clientes / f"{cliente_id}.json"
    payload = {"id": cliente_id, "name": cliente_id.upper(), "regionais": [], **extra}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=4), encoding="utf-8")
    return path


def _upload(data: bytes = PNG, filename: str = "logo.png") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


def _post(cliente_id: str, upload: UploadFile | None = None) -> dict:
    return asyncio.run(server.upload_cliente_logo(cliente_id, upload or _upload()))


def test_logo_e_gravada_e_referenciada_no_cadastro(dirs):
    clientes, logos = dirs
    path = _cliente(clientes)

    result = _post("tim")

    assert result == {"ok": True, "logo": "tim.png"}
    assert (logos / "tim.png").read_bytes() == PNG
    assert json.loads(path.read_text(encoding="utf-8"))["logo"] == "tim.png"


def test_logo_anterior_e_removida_mesmo_com_outra_extensao(dirs):
    clientes, logos = dirs
    _cliente(clientes, logo="tim.jpg")
    (logos / "tim.jpg").write_bytes(b"logo-antiga")

    result = _post("tim", _upload(filename="nova.webp"))

    assert result["logo"] == "tim.webp"
    assert not (logos / "tim.jpg").exists()
    assert sorted(path.name for path in logos.iterdir()) == ["tim.webp"]


def test_cliente_inexistente_responde_404(dirs):
    _clientes, logos = dirs

    with pytest.raises(server.HTTPException) as excinfo:
        _post("nao-existe")

    assert excinfo.value.status_code == 404
    assert list(logos.iterdir()) == []


def test_formato_nao_suportado_e_recusado_antes_de_gravar(dirs):
    clientes, logos = dirs
    _cliente(clientes)

    with pytest.raises(server.HTTPException) as excinfo:
        _post("tim", _upload(filename="logo.exe"))

    assert excinfo.value.status_code == 400
    assert list(logos.iterdir()) == []
    assert "logo" not in json.loads((clientes / "tim.json").read_text(encoding="utf-8"))
