"""Roda o monitor de fundos: coleta, digest, painel, deck e planilha.

O mesmo que `python -m shelf_monitor run`, para rodar pelo botao de executar
do VS Code com o Python que instalou as dependencias (instalar.py). Argumentos
extras seguem para o `run` (ex.: --send-email).
"""
import os
import runpy
import sys
from pathlib import Path

PASTA = Path(__file__).resolve().parent
os.chdir(PASTA)
sys.path.insert(0, str(PASTA))
# O console do Windows nao e UTF-8: sem isto, um simbolo das legendas derruba a
# saida (o mesmo motivo do PYTHONIOENCODING no run_monitor.bat).
for fluxo in (sys.stdout, sys.stderr):
    if hasattr(fluxo, "reconfigure"):
        fluxo.reconfigure(encoding="utf-8")
sys.argv = ["shelf_monitor", "run", *sys.argv[1:]]
runpy.run_module("shelf_monitor", run_name="__main__", alter_sys=True)
