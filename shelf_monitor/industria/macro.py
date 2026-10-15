"""O contexto macro da industria: CDI, Selic, IPCA e dolar, pelo SGS do BCB.

Duas perguntas da Fase 2 dependem dele:

- **os fundos batem o CDI?** (G1) - o CDI acumulado de cada janela, na mesma
  convencao do retorno: da base (fechamento da competencia anterior a janela)
  ao ultimo pregao da competencia de referencia. Entram as taxas diarias com
  data depois da base e ate o fim, inclusive - a convencao do "CDI do mes" da
  lamina, que soma os dias uteis do proprio mes;
- **o que o macro fazia enquanto o dinheiro andava?** (H2) - CDI e IPCA de
  cada mes, Selic e dolar no fechamento, ao lado da captacao por macroclasse.

Sem o SGS (sem rede e sem cache), o bloco sai sem a secao, com o motivo, e o
retorno continua sem o "% do CDI".
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date

from shelf_monitor.config import Config
from shelf_monitor.market import sgs

CHAVES = ("cdi", "selic_meta", "ipca", "dolar")

# Dias uteis no ano: a convencao do CDI anualizado.
DIAS_UTEIS_NO_ANO = 252


@dataclass
class Macro:
    """As series do SGS de uma execucao, ja em (data, valor) ordenados."""

    series: dict[str, sgs.Observacoes]
    avisos: list[str] = field(default_factory=list)

    def _datas(self, chave: str) -> list[date]:
        return [d for d, _ in self.series.get(chave, [])]

    def cdi_entre(self, base: date | None, fim: date | None) -> float | None:
        """CDI acumulado de `base` (exclusive) a `fim` (inclusive), em fracao.

        None quando a serie nao cobre a janela: sem uma observacao ate a base e
        outra no fim (ou depois), o produto sairia curto sem aviso.
        """
        cdi = self.series.get("cdi") or []
        if base is None or fim is None or not cdi or fim <= base:
            return None
        if cdi[0][0] > base or cdi[-1][0] < fim:
            return None
        datas = self._datas("cdi")
        fator = 1.0
        for _, taxa in cdi[bisect_right(datas, base):bisect_right(datas, fim)]:
            fator *= 1.0 + taxa / 100.0
        return fator - 1.0

    def valor_em(self, chave: str, dia: date | None) -> float | None:
        """O ultimo valor da serie ate o dia (inclusive): Selic e dolar no fechamento."""
        observacoes = self.series.get(chave) or []
        if dia is None or not observacoes:
            return None
        i = bisect_right(self._datas(chave), dia)
        return observacoes[i - 1][1] if i else None

    def ipca_do_mes(self, competencia: str) -> float | None:
        """IPCA da competencia (AAAAMM), em fracao."""
        ipca = self.series.get("ipca") or []
        alvo = date(int(competencia[:4]), int(competencia[4:6]), 1)
        datas = self._datas("ipca")
        i = bisect_left(datas, alvo)
        if i < len(ipca) and ipca[i][0] == alvo:
            return ipca[i][1] / 100.0
        return None

    def ipca_acumulado(self, competencias) -> float | None:
        """IPCA composto das competencias; None se faltar alguma."""
        fator = 1.0
        for competencia in competencias:
            mensal = self.ipca_do_mes(competencia)
            if mensal is None:
                return None
            fator *= 1.0 + mensal
        return fator - 1.0 if competencias else None

    def cdi_ao_ano(self, dia: date | None) -> float | None:
        """A taxa diaria do CDI no dia, anualizada em 252 dias uteis."""
        taxa = self.valor_em("cdi", dia)
        return None if taxa is None else (1.0 + taxa / 100.0) ** DIAS_UTEIS_NO_ANO - 1.0


def carregar_macro(
    config: Config, de: date, ate: date, *, baixar: bool = True, hoje: date | None = None
) -> Macro:
    series, avisos = sgs.carregar(config, CHAVES, de, ate, baixar=baixar, hoje=hoje)
    return Macro(series, avisos)


def _variacao(fim: float | None, inicio: float | None) -> float | None:
    if fim is None or not inicio:
        return None
    return fim / inicio - 1.0


def janela_macro(macro: Macro, janela) -> dict:
    """CDI, IPCA, Selic e dolar de uma janela (`janelas.Janela`)."""
    dolar_base = macro.valor_em("dolar", janela.data_base)
    dolar_fim = macro.valor_em("dolar", janela.ate)
    cdi = macro.cdi_entre(janela.data_base, janela.ate)
    ipca = macro.ipca_acumulado(janela.competencias)
    return {
        "cdi": None if cdi is None else round(cdi, 6),
        "ipca": None if ipca is None else round(ipca, 6),
        "selic_base": macro.valor_em("selic_meta", janela.data_base),
        "selic_fim": macro.valor_em("selic_meta", janela.ate),
        "dolar_base": dolar_base,
        "dolar_fim": dolar_fim,
        "dolar_variacao": None if _variacao(dolar_fim, dolar_base) is None
        else round(_variacao(dolar_fim, dolar_base), 6),
    }
