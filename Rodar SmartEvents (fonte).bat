@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title SmartEvents (fonte)
set "VENV=.venv"
set "PY=%VENV%\Scripts\python.exe"

echo ============================================
echo   SmartEvents - execucao pelo codigo-fonte
echo ============================================
echo Dica: o jeito mais simples e dar duplo clique em dist\main\main.exe
echo       (nao precisa de Python). Este .bat e para rodar pelo codigo.
echo.

REM --- Verifica se o ambiente virtual existe e funciona nesta maquina ---
if exist "%PY%" (
    "%PY%" --version >nul 2>&1
    if errorlevel 1 (
        echo Ambiente virtual invalido ^(provavelmente copiado de outro PC^). Recriando...
        rmdir /s /q "%VENV%"
    )
)

REM --- Primeira execucao: cria o ambiente e instala tudo ---
if not exist "%PY%" (
    echo Primeira execucao - preparando ambiente...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo.
        echo ERRO: Python nao encontrado. Instale o Python 3.10+ e marque
        echo "Add Python to PATH" na instalacao. Depois rode este arquivo de novo.
        echo.
        pause
        exit /b 1
    )
    echo Instalando dependencias ^(pode demorar alguns minutos^)...
    "%PY%" -m pip install --upgrade pip
    "%PY%" -m pip install -r requirements.txt
    echo Instalando navegador do Playwright ^(renovacao de sessao^)...
    "%PY%" -m playwright install chromium
)

echo.
echo Como deseja iniciar?
echo   [1] Producao  (requer VPN do cliente ativa)
echo   [2] Demonstracao com dados ficticios (--mock, nao precisa de VPN)
echo.
set /p MODO="Digite 1 ou 2 e pressione Enter (padrao 1): "
if "%MODO%"=="2" (
    "%PY%" main.py --mock
) else (
    "%PY%" main.py
)
if errorlevel 1 pause
endlocal
