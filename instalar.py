"""Instala as dependencias do monitor com o Python que roda este arquivo.

Feito para a maquina de empresa (15/10/2026): o terminal nao serve de guia,
entao o arquivo roda pelo botao de executar do VS Code, com o Python que o
editor usa. O que ele faz:

- diz que Python esta em uso e quais pacotes do requirements.txt ja existem
  nele - so instala se faltar algum. O azure-devops fica de fora: e do
  sprint_metrics, nao do monitor, e tem caminhos de arquivo longos o bastante
  para estourar o limite do Windows em pasta funda (medido em 15/10/2026);
- quando a rede bloqueia o repositorio publico (PyPI) - o pip responde "Could
  not find a version that satisfies the requirement (from versions: none)",
  sem outra mensagem -, explica o bloqueio em vez de mostrar o erro tecnico, e
  pede o endereco do repositorio interno de pacotes, se a TI tiver passado um.
  Com ele a instalacao e refeita e o endereco fica gravado para o usuario
  (`pip config --user`): as proximas instalacoes ja o usam;
- o navegador do Playwright (Chromium) vem de outro endereco, que a mesma rede
  pode barrar. Sem ele, o rodar_monitor.py usa o Microsoft Edge da maquina
  (medido em 15/10/2026: a vitrine da XP sai igual, 678 fundos).
"""
import importlib.metadata as metadata
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

PASTA = Path(__file__).resolve().parent
REQUISITOS = PASTA / "requirements.txt"
EDGE = ("Microsoft", "Edge", "Application", "msedge.exe")
# Pacotes do requirements.txt que o monitor nao usa.
SO_DO_SPRINT_METRICS = {"azure-devops"}


def python(*args: str) -> int:
    print(">", "python", " ".join(args), flush=True)
    return subprocess.call([sys.executable, *args], cwd=PASTA)


def requisitos_do_monitor() -> dict[str, str]:
    """{nome: linha do requirements.txt} dos pacotes que o monitor usa."""
    requisitos = {}
    for linha in REQUISITOS.read_text(encoding="utf-8").splitlines():
        linha = linha.split("#", 1)[0].strip()
        if not linha:
            continue
        nome = re.split(r"[<>=!~;\[ ]", linha, maxsplit=1)[0]
        if nome.lower() not in SO_DO_SPRINT_METRICS:
            requisitos[nome] = linha
    return requisitos


def pacotes_faltando(requisitos: dict[str, str]) -> list[str]:
    """Os pacotes do monitor que este Python ainda nao tem."""
    faltam = []
    for nome in requisitos:
        try:
            metadata.version(nome)
        except metadata.PackageNotFoundError:
            faltam.append(nome)
    return faltam


def edge_instalado() -> bool:
    bases = (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"))
    return any(base and Path(base, *EDGE).exists() for base in bases)


def instalar_pelo_repositorio_interno(faltam: list[str], pacotes: list[str]) -> bool:
    """Explica o bloqueio e tenta o repositorio interno, se a pessoa tiver um."""
    print(
        "\nO pip nao conseguiu baixar os pacotes: a rede bloqueia o repositorio\n"
        "publico de pacotes Python (PyPI). Isso nao se resolve no computador.\n"
        "Peca a TI a liberacao de pypi.org e files.pythonhosted.org, ou o\n"
        "endereco do repositorio interno de pacotes (Artifactory, Nexus...).\n"
        f"Pacotes que faltam neste Python: {', '.join(faltam)}.\n",
        flush=True,
    )
    try:
        endereco = input(
            "Se ja tem o endereco do repositorio interno, cole aqui e aperte\n"
            "Enter (ou so Enter para sair): "
        )
    except EOFError:
        endereco = ""
    # strip() tira tambem o espaco invisivel que a copia as vezes traz.
    endereco = endereco.strip()
    if not endereco:
        return False
    host = urlsplit(endereco).hostname or ""
    extras = ["--trusted-host", host] if endereco.startswith("http://") and host else []
    if python("-m", "pip", "install", "--index-url", endereco, *extras, *pacotes):
        print("\nO repositorio interno tambem recusou. Mande a mensagem acima para a TI.")
        return False
    python("-m", "pip", "config", "--user", "set", "global.index-url", endereco)
    if extras:
        python("-m", "pip", "config", "--user", "set", "global.trusted-host", host)
    print("Endereco gravado: as proximas instalacoes ja usam o repositorio interno.")
    return True


def main() -> int:
    print("Python usado:", sys.executable, "-", sys.version.split()[0], flush=True)
    if sys.version_info < (3, 10):
        print("O monitor precisa do Python 3.10 ou mais novo (de preferencia o 3.11).")
        return 1

    requisitos = requisitos_do_monitor()
    pacotes = list(requisitos.values())
    faltam = pacotes_faltando(requisitos)
    if not faltam:
        print("Todos os pacotes do monitor ja estao instalados neste Python.")
    else:
        print("Faltam:", ", ".join(faltam), flush=True)
        if python("-m", "pip", "install", *pacotes):
            if not instalar_pelo_repositorio_interno(faltam, pacotes):
                return 1

    if python("-m", "playwright", "install", "chromium"):
        if edge_instalado():
            print(
                "\nO download do Chromium foi bloqueado - sem problema: o\n"
                "rodar_monitor.py usa o Microsoft Edge desta maquina."
            )
        else:
            print(
                "\nAtencao: sem o Chromium e sem o Microsoft Edge, as vitrines da XP,\n"
                "do BTG e do Itau nao serao coletadas. Peca a TI a liberacao de\n"
                "playwright.azureedge.net e cdn.playwright.dev."
            )
    print("\nPronto: dependencias instaladas. Agora rode o rodar_monitor.py.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
