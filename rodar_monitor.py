"""Roda o monitor de fundos: coleta, digest, painel, deck e planilha.

O mesmo que `python -m shelf_monitor run`, para rodar pelo botao de executar
do VS Code com o Python que instalou as dependencias (instalar.py). Argumentos
extras seguem para o `run` (ex.: --send-email).

Sem o Chromium do Playwright - a rede da empresa pode barrar o download -, a
coleta usa o Microsoft Edge da maquina (SHELF_BROWSER_CHANNEL=msedge), salvo
quando um navegador ja foi escolhido no ambiente.
"""
import os
import runpy
import sys
from pathlib import Path

PASTA = Path(__file__).resolve().parent
EDGE = ("Microsoft", "Edge", "Application", "msedge.exe")


def chromium_do_playwright() -> bool:
    caminho = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if caminho == "0":
        # Navegadores dentro do pacote: nao ha pasta para conferir.
        return True
    raiz = Path(caminho) if caminho else Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    return any(raiz.glob("chromium-*"))


def edge_instalado() -> bool:
    bases = (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"))
    return any(base and Path(base, *EDGE).exists() for base in bases)


os.chdir(PASTA)
sys.path.insert(0, str(PASTA))
try:
    # O .env pode ja escolher o navegador; o config o carrega so depois.
    from dotenv import load_dotenv

    load_dotenv(PASTA / ".env")
except ImportError:
    pass
# O console do Windows nao e UTF-8: sem isto, um simbolo das legendas derruba a
# saida (o mesmo motivo do PYTHONIOENCODING no run_monitor.bat).
for fluxo in (sys.stdout, sys.stderr):
    if hasattr(fluxo, "reconfigure"):
        fluxo.reconfigure(encoding="utf-8")
escolhido = os.environ.get("SHELF_BROWSER_CHANNEL") or os.environ.get("SHELF_CHROMIUM_PATH")
if os.name == "nt" and not escolhido and not chromium_do_playwright() and edge_instalado():
    os.environ["SHELF_BROWSER_CHANNEL"] = "msedge"
    print("Chromium do Playwright ausente: a coleta usa o Microsoft Edge desta maquina.", flush=True)
sys.argv = ["shelf_monitor", "run", *sys.argv[1:]]
runpy.run_module("shelf_monitor", run_name="__main__", alter_sys=True)
