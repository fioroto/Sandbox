"""Camada compartilhada dos blocos analiticos.

Existe porque cada leitura nova (captacao, rentabilidade, custo, mandato)
reimplementou o que a anterior ja tinha: a regra de vitrine, a mediana, a
media ponderada, o rotulo da casa, o formatador de percentual e os limiares
de cobertura. O efeito nao era so codigo repetido - era numero divergente. O
caso que obrigou este modulo a existir: `_MIN_COBERTURA` significava tres
coisas diferentes em quatro modulos (0,4 da vitrine comparavel, 0,5 da
vitrine respondida, 0,8 das COMPETENCIAS), e nada no nome dizia qual.

Regra: um conceito, um nome, um lugar.
"""
from shelf_monitor.report.shared.limiares import (
    CARO,
    BARATO,
    MIN_AMOSTRA_CATEGORIA,
    MIN_COBERTURA_VITRINE,
    MIN_COBERTURA_CUSTO,
    MIN_COBERTURA_JANELA,
    MIN_VITRINE,
    SOBRE_INDICE,
    SUB_INDICE,
)
from shelf_monitor.report.shared.informe import (
    caminho_da_competencia,
    ler_competencia,
    ler_filtrado,
    limpar as limpar_informe,
)
from shelf_monitor.report.shared.numeros import (
    mediana,
    ponderada,
    quartis,
    arredonda,
)
from shelf_monitor.report.shared.plataformas import ordem_canonica, rotulo_da_casa

__all__ = [
    "CARO",
    "BARATO",
    "MIN_AMOSTRA_CATEGORIA",
    "MIN_COBERTURA_VITRINE",
    "MIN_COBERTURA_CUSTO",
    "MIN_COBERTURA_JANELA",
    "MIN_VITRINE",
    "SOBRE_INDICE",
    "SUB_INDICE",
    "caminho_da_competencia",
    "ler_competencia",
    "ler_filtrado",
    "limpar_informe",
    "mediana",
    "ponderada",
    "quartis",
    "arredonda",
    "ordem_canonica",
    "rotulo_da_casa",
]
