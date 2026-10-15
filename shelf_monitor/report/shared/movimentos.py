"""O que conta como movimento de prateleira: entrada, saida ou alteracao na vitrine.

Uma definicao so, para toda superficie que conta movimento. Antes cada uma
contava o seu: o digest ja filtrava a vitrine de varejo, mas o assunto do
e-mail, o resumo do console, o deck e o payload do dashboard contavam o diff
inteiro - catalogo Open Finance e restritos inclusos. Medido no BTG de 13 para
19/09: 236 movimentos no diff inteiro, 174 na vitrine; o deck ainda dava como
exemplo de entrada fundos que so estavam no catalogo.

O diff continua rodando sobre tudo que foi coletado (fundo novo so no Open
Finance e sinal de registro); o que muda e o que cada superficie chama de
movimento. Nova linha de base e primeira coleta nao tem movimento nenhum.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from shelf_monitor.models import FundChange, FundOffer, RunReport, ShelfDiff, ShelfSnapshot
from shelf_monitor.profile import na_vitrine

__all__ = ["Movimentos", "movimentos_de_vitrine", "movimentos_do_run", "na_vitrine"]


@dataclass
class Movimentos:
    """Movimento de vitrine de uma casa (ou a soma de varias)."""

    entradas: list[FundOffer] = field(default_factory=list)
    saidas: list[FundOffer] = field(default_factory=list)
    alteracoes: list[FundChange] = field(default_factory=list)
    # Mudancas que ficaram so no catalogo ou nos restritos: contadas, nao listadas.
    so_catalogo: int = 0

    @property
    def vazio(self) -> bool:
        return not (self.entradas or self.saidas or self.alteracoes)


def movimentos_de_vitrine(diff: ShelfDiff, snapshot: ShelfSnapshot | None) -> Movimentos:
    """Entradas, saidas e alteracoes na vitrine; o resto vira `so_catalogo`.

    `changed` so carrega identity e nome, entao a pertinencia dele vem do
    snapshot atual - as identidades que estao na vitrine de hoje. Diff sem
    comparacao (primeira coleta, nova linha de base) nao tem movimento.
    """
    if diff.is_first_run:
        return Movimentos()
    vitrine_ids = (
        {f.identity for f in snapshot.funds if na_vitrine(f)} if snapshot else set()
    )
    entradas = [f for f in diff.added if na_vitrine(f)]
    saidas = [f for f in diff.removed if na_vitrine(f)]
    alteracoes = [c for c in diff.changed if c.identity in vitrine_ids]
    so_catalogo = (
        (len(diff.added) - len(entradas))
        + (len(diff.removed) - len(saidas))
        + (len(diff.changed) - len(alteracoes))
    )
    return Movimentos(entradas, saidas, alteracoes, so_catalogo)


def movimentos_do_run(report: RunReport) -> Movimentos:
    """A soma de todas as casas - o numero do assunto e da frase de abertura."""
    total = Movimentos()
    for diff in report.shelf_diffs:
        casa = movimentos_de_vitrine(diff, report.snapshot_for(diff.platform))
        total.entradas += casa.entradas
        total.saidas += casa.saidas
        total.alteracoes += casa.alteracoes
        total.so_catalogo += casa.so_catalogo
    return total
