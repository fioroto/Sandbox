"""A semana de referencia do informe diario da CVM, e os dias que contam nela.

O informe do mes corrente e regravado toda noite, e os ultimos dias chegam com
parte dos fundos: em 28/09/2026 a sexta 25/09 tinha 2% das linhas. Somado, um
dia assim puxa a media por dia para zero - numa semana ele e 20 a 25% do
denominador. A regra de que dia conta e de qual semana vale mora aqui, uma vez,
para o flash da industria e a captacao semanal do digest medirem a mesma
semana:

- **dia completo**: ao menos 90% dos fundos de um dia tipico (a mediana dos
  dias lidos, sem os cinco ultimos, que sao os que ainda estao chegando). Dia
  incompleto no FIM da serie sai e e reportado; no meio dela, dia com menos da
  metade do tipico e feriado com linha avulsa (07/09/2026 teve 2 linhas no
  mercado inteiro) e tambem sai;
- **semana de referencia**: a do ultimo dia completo, mesmo parcial, desde que
  tenha ao menos 3 dias completos; com menos, a anterior. A comparacao e por
  dia util, entao a semana curta nao parece fraca. Esperar a sexta chegar
  atrasava o flash de segunda-feira em dez dias;
- **comparacao**: os dias completos das 12 semanas anteriores a segunda-feira
  da semana de referencia - perto de tres meses, a visao de medio prazo.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from shelf_monitor.report.shared.numeros import mediana
from shelf_monitor.texto import periodo_br

SEMANAS_DE_COMPARACAO = 12
# Dia no fim da serie com menos que isto dos fundos de um dia tipico ainda esta
# chegando.
MIN_COBERTURA_DO_DIA = 0.90
# Dia no meio da serie com menos que isto e feriado com linha avulsa.
MIN_COBERTURA_DE_DIA_UTIL = 0.50
# Semana em curso com menos dias completos que isto cede a vez a anterior.
MIN_DIAS_NA_SEMANA = 3
# Os ultimos dias lidos ficam fora do dia tipico: sao os que ainda estao chegando.
_DIAS_RECENTES = 5


def segunda_de(dia: date) -> date:
    """A segunda-feira da semana do dia."""
    return dia - timedelta(days=dia.weekday())


@dataclass(frozen=True)
class DiasDoInforme:
    """Os dias lidos, separados em completos, incompletos (no fim) e descartados."""

    completos: tuple[date, ...]
    # (dia, fracao do dia tipico), em ordem: os que ainda estao chegando.
    incompletos: tuple[tuple[date, float], ...] = ()
    # Dias no meio da serie com linha avulsa - feriado, nao pregao.
    descartados: tuple[date, ...] = ()
    tipico: float = 0.0


def dias_completos(contagem: dict[date, int]) -> DiasDoInforme:
    """Quais dias contam, pela quantidade de fundos que reportaram em cada um.

    `contagem` e dia -> fundos (ou produtos) com linha no informe naquele dia.
    """
    dias = sorted(contagem)
    if not dias:
        return DiasDoInforme(())
    tipico = float(mediana([contagem[d] for d in (dias[:-_DIAS_RECENTES] or dias)]) or 0)
    if tipico <= 0:
        return DiasDoInforme(tuple(dias))
    incompletos: list[tuple[date, float]] = []
    while dias and contagem[dias[-1]] < MIN_COBERTURA_DO_DIA * tipico:
        dia = dias.pop()
        incompletos.append((dia, contagem[dia] / tipico))
    incompletos.reverse()
    descartados = tuple(d for d in dias if contagem[d] < MIN_COBERTURA_DE_DIA_UTIL * tipico)
    fora = set(descartados)
    return DiasDoInforme(
        tuple(d for d in dias if d not in fora), tuple(incompletos), descartados, tipico
    )


@dataclass(frozen=True)
class Semana:
    """Uma semana: a segunda-feira e os dias completos dela, de segunda a sexta."""

    segunda: date
    dias: tuple[date, ...] = ()

    @property
    def sexta(self) -> date:
        return self.segunda + timedelta(days=4)

    @property
    def de(self) -> date:
        return self.dias[0] if self.dias else self.segunda

    @property
    def ate(self) -> date:
        return self.dias[-1] if self.dias else self.sexta

    def rotulo(self) -> str:
        """"de 05/10/2026 a 09/10/2026" - os dias com informe, nao o calendario."""
        return periodo_br(self.de, self.ate)

    def to_dict(self) -> dict:
        return {
            "de": self.de.isoformat(),
            "ate": self.ate.isoformat(),
            "segunda": self.segunda.isoformat(),
            "sexta": self.sexta.isoformat(),
            "dias": len(self.dias),
        }


def _dias_da_semana(segunda: date, dias: tuple[date, ...]) -> tuple[date, ...]:
    sexta = segunda + timedelta(days=4)
    return tuple(d for d in dias if segunda <= d <= sexta)


@dataclass(frozen=True)
class RecorteSemanal:
    """A semana de referencia, a comparacao e o que ficou de fora."""

    semana: Semana
    # Os dias completos das `semanas` anteriores (de segunda a sexta).
    comparacao: tuple[date, ...]
    inicio_da_comparacao: date
    ultimo_completo: date
    dias: DiasDoInforme
    semanas: int = SEMANAS_DE_COMPARACAO

    def semanas_anteriores(self) -> list[Semana]:
        """As semanas da comparacao, da mais antiga para a mais recente."""
        return [
            Semana(segunda, _dias_da_semana(segunda, self.comparacao))
            for segunda in (
                self.semana.segunda - timedelta(weeks=n) for n in range(self.semanas, 0, -1)
            )
        ]

    def todas(self) -> list[Semana]:
        """As semanas da comparacao e a de referencia, em ordem."""
        return [*self.semanas_anteriores(), self.semana]

    def comparacao_dict(self) -> dict:
        return {
            "de": self.comparacao[0].isoformat() if self.comparacao else None,
            "ate": self.comparacao[-1].isoformat() if self.comparacao else None,
            "semanas": self.semanas,
            "dias": len(self.comparacao),
        }

    def incompletos_dict(self) -> list[dict]:
        return [
            {"dia": dia.isoformat(), "cobertura": round(fracao, 3)}
            for dia, fracao in self.dias.incompletos
        ]

    def to_dict(self) -> dict:
        return {
            "semana": self.semana.to_dict(),
            "comparacao": self.comparacao_dict(),
            "ultimo_dia_completo": self.ultimo_completo.isoformat(),
            "dias_incompletos": self.incompletos_dict(),
            "dias_descartados": [d.isoformat() for d in self.dias.descartados],
        }


def recorte_semanal(
    contagem: dict[date, int], semanas: int = SEMANAS_DE_COMPARACAO
) -> RecorteSemanal | None:
    """A semana de referencia e as `semanas` anteriores; None sem dia completo.

    Quem chama garante que `contagem` cubra a comparacao inteira: se
    `inicio_da_comparacao` cair antes do primeiro mes lido, le o que falta e
    chama de novo.
    """
    dias = dias_completos(contagem)
    if not dias.completos:
        return None
    # Dia de fim de semana com linha nao e pregao: fica fora da semana e da
    # comparacao, que contam de segunda a sexta.
    uteis = tuple(d for d in dias.completos if d.weekday() < 5)
    if not uteis:
        return None
    ultimo = uteis[-1]
    segunda = segunda_de(ultimo)
    if sum(1 for d in uteis if segunda <= d <= ultimo) < MIN_DIAS_NA_SEMANA:
        segunda -= timedelta(days=7)
    inicio = segunda - timedelta(weeks=semanas)
    return RecorteSemanal(
        semana=Semana(segunda, _dias_da_semana(segunda, uteis)),
        comparacao=tuple(d for d in uteis if inicio <= d < segunda),
        inicio_da_comparacao=inicio,
        ultimo_completo=ultimo,
        dias=dias,
        semanas=semanas,
    )


def prazos_da_semana(semana: dict, comparacao: dict) -> list[str]:
    """As linhas de prazo de uma leitura semanal, com as datas e os dias uteis.

    `semana` e `comparacao` no formato de `RecorteSemanal.to_dict()`.
    """
    linhas = [
        f"semana: {periodo_br(semana['de'], semana['ate'])} "
        f"({semana['dias']} dias úteis com informe)"
    ]
    if comparacao.get("de"):
        linhas.append(
            f"comparação: {comparacao['semanas']} semanas anteriores, "
            f"{periodo_br(comparacao['de'], comparacao['ate'])} ({comparacao['dias']} dias úteis)"
        )
    return linhas
