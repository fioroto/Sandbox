"""As janelas do monitor da industria: 1 mes, 3 meses, no ano e 12 meses.

Toda janela termina na competencia de referencia e declara o que o numero
cobre: o rotulo, as competencias, as datas do primeiro e do ultimo pregao e a
base (o fechamento da competencia anterior, convencao de lamina - o PL de
abertura da captacao em % e o ponto de partida do retorno).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from shelf_monitor.industria.base import BaseIndustria, competencia_anterior
from shelf_monitor.report.retail_flow import months_back
from shelf_monitor.texto import contagem, data_br, periodo_br

ORDEM = ("1m", "3m", "ano", "12m")
ROTULOS = {"1m": "1 mês", "3m": "3 meses", "ano": "no ano", "12m": "12 meses"}


@dataclass(frozen=True)
class Janela:
    chave: str
    rotulo: str
    competencias: tuple[str, ...]
    base: str
    de: date | None
    ate: date | None
    data_base: date | None
    faltando: tuple[str, ...]

    @property
    def completa(self) -> bool:
        return not self.faltando

    def texto(self) -> str:
        """"12 meses (de 01/09/2025 a 31/08/2026)"."""
        return f"{self.rotulo} ({periodo_br(self.de, self.ate)})"

    def to_dict(self) -> dict:
        return {
            "chave": self.chave,
            "rotulo": self.rotulo,
            "competencias": list(self.competencias),
            "base": self.base,
            "de": self.de.isoformat() if self.de else None,
            "ate": self.ate.isoformat() if self.ate else None,
            "data_base": self.data_base.isoformat() if self.data_base else None,
            "base_do_calculo": "fechamento da competência anterior (convenção de lâmina)",
            "faltando": list(self.faltando),
        }


def janelas(base: BaseIndustria) -> dict[str, Janela]:
    """As quatro janelas que terminam na competencia de referencia."""
    competencia = base.competencia
    referencia = date(int(competencia[:4]), int(competencia[4:6]), 1)
    pedidas = {
        "1m": months_back(1, referencia=referencia),
        "3m": months_back(3, referencia=referencia),
        "ano": [m for m in months_back(12, referencia=referencia) if m[:4] == competencia[:4]],
        "12m": months_back(12, referencia=referencia),
    }
    saida = {}
    for chave in ORDEM:
        meses = pedidas[chave]
        presentes = [m for m in meses if m in base.serie.meses]
        anterior = competencia_anterior(meses[0])
        de = base.serie.calendario(presentes[0])[0] if presentes else None
        ate = base.serie.calendario(presentes[-1])[1] if presentes else None
        data_base = base.serie.calendario(anterior)[1] if anterior in base.serie.meses else None
        saida[chave] = Janela(
            chave=chave,
            rotulo=ROTULOS[chave],
            competencias=tuple(presentes),
            base=anterior,
            de=de,
            ate=ate,
            data_base=data_base,
            faltando=tuple(m for m in meses if m not in presentes),
        )
    return saida


def prazos(jan: dict[str, Janela]) -> list[str]:
    """Uma linha por janela, para sair junto dos numeros em toda superficie."""
    linhas = []
    for chave in ORDEM:
        janela = jan.get(chave)
        if janela is None or not janela.competencias:
            continue
        texto = f"{janela.rotulo}: {periodo_br(janela.de, janela.ate)}"
        if janela.data_base:
            texto += f"; base no fechamento de {data_br(janela.data_base)}"
        if janela.faltando:
            texto += f" (sem informe em {contagem(len(janela.faltando), 'competência', 'competências')})"
        linhas.append(texto)
    return linhas
