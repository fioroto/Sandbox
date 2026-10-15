"""Dinamica da captacao (Fase 2B): giro, concentracao, entrantes, fluxo x
retorno e a captacao implicita por tipo de cotista.

Tudo sai das linhas do painel (`painel.linhas_do_mes`), no universo
disponivel, e responde a uma pergunta da secao 5 da spec:

- **giro** (B5): quanto entra e quanto sai em relacao ao tamanho. Usa o fluxo
  DECLARADO (captacao e resgates brutos, com o dinheiro de fundos) sobre o PL
  bruto medio - a mesma camada no numerador e no denominador. A camada do
  investidor so tem o liquido;
- **concentracao** (C3): HHI e fatia das 5 maiores gestoras (grupo economico
  da gestora da estrategia) em cada peer group, no fim e na base da janela;
- **entrantes e saidas** (C4): grupos gestores cujo primeiro produto no
  universo aparece dentro da janela, e os que tinham produto e nao tem mais.
  A serie comeca na base: "entrou" quer dizer "nao tinha produto no universo
  na base", nao "nunca teve";
- **fluxo x retorno** (G2): a captacao da janela em % do PL de abertura, por
  quartil de retorno da mesma janela no peer group. Mesma janela nos dois
  eixos: mostra associacao, nao causa;
- **captacao implicita por segmento** (D4): a formula da camada do investidor
  aplicada a cada tipo de cotista do perfil mensal (secao 4 da spec), so no
  agregado e com o ruido do arredondamento do perfil ao lado.
"""
from __future__ import annotations

import math
from collections.abc import Callable

from shelf_monitor.industria.agregados import dimensoes
from shelf_monitor.industria.base import BaseIndustria, competencia_anterior
from shelf_monitor.industria.cotistas import CHAVES, SEGMENTOS
from shelf_monitor.industria.fluxos import Chave, fluxos_por
from shelf_monitor.industria.janelas import Janela
from shelf_monitor.industria.painel import LinhaMes, linhas_do_mes
from shelf_monitor.industria.taxonomia import N1_ORDEM
from shelf_monitor.industria.universo import UNIVERSO
from shelf_monitor.report.shared.limiares import (
    ERRO_DA_FATIA_DO_PERFIL,
    MULTIPLO_DO_RUIDO_PUBLICAVEL,
)

# Quantas gestoras contam no "top" da concentracao e nas listas de entrantes.
TOP_CONCENTRACAO = 5
TOP_LISTA = 20

ROTULOS_QUARTIL = {
    1: "1º quartil (melhor retorno)",
    2: "2º quartil",
    3: "3º quartil",
    4: "4º quartil (pior retorno)",
    0: "Sem retorno na janela",
}


def _do_universo(base: BaseIndustria, mes: str):
    for linha in linhas_do_mes(base, mes):
        if linha.motivo == UNIVERSO:
            yield linha, dimensoes(base, linha)


def _ordem_n1(n1: str) -> int:
    return N1_ORDEM.index(n1) if n1 in N1_ORDEM else len(N1_ORDEM)


# --- giro (B5) -------------------------------------------------------------------


def giro_por(base: BaseIndustria, janela: Janela, chave: Chave) -> dict:
    """grupo -> captacao e resgates brutos, PL bruto medio, entrada e giro.

    PL medio: a media do PL bruto no fim de cada competencia da janela. Giro =
    resgates / PL medio; entrada = captacao bruta / PL medio. Em 12 meses, sao
    taxas anuais.
    """
    soma_pl: dict = {}
    for mes in janela.competencias:
        for linha, dim in _do_universo(base, mes):
            grupo = chave(linha, dim)
            if grupo is not None:
                soma_pl[grupo] = soma_pl.get(grupo, 0.0) + linha.pl
    meses = len(janela.competencias)
    saida = {}
    for grupo, fluxo in fluxos_por(base, janela, chave).items():
        pl_medio = soma_pl.get(grupo, 0.0) / meses if meses else 0.0
        saida[grupo] = {
            "captacao": round(fluxo.captacao, 2),
            "resgates": round(fluxo.resgates, 2),
            "captacao_liquida": round(fluxo.captacao_liquida, 2),
            "pl_medio": round(pl_medio, 2),
            "entrada": round(fluxo.captacao / pl_medio, 4) if pl_medio > 0 else None,
            "giro": round(fluxo.resgates / pl_medio, 4) if pl_medio > 0 else None,
        }
    return saida


# --- concentracao (C3) -------------------------------------------------------------


def hhi(valores) -> float | None:
    """Indice Herfindahl-Hirschman, de 0 a 10.000, das fatias positivas."""
    positivos = [v for v in valores if v > 0]
    total = sum(positivos)
    if total <= 0:
        return None
    return round(sum((v / total) ** 2 for v in positivos) * 10_000, 1)


