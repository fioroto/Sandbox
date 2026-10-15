"""Instala as dependencias do monitor com o Python que roda este arquivo.

Para a maquina em que o `pip` do terminal nao alcanca o repositorio de
pacotes, mas o Python do editor sim (medido em 15/10/2026 numa maquina de
empresa: "Could not find a version that satisfies the requirement" no
terminal, instalacao normal ao rodar um script pelo VS Code). Abra este
arquivo no VS Code e use o botao de executar: ele chama o pip e o Playwright
com o mesmo interpretador, sem nada para digitar.
"""
import subprocess
import sys
from pathlib import Path

PASTA = Path(__file__).resolve().parent


def rodar(*args: str) -> None:
    print(">", "python", " ".join(args), flush=True)
    subprocess.check_call([sys.executable, *args], cwd=PASTA)


print("Python usado:", sys.executable, "-", sys.version.split()[0], flush=True)
if sys.version_info < (3, 10):
    sys.exit("O monitor precisa do Python 3.10 ou mais novo (de preferencia o 3.11).")
rodar("-m", "pip", "install", "-r", "requirements.txt")
rodar("-m", "playwright", "install", "chromium")
print("Pronto: dependencias instaladas. Agora rode o rodar_monitor.py.", flush=True)
