@echo off
chcp 65001 >nul 2>&1
title Monitor de Prateleiras de Fundos

echo ============================================================
echo   Monitor de Prateleiras de Fundos
echo   %date% %time%
echo ============================================================
echo.

cd /d "%~dp0"

REM Saida do Python em UTF-8 tambem quando redirecionada para arquivo
REM (run_monitor.bat > log.txt): na pagina de codigo do Windows, o simbolo
REM das legendas de ETF derrubava o console com UnicodeEncodeError.
set PYTHONIOENCODING=utf-8

REM --- Ambiente virtual com Python 3.10 ou mais novo ---------------------
REM Desde 21/09/2026 o monitor usa @dataclass(slots=True), que nao existe no
REM Python 3.9. O .venv desta pasta tinha sido criado com o 3.9 da Microsoft
REM Store (o "python" do PATH em 29/08/2026), e toda execucao morria na
REM importacao, antes do doctor. Venv anterior ao 3.10 e recriado aqui.
call :garantir_venv
if errorlevel 1 goto :fim
call .venv\Scripts\activate.bat

REM --- Dependencias: o pip so baixa o que falta --------------------------
REM Antes so se conferia o "requests": pacote novo no requirements.txt nunca
REM chegava a um venv ja criado.
echo [*] Conferindo as dependencias do requirements.txt...
echo     (na primeira vez baixa os pacotes; pode levar alguns minutos)
python -m pip install --disable-pip-version-check --prefer-binary -q -r requirements.txt
if errorlevel 1 (
    echo [X] Falha ao instalar as dependencias. Verifique a saida acima.
    goto :fim
)
echo.

REM --- Garante Playwright e navegadores Chromium ------------------------
echo [*] Verificando e garantindo os navegadores do Playwright...
python -m playwright install chromium
echo.

REM --- Doctor: verifica acesso as fontes ---------------------------------
echo [1/3] Verificando acesso as fontes (doctor)...
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
echo [2/3] Executando coleta completa de fundos...
echo ============================================================
echo.

REM --- Run: coleta, diff, digest, dashboard, deck e planilha -------------
REM Para tambem catalogar ETFs (todas as categorias, incl. Renda Fixa) e o
REM fluxo semanal, adicione --with-etf abaixo (opt-in: baixa dados extra da
REM CVM). Ou rode em separado: python -m shelf_monitor etf --export
python -m shelf_monitor run
set FUNDOS_RC=%errorlevel%

echo.
echo ============================================================
echo [3/3] Monitor de previdencia...
echo ============================================================
echo.

REM --- Previdencia: modulo independente, pastas proprias em data\previdencia\
REM Vitrines de PGBL/VGBL (XP, BTG, Itau Private). Uma falha aqui nao mexe no
REM que o monitor de fundos ja gravou.
python -m shelf_monitor.previdencia run
set PREV_RC=%errorlevel%

echo.
echo ============================================================
if not "%PREV_RC%"=="0" (
    echo [!] O monitor de previdencia terminou com erros. Verifique a saida acima.
) else (
    echo [OK] Previdencia: saidas em data\previdencia\reports\
)
if not "%FUNDOS_RC%"=="0" (
    echo [!] A execucao de fundos terminou com erros. Verifique a saida acima.
) else (
    echo [OK] Monitoramento concluido com sucesso!
    echo.
    echo Saidas geradas em data\reports\:
    echo   - dashboard.html    ^(Dashboard comparativo^)
    echo   - prateleiras-*.xlsx ^(Planilha consolidada^)
    echo   - digest-*.md/.html  ^(Resumo de mudancas^)
    echo.
    echo   Catalogo de ETFs ^(incl. Renda Fixa^) e fluxo semanal: rode
    echo   python -m shelf_monitor etf --export ^(ou use --with-etf acima^)
)
echo ============================================================

:fim
echo.
pause
exit /b


REM ======================================================================
REM Garante .venv\ com Python 3.10+; devolve errorlevel 1 se nao conseguir.
:garantir_venv
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
    if not errorlevel 1 exit /b 0
    echo [!] O .venv usa Python anterior ao 3.10, que o monitor nao suporta.
    echo     Recriando o ambiente virtual...
    rmdir /s /q ".venv"
)
if exist ".venv\Scripts\python.exe" (
    echo [X] Nao foi possivel apagar o .venv antigo. Feche os programas que o
    echo     estejam usando ^(terminal, editor^) e rode de novo.
    exit /b 1
)
call :python_base
if not defined PYTHON_BASE (
    echo [X] Nenhum Python 3.10 ou mais novo encontrado.
    echo     Instale o Python 3.11 ^(python.org^) e rode de novo.
    exit /b 1
)
echo [*] Criando o ambiente virtual .venv com %PYTHON_BASE%...
%PYTHON_BASE% -m venv .venv
if errorlevel 1 (
    echo [X] Falha ao criar o ambiente virtual.
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip setuptools wheel
exit /b 0


REM Python base para criar o venv. O launcher "py" acha a versao pedida mesmo
REM quando o "python" do PATH e outro (o alias da Microsoft Store, por exemplo).
REM 3.11 primeiro: e a versao em que a suite de testes roda.
:python_base
set "PYTHON_BASE="
for %%V in (3.11 3.12 3.13 3.10) do (
    if not defined PYTHON_BASE (
        py -%%V -c "import sys" >nul 2>&1 && set "PYTHON_BASE=py -%%V"
    )
)
if defined PYTHON_BASE exit /b 0
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PYTHON_BASE=python"
exit /b 0
