"""Universo de referencia do custo para a previdencia.

O custo compara a taxa de cada fundo com a mediana da categoria dele no
mercado, e so aceita na mediana classe de varejo: `CustoClasse.restrita` tira
a exclusiva e a de publico profissional, a mesma regra que a vitrine usa.

Na previdencia essa regra apaga a referencia inteira. Medido em 2026-09-22
sobre o cadastro importado: das 4.609 classes de previdencia, 2.086 tinham taxa
comparavel (percentual, entrega recente), mas so 21 serviam de referencia -
1.608 saiam por exclusivas e 333 por publico profissional. Nenhuma categoria de
previdencia chegava as 20 classes de `MIN_AMOSTRA_CATEGORIA`, e o custo saia
"nenhuma vitrine com cobertura suficiente" para toda casa.

E o mesmo fato da particao da vitrine (`eixo.py`): o FIE tem um cotista, a
seguradora, entao e sempre "exclusivo" e de publico "profissional" - e a taxa
dele e o preco do plano que o cliente compra. So nas classes de categoria
previdenciaria, essas duas marcas deixam de tirar a classe da referencia. As
outras guardas do custo seguem valendo: taxa zero (master ou feeder cobrando na
outra ponta), taxa em reais e entrega velha continuam fora.
"""
from __future__ import annotations

from dataclasses import replace

from shelf_monitor.previdencia.eixo import eh_rotulo_previdencia
from shelf_monitor.report.custo import CustoClasse, IndiceCusto


def como_referencia(classe: CustoClasse) -> CustoClasse:
    """A classe sem as marcas de veiculo, se for de previdencia."""
    if eh_rotulo_previdencia(classe.categoria) and classe.restrita:
        return replace(classe, exclusivo=False, publico_alvo="")
    return classe


class IndiceCustoPrevidencia(IndiceCusto):
    """`IndiceCusto` em que a classe de previdencia exclusiva serve de referencia."""

    def __init__(self, classes: dict[str, CustoClasse]) -> None:
        super().__init__({cnpj: como_referencia(c) for cnpj, c in classes.items()})