def fatia_dos_maiores(valores, n: int = TOP_CONCENTRACAO) -> float | None:
    positivos = sorted((v for v in valores if v > 0), reverse=True)
    total = sum(positivos)
    return round(sum(positivos[:n]) / total, 4) if total > 0 else None


def _pl_por_gestora(base: BaseIndustria, mes: str, chave: Callable) -> dict:
    """grupo -> {gestora: PL do investidor} no fim do mes."""
    saida: dict = {}
    if mes not in base.serie.meses:
        return saida
    for linha, dim in _do_universo(base, mes):
        grupo = chave(linha, dim)
        if grupo is None:
            continue
        por_gestora = saida.setdefault(grupo, {})
        por_gestora[dim.grupo] = por_gestora.get(dim.grupo, 0.0) + linha.pl_investidor
    return saida


def concentracao_por(base: BaseIndustria, janela: Janela, chave: Callable) -> dict:
    """grupo -> HHI, fatia das 5 maiores e a lider, no fim e na base da janela."""
    if not janela.competencias:
        return {}
    fim = _pl_por_gestora(base, janela.competencias[-1], chave)
    antes = _pl_por_gestora(base, janela.base, chave)
    saida = {}
    for grupo, por_gestora in fim.items():
        anterior = antes.get(grupo, {})
        lider = max(por_gestora, key=por_gestora.get) if por_gestora else None
        total = sum(v for v in por_gestora.values() if v > 0)
        indice, indice_antes = hhi(por_gestora.values()), hhi(anterior.values())
        saida[grupo] = {
            "pl_investidor": round(total, 2),
            "gestoras": sum(1 for v in por_gestora.values() if v > 0),
            "gestoras_12m_antes": sum(1 for v in anterior.values() if v > 0),
            "hhi": indice,
            "hhi_12m_antes": indice_antes,
            "variacao_hhi": None if indice is None or indice_antes is None else round(indice - indice_antes, 1),
            "top5": fatia_dos_maiores(por_gestora.values()),
            "top5_12m_antes": fatia_dos_maiores(anterior.values()),
            "lider": lider,
            "fatia_do_lider": round(por_gestora[lider] / total, 4) if lider and total > 0 else None,
        }
    return saida


# --- entrantes e saidas (C4) ------------------------------------------------------------


def gestoras_que_entraram_e_sairam(base: BaseIndustria, janela: Janela) -> dict:
    """Grupos gestores com o primeiro produto no universo dentro da janela, e os
    que estavam no universo na base ou na janela e nao estao no fim."""
    if not janela.competencias:
        return {"entraram": {"grupos": 0, "lista": []}, "sairam": {"grupos": 0, "lista": []}}
    ultima = janela.competencias[-1]
    meses_por_grupo: dict[str, list[str]] = {}
    tipo: dict[str, str] = {}
    pl_do_mes: dict[tuple[str, str], float] = {}
    n2_do_grupo: dict[str, dict[str, float]] = {}
    for mes in base.serie.meses:
        if mes > ultima:
            continue
        for linha, dim in _do_universo(base, mes):
            meses = meses_por_grupo.setdefault(dim.grupo, [])
            if not meses or meses[-1] != mes:
                meses.append(mes)
            tipo[dim.grupo] = dim.tipo_gestora
            pl_do_mes[(dim.grupo, mes)] = pl_do_mes.get((dim.grupo, mes), 0.0) + linha.pl_investidor
            if mes == ultima:
                pesos = n2_do_grupo.setdefault(dim.grupo, {})
                pesos[dim.n2] = pesos.get(dim.n2, 0.0) + linha.pl_investidor
    fluxos = fluxos_por(base, janela, lambda l, d: d.grupo)
    competencias = set(janela.competencias)
    entraram, sairam = [], []
    for grupo, meses in meses_por_grupo.items():
        primeira, ultima_presenca = meses[0], meses[-1]
        if primeira in competencias:
            pesos = n2_do_grupo.get(grupo) or {}
            fluxo = fluxos.get(grupo)
            entraram.append({
                "grupo": grupo, "tipo": tipo.get(grupo), "primeira": primeira,
                "presente_no_fim": ultima_presenca == ultima,
                "pl_investidor": round(pl_do_mes.get((grupo, ultima), 0.0), 2),
                "fluxo_investidor": round(fluxo.fluxo_investidor, 2) if fluxo else 0.0,
                "n2": max(pesos, key=pesos.get) if pesos else None,
            })
        if ultima_presenca != ultima and (ultima_presenca in competencias or ultima_presenca == janela.base):
            sairam.append({
                "grupo": grupo, "tipo": tipo.get(grupo), "ultima": ultima_presenca,
                "pl_investidor": round(pl_do_mes.get((grupo, ultima_presenca), 0.0), 2),
            })
    entraram.sort(key=lambda i: (-i["pl_investidor"], i["grupo"]))
    sairam.sort(key=lambda i: (-i["pl_investidor"], i["grupo"]))
    total_fluxo = sum(f.fluxo_investidor for f in fluxos.values())
    fluxo_dos_novos = sum(i["fluxo_investidor"] for i in entraram)
    return {
        "grupos_no_fim": sum(1 for m in meses_por_grupo.values() if m[-1] == ultima),
        "grupos_na_base": sum(1 for m in meses_por_grupo.values() if janela.base in m),
        "entraram": {
            "grupos": len(entraram),
            "pl_investidor": round(sum(i["pl_investidor"] for i in entraram), 2),
            "fluxo_investidor": round(fluxo_dos_novos, 2),
            "lista": entraram[:TOP_LISTA],
        },
        "sairam": {
            "grupos": len(sairam),
            "pl_investidor": round(sum(i["pl_investidor"] for i in sairam), 2),
            "lista": sairam[:TOP_LISTA],
        },
        "fluxo_do_universo": round(total_fluxo, 2),
    }


