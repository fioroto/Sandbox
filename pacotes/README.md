# Pacotes para instalar sem internet

`windows-py314/`: os pacotes do monitor (o `requirements.txt` sem o
`azure-devops`, que e do `sprint_metrics`) para Windows 64 bits com Python
3.14 - 22 arquivos, 52 MB, baixados em 15/10/2026 para a maquina de empresa em
que a rede bloqueia o repositorio publico de pacotes (PyPI).

O `instalar.py` usa a pasta da versao do Python que o roda
(`windows-py<maior><menor>`) antes de tudo, sem internet, e so cai para a rede
se faltar algo. O navegador nao vai aqui: sem o Chromium do Playwright, o
`rodar_monitor.py` coleta com o Microsoft Edge da maquina.

Para gerar de novo - pacotes atualizados ou outra versao do Python -, numa
maquina com acesso ao PyPI, com o `requirements.txt` sem a linha do
`azure-devops` em `req-monitor.txt` (troque 3.14/cp314 pela versao desejada e
o nome da pasta junto):

    python -m pip download -r req-monitor.txt -d pacotes/windows-py314 --only-binary=:all: --platform win_amd64 --python-version 3.14 --implementation cp --abi cp314 --abi abi3 --abi none
