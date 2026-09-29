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

# --- universo e estrutura da industria de fundos ----------------------------
# Fracoes do PL de uma classe, lidas do perfil mensal da CVM (quem e o dono do
# dinheiro) ou da carteira (em que a classe investe). Medido em 28/09/2026 no
# perfil de 08/2026 e na carteira de 08/2026; ver `shelf_monitor/industria`.

# Classe com ao menos esta fracao do PL em maos de outros fundos e master:
# veiculo interno da estrutura, que empresta a estrategia aos feeders mas nao
# e vendido a ninguem. 3.893 classes e R$ 2,9 tri em 08/2026.
MIN_FATIA_FUNDOS_MASTER = 0.90

# A partir daqui o dinheiro de um tipo de cotista domina a classe a ponto de
# ela nao ser produto de PF/PJ: previdencia (seguradora e EAPC), institucional
# (fundo de pensao, RPPS, banco...) e nao residente. Mais alto que o do master
# de proposito: o master e estrutura declarada pela carteira, estes sao
# composicao de cotistas, que oscila de mes a mes.
MIN_FATIA_DOMINANTE = 0.95

# Restrito de fato: o maior cotista tem ao menos esta fracao do PL e a classe
# tem ate MAX_COTISTAS_RESTRITO cotistas - clube de familia com nome de fundo.
MIN_FATIA_MAIOR_COTISTA_RESTRITO = 0.50
MAX_COTISTAS_RESTRITO = 10

# Carteira: classe com ao menos esta fracao em cotas de UMA classe e feeder ou
# espelho dela - o mesmo corte de `familias.LIMIAR_ESPELHO`.
MIN_FATIA_ALVO_UNICO = 0.95
# Com ao menos esta fracao em cotas de fundos, sem alvo unico, e FoF.
MIN_FATIA_FOF = 0.67
# Entre esta fracao e MIN_FATIA_FUNDOS_MASTER detida por fundos, a classe e
# hibrida: tem investidor final e fundo ao mesmo tempo.
MIN_FATIA_HIBRIDO = 0.10

# Perfil e carteira medem a fatia detida por fundos por caminhos diferentes;
# acima desta diferenca, em pontos, a classe vai para o alerta de qualidade.
# 9,3% do PL passava de 25 p.p. em 08/2026 (clubes, sigilo e fundos de
# previdencia, que a carteira nao ve).
MAX_DIVERGENCIA_PERFIL_CARTEIRA = 0.25

# Multimercados Livre com ao menos esta fracao em credito na carteira da raiz,
# e menos que MAX_ACOES_MM_CREDITO em acoes, e credito com outro nome.
MIN_CREDITO_MM_LIVRE = 0.60
MAX_ACOES_MM_CREDITO = 0.10

# Retorno de cota acima disto num mes nao e desempenho, e evento na cota:
# grupamento, desdobramento ou erro de dado. Medido no painel de 10/2025 a
# 08/2026: 293 linhas passavam de 50% no mes (uma chegava a x1.000.000) e
# somavam R$ 2,4 tri de "rendimento". Nesses meses o fluxo implicito dos fundos
# cai no rateio e o rendimento vai para o residuo, publicado.
MAX_RETORNO_MENSAL_PLAUSIVEL = 0.50
# O mesmo para a janela de rentabilidade (12 ou 3 meses): fora desta faixa o
# retorno sai da mediana do peer group e do quartil.
MAX_RETORNO_JANELA_PLAUSIVEL = 5.0
MIN_RETORNO_JANELA_PLAUSIVEL = -0.95
