# -*- mode: python ; coding: utf-8 -*-

import os
import sys
import json
import importlib.util
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_all

# Fase 3 — dois modos de build, escolhidos por SMARTEVENTS_BUILD_MODE:
#
#   base   → build-base GENERICO, feito uma vez por versao. Sem evento, sem VIP e
#            sem catalogo de cliente; a selecao de eventos vira depois, num
#            `.sepack`, e nunca dispara o PyInstaller de novo.
#   legacy → caminho historico por perfil (`build.py legacy-profile`), preservado
#            durante a migracao para permitir rollback. E o default: sem a
#            variavel, o build antigo continua identico.
_build_mode = (os.environ.get('SMARTEVENTS_BUILD_MODE') or 'legacy').strip().lower()
if _build_mode not in ('base', 'legacy'):
    raise RuntimeError(
        f"SMARTEVENTS_BUILD_MODE invalido: {_build_mode!r} (use 'base' ou 'legacy')."
    )

# requests é importado por core/collector.py e core/database.py. A análise estática do
# PyInstaller não estava empacotando o pacote (provável conflito de resolução — ver
# _get_requests() em core/database.py), então forçamos a coleta dele e de suas deps,
# incluindo o bundle de certificados do certifi (necessário para HTTPS ao iManager).
_requests_hidden = (
    collect_submodules('requests')
    + collect_submodules('urllib3')
    + collect_submodules('charset_normalizer')
    + ['certifi', 'idna']
)

# Playwright: empacota o pacote Python + driver (node.exe + package) e, separadamente, o
# navegador Chromium headless usado pela renovação de sessão (main.py --get-session).
# O modo --headless usa chromium_headless_shell; ffmpeg/winldd são auxiliares pequenos.
# O core/session_renew.py aponta PLAYWRIGHT_BROWSERS_PATH para 'ms-playwright/' no bundle.
_pw_datas, _pw_binaries, _pw_hidden = collect_all('playwright')

# clientes.json (catálogo Cliente→Regional→IP) embutido como SEMENTE: o .exe é compartilhável
# SOZINHO e, na 1ª execução, core.credentials.seed_files() copia esta cópia para
# <pasta do .exe>/data/clientes.json (gravável e editável depois, sem recompilar). As CREDENCIAIS
# (data/credentials.json) NÃO são embutidas — seed_files() cria um arquivo vazio para o operador
# digitar suas contas (o .exe circula entre clientes e não pode carregar segredos).
#
# No modo `base` nada disso e embutido: o catalogo Cliente→Regional→IP e um cadastro
# especifico e chega junto com o `.sepack`, conciliado por id na importacao.
_cred_seed = []
if _build_mode != 'base':
    _cat_file = Path(os.environ.get('SMARTEVENTS_CLIENT_CATALOG_SEED', 'data/clientes.json'))
    if _cat_file.exists():
        _cred_seed = [(str(_cat_file), 'data')]

_profile_datas = []
_profile_file = os.environ.get('SMARTEVENTS_BUILD_PROFILE_FILE', '').strip()
if _profile_file:
    _profile_path = Path(_profile_file)
    if not _profile_path.is_file():
        raise RuntimeError(f"Perfil de runtime ausente: {_profile_path}")
    _profile_datas = [(str(_profile_path), '.')]

