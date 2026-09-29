"""Captacao por janela e por dimensao, na camada do investidor.

Tudo aqui soma linhas do painel (`painel.linhas_do_mes`) das competencias de
uma janela. Tres grandezas para cada grupo:

- **fluxo do investidor**: a captacao liquida declarada menos o que veio de
  outros fundos (ver `painel`). E o numero que responde "para onde vai o
  dinheiro de quem investe", sem contar duas vezes o FoF e o espelho;
- **PL de abertura e de fim** da camada do investidor, para o fluxo em % e
  para a decomposicao;
- **rendimento**: o PL do investidor do mes anterior vezes o retorno do mes.

A decomposicao fecha por construcao: variacao do PL = fluxo + rendimento +
residuo. O residuo (incorporacao, amortizacao, fundo que parou de informar,
defasagem entre cota e fluxo) e publicado como qualidade, nunca escondido.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from shelf_monitor.industria.agregados import Dimensoes, dimensoes
from shelf_monitor.industria.base import BaseIndustria, competencia_anterior
from shelf_monitor.industria.cotistas import CHAVES
from shelf_monitor.industria.janelas import Janela
from shelf_monitor.industria.painel import LinhaMes, linhas_do_mes
from shelf_monitor.industria.universo import UNIVERSO, inicio_do_mes

Chave = Callable[[LinhaMes, Dimensoes], object]


@dataclass
class Fluxo:
    """O que um grupo de produtos fez numa janela."""

    captacao: float = 0.0
    resgates: float = 0.0
    fluxo_investidor: float = 0.0
    fluxo_fundos: float = 0.0
    rendimento_investidor: float = 0.0
    pl_investidor_abertura: float = 0.0
    pl_investidor_fim: float = 0.0
    pl_fim: float = 0.0
    cotistas_abertura: int = 0
    cotistas_fim: int = 0
    produtos_fim: int = 0
    _classes_fim: set[str] = field(default_factory=set, repr=False)

    @property
    def captacao_liquida(self) -> float:
        return self.captacao - self.resgates

    @property
    def sobre_pl(self) -> float | None:
        """Fluxo do investidor em fracao do PL de abertura."""
        if self.pl_investidor_abertura <= 0:
            return None
        return self.fluxo_investidor / self.pl_investidor_abertura

    @property
    def variacao_pl(self) -> float:
        return self.pl_investidor_fim - self.pl_investidor_abertura

    @property
    def residuo(self) -> float:
        return self.variacao_pl - self.fluxo_investidor - self.rendimento_investidor

    @property
    def classes_fim(self) -> int:
        return len(self._classes_fim)

    def to_dict(self) -> dict:
        return {
            "captacao": round(self.captacao, 2),
            "resgates": round(self.resgates, 2),
            "captacao_liquida": round(self.captacao_liquida, 2),
            "fluxo_investidor": round(self.fluxo_investidor, 2),
            "fluxo_fundos": round(self.fluxo_fundos, 2),
            "rendimento_investidor": round(self.rendimento_investidor, 2),
            "pl_investidor_abertura": round(self.pl_investidor_abertura, 2),
            "pl_investidor_fim": round(self.pl_investidor_fim, 2),
            "pl_fim": round(self.pl_fim, 2),
            "variacao_pl": round(self.variacao_pl, 2),
            "residuo": round(self.residuo, 2),
            "sobre_pl": None if self.sobre_pl is None else round(self.sobre_pl, 6),
            "cotistas_abertura": self.cotistas_abertura,
            "cotistas_fim": self.cotistas_fim,
            "produtos_fim": self.produtos_fim,
            "classes_fim": self.classes_fim,
        }


def _rendimento(linha: LinhaMes) -> float:
    if linha.pl_anterior is None or linha.retorno is None:
        return 0.0
    fatia = linha.fatia_fundos_anterior if linha.fatia_fundos_anterior is not None else linha.fatia_fundos
    return linha.pl_anterior * (1.0 - fatia) * linha.retorno


def fluxos_por(
    base: BaseIndustria,
    janela: Janela,
    chave: Chave,
    motivos: tuple[str, ...] = (UNIVERSO,),
) -> dict:
    """Fluxo de cada grupo (chave) na janela, para os motivos pedidos."""
    saida: dict = {}
    if not janela.competencias:
        return saida
    ultima = janela.competencias[-1]
    for mes in janela.competencias:
        for linha in linhas_do_mes(base, mes):
            if linha.motivo not in motivos:
                continue
            grupo = chave(linha, dimensoes(base, linha))
            if grupo is None:
                continue
            fluxo = saida.setdefault(grupo, Fluxo())
            fluxo.captacao += linha.captacao
            fluxo.resgates += linha.resgates
            fluxo.fluxo_investidor += linha.fluxo_investidor
            fluxo.fluxo_fundos += linha.fluxo_fundos
            fluxo.rendimento_investidor += _rendimento(linha)
            if mes == ultima:
                fluxo.pl_investidor_fim += linha.pl_investidor
                fluxo.pl_fim += linha.pl
                fluxo.cotistas_fim += linha.cotistas
                fluxo.produtos_fim += 1
                fluxo._classes_fim.add(linha.classe)
    if janela.base in base.serie.meses:
        for linha in linhas_do_mes(base, janela.base):
            if linha.motivo not in motivos:
                continue
            grupo = chave(linha, dimensoes(base, linha))
            if grupo is None:
                continue
            fluxo = saida.setdefault(grupo, Fluxo())
            fluxo.pl_investidor_abertura += linha.pl_investidor
            fluxo.cotistas_abertura += linha.cotistas
    return saida


def total(base: BaseIndustria, janela: Janela, motivos: tuple[str, ...] = (UNIVERSO,)) -> Fluxo:
    return fluxos_por(base, janela, lambda l, d: "total", motivos).get("total", Fluxo())


def historico_mensal(base: BaseIndustria, motivos: tuple[str, ...] = (UNIVERSO,)) -> list[dict]:
    """Uma linha por competencia da serie: PL, fluxo, rendimento, residuo."""
    saida = []
    anterior_pl_inv = None
    for mes in base.serie.meses:
        linhas = [l for l in linhas_do_mes(base, mes) if l.motivo in motivos]
        pl_inv = sum(l.pl_investidor for l in linhas)
        fluxo = sum(l.fluxo_investidor for l in linhas)
        rendimento = sum(_rendimento(l) for l in linhas)
        item = {
            "competencia": mes,
            "pl": round(sum(l.pl for l in linhas), 2),
            "pl_investidor": round(pl_inv, 2),
            "captacao_liquida": round(sum(l.captacao_liquida for l in linhas), 2),
            "fluxo_investidor": round(fluxo, 2),
            "rendimento_investidor": round(rendimento, 2),
            "cotistas": sum(l.cotistas for l in linhas),
            "produtos": len(linhas),
            "residuo": (
                round(pl_inv - anterior_pl_inv - fluxo - rendimento, 2)
                if anterior_pl_inv is not None else None
            ),
        }
        saida.append(item)
        anterior_pl_inv = pl_inv
    return saida


def mapa_de_calor(base: BaseIndustria, chave: Chave, meses: list[str]) -> dict:
    """grupo -> {competencia: fluxo do investidor em fracao do PL do mes anterior}."""
    pl_do_mes: dict[str, dict] = {}
    fluxo_do_mes: dict[str, dict] = {}
    for mes in set(meses) | {competencia_anterior(m) for m in meses}:
        if mes not in base.serie.meses:
            continue
        pl, fluxo = {}, {}
        for linha in linhas_do_mes(base, mes):
            if linha.motivo != UNIVERSO:
                continue
            grupo = chave(linha, dimensoes(base, linha))
            if grupo is None:
                continue
            pl[grupo] = pl.get(grupo, 0.0) + linha.pl_investidor
            fluxo[grupo] = fluxo.get(grupo, 0.0) + linha.fluxo_investidor
        pl_do_mes[mes], fluxo_do_mes[mes] = pl, fluxo
    saida: dict = {}
    for mes in meses:
        anterior = pl_do_mes.get(competencia_anterior(mes), {})
        for grupo, valor in fluxo_do_mes.get(mes, {}).items():
            base_pl = anterior.get(grupo, 0.0)
            saida.setdefault(grupo, {})[mes] = round(valor / base_pl, 6) if base_pl > 0 else None
    return saida


def segmentos_do_mes(base: BaseIndustria, mes: str, chave: Chave | None = None) -> dict:
    """grupo -> {segmento: PL}. Sem `chave`, um grupo so ("total")."""
    saida: dict = {}
    if mes not in base.serie.meses:
        return saida
    for linha in linhas_do_mes(base, mes):
        if linha.motivo != UNIVERSO:
            continue
        perfil = base.perfil_do_mes(linha.classe, mes)
        if perfil is None:
            continue
        grupo = chave(linha, dimensoes(base, linha)) if chave else "total"
        if grupo is None:
            continue
        acumulado = saida.setdefault(grupo, {c: 0.0 for c in CHAVES})
        for i, segmento in enumerate(CHAVES):
            acumulado[segmento] += linha.pl * perfil.fatias[i]
    return saida


def presenca(base: BaseIndustria) -> dict[tuple[str, str], tuple[str, str]]:
    """(classe, subclasse) -> (primeira, ultima) competencia no universo."""
    chave_memo = ("presenca",)
    if chave_memo in base._memo:
        return base._memo[chave_memo]
    saida: dict[tuple[str, str], tuple[str, str]] = {}
    for mes in base.serie.meses:
        for linha in linhas_do_mes(base, mes):
            if linha.motivo != UNIVERSO:
                continue
            chave = (linha.classe, linha.subclasse)
            primeira = saida.get(chave, (mes, mes))[0]
            saida[chave] = (primeira, mes)
    base._memo[chave_memo] = saida
    return saida


def lancamentos_e_encerramentos(base: BaseIndustria, janela: Janela) -> dict:
    """Produtos que entraram e sairam do universo na janela.

    Entrou: a primeira competencia no universo cai na janela, a serie ja
    existia antes dela (a primeira competencia da serie nao conta como
    lancamento) e o cadastro da classe comeca no maximo 45 dias antes.
    Saiu: estava no universo na janela ou na base e nao esta na competencia
    de referencia.
    """
    competencias = set(janela.competencias)
    inicio_da_serie = base.serie.meses[0] if base.serie.meses else None
    ultima = janela.competencias[-1] if janela.competencias else None
    entrou, saiu = [], []
    for (classe, sub), (primeira, ultima_presenca) in presenca(base).items():
        produto = base.cadastro.produto(classe, sub)
        if primeira in competencias and primeira != inicio_da_serie:
            inicio = produto.inicio if produto else None
            limite = inicio_do_mes(janela.competencias[0])
            if inicio is None or (limite - inicio).days <= 45:
                entrou.append((classe, sub, primeira))
        if ultima and ultima_presenca != ultima and (
            ultima_presenca in competencias or ultima_presenca == janela.base
        ):
            saiu.append((classe, sub, ultima_presenca))
    return {"entrou": entrou, "saiu": saiu}
