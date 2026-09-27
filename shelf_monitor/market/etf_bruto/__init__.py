"""Extrator de dados brutos de ETF (`python -m shelf_monitor etf-bruto`).

Captura, sem calcular nada, o que cada fonte do mapeamento "Indicadores de ETF
e a melhor origem de cada um" (set/2026) publica sobre os ETFs listados:

| Fonte                         | Modulo          | O que fica em data/etf/bruto/        |
|-------------------------------|-----------------|--------------------------------------|
| B3, portal de arquivos        | b3_arquivos     | instrumentos e negocios do pregao    |
| B3, fundos listados           | universo        | lista e detalhe de cada ETF (JSON)   |
| CVM, cadastro                 | universo        | linhas FIIM do registro de classes   |
| Fundos.NET                    | fnet            | informe diario, regulamentos em PDF  |
| Regulamento (texto)           | regulamento     | texto do PDF + sugestoes com trecho  |
| CVM, carteira mensal (CDA)    | composicao      | posicoes dos ETFs no mes             |
| Gestora (arquivo de carteira) | composicao      | o arquivo como veio                  |
| B3, indices                   | indices_b3      | serie anual e carteira do dia        |
| Comdinheiro                   | comdinheiro     | JSON de cada consulta / exportacoes  |
| XP, cadastro interno          | manual          | planilhas em data/etf/manual/        |

O mapeamento em si mora em `mapa.py` (fontes e indicadores) e a cobertura -
quantos ETFs tem o insumo de cada indicador - em `cobertura.py`. O que e
derivado (mediana de 63 pregoes, premio, tracking difference) fica para a
etapa seguinte: aqui so se guarda o insumo, com o manifesto de cada execucao.
"""