# --- fluxo x retorno (G2) ------------------------------------------------------------


def fluxo_por_quartil(base: BaseIndustria, janela: Janela, por_classe: dict) -> list[dict]:
    """A captacao da janela por quartil de retorno da classe no peer group.

    `por_classe` e o de `desempenho.rentabilidade_por_peer_group` na mesma
    janela. Classe sem quartil (sem retorno, ou fora do universo no fim) vai
    para a linha "sem retorno", para a soma fechar com o universo.
    """
    def quartil(linha: LinhaMes, _dim) -> int:
        return (por_classe.get(linha.classe) or {}).get("quartil") or 0

    fluxos = fluxos_por(base, janela, quartil)
    saida = []
    for q in (1, 2, 3, 4, 0):
        fluxo = fluxos.get(q)
        if fluxo is None:
            continue
        saida.append({
            "quartil": q or None,
            "rotulo": ROTULOS_QUARTIL[q],
            "classes_fim": fluxo.classes_fim,
            "pl_investidor_abertura": round(fluxo.pl_investidor_abertura, 2),
            "pl_investidor_fim": round(fluxo.pl_investidor_fim, 2),
            "fluxo_investidor": round(fluxo.fluxo_investidor, 2),
            "sobre_pl": None if fluxo.sobre_pl is None else round(fluxo.sobre_pl, 6),
        })
    return saida


# --- captacao implicita por segmento (D4) ------------------------------------------------

SEM_PERFIL = "sem_perfil"
_FUNDOS = "fundos"
_DO_INVESTIDOR = tuple(c for c in CHAVES if c != _FUNDOS)
_ROTULOS = {s.chave: s.rotulo for s in SEGMENTOS} | {SEM_PERFIL: "Sem perfil mensal"}
_GRUPOS = {s.chave: s.grupo for s in SEGMENTOS} | {SEM_PERFIL: "outros"}


class _Acumulador:
    """Soma por segmento e o ruido do arredondamento do perfil, num grupo."""

    def __init__(self) -> None:
        self.valores = {c: 0.0 for c in (*CHAVES, SEM_PERFIL)}
        self.fluxo_investidor = 0.0
        self.captacao_liquida = 0.0
        # (classe, mes) -> [PL(t) mais o fluxo rateado, PL(t-1) x (1 + r)]: as
        # subclasses de uma classe dividem o mesmo perfil, entao o erro do
        # arredondamento delas soma antes de elevar ao quadrado.
        self._exposicao: dict[tuple[str, str], list[float]] = {}

    def somar(self, linha: LinhaMes, mes: str, atual, anterior) -> None:
        self.fluxo_investidor += linha.fluxo_investidor
        self.captacao_liquida += linha.captacao_liquida
        if atual is None:
            self.valores[SEM_PERFIL] += linha.captacao_liquida
            return
        exposicao = self._exposicao.setdefault((linha.classe, mes), [0.0, 0.0])
        if linha.pl_anterior is None or anterior is None or linha.retorno is None:
            # Sem o mes anterior, o rateio do painel: o fluxo pelas fatias de t.
            for i, chave in enumerate(CHAVES):
                self.valores[chave] += linha.captacao_liquida * atual.fatias[i]
            exposicao[0] += abs(linha.captacao_liquida)
            return
        corrigido = linha.pl_anterior * (1.0 + linha.retorno)
        for i, chave in enumerate(CHAVES):
            self.valores[chave] += linha.pl * atual.fatias[i] - corrigido * anterior.fatias[i]
        exposicao[0] += linha.pl
        exposicao[1] += corrigido

    @property
    def ruido(self) -> float:
        """Desvio do erro de arredondamento somado, igual para todo segmento.

        Conservador: trata o erro de t e o de t-1 como independentes. Quando a
        fatia nao muda de um mes para o outro, os dois se cancelam.
        """
        variancia = sum(a * a + b * b for a, b in self._exposicao.values())
        return math.sqrt(variancia) * ERRO_DA_FATIA_DO_PERFIL

    def to_dict(self) -> dict:
        ruido = self.ruido
        piso = ruido * MULTIPLO_DO_RUIDO_PUBLICAVEL
        segmentos = []
        for chave in (*_DO_INVESTIDOR, SEM_PERFIL):
            valor = self.valores[chave]
            if chave == SEM_PERFIL and not valor:
                continue
            segmentos.append({
                "chave": chave,
                "rotulo": _ROTULOS[chave],
                "grupo": _GRUPOS[chave],
                "captacao": round(valor, 2),
                "no_ruido": chave != SEM_PERFIL and abs(valor) < piso,
            })
        soma = sum(s["captacao"] for s in segmentos)
        return {
            "segmentos": segmentos,
            "fundos": round(self.valores[_FUNDOS], 2),
            "fluxo_investidor": round(self.fluxo_investidor, 2),
            "captacao_liquida": round(self.captacao_liquida, 2),
            # O que a formula por segmento nao explica do fluxo do investidor:
            # a variacao do PL que nao e captacao nem rendimento (incorporacao,
            # amortizacao, defasagem entre cota e fluxo).
            "residuo": round(self.fluxo_investidor - soma, 2),
            "ruido": round(ruido, 2),
            "piso": round(piso, 2),
        }


