"""Somas do painel por dimensao: estrategia, papel, gestora, publico, casa.

Cada linha do mes ganha as dimensoes do produto (as da classe, resolvidas pela
estrutura e pela taxonomia) e as medidas sao somadas na camada do investidor.
Um lugar so para as somas: todas as superficies leem daqui, e os totais por
dimensao fecham com o total do universo (travado em teste).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from shelf_monitor.industria.base import BaseIndustria
from shelf_monitor.industria.cadastro import ROTULOS_PUBLICO
from shelf_monitor.industria.estrutura import PAPEIS
from shelf_monitor.industria.painel import LinhaMes
from shelf_monitor.industria.taxonomia import N1_ORDEM


@dataclass(frozen=True, slots=True)
class Dimensoes:
    n1: str
    n2: str
    origem: str
    confianca: str
    papel: str
    grupo: str
    tipo_gestora: str
    grupo_veiculo: str
    publico: str
    casa_dedicada: str | None
    raiz: str


def dimensoes(base: BaseIndustria, linha: LinhaMes) -> Dimensoes:
    estrutura = base.estrutura.de(linha.classe)
    estrategia = base.taxonomia.de(linha.classe)
    produto = base.cadastro.produto(linha.classe, linha.subclasse)
    return Dimensoes(
        n1=estrategia.n1,
        n2=estrategia.n2,
        origem=estrategia.origem,
        confianca=estrategia.confianca,
        papel=estrutura.papel,
        grupo=estrutura.grupo_estrategia.nome,
        tipo_gestora=estrutura.grupo_estrategia.tipo,
        grupo_veiculo=estrutura.grupo.nome,
        publico=produto.publico if produto else "",
        casa_dedicada=estrutura.casa_dedicada,
        raiz=estrutura.raiz,
    )


@dataclass
class Medidas:
    produtos: int = 0
    pl: float = 0.0
    pl_investidor: float = 0.0
    captacao_liquida: float = 0.0
    fluxo_investidor: float = 0.0
    cotistas: int = 0
    _classes: set[str] = field(default_factory=set, repr=False)

    def somar(self, linha: LinhaMes) -> None:
        self.produtos += 1
        self.pl += linha.pl
        self.pl_investidor += linha.pl_investidor
        self.captacao_liquida += linha.captacao_liquida
        self.fluxo_investidor += linha.fluxo_investidor
        self.cotistas += linha.cotistas
        self._classes.add(linha.classe)

    @property
    def classes(self) -> int:
        return len(self._classes)

    def to_dict(self, total_pl_investidor: float | None = None) -> dict:
        saida = {
            "produtos": self.produtos,
            "classes": self.classes,
            "pl": round(self.pl, 2),
            "pl_investidor": round(self.pl_investidor, 2),
            "captacao_liquida": round(self.captacao_liquida, 2),
            "fluxo_investidor": round(self.fluxo_investidor, 2),
            "cotistas": self.cotistas,
        }
        if total_pl_investidor:
            saida["fatia"] = round(self.pl_investidor / total_pl_investidor, 6)
        return saida


def agregar(
    itens: Iterable[tuple[LinhaMes, Dimensoes]],
    chave: Callable[[LinhaMes, Dimensoes], str | tuple | None],
) -> dict:
    saida: dict = {}
    for linha, dim in itens:
        valor = chave(linha, dim)
        if valor is None:
            continue
        saida.setdefault(valor, Medidas()).somar(linha)
    return saida


def _ordenado(grupos: dict, total: float, rotulo: Callable[[object], dict]) -> list[dict]:
    return [
        {**rotulo(chave), **medidas.to_dict(total)}
        for chave, medidas in sorted(grupos.items(), key=lambda kv: -kv[1].pl_investidor)
    ]


def secoes_da_estrutura(base: BaseIndustria, itens: list[tuple[LinhaMes, Dimensoes]]) -> dict:
    """Papel na estrutura, espelhos e casas dedicadas, com a qualidade da carteira."""
    total = sum(l.pl_investidor for l, _ in itens)
    papeis = agregar(itens, lambda l, d: d.papel)
    casas = agregar(itens, lambda l, d: d.casa_dedicada)
    matriz = agregar(itens, lambda l, d: (d.casa_dedicada, d.grupo) if d.casa_dedicada else None)
    divergentes = sum(
        l.pl_investidor for l, _ in itens if base.estrutura.de(l.classe).alerta_de_divergencia
    )
    sem_carteira = papeis.get("sem_carteira")
    return {
        "papeis": [
            {"papel": chave, "rotulo": PAPEIS[chave], **papeis[chave].to_dict(total)}
            for chave in PAPEIS if chave in papeis
        ],
        "casas_dedicadas": _ordenado(casas, total, lambda c: {"casa": c}),
        "parcerias": [
            {"casa": casa, "gestora": grupo, **medidas.to_dict(total)}
            for (casa, grupo), medidas in sorted(matriz.items(), key=lambda kv: -kv[1].pl_investidor)[:40]
        ],
        "com_carteira": base.mapa is not None,
        "competencias_da_carteira": list(base.mapa.competencias) if base.mapa else [],
        "pl_sem_carteira": round(sem_carteira.pl_investidor, 2) if sem_carteira else 0.0,
        "pl_com_divergencia": round(divergentes, 2),
    }


def secoes_da_estrategia(itens: list[tuple[LinhaMes, Dimensoes]]) -> dict:
    total = sum(l.pl_investidor for l, _ in itens)
    n1 = agregar(itens, lambda l, d: d.n1)
    n2 = agregar(itens, lambda l, d: (d.n1, d.n2))
    origem = agregar(itens, lambda l, d: d.origem)
    confianca = agregar(itens, lambda l, d: d.confianca)
    ordem = {nome: i for i, nome in enumerate(N1_ORDEM)}
    return {
        "n1": [
            {"n1": chave, **n1[chave].to_dict(total)}
            for chave in sorted(n1, key=lambda c: (ordem.get(c, len(ordem)), c))
        ],
        "n2": [
            {"n1": a, "n2": b, **m.to_dict(total)}
            for (a, b), m in sorted(
                n2.items(), key=lambda kv: (ordem.get(kv[0][0], len(ordem)), -kv[1].pl_investidor)
            )
        ],
        "origem": {k: round(m.pl_investidor / total, 4) if total else None for k, m in origem.items()},
        "confianca": {k: round(m.pl_investidor / total, 4) if total else None for k, m in confianca.items()},
    }


def secoes_de_gestoras(itens: list[tuple[LinhaMes, Dimensoes]]) -> dict:
    """Grupo economico da gestora da ESTRATEGIA (a da raiz), e o tipo dele."""
    total = sum(l.pl_investidor for l, _ in itens)
    grupos = agregar(itens, lambda l, d: (d.grupo, d.tipo_gestora))
    tipos = agregar(itens, lambda l, d: d.tipo_gestora)
    curado = sum(m.pl_investidor for (_, tipo), m in grupos.items() if tipo != "nao_classificado")
    return {
        "grupos": [
            {"grupo": g, "tipo": t, **m.to_dict(total)}
            for (g, t), m in sorted(grupos.items(), key=lambda kv: -kv[1].pl_investidor)[:30]
        ],
        "tipos": _ordenado(tipos, total, lambda t: {"tipo": t}),
        "grupos_no_universo": len(grupos),
        "cobertura_curada": round(curado / total, 4) if total else None,
    }


def secoes_de_publico(itens: list[tuple[LinhaMes, Dimensoes]]) -> list[dict]:
    total = sum(l.pl_investidor for l, _ in itens)
    publicos = agregar(itens, lambda l, d: d.publico)
    return [
        {"publico": chave, "rotulo": ROTULOS_PUBLICO.get(chave, chave), **publicos[chave].to_dict(total)}
        for chave in ("geral", "qualificado", "profissional", "") if chave in publicos
    ]
