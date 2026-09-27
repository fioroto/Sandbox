"""Os limiares de corte dos blocos analiticos, um nome por conceito.

Antes deste modulo havia cinco nomes para dois conceitos, e o mesmo nome
`_MIN_COBERTURA` significava tres coisas em modulos diferentes:

    custo._MIN_COBERTURA          = 0.4   fracao da VITRINE comparavel
    mandato._MIN_COBERTURA        = 0.5   fracao da VITRINE respondida
    performance._MIN_COBERTURA    = 0.5   fracao da VITRINE com serie
    rentabilidade._MIN_COBERTURA  = 0.8   fracao das COMPETENCIAS (outro eixo)
    performance._MIN_COBERTURA_JANELA = 0.8   o mesmo que o de cima, outro nome
    retail_flow._MIN_MATCH_RATIO  = 0.5   o mesmo que performance, outro nome

Quem lesse um modulo e fosse para o outro levava a definicao errada junto.
"""
from __future__ import annotations

# --- cobertura: quanto da VITRINE precisa ter respondido para a casa entrar --

# Com meia vitrine casada, a soma descreve a amostra que casou, nao a vitrine.
# Vale para captacao, rentabilidade ponderada e mandato.
MIN_COBERTURA_VITRINE = 0.5

# Custo aceita menos porque ~40% do mercado com taxa nao reentrega o extrato:
# exigir 0,5 aqui derrubaria casa que tem amostra suficiente para a mediana.
# A cobertura efetiva sai impressa ao lado de todo indice, entao o leitor ve
# sobre quanto o numero fala.
MIN_COBERTURA_CUSTO = 0.4

# --- cobertura: quanto da JANELA o fundo precisa ter sido observado ----------

# Outro eixo: aqui a fracao e de COMPETENCIAS, nao de fundos. Sem isto, um
# fundo visto so nos dois ultimos meses entraria com retorno de duas
# competencias ao lado de outro com doze.
MIN_COBERTURA_JANELA = 0.8

# --- amostra minima ---------------------------------------------------------

# Abaixo disto a mediana da categoria e ruido, nao referencia.
MIN_AMOSTRA_CATEGORIA = 20

# Vitrine menor que isto nao e vitrine: e o que sobrou de uma coleta parcial.
MIN_VITRINE = 30

# Fundos com aplicacao minima lida da propria vitrine para o ticket da casa
# significar algo. Abaixo disso a casa entra como "sem base na coleta", nunca
# como "nao tem fundo barato" - era o que a leitura de ticket e o posicionamento
# competitivo decidiam com duas regras diferentes.
MIN_AMOSTRA_TICKET = 30

# --- faixas de indice -------------------------------------------------------

# Fora desta faixa a diferenca e aposta declarada, nao arredondamento. Usada
# pelo posicionamento por categoria e pelo mandato.
SOBRE_INDICE = 1.35
SUB_INDICE = 0.7

# Custo usa uma faixa propria e mais estreita no topo: 1,3 e nao 1,35. A
# diferenca e deliberada - a taxa e um numero declarado, com menos ruido de
# amostra que um share de categoria, entao o corte pode ser mais sensivel.
CARO = 1.3
BARATO = 0.7
