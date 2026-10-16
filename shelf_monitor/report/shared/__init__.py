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
    MAX_ACOES_MM_CREDITO,
    MAX_COTISTAS_RESTRITO,
    MAX_DIVERGENCIA_PERFIL_CARTEIRA,
    MAX_RETORNO_JANELA_PLAUSIVEL,
    MAX_RETORNO_MENSAL_PLAUSIVEL,
    MIN_AMOSTRA_CATEGORIA,
    MIN_AMOSTRA_TICKET,
    MIN_COBERTURA_VITRINE,
    MIN_COBERTURA_CUSTO,
    MIN_COBERTURA_JANELA,
    MIN_COMPARAVEIS_CLASSE_CUSTO,
    MIN_CREDITO_MM_LIVRE,
    MIN_FATIA_ALVO_UNICO,
    MIN_FATIA_DOMINANTE,
    MIN_FATIA_FOF,
    MIN_FATIA_FUNDOS_MASTER,
    MIN_FATIA_HIBRIDO,
    MIN_FATIA_MAIOR_COTISTA_RESTRITO,
    MIN_RETORNO_JANELA_PLAUSIVEL,
    MIN_VITRINE,
    SOBRE_INDICE,
    SUB_INDICE,
)
from shelf_monitor.report.shared.fontes import (
    Fonte,
    fonte_da_vitrine,
    fontes_do_relatorio,
)
from shelf_monitor.report.shared.informe import (
    caminho_da_competencia,
    ler_competencia,
    ler_filtrado,
    limpar as limpar_informe,
)
from shelf_monitor.report.shared.movimentos import (
    Movimentos,
    movimentos_de_vitrine,
    movimentos_do_run,
    na_vitrine,
)
from shelf_monitor.report.shared.numeros import (
    mediana,
    ponderada,
    quartis,
    arredonda,
)
from shelf_monitor.report.shared.plataformas import (
    ordem_canonica,
    rotulo_da_casa,
    rotulos_sobrepostos,
)

__all__ = [
    "CARO",
    "BARATO",
    "MAX_ACOES_MM_CREDITO",
    "MAX_COTISTAS_RESTRITO",
    "MAX_DIVERGENCIA_PERFIL_CARTEIRA",
    "MAX_RETORNO_JANELA_PLAUSIVEL",
    "MAX_RETORNO_MENSAL_PLAUSIVEL",
    "MIN_RETORNO_JANELA_PLAUSIVEL",
    "MIN_CREDITO_MM_LIVRE",
    "MIN_FATIA_ALVO_UNICO",
    "MIN_FATIA_DOMINANTE",
    "MIN_FATIA_FOF",
    "MIN_FATIA_FUNDOS_MASTER",
    "MIN_FATIA_HIBRIDO",
    "MIN_FATIA_MAIOR_COTISTA_RESTRITO",
    "MIN_AMOSTRA_CATEGORIA",
    "MIN_AMOSTRA_TICKET",
    "MIN_COBERTURA_VITRINE",
    "MIN_COBERTURA_CUSTO",
    "MIN_COBERTURA_JANELA",
    "MIN_COMPARAVEIS_CLASSE_CUSTO",
    "MIN_VITRINE",
    "SOBRE_INDICE",
    "SUB_INDICE",
    "Fonte",
    "fonte_da_vitrine",
    "fontes_do_relatorio",
    "caminho_da_competencia",
    "ler_competencia",
    "ler_filtrado",
    "limpar_informe",
    "Movimentos",
    "movimentos_de_vitrine",
    "movimentos_do_run",
    "na_vitrine",
    "mediana",
    "ponderada",
    "quartis",
    "arredonda",
    "ordem_canonica",
    "rotulo_da_casa",
    "rotulos_sobrepostos",
]