def captacao_por_segmento(base: BaseIndustria, janela: Janela, chave: Chave | None = None) -> dict:
    """grupo -> captacao implicita de cada tipo de cotista na janela.

    Por classe e mes, com w = fatia do segmento no perfil e r = retorno:
    PL(t) x w(t) - PL(t-1) x w(t-1) x (1 + r); sem o mes anterior, o fluxo
    declarado x w(t). A soma dos segmentos do investidor mais o residuo e o
    fluxo do investidor; o segmento "fundos" e o fluxo dos fundos do painel.
    """
    grupos: dict = {}
    for mes in janela.competencias:
        anterior_mes = competencia_anterior(mes)
        for linha, dim in _do_universo(base, mes):
            grupo = chave(linha, dim) if chave else "total"
            if grupo is None:
                continue
            atual = base.perfil_do_mes(linha.classe, mes)
            anterior = base.perfil_do_mes(linha.classe, anterior_mes) if atual is not None else None
            grupos.setdefault(grupo, _Acumulador()).somar(linha, mes, atual, anterior)
    return {grupo: acumulador.to_dict() for grupo, acumulador in grupos.items()}


# --- a secao do bloco ----------------------------------------------------------------------


def secao_dinamica(base: BaseIndustria, jan: dict[str, Janela], rentabilidade: dict) -> dict:
    """A chave `dinamica` do bloco, toda na janela de 12 meses."""
    doze = jan["12m"]
    giro_n1 = giro_por(base, doze, lambda l, d: d.n1)
    giro_total = giro_por(base, doze, lambda l, d: "total").get("total")
    conc_n2 = concentracao_por(base, doze, lambda l, d: (d.n1, d.n2))
    conc_total = concentracao_por(base, doze, lambda l, d: "total").get("total")
    seg_total = captacao_por_segmento(base, doze).get("total")
    seg_n1 = captacao_por_segmento(base, doze, lambda l, d: d.n1)
    seg_n2 = captacao_por_segmento(base, doze, lambda l, d: (d.n1, d.n2))
    return {
        "janela": doze.chave,
        "giro": {
            "total": giro_total,
            "n1": [{"n1": n1, **giro_n1[n1]} for n1 in sorted(giro_n1, key=_ordem_n1)],
        },
        "concentracao": {
            "universo": conc_total,
            "n2": [
                {"n1": n1, "n2": n2, **item}
                for (n1, n2), item in sorted(conc_n2.items(), key=lambda kv: (_ordem_n1(kv[0][0]), -kv[1]["pl_investidor"]))
            ],
        },
        "gestoras": gestoras_que_entraram_e_sairam(base, doze),
        "fluxo_x_retorno": fluxo_por_quartil(base, doze, rentabilidade["12m"]["por_classe"]),
        "captacao_por_segmento": {
            "total": seg_total,
            "n1": [{"n1": n1, **seg_n1[n1]} for n1 in sorted(seg_n1, key=_ordem_n1)],
            "n2": [
                {"n1": n1, "n2": n2, **item}
                for (n1, n2), item in sorted(seg_n2.items(), key=lambda kv: (_ordem_n1(kv[0][0]), kv[0][1]))
            ],
        },
    }
