"""O painel mensal da industria: produto x competencia, na camada do investidor.

Uma linha por produto (CNPJ da classe, ID da subclasse) que entregou o informe
diario no mes, com o motivo do universo, o PL, a captacao, os resgates e a
fatia do PL detida por outros fundos (perfil mensal). E a matriz de onde saem
todos os indicadores; nada e gravado - o painel e refeito dos caches em cada
execucao.

**Existencia no tempo.** O produto existe no mes em que entregou o informe. A
situacao da classe tem data (`universo._operacional_no_mes`); o resto do
cadastro e fixo.

**Camada do investidor.** Com s = fatia do PL em maos de fundos (perfil mensal
da classe no mes, ou o mais recente anterior), r = retorno da classe no mes e
F = captacao liquida declarada:

    pl_inv   = PL x (1 - s)
    f_fundos = PL(t) x s(t) - PL(t-1) x s(t-1) x (1 + r)
    f_inv    = F - f_fundos

Sem PL ou perfil no mes anterior, f_inv cai no rateio F x (1 - s). A
identidade f_inv + f_fundos = F vale sempre, e e travada em teste.
"""
from __future__ import annotations

from dataclasses import dataclass

from shelf_monitor.industria.base import BaseIndustria, competencia_anterior
from shelf_monitor.industria.cadastro import Produto
from shelf_monitor.industria.cotistas import CHAVES, PerfilClasse
from shelf_monitor.industria.universo import (
    UNIVERSO,
    Degrau,
    montar_funil,
    motivo,
)
from shelf_monitor.report.rentabilidade import MesSubclasse
from shelf_monitor.report.shared.limiares import MAX_RETORNO_MENSAL_PLAUSIVEL


@dataclass(slots=True)
class LinhaMes:
    """Um produto num mes do informe, com o motivo e a camada do investidor."""

    classe: str
    subclasse: str
    motivo: str
    pl: float
    pl_anterior: float | None
    captacao: float
    resgates: float
    cotistas: int
    retorno: float | None
    fatia_fundos: float
    fatia_fundos_anterior: float | None
    fonte_fatia: str
    evento_na_cota: bool = False

    @property
    def captacao_liquida(self) -> float:
        return self.captacao - self.resgates

    @property
    def pl_investidor(self) -> float:
        return self.pl * (1.0 - self.fatia_fundos)

    @property
    def fluxo_fundos(self) -> float:
        """Captacao implicita que veio de outros fundos no mes."""
        if self.pl_anterior is None or self.fatia_fundos_anterior is None or self.retorno is None:
            return self.captacao_liquida * self.fatia_fundos
        return (
            self.pl * self.fatia_fundos
            - self.pl_anterior * self.fatia_fundos_anterior * (1.0 + self.retorno)
        )

    @property
    def fluxo_investidor(self) -> float:
        return self.captacao_liquida - self.fluxo_fundos


def _retorno_do_mes(atual: MesSubclasse, anterior: MesSubclasse | None) -> float | None:
    """Retorno da subclasse no mes, com base no fechamento anterior (lamina).

    None quando nao ha cota, e quando o salto passa de
    MAX_RETORNO_MENSAL_PLAUSIVEL: e evento na cota, nao desempenho.
    """
    base = anterior.cota_fim if anterior is not None and anterior.cota_fim > 0 else atual.cota_ini
    if base <= 0 or atual.cota_fim <= 0:
        return None
    retorno = atual.cota_fim / base - 1.0
    return None if abs(retorno) > MAX_RETORNO_MENSAL_PLAUSIVEL else retorno


def _evento_na_cota(atual: MesSubclasse, anterior: MesSubclasse | None) -> bool:
    base = anterior.cota_fim if anterior is not None and anterior.cota_fim > 0 else atual.cota_ini
    return base > 0 and atual.cota_fim > 0 and abs(atual.cota_fim / base - 1.0) > MAX_RETORNO_MENSAL_PLAUSIVEL


def _fatia(perfil: PerfilClasse | None) -> float:
    return perfil.fundos if perfil is not None and perfil.informado else 0.0


def motivo_na_base(
    base: BaseIndustria, produto: Produto | None, cnpj: str, competencia: str
) -> str:
    """O motivo do universo com o que a base sabe: perfil fixo e, para a classe
    sem perfil, a fatia detida por fundos lida da carteira."""
    perfil = base.perfil_fixo.get(cnpj)
    detida = None
    if base.mapa is not None and (perfil is None or not perfil.informado):
        estrutura = base.estrutura.de(cnpj)
        if estrutura.fonte_detida == "carteira":
            detida = estrutura.fatia_detida
    return motivo(produto, perfil, competencia, detida_pela_carteira=detida)


def linhas_do_mes(base: BaseIndustria, competencia: str) -> list[LinhaMes]:
    """As linhas do painel numa competencia, universo inteiro (todo motivo).

    Memorizadas na base: as janelas de 1, 3 e 12 meses leem as mesmas
    competencias.
    """
    chave_memo = ("linhas", competencia)
    if chave_memo in base._memo:
        return base._memo[chave_memo]
    linhas = _linhas_do_mes(base, competencia)
    base._memo[chave_memo] = linhas
    return linhas


