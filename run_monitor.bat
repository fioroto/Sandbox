@echo off
chcp 65001 >nul 2>&1
title Monitor de Prateleiras de Fundos

echo ============================================================
echo   Monitor de Prateleiras de Fundos
echo   %date% %time%
echo ============================================================
echo.

cd /d "%~dp0"

REM --- Ativa o venv se existir -------------------------------------------
if exist ".venv\Scripts\activate.bat" (
    echo [*] Ativando ambiente virtual...
    call .venv\Scripts\activate.bat
) else (
    echo [!] Ambiente virtual nao encontrado em .venv\
    echo     Criando ambiente virtual...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip setuptools wheel
    echo [*] Instalando dependencias...
    pip install --prefer-binary -r requirements.txt
    echo.
)

REM --- Checa se as dependencias estao instaladas -------------------------
pip show requests >nul 2>&1
if errorlevel 1 (
    python -m pip install --upgrade pip setuptools wheel
    echo [*] Instalando dependencias do requirements.txt...
    pip install --prefer-binary -r requirements.txt
    echo.
)

REM --- Garante Playwright e navegadores Chromium ------------------------
echo [*] Verificando e garantindo os navegadores do Playwright...
playwright install chromium
echo.

REM --- Doctor: verifica acesso as fontes ---------------------------------
echo [1/2] Verificando acesso as fontes (doctor)...
echo.
python -m shelf_monitor doctor
if errorlevel 1 (
    echo.
    echo [!] Alguma fonte esta inacessivel. Verifique a saida acima.
    echo     O monitoramento continuara, mas pode haver dados faltando.
    echo.
)

echo.
echo ============================================================
echo [2/2] Executando coleta completa...
echo ============================================================
echo.

REM --- Run: coleta, diff, digest e dashboard -----------------------------
python -m shelf_monitor run

echo.
echo ============================================================
if errorlevel 1 (
    echo [!] A execucao terminou com erros. Verifique a saida acima.
) else (
    echo [OK] Monitoramento concluido com sucesso!
    echo.
    echo Saidas geradas em data\reports\:
    echo   - dashboard.html    ^(Dashboard comparativo^)
    echo   - prateleiras-*.xlsx ^(Planilha consolidada^)
    echo   - digest-*.md/.html  ^(Resumo de mudancas^)
)
echo ============================================================
echo.

pause