_ms_playwright = Path(
    os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    or (Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright")
)
# O navegador não é compatível por "ser o mais novo": cada versão do pacote
# Playwright exige revisões exatas, declaradas no browsers.json do próprio driver.
# Empacotar qualquer pasta chromium-* disponível criou builds que continham um
# browser incompatível e derrubavam toda renovação de sessão.
_playwright_spec = importlib.util.find_spec("playwright")
if not _playwright_spec or not _playwright_spec.origin:
    raise RuntimeError("Playwright não está instalado no ambiente do build.")
_browser_manifest = (
    Path(_playwright_spec.origin).parent / "driver" / "package" / "browsers.json"
)
_browser_entries = json.loads(_browser_manifest.read_text(encoding="utf-8"))["browsers"]
_required_browser_names = {"chromium", "chromium-headless-shell", "ffmpeg", "winldd"}
_browser_dirs = [
    f"{entry['name'].replace('-', '_')}-{entry['revision']}"
    for entry in _browser_entries
    if entry["name"] in _required_browser_names
]
_missing_browser_dirs = [
    name for name in _browser_dirs if not (_ms_playwright / name).is_dir()
]
if _missing_browser_dirs:
    raise RuntimeError(
        "Browsers compatíveis com o Playwright estão ausentes: "
        + ", ".join(_missing_browser_dirs)
        + ". Execute: .venv\\Scripts\\playwright.exe install chromium"
    )
_browser_datas = [
    (str(_ms_playwright / d), f"ms-playwright/{d}")
    for d in _browser_dirs
]

# Permite gerar distribuições com uma semente isolada sem alterar ``server_data`` da
# instalação de desenvolvimento. Sem a variável, o build normal continua usando a
# pasta padrão do projeto.
_server_data_seed = Path(os.environ.get("SMARTEVENTS_SERVER_DATA_SEED", "server_data"))

# Fase 4 — verificação de assinatura do `.sepack` dentro do executável distribuído:
# a chave PÚBLICA (registro em keys/sepack_signing/) e o ISSigTool.exe (verificador)
# entram no bundle; a chave PRIVADA de release nunca é lida por este spec nem
# empacotada. O ISSigTool.exe é opcional: sem o cache local do Inno Setup (não é
# pré-requisito de quem só roda o PyInstaller), o build continua — só não poderá
# verificar um `.sepack` assinado até o binário ser adicionado depois.
_signing_datas = [('keys/sepack_signing', 'keys/sepack_signing')]
_issigtool_source = Path('.build-tools') / 'InnoSetup7' / 'ISSigTool.exe'
if _issigtool_source.is_file():
    _signing_datas.append((str(_issigtool_source), 'issigtool'))

# O build-base é o ganho arquitetural da Fase 3: ele só pode ser reusado por
# qualquer seleção de eventos porque não carrega nenhuma. A checagem vive aqui, e
# não só no build.py, para que nenhum caminho consiga produzir um "base" com
# semente de perfil — o cache ficaria válido no manifesto e errado no conteúdo.
if _build_mode == 'base':
    if not os.environ.get("SMARTEVENTS_SERVER_DATA_SEED", "").strip():
        raise RuntimeError(
            "O build-base exige SMARTEVENTS_SERVER_DATA_SEED apontando para a semente generica."
        )
    sys.path.insert(0, str(Path(SPECPATH).resolve()))
    from core.base_build import assert_generic_seed
    assert_generic_seed(_server_data_seed)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_pw_binaries,
    datas=[
        ('frontend', 'frontend'),
        ('server_frontend', 'server_frontend'),
        (str(_server_data_seed), 'server_data'),
        ('core/session_renew.py', 'core'),  # garante o módulo de renovação no bundle
        ('alarms/catalogo-alarmes.csv', 'alarms'),  # catálogo nome→pares (coleta de alarmes)
    ] + _cred_seed + _profile_datas + _signing_datas + collect_data_files('certifi') + _pw_datas + _browser_datas,
    hiddenimports=[
        'server',  # importado por main.py no modo --serve
        'core.session_renew',  # importado por main.py no modo --get-session
        # importado por main.py nos modos --inspect/--import-event-package. A
        # chave publica e o verificador de assinatura do .sepack (Fase 4) entram
        # via `_signing_datas`, acima; a chave PRIVADA nunca e empacotada.
        'core.event_package',
        'core.package_signing',
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'multipart',          # python-multipart (UploadFile)
        'pandas',             # /api/parse-sites
        'openpyxl',           # leitura de planilhas .xlsx em /api/parse-sites
    ] + _requests_hidden + _pw_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# Build ONEDIR (pasta dist/main/ com o .exe + _internal/), NÃO onefile.
# O onefile comprime tudo (~400 MB com o Chromium) num único .exe e DESCOMPRIME para uma
# pasta temporária _MEI a cada abertura. Ao enviar esse .exe para outra máquina, qualquer
# truncamento/antivírus na transferência corrompe o stream e o bootloader falha com
# "decompression resulted in return code -1". O onedir não descomprime nada em runtime
# (os arquivos já ficam soltos ao lado do .exe), eliminando essa classe de erro e abrindo
# mais rápido. Distribuição: zipar a pasta dist/main/ (o build.py já gera o .zip).
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # binaries/datas vão para o COLLECT (pasta), não para dentro do .exe
    name='SmartEvents',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX desabilitado: comprimir o Chromium/node empacotados pode corrompê-los
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/logoSmartEvents.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SmartEvents',
)