def _linhas_do_mes(base: BaseIndustria, competencia: str) -> list[LinhaMes]:
    anterior = competencia_anterior(competencia)
    linhas: list[LinhaMes] = []
    for (cnpj, sub), serie in base.serie.por_subclasse():
        atual = serie.get(competencia)
        if atual is None:
            continue
        mes_anterior = serie.get(anterior)
        produto = base.cadastro.produto(cnpj, sub)
        perfil = base.perfil_do_mes(cnpj, competencia)
        perfil_anterior = base.perfil_do_mes(cnpj, anterior)
        if perfil is not None and perfil is base.perfis.get(competencia, {}).get(cnpj):
            fonte = "perfil"
        else:
            fonte = "perfil_anterior" if perfil is not None else "sem_perfil"
        linhas.append(LinhaMes(
            classe=cnpj,
            subclasse=sub,
            motivo=motivo_na_base(base, produto, cnpj, competencia),
            pl=atual.pl_fim,
            pl_anterior=mes_anterior.pl_fim if mes_anterior is not None else None,
            captacao=atual.captacao,
            resgates=atual.resgates,
            cotistas=atual.cotistas_fim,
            retorno=_retorno_do_mes(atual, mes_anterior),
            fatia_fundos=_fatia(perfil),
            fatia_fundos_anterior=(
                _fatia(perfil_anterior) if perfil_anterior is not None else None
            ),
            fonte_fatia=fonte,
            evento_na_cota=_evento_na_cota(atual, mes_anterior),
        ))
    return linhas


def funil_do_mes(base: BaseIndustria, competencia: str) -> list[Degrau]:
    """O funil da competencia: cadastro inteiro mais o que so esta no informe.

    PL de cada produto: o do informe no fim do mes. Estruturados e ETF nao
    entregam o informe diario, entao entram com o PL do cadastro - uma vez por
    classe, no primeiro produto dela, para a soma nao multiplicar o PL da
    classe pelo numero de subclasses. Produto do universo sem informe no mes
    conta como produto, com PL zero.
    """
    pl_do_informe: dict[tuple[str, str], float] = {}
    for chave, serie in base.serie.por_subclasse():
        atual = serie.get(competencia)
        if atual is not None:
            pl_do_informe[chave] = atual.pl_fim

    itens: list[tuple[str, str, float]] = []
    vistos: set[tuple[str, str]] = set()
    classes_com_pl: set[str] = set()
    for chave in sorted(base.cadastro.produtos):
        produto = base.cadastro.produtos[chave]
        chave_motivo = motivo_na_base(base, produto, produto.classe, competencia)
        if chave in pl_do_informe:
            pl = pl_do_informe[chave]
        elif chave_motivo in ("fora_do_escopo", "etf") and produto.classe not in classes_com_pl:
            pl = produto.pl_cadastro or 0.0
            classes_com_pl.add(produto.classe)
        else:
            pl = 0.0
        itens.append((chave_motivo, produto.classe, pl))
        vistos.add(chave)
    for chave, pl in pl_do_informe.items():
        if chave in vistos:
            continue
        produto = base.cadastro.produto(*chave)
        cnpj = chave[0]
        itens.append((motivo_na_base(base, produto, cnpj, competencia), cnpj, pl))
    return montar_funil(itens)


@dataclass
class Camadas:
    """O PL do universo numa competencia, bruto e na camada do investidor."""

    competencia: str
    pl_bruto: float
    pl_investidor: float
    segmentos: dict[str, float]
    sem_perfil: float
    produtos: int
    classes: int
    cotistas: int

    @property
    def fatia_fundos(self) -> float | None:
        return 1 - self.pl_investidor / self.pl_bruto if self.pl_bruto else None

    def to_dict(self) -> dict:
        return {
            "competencia": self.competencia,
            "pl_bruto": round(self.pl_bruto, 2),
            "pl_investidor": round(self.pl_investidor, 2),
            "segmentos": {k: round(v, 2) for k, v in self.segmentos.items()},
            "sem_perfil": round(self.sem_perfil, 2),
            "produtos": self.produtos,
            "classes": self.classes,
            "cotistas": self.cotistas,
        }


def camadas_do_universo(base: BaseIndustria, linhas: list[LinhaMes]) -> Camadas:
    """PL do universo por segmento de cotista, a partir das linhas do mes."""
    competencia = base.competencia
    segmentos = {chave: 0.0 for chave in CHAVES}
    pl_bruto = pl_inv = sem_perfil = 0.0
    classes: set[str] = set()
    cotistas = produtos = 0
    for linha in linhas:
        if linha.motivo != UNIVERSO:
            continue
        produtos += 1
        classes.add(linha.classe)
        cotistas += linha.cotistas
        pl_bruto += linha.pl
        pl_inv += linha.pl_investidor
        perfil = base.perfil_do_mes(linha.classe, competencia)
        if perfil is None:
            sem_perfil += linha.pl
            continue
        for i, chave in enumerate(CHAVES):
            segmentos[chave] += linha.pl * perfil.fatias[i]
    return Camadas(
        competencia=competencia,
        pl_bruto=pl_bruto,
        pl_investidor=pl_inv,
        segmentos=segmentos,
        sem_perfil=sem_perfil,
        produtos=produtos,
        classes=len(classes),
        cotistas=cotistas,
    )


def produto_da_linha(base: BaseIndustria, linha: LinhaMes) -> Produto | None:
    return base.cadastro.produto(linha.classe, linha.subclasse)
