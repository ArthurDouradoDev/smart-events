# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_all

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
_cred_seed = []
_cat_file = Path('data/clientes.json')
if _cat_file.exists():
    _cred_seed = [(str(_cat_file), 'data')]

_ms_playwright = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
_browser_dirs = [
    "chromium-1223",
    "chromium_headless_shell-1223",
    "ffmpeg-1011",
    "winldd-1007",
]
_browser_datas = [
    (str(_ms_playwright / d), f"ms-playwright/{d}")
    for d in _browser_dirs
    if (_ms_playwright / d).exists()
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_pw_binaries,
    datas=[
        ('frontend', 'frontend'),
        ('server_frontend', 'server_frontend'),
        ('server_data', 'server_data'),  # semente: evento Rio + VIPs finalizados
        ('core/session_renew.py', 'core'),  # garante o módulo de renovação no bundle
        ('alarms/catalogo-alarmes.csv', 'alarms'),  # catálogo nome→pares (coleta de alarmes)
    ] + _cred_seed + collect_data_files('certifi') + _pw_datas + _browser_datas,
    hiddenimports=[
        'server',  # importado por main.py no modo --serve
        'core.session_renew',  # importado por main.py no modo --get-session
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
    name='main',
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
    name='main',  # gera dist/main/ (main.exe + _internal/)
)
