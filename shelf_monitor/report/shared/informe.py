"""Leitura do informe diario da CVM, uma vez por competencia por execucao.

Tres modulos precisam do mesmo arquivo na mesma execucao - `retail_flow` (o
fluxo dos fundos de vitrine), `performance` (a mediana de referencia, que
precisa do universo inteiro) e `rentabilidade` (a serie mensal). Cada um
chamava `fetch_inf_diario_month` e `read_daily_flows` por conta propria:

- a competencia CORRENTE era baixada com `force=True` pelos tres, entao um
  `run` fazia tres downloads do mesmo zip de ~7 MB;
- o zip era descompactado e parseado uma vez por modulo. Medido em 2026-09-21:
  3,5 s e 302 mil linhas so para a competencia de 09/2026.

Aqui os dois passos sao memoizados por processo. `force` vale para a primeira
chamada da competencia e nao para as seguintes, que e exatamente a semantica
que se queria: "o arquivo do mes corrente esta velho, baixe de novo" e uma
afirmacao sobre a execucao, nao sobre cada chamador.

**Ciclo de vida.** O cache guarda os registros completos - ~91 MB por
competencia. Isso nao aumenta o pico: hoje `performance` ja carrega o universo
inteiro de todas as competencias de uma vez, e o fazia ao lado da copia
filtrada do `retail_flow`. Mas prolonga a vida deles, entao `build_report`
chama `limpar()` quando o ultimo consumidor termina.
"""
from __future__ import annotations

from pathlib import Path

from shelf_monitor.config import Config

# competencia -> caminho do zip (ou None quando a fonte nao respondeu)
_CAMINHOS: dict[str, Path | None] = {}
# competencia -> registros completos, sem filtro de CNPJ
_REGISTROS: dict[str, list] = {}


def limpar() -> None:
    """Esquece tudo. Chamado quando o ultimo consumidor da execucao termina."""
    _CAMINHOS.clear()
    _REGISTROS.clear()


def caminho_da_competencia(
    config: Config, yyyymm: str, *, force: bool = False, revalidar: bool = False
) -> Path | None:
    """O zip da competencia, baixado no maximo uma vez por execucao.

    `revalidar` e o GET condicional de `fetch_inf_diario_month`: para o mes
    fechado que a CVM ainda regrava.
    """
    if yyyymm in _CAMINHOS:
        return _CAMINHOS[yyyymm]
    from shelf_monitor.market.informe_diario import fetch_inf_diario_month

    caminho = fetch_inf_diario_month(config, yyyymm, force=force, revalidar=revalidar)
    _CAMINHOS[yyyymm] = caminho
    return caminho


def ler_competencia(
    config: Config, yyyymm: str, *, force: bool = False, revalidar: bool = False
) -> list:
    """Todos os registros da competencia, parseados no maximo uma vez.

    Devolve a lista COMPLETA, sem filtro de CNPJ: quem quer um recorte filtra
    em memoria, que custa uma passada numa lista contra descompactar e parsear
    o arquivo de novo.

    A lista e compartilhada entre os chamadores - ninguem deve muta-la.
    """
    if yyyymm in _REGISTROS:
        return _REGISTROS[yyyymm]

    from shelf_monitor.report.retail_flow import read_daily_flows

    caminho = caminho_da_competencia(config, yyyymm, force=force, revalidar=revalidar)
    registros = read_daily_flows(caminho, None) if caminho else []
    _REGISTROS[yyyymm] = registros
    return registros


def ler_sem_guardar(config: Config, yyyymm: str) -> list:
    """Os registros da competencia, sem guardar no cache do processo.

    Para quem le uma competencia fechada uma vez so e a resume na hora - a serie
    mensal. Guardar os ~91 MB de cada uma ate o fim da execucao somaria 12 x 91
    MB na primeira montagem da janela de 12 meses, sem nenhum segundo leitor.
    Se outro modulo ja leu a competencia nesta execucao, reaproveita.
    """
    if yyyymm in _REGISTROS:
        return _REGISTROS[yyyymm]
    from shelf_monitor.report.retail_flow import read_daily_flows

    caminho = caminho_da_competencia(config, yyyymm)
    return read_daily_flows(caminho, None) if caminho else []


def ler_filtrado_sem_guardar(config: Config, yyyymm: str, alvo: set[str]) -> list:
    """O recorte de CNPJ de uma competencia, sem guardar a competencia inteira.

    Para quem precisa de poucos fundos de um mes que nenhum outro bloco le - o
    mes antes da janela de 3 meses, so para a base da serie semanal. Filtrar na
    leitura evita parsear os ~300 mil registros do mes para guardar mil. Se
    outro modulo ja leu a competencia nesta execucao, reaproveita.
    """
    if yyyymm in _REGISTROS:
        return [r for r in _REGISTROS[yyyymm] if r.cnpj in alvo]
    from shelf_monitor.report.retail_flow import read_daily_flows

    caminho = caminho_da_competencia(config, yyyymm)
    return read_daily_flows(caminho, alvo) if caminho else []


def ler_filtrado(
    config: Config,
    yyyymm: str,
    alvo: set[str] | None,
    *,
    force: bool = False,
    revalidar: bool = False,
) -> list:
    """`ler_competencia` com o recorte de CNPJ aplicado em memoria."""
    registros = ler_competencia(config, yyyymm, force=force, revalidar=revalidar)
    if alvo is None:
        return registros
    return [r for r in registros if r.cnpj in alvo]
